"""Flask 웹 레이어 — 차익 큐레이션 JSON API (W1).

엔드포인트:
  GET /health                 헬스체크
  GET /api/listings           차익 스코어순 목록 (min_score/type/region/sort 쿼리 필터)
  GET /api/listings/<case_no> 단건 상세 (없으면 404)

데이터는 PoC 샘플(pipeline.run). 키가 있으면 추후 라이브로 전환(F10). FastAPI/pydantic 미사용
(Python 3.14 빌드 리스크 회피) — 순수 파이썬 Flask.
"""
from __future__ import annotations

import html
import logging
import os
import threading
import time
from pathlib import Path

from flask import Flask, g, jsonify, request

from . import (
    bidsim,
    pipeline,
    query,
    report,
    store,
    store_rest,
)

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DB_ENV = "AUCTION_DB"   # 설정 시 라이브 적재 DB에서 서빙, 미설정 시 샘플 계산


def _mark_source(src: str) -> None:
    """이번 응답이 어떤 데이터 출처(db/sample*)인지 flask.g에 기록. after_request가 헤더로 노출.

    요청 컨텍스트 밖(테스트에서 _scored 직접 호출)에서는 조용히 무시한다.
    """
    try:
        g.data_source = src
    except RuntimeError:
        pass


def _probe_source() -> str:
    """데이터 출처를 '읽기전용'으로 판정(로드 없이). /health·헤더용.

    반환: db | sample(db-empty) | sample(db-error) | sample(no-db)
    """
    db_path = os.environ.get(DB_ENV)
    if db_path:
        try:
            conn = store.connect(db_path)
            try:
                return "db" if store.has_rows(conn) else "sample(db-empty)"
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — 상태 조회 실패도 폴백 상태의 일부
            return "sample(db-error)"
    if store_rest.enabled():
        try:
            return "db" if store_rest.has_rows() else "sample(db-empty)"
        except Exception:  # noqa: BLE001
            return "sample(db-error)"
    return "sample(no-db)"


def _scored():
    """채점된 매물 목록.

    AUCTION_DB 환경변수가 가리키는 DB에 적재된 결과가 있으면 그것을 서빙한다
    (새로고침 작업이 `run.py --live`로 채워둔 라이브 결과 — 매 요청 API 호출 회피).
    DB가 없거나 비었으면 샘플 데이터로 폴백하되, '라이브인 줄 오인'을 막기 위해
    폴백 사유를 로그로 남기고 출처를 표시한다(응답 헤더 X-Data-Source, /health).
    """
    db_path = os.environ.get(DB_ENV)
    if db_path:
        try:
            conn = store.connect(db_path)
            try:
                if store.has_rows(conn):
                    _mark_source("db")
                    return _enrich_naver(store.load_scored(conn))
            finally:
                conn.close()
            # 연결은 됐지만 적재 결과 0건 — 조용히 샘플로 넘어가지 않도록 경고(침묵실패 방지).
            logger.warning(
                "AUCTION_DB(%s) 연결됐으나 적재 결과 0건 → 샘플 폴백(라이브 데이터 아님). "
                "새로고침(run.py --live)이 실패했거나 아직 실행 전일 수 있음.", db_path)
            _mark_source("sample(db-empty)")
        except Exception as e:  # noqa: BLE001 — DB 문제 시 샘플로 안전 폴백
            logger.error("DB 서빙 실패(%s) → 샘플 폴백: %s", db_path, e, exc_info=True)
            _mark_source("sample(db-error)")
    elif store_rest.enabled():
        # 클라우드 서빙(Vercel): AUCTION_DB 미설정 + SUPABASE_URL 있으면 REST 에서 읽는다.
        try:
            rows = store_rest.load_scored()
            if rows:
                _mark_source("db")
                return _enrich_naver(rows)
            logger.warning(
                "Supabase 연결됐으나 적재 결과 0건 → 샘플 폴백(라이브 데이터 아님). "
                "새로고침(run.py --live)이 실패했거나 아직 실행 전일 수 있음.")
            _mark_source("sample(db-empty)")
        except Exception as e:  # noqa: BLE001 — REST 문제 시 샘플로 안전 폴백
            logger.error("Supabase 서빙 실패 → 샘플 폴백: %s", e, exc_info=True)
            _mark_source("sample(db-error)")
    else:
        _mark_source("sample(no-db)")
    return _enrich_naver(pipeline.run())


# ── 데이터 신선도 (2026-08-24 침묵실패 감사 CRITICAL) ─────────────────────────
# 크롤이 며칠 조용히 실패해도(부분 파서 드리프트, 커버리지 플로어 반복 발동, 국토부 장애)
# 화면·API 는 '오늘 데이터'처럼 보였다 — rendered_at 은 렌더 시각이라 데이터 나이의 근거가
# 못 된다. 여기서 실제 수집 시각을 계산해 전 화면 배너·/health 에 노출한다.
STALE_HOURS = 36.0   # watchdog.ps1 의 신선도 임계와 동일값 — 두 감시가 같은 기준을 봐야 한다


def _parse_ts(ts: str):
    """'2026-08-24 12:52:35'(SQLite, naive 로컬) / KST ISO(store_rest) 둘 다 aware 로."""
    import datetime as _dt  # noqa: PLC0415
    s = ts.strip().replace("Z", "+00:00")
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    d = _dt.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.datetime.now().astimezone().tzinfo)
    return d


def data_freshness() -> tuple[str | None, float | None]:
    """(수집시각 ISO, 나이(시간)) — 백엔드 미구성/조회 실패는 (None, None) = '미상'.

    '미상'과 '신선'을 구분해 반환하는 게 핵심이다 — 실패를 0시간으로 보고하면
    그게 또 하나의 침묵실패가 된다.
    """
    import datetime as _dt  # noqa: PLC0415
    ts = None
    try:
        db_path = os.environ.get(DB_ENV)
        if db_path:
            conn = store.connect(db_path)
            try:
                ts = store.last_fetched(conn)
            finally:
                conn.close()
        elif store_rest.enabled():
            ts = store_rest.last_refreshed()
    except Exception as e:  # noqa: BLE001 — 신선도 조회 실패가 페이지를 죽이면 안 됨
        logger.warning("데이터 신선도 조회 실패(미상 처리): %s", e)
        return None, None
    if not ts:
        return None, None
    try:
        age_h = (_dt.datetime.now(_dt.UTC) - _parse_ts(ts)).total_seconds() / 3600.0
    except ValueError:
        logger.warning("데이터 신선도 시각 파싱 실패(미상 처리): %r", ts)
        return None, None
    return ts, max(age_h, 0.0)


# 권리 로드 최근 실패 기록(모듈 수준) — /health 노출 + fail-closed 게이트용(적대감사 F7).
# Supabase 스키마 드리프트(400) 등으로 배지가 '전멸'하면 종전엔 경고 로그 한 줄뿐이었고,
# 히어로의 clean-배지 게이트가 `if not badges: return True` 로 통째로 우회됐다.
_rights_last_error: dict = {"at": 0.0, "msg": ""}


def _rights_rows() -> list[dict]:
    """권리 요지 전량 행. 요청당 1회만 로드(g 캐시) — 배지·재매각이 함께 소비한다.

    (2026-07-23) 재매각 배지 도입 전엔 _rights_badges 가 직접 로드했다. 소비자가 둘로
    늘면서 같은 테이블을 요청당 두 번 읽게 되므로 로더를 분리해 캐시한다(왕복 절반).
    실패는 빈 리스트 폴백이되 **침묵하지 않는다** — g._rights_failed 로 같은 요청 안의
    소비자(히어로 게이트 등)가 '없음'과 '로드 실패'를 구분하고, 모듈 기록으로 /health 가 노출.
    """
    import time as _time  # noqa: PLC0415

    from flask import has_request_context  # noqa: PLC0415
    if has_request_context() and hasattr(g, "_rightsrows"):
        return g._rightsrows
    rows: list[dict] = []
    db_path = os.environ.get(DB_ENV)
    try:
        if db_path:
            conn = store.connect(db_path)
            try:
                rows = store.fetch_all_rights(conn)
            finally:
                conn.close()
        elif store_rest.enabled():
            rows = store_rest.load_all_rights()
    except Exception as e:  # noqa: BLE001 — 로드 실패는 '미확인' 폴백(침묵 아님 — 에러 로그+플래그)
        logger.error("권리 요지 로드 실패 → 배지·재매각 전량 미표시 폴백: %s", e)
        _rights_last_error["at"] = _time.time()
        _rights_last_error["msg"] = str(e)[:200]
        if has_request_context():
            g._rights_failed = True
        rows = []
    if has_request_context():
        g._rightsrows = rows
    return rows


# 권리 행에서 파생되는 맵(배지·재매각)의 프로세스 캐시.
# 배지 판정(summarize)은 11,833건에 **1.6초**가 드는데(실측 2026-07-23) 원천 데이터는
# 하루 한 번 새로고침 때만 바뀐다. 매 요청 재계산은 순수 낭비다.
# ⚠️ 스테일 상한(적대감사 F6 정정): 로컬 SQLite 경로는 지문이 파일을 직접 보므로 즉시 반영이
# 맞지만, 클라우드(REST) 경로의 입력 행 자체가 store_rest 의 TTL 캐시(기본 600초)를 통과한다 —
# 즉 프로덕션의 실질 스테일 상한 = SUPABASE_CACHE_TTL 이다("스테일 창 없음"은 로컬만의 사실).
_derived_cache: dict = {"key": None, "badges": None, "resales": None}


# 판정이 실제로 읽는 필드 — 지문은 이것들의 변화를 잡아야 한다.
_FP_FIELDS = ("schedule", "remark", "surviving_rights", "lien_note", "senior_lien")


def _rights_fingerprint(rows: list[dict]) -> tuple:
    """행 집합의 지문 — 한 번 순회(수십 ms)로 1.6초 재계산을 건너뛴다.

    (행 수, 최신 fetched_at)만으로는 부족하다: 같은 크롤 런의 재크롤은 행 수·max(fetched_at)을
    못 움직인다(런당 now 1회 공유). 1차 보강(필드 길이 합)도 **등길이 수정**('유찰'→'매각',
    5,000→8,000만원 — 기일 결과 어휘가 전부 2자라 계통적)과 **필드 간 문구 이동**(remark→
    surviving_rights: 길이합 불변인데 clean↔burden 판정이 뒤집힘)을 놓쳤다(적대감사 F5, 실행 재현).
    그래서 행마다 판정 필드들을 **crc32 로 체인**(행 내 필드 순서·내용 반영)하고 행 간에는
    합산한다(행 순서 무관). 출처 식별자를 함께 넣어 DB 간 캐시 누출을 막는다.
    """
    import zlib  # noqa: PLC0415
    src = os.environ.get(DB_ENV) or os.environ.get("SUPABASE_URL") or ""
    if not rows:
        return (src, 0, "", 0)
    latest = ""
    sig = 0
    for r in rows:
        f = r.get("fetched_at") or ""
        if f > latest:
            latest = f
        h = 0
        for k in _FP_FIELDS:
            v = r.get(k)
            if v:
                s = v if isinstance(v, str) else str(v)
                h = zlib.crc32(s.encode("utf-8", "ignore"), h)
            h = zlib.crc32(b"|", h)   # 필드 경계 — 이동·병합이 같은 해시가 되지 않게
        sig = (sig + h) & 0xFFFFFFFFFFFFFFFF
    return (src, len(rows), latest, sig)


def _derived_maps() -> tuple[dict, dict]:
    """(배지, 재매각) 맵을 **한 번의 순회**로 만들고 지문 캐시에 담는다.

    이전엔 두 함수가 각자 전체 행을 돌며 `CaseRights.from_row` 를 **중복 수행**했고,
    그 결과가 매 요청 재계산됐다. 배지 판정만 1.6초다(11,833건, 실측 2026-07-23).
    원천은 하루 한 번 크롤 때만 바뀌므로 지문이 같으면 그대로 재사용한다.
    """
    from .courtauction_detail import CaseRights, resale_history, summarize  # noqa: PLC0415
    rows = _rights_rows()
    key = _rights_fingerprint(rows)
    if _derived_cache["key"] == key and _derived_cache["badges"] is not None:
        return _derived_cache["badges"], _derived_cache["resales"]

    badges: dict = {}
    resales: dict = {}
    for r in rows:
        cr = CaseRights.from_row(r)
        uid = f"{cr.court}|{cr.case_no}|{cr.item_no}"
        # (서빙감사 2026-07-12 #13) 빈/부분 응답(작성일·최선순위·인수권리 전무)은 판정 근거가
        # 0 이므로 배지를 만들지 않는다 — '✓ 인수 없음'으로 오판하지 않고 '미확인'으로 폴백.
        # (2026-07-22) 자유기술란 3칸이 전부 빈 요지도 대항력 판정근거 0 → 배지 미생성(초록 오표시 방지).
        if not (cr.is_empty or not cr.opposability_assessable):
            badges[uid] = summarize(cr)
        # 재매각은 **배지 게이트와 무관**하게 판정한다 — 자유기술란이 비어도 기일 이력은 살아 있다.
        info = resale_history(cr.schedule)
        if info:
            resales[uid] = info

    _derived_cache.update(key=key, badges=badges, resales=resales)
    return badges, resales


def _resale_map() -> dict:
    """(court|case_no|item_no) → ResaleHistory. 재매각 아닌 물건은 키 자체가 없다.

    '과거에 낙찰됐다가 미납·불허가로 되돌아온 물건'은 통계가 아니라 **그 물건의 확정 사실**이라
    표본 게이트 없이 그대로 표시한다. 판정 단위는 물건(item) — schedule 이 물건별로 다르다.
    """
    return _derived_maps()[1]


def _rights_badges() -> dict:
    """(court|case_no|item_no) → RightsBadge. 크롤된 물건만 담긴다(없으면 '미확인' 렌더).

    목록의 '인수 부담' 칩·예상 투입 계산용.
    """
    return _derived_maps()[0]


def _naver_map() -> dict:
    """(court|case_no|item_no) → naver_prices dict. 요청당 1회 로드(g 캐시). 실패 시 {} 폴백."""
    from flask import g, has_request_context  # noqa: PLC0415
    if has_request_context() and hasattr(g, "_navermap"):
        return g._navermap
    rows: list[dict] = []
    db_path = os.environ.get(DB_ENV)
    try:
        if db_path:
            conn = store.connect(db_path)
            try:
                rows = store.load_all_naver(conn)
            finally:
                conn.close()
        elif store_rest.enabled():
            rows = store_rest.load_all_naver()
    except Exception as e:  # noqa: BLE001 — 네이버 로드 실패는 국토부 추정으로 무해 degrade
        logger.warning("네이버 KB시세 로드 실패 → 국토부 추정 유지: %s", e)
        rows = []
    m = {f"{r['court']}|{r['case_no']}|{r['item_no']}": r for r in rows}
    if has_request_context():
        g._navermap = m
    return m


def _enrich_naver(rows: list) -> list:
    """각 물건에 네이버 KB시세·호가를 붙이고, KB 있으면 시세·차익을 KB 기준으로 재계산(score.market_view)."""
    from .score import market_view  # noqa: PLC0415
    nm = _naver_map()
    return [market_view(s, nm.get(f"{s.court}|{s.case_no}|{s.item_no}")) for s in rows]


def _burden_of(badges: dict):
    """badges → (물건 → 인수금액 원) 콜러블. 미크롤 물건은 0(차감 없음)."""
    def f(s):
        b = badges.get(f"{s.court}|{s.case_no}|{s.item_no}")
        return b.assumed if b else 0
    return f


def _uncertain_of(badges: dict):
    """badges → (물건 → 인수 부담인데 금액 미상인가). 정렬 하위 티어 강등용(서빙감사 #1·#9)."""
    def f(s):
        b = badges.get(f"{s.court}|{s.case_no}|{s.item_no}")
        return bool(b and b.amount_unknown)
    return f


def _filtered(args, badges: dict | None = None, evaluable_default: bool = False):
    """요청 쿼리(min_profit[억]/min_score/type/region/sort)로 필터·정렬된 목록.

    정렬·최소차익 필터는 인수금 차감 후 유효 차익 기준 — 화면 표시(p_adj)와 일치
    (감사 2026-07-10: 순위-표시 역전 해소).

    evaluable_default: 지도(③)처럼 '차익후보(평가 가능)만'을 기본으로 하는 표면용.
    `all=1`이면 미지원·시세추정불가까지 전부 노출, `evaluable=0/1`로 명시 오버라이드.
    listings/export 등 기존 표면은 evaluable_default=False라 동작 불변.
    """
    min_score = args.get("min_score", type=float)          # API 하위호환용
    min_profit_eok = args.get("min_profit", type=float)    # UI: 억 단위 입력
    min_profit = int(min_profit_eok * 1e8) if min_profit_eok else None
    ptype = args.get("type")
    region = args.get("region")
    sort = args.get("sort", query.DEFAULT_SORT)
    if sort not in query.SORT_KEYS:
        sort = query.DEFAULT_SORT
    if args.get("evaluable") is not None:
        evaluable_only = _truthy(args.get("evaluable"))
    else:
        evaluable_only = evaluable_default and not _truthy(args.get("all"))
    bd = badges if badges is not None else _rights_badges()
    burden = _burden_of(bd)
    return query.sort_items(
        query.apply_filters(_scored(), min_score, ptype, region,
                            min_profit=min_profit, burden_of=burden,
                            evaluable_only=evaluable_only),
        sort, burden_of=burden, uncertain_of=_uncertain_of(bd))


def create_app() -> Flask:
    """앱 팩토리 — 라우트는 전부 views/ blueprint (2026-08-24 분리, 감사 코드품질 CRITICAL).

    여기 남는 것: Jinja 전역·필터, 앱 설정 스냅샷(관리자 키·CSS 해시), 요청 훅
    (X-Data-Source 태깅·rate limit·신선도 주입), blueprint 등록, 캐시 웜업.
    """
    app = Flask(__name__, template_folder=str(ROOT / "templates"))
    app.json.ensure_ascii = False   # 한글 그대로 직렬화
    app.json.sort_keys = False
    # 전용면적 평 환산은 모든 화면(홈·리스트·상세)에서 쓰므로 Jinja 전역으로 한 번만 등록.
    app.jinja_env.globals["pyeong"] = report.pyeong
    # 시뮬레이터 영수증은 소액(인지세 15만·법무비 50만)이 섞여 억 단위 표기로는 전부 '0.00억'이 된다.
    app.jinja_env.globals["won_fine"] = report.won_fine
    # 법원 축약명("고양지원")만으로는 경매사건검색 드롭다운(정식 명칭)에서 못 찾는다 — QA 2026-07-26.
    from . import court_names  # noqa: PLC0415
    app.jinja_env.globals["full_court_name"] = court_names.full_court_name

    # 법원 자유텍스트 HTML 엔티티 복원 필터. 크롤 시점(_sanitize)에서 이미 해제하지만,
    # 재크롤 전 DB/Supabase 에 남은 옛 데이터(&amp;quot; &lt; …)를 렌더 시점에도 복원해
    # 화면이 즉시 정상화되게 한다. 반환값은 평문이라 Jinja 자동이스케이프가 다시 안전하게 처리.
    def _deent(value):
        if not value:
            return value
        s = str(value)
        for _ in range(3):
            u = html.unescape(s)
            if u == s:
                break
            s = u
        return s

    app.jinja_env.filters["deent"] = _deent

    @app.after_request
    def _tag_data_source(resp):
        # 모든 응답에 데이터 출처를 노출 — 샘플을 라이브로 오인하는 것을 방지.
        resp.headers["X-Data-Source"] = getattr(g, "data_source", "n/a")
        return resp

    # ── 접근 제어 + 요청 제한 (2026-08-24 보안감사) ──────────────────────────────
    # 발견: ①워치리스트(운영자의 입찰 관심 = 금전 직결 정보)가 공개 URL에서 무인증
    # 읽기/쓰기 ②/find 라이브 조회가 익명 사용자발로 대법원 사이트 요청을 무제한 유발
    # (밴 → 크롤 전체 마비) ③전 API rate limit 부재.
    # 키는 앱 생성 시점에 config 로 스냅샷 — 가드 자체는 views/auth.py(_is_admin).
    app.config["AUCTION_ADMIN_KEY"] = os.environ.get("AUCTION_ADMIN_KEY", "").strip()

    from .views.auth import _is_admin  # noqa: PLC0415

    # 인메모리 슬라이딩 윈도(프로세스/서버리스 인스턴스 단위). 완전한 방어가 아니라
    # 단일 IP 폭주를 끊는 1차 저지선 — 인스턴스가 늘면 한도도 같이 늘어나는 한계는 안다.
    _RL_MAX = int(os.environ.get("AUCTION_RL_MAX", "120"))     # 윈도당 요청 수
    _RL_WIN = float(os.environ.get("AUCTION_RL_WIN", "60"))    # 윈도(초)
    _rl_lock = threading.Lock()
    _rl_hits: dict[str, list[float]] = {}

    @app.before_request
    def _rate_limit():
        p = request.path
        if not (p.startswith("/api/") or p.startswith("/export") or p == "/find"):
            return None
        if _is_admin():
            return None
        # Vercel 프록시 뒤에서는 X-Forwarded-For 첫 항목이 클라이언트 IP.
        ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
              or request.remote_addr or "?")
        now = time.time()
        with _rl_lock:
            hits = [t for t in _rl_hits.get(ip, ()) if now - t < _RL_WIN]
            if len(hits) >= _RL_MAX:
                _rl_hits[ip] = hits
                return jsonify({"error": "rate_limited",
                                "detail": f"분당 {_RL_MAX}회를 초과했습니다."}), 429
            hits.append(now)
            _rl_hits[ip] = hits
            if len(_rl_hits) > 10_000:   # 악의적 IP 스푸핑으로 딕셔너리가 무한 성장하는 것 방지
                _rl_hits.clear()
        return None

    @app.context_processor
    def _inject_freshness():
        # 전 템플릿에 데이터 나이 노출(2026-08-24 침묵실패 감사) — base.html 이 36h 초과 시
        # 배너를 띄운다. 조회 실패는 (None, None)='미상' 이라 배너가 조용히 빠질 수 있는데,
        # 그 경우도 /health 의 data_asof=null 로는 잡힌다(완전 침묵은 아님).
        asof, age_h = data_freshness()
        return {"data_asof": asof, "data_age_hours": age_h, "data_stale_hours": STALE_HOURS}

    # base.css 콘텐츠 해시(앱 생성 시 1회) — <link href="/base.css?v=..."> 캐시 버스팅.
    # 인라인 61KB CSS 를 외부화하면서(2026-07-23) 브라우저·SW 캐시가 가능해졌고,
    # 배포로 파일이 바뀌면 해시가 바뀌어 즉시 새 CSS 를 받는다(immutable 캐시와 안전 공존).
    # 서빙(views/assets.py)은 app.config["BASE_CSS_V"] 와 대조한다.
    import hashlib  # noqa: PLC0415
    try:
        _css_v = hashlib.md5((ROOT / "static" / "base.css").read_bytes()).hexdigest()[:8]
    except OSError:
        logger.warning("static/base.css 없음 — 스타일이 렌더되지 않습니다(배포 번들 확인 필요)")
        _css_v = "0"
    app.config["BASE_CSS_V"] = _css_v
    app.jinja_env.globals["base_css_v"] = _css_v

    # 렌더 시각(UTC ISO) — base.html <meta name="rendered-at"> 용. SW 캐시본의 '나이'를
    # 페이지 스스로 계산해 '저장된 화면 · N분 전 기준' 정직성 표식을 띄운다(적대감사 F4).
    import datetime as _dt  # noqa: PLC0415
    app.jinja_env.globals["rendered_at"] = (
        lambda: _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"))

    # 딜 시뮬 — 세율·규제 데이터는 Jinja 전역으로 템플릿에 주입(엔진 JS 는 views/assets.py).
    from . import regulation  # noqa: PLC0415
    app.jinja_env.globals["regulation_classify"] = regulation.classify
    # 규칙 파일 부재가 앱 부팅을 죽이면 안 된다(콜드부팅 실패 = 사이트 전체 500 — 2026-07-24 전례).
    # 실패 시 "null" 주입 → 템플릿 JS 가 존만 비활성하고 나머지 페이지는 정상.
    try:
        app.jinja_env.globals["dealsim_rules_json"] = (
            ROOT / "data" / "dealsim_rules.json").read_text(encoding="utf-8")
    except OSError:
        logger.error("data/dealsim_rules.json 없음 — 딜 시뮬 존 비활성(배포 번들 확인 필요)")
        app.jinja_env.globals["dealsim_rules_json"] = "null"

    # ── 라우트 전부 — views/ blueprint 9종 등록 ──
    from .views import register_views  # noqa: PLC0415
    register_views(app)

    # (2026-07-24 QA ⑦) 기동 직후 첫 요청이 콜드 캐시(권리배지 파생캐시 등)로 계산돼
    # 웜업 후와 칩 카운트가 달라지는 문제 — 백그라운드로 한 번 미리 데워 첫 응답부터
    # 정상상태와 동일하게 만든다. 실패해도 서빙엔 영향 없음(다음 요청이 다시 계산).
    def _warm_caches():
        try:
            _rights_badges()
            _scored()
            logger.info("캐시 웜업 완료(권리배지·채점결과)")
        except Exception as e:  # noqa: BLE001 — 웜업 실패는 치명 아님, 로그만
            logger.warning("캐시 웜업 실패(무시하고 요청 시 계산): %s", e)

    # pytest(앱을 다회 생성)와 명시적 opt-out(AUCTION_WARM=0)에선 웜업 생략.
    if (os.environ.get("AUCTION_WARM", "1") != "0"
            and "PYTEST_CURRENT_TEST" not in os.environ):
        # threading 은 모듈 상단 임포트(2026-08-24 rate limit 도입으로 승격) — 여기서 지역
        # 임포트하면 create_app 스코프 전체에서 상단 임포트를 가리는 UnboundLocalError.
        threading.Thread(target=_warm_caches, name="warm-caches", daemon=True).start()

    return app


def _truthy(val: str | None) -> bool:
    """env flag → bool. 미설정/빈값/0/false/no/off 는 False."""
    return (val or "").strip().lower() in {"1", "true", "yes", "on"}


# 시뮬레이터 입력 상한 — 사용자 입력이 그대로 계산에 들어가므로 방어적으로 자른다.
_MAX_WON = 10_000_000_000_000     # 10조(현실 경매가 상한을 한참 넘김)
_MAX_COST = 10_000_000_000        # 부대비용 항목 상한 100억


def _clamp_int(raw: str | None, lo: int, hi: int, default: int = 0) -> int:
    """쿼리 문자열 → [lo, hi] 정수. 파싱 실패·미입력은 default(침묵 0 폴백 금지)."""
    if raw is None or raw == "":
        return default
    try:
        return max(lo, min(hi, int(float(raw))))
    except (TypeError, ValueError):
        return default


def _clamp_float(raw: str | None, lo: float, hi: float, default: float) -> float:
    if raw is None or raw == "":
        return default
    try:
        return max(lo, min(hi, float(raw)))
    except (TypeError, ValueError):
        return default


def _sim_payload(inp: bidsim.SimInput) -> dict:
    """SimInput → 시뮬레이션 결과 JSON(템플릿 초기 렌더·API 공용 — 두 경로가 갈리지 않게)."""
    r = bidsim.simulate(inp)
    be = bidsim.breakeven_bid(inp)
    return {
        "bid": inp.bid_price,
        "sell": inp.sell_price,
        "acquisition_tax": r.acquisition_tax,
        "stamp_tax": r.stamp_tax,
        "registry_cost": r.registry_cost,
        "other_costs": r.other_costs,
        "total_acquisition": r.total_acquisition,
        "loan_amount": r.loan_amount,
        "equity": r.equity,
        "interest_total": r.interest_total,
        "agent_fee": r.agent_fee,
        "capital_gain": r.capital_gain,
        "transfer_tax": r.transfer_tax,
        "transfer_detail": r.transfer_detail,
        "net_profit": r.net_profit,
        "roi": r.roi,
        "roi_annual": r.roi_annual,
        "breakeven_bid": be,
        "breakeven_headroom": (be - inp.bid_price) if be is not None else None,
        "tax_mode": inp.tax_mode,
    }


if __name__ == "__main__":
    # 개발 편의용 진입점. 프로덕션 서빙은 waitress(scripts/start.ps1 / src.serve)를 쓴다.
    # (콜드부팅 최적화) 모듈 임포트 시 앱 이중생성 제거 — 서빙(api/index.py·src.serve)은 각자
    # create_app()을 부르므로, 여기서 만든 app은 이 dev 러너 전용이다.
    app = create_app()
    # debug/reloader 는 명시적 env flag(AUCTION_DEBUG=1)로만 켜지고, 기본값은 항상 off.
    debug = _truthy(os.environ.get("AUCTION_DEBUG"))
    host = os.environ.get("AUCTION_HOST", "127.0.0.1")
    port = int(os.environ.get("AUCTION_PORT", "8000"))
    app.run(host=host, port=port, debug=debug, use_reloader=debug)
