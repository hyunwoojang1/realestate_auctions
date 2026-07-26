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
from pathlib import Path

from flask import Flask, abort, g, jsonify, redirect, render_template, request

from . import (
    backtest,
    bidsim,
    casesearch,
    compare,
    digest,
    pipeline,
    query,
    report,
    sale_calendar,
    score,
    stats,
    store,
    store_rest,
    watchlist,
)
from .models import AuctionListing, ScoredListing

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

    # ── PWA(홈 화면 앱) — iOS Safari '홈 화면에 추가' 시 standalone 앱으로 열리게. ──
    # Vercel rewrite 가 모든 경로를 Flask 로 보내므로 정적 폴더 대신 명시 라우트로 서빙한다.
    _STATIC = ROOT / "static"

    # base.css 콘텐츠 해시(앱 생성 시 1회) — <link href="/base.css?v=..."> 캐시 버스팅.
    # 인라인 61KB CSS 를 외부화하면서(2026-07-23) 브라우저·SW 캐시가 가능해졌고,
    # 배포로 파일이 바뀌면 해시가 바뀌어 즉시 새 CSS 를 받는다(immutable 캐시와 안전 공존).
    import hashlib  # noqa: PLC0415
    try:
        _css_v = hashlib.md5((_STATIC / "base.css").read_bytes()).hexdigest()[:8]
    except OSError:
        logger.warning("static/base.css 없음 — 스타일이 렌더되지 않습니다(배포 번들 확인 필요)")
        _css_v = "0"
    app.jinja_env.globals["base_css_v"] = _css_v

    # 렌더 시각(UTC ISO) — base.html <meta name="rendered-at"> 용. SW 캐시본의 '나이'를
    # 페이지 스스로 계산해 '저장된 화면 · N분 전 기준' 정직성 표식을 띄운다(적대감사 F4).
    import datetime as _dt  # noqa: PLC0415
    app.jinja_env.globals["rendered_at"] = (
        lambda: _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"))

    @app.get("/base.css")
    def base_css():
        from flask import send_file  # noqa: PLC0415
        resp = send_file(str(_STATIC / "base.css"), mimetype="text/css")
        # (적대감사 F9) 영구 캐시는 **현재 해시와 일치하는 v** 에만 준다 — 배포 경계에서
        # 옛 v URL 로 새 내용이 immutable 1년 고정되는 것을 막는다(불일치·무버전은 no-cache).
        if request.args.get("v") == _css_v:
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/sw.js")
    def service_worker():
        from flask import send_file  # noqa: PLC0415
        resp = send_file(str(_STATIC / "sw.js"), mimetype="application/javascript")
        # SW 스크립트는 no-cache — 브라우저가 매 로드마다 갱신 여부를 확인해야
        # VERSION 올림(옛 캐시 청소)이 지체 없이 전파된다.
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/manifest.webmanifest")
    def manifest():
        return jsonify({
            "name": "아파트 경매 1차 필터",
            "short_name": "아파트 경매",
            "description": "시세>최저가 차익 매물 큐레이션 — 실거래 검증·권리분석",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#2563eb",
            "lang": "ko",
            "icons": [
                {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
                {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png",
                 "purpose": "maskable"},
            ],
        })

    def _png(name: str):
        from flask import send_file  # noqa: PLC0415
        resp = send_file(str(_STATIC / name), mimetype="image/png")
        resp.headers["Cache-Control"] = "public, max-age=604800"   # 1주 캐시
        return resp

    # ── 딜 시뮬(신분·기간 전략 비교, GOAL_DEAL_SIM Phase 3) ──
    # 엔진은 정적 JS(세율 하드코딩 없음), 세율·규제 데이터는 Jinja 전역으로 템플릿에 주입 —
    # detail 라우트 시그니처를 건드리지 않아 _dealsim.html include 만으로 동작한다.
    @app.get("/dealsim.js")
    def dealsim_engine():
        from flask import send_file  # noqa: PLC0415
        resp = send_file(str(ROOT / "static" / "dealsim.js"), mimetype="application/javascript")
        resp.headers["Cache-Control"] = "no-cache"   # 배포 즉시 새 엔진 반영(용량 작아 재검증 비용 미미)
        return resp

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

    @app.get("/apple-touch-icon.png")
    def apple_icon():
        return _png("apple-touch-icon.png")

    # iOS 가 접미사 변형(-precomposed·-120 등)으로도 요청 — 같은 아이콘으로 응답.
    @app.get("/apple-touch-icon-precomposed.png")
    @app.get("/apple-touch-icon-120x120.png")
    @app.get("/apple-touch-icon-152x152.png")
    @app.get("/apple-touch-icon-180x180.png")
    def apple_icon_variants():
        return _png("apple-touch-icon.png")

    @app.get("/icon-192.png")
    def icon192():
        return _png("icon-192.png")

    @app.get("/icon-512.png")
    def icon512():
        return _png("icon-512.png")

    @app.get("/")
    def index():

        from . import tax  # noqa: PLC0415
        # (2026-07-20) 사건번호 라우팅 — 홈 검색창에 "2025-101763"처럼 사건번호를 넣으면
        # 단지명 검색이 아니라 사건 조회(/find)로 보낸다. 판별은 엄격(이름검색 오탈취 방지).
        _q0 = request.args.get("q", "").strip()
        if _q0 and casesearch.looks_like_case_no(_q0):
            from urllib.parse import quote  # noqa: PLC0415
            return redirect(f"/find?q={quote(_q0)}")
        badges = _rights_badges()
        all_scored = _scored()   # 전체 채점 결과(출처 표시는 _scored 내부에서)
        now_dt = query.now_kst()          # KST 벽시계 — 서버 TZ 무관(리뷰 MEDIUM)
        today = now_dt.date()

        # (2026-07-24) 당일 입찰 마감(개시시각+버퍼) 경과 물건은 실질 입찰 불가 → 추천·임박에서 제외.
        def _biddable(s):
            return not query.bidding_closed(s, now_dt)

        def _clean(s):
            b = badges.get(f"{s.court}|{s.case_no}|{s.item_no}")
            return bool(b and b.is_clean)

        # 평가 가능(시세 추정된) 물건 = 검색 우선 홈의 기본 모수. 89% 노이즈(미지원·시세추정불가)는
        # 여기서 빠지고 '전체 탐색'(all=1)에서만 보인다.
        evaluable = [s for s in all_scored if query.is_evaluable(s)]
        burden = _burden_of(badges)
        # 칩 카운트 모수(2026-07-24 QA HIGH①) — 기본 뷰는 손해물건(효과차익 ≤ 0)을 숨기므로
        # (아래 items 필터), 칩 배지도 같은 모수로 세야 '803 클릭 → 429건' 불일치가 없다.
        # sort=profit_asc 로 손해를 일부러 보는 경우만 예외인데, 칩은 기본 뷰 진입 UI라 기본 기준.
        chip_base = [s for s in evaluable
                     if (query.decision_profit(s) or 0) - burden(s) > 0]
        # 커버리지(활성 필터와 무관하게 고정) — 정직한 밀도 노출. 요약 스트립용 전수 통계라
        # 칩과 달리 손해 숨김을 적용하지 않는다(적재 전수 기준).
        coverage = {
            "total": len(all_scored),
            "eval": len(evaluable),
            "noise": len(all_scored) - len(evaluable),
            "pos": sum(1 for s in evaluable if (query.decision_profit(s) or 0) > 0),
            "soon": sum(1 for s in evaluable if query.is_soon(s, today) and _biddable(s)),
            "clean": sum(1 for s in evaluable if _clean(s)),
            "high": sum(1 for s in evaluable if query.is_high_profit(s)),
        }
        chip_ct = {
            "soon": sum(1 for s in chip_base if query.is_soon(s, today) and _biddable(s)),
            "clean": sum(1 for s in chip_base if _clean(s)),
            "high": sum(1 for s in chip_base if query.is_high_profit(s)),
        }

        all_mode = request.args.get("all") == "1"
        q = request.args.get("q", "").strip()   # 단지명·주소 이름 검색('내가 본 그 아파트')
        region = request.args.get("region", "")
        ptype = request.args.get("type", "")
        budget = request.args.get("budget", "")
        area = request.args.get("area", "")
        fails = request.args.get("fails", "")
        sort = request.args.get("sort", "score")   # 홈 기본 = 점수 높은순(사용자 2026-07-16)
        if sort not in query.SORT_KEYS:
            sort = "score"
        chip_clean = request.args.get("clean") == "1"
        chip_soon = request.args.get("soon") == "1"
        chip_high = request.args.get("high") == "1"
        # 면적 브래킷(평대)·유찰 하한 파싱 — 알 수 없는 값은 무필터로 흘려보냄.
        min_area, max_area = query.area_bounds(area)
        if area and (min_area, max_area) == (None, None):
            area = ""
        try:
            min_fails = int(fails) if fails else None
        except ValueError:
            fails = ""
            min_fails = None
        has_filter = bool(q or region or ptype or budget or area or fails
                          or chip_clean or chip_soon or chip_high)

        # 예산(최저입찰가) — '8plus'=8억 이상, 그 외 숫자=상한(억).
        max_bid = min_bid = None
        if budget == "8plus":
            min_bid = 800_000_000
        elif budget:
            try:
                max_bid = int(float(budget) * 1e8)
            except ValueError:
                budget = ""

        # 이름 검색 시엔 전체 모수에서 찾는다 — 사용자가 본 단지가 시세추정 안 된 유형이어도
        # '없다'가 아니라 '찾았다'가 되도록(평가가능 모수로 좁히면 빌라·상가는 통째로 숨음).
        base = all_scored if (all_mode or q) else evaluable
        items = query.apply_filters(base, property_type=ptype or None, region=region or None,
                                    max_bid=max_bid, min_bid=min_bid,
                                    min_area=min_area, max_area=max_area, min_fails=min_fails,
                                    q=q or None)
        if chip_clean:
            items = [s for s in items if _clean(s)]
        if chip_soon:
            # (리뷰 HIGH) 검색(q) 중엔 마감물건도 유지 — 검색은 감추지 않는다. q 없을 때만 마감 제외.
            items = [s for s in items if query.is_soon(s, today) and (bool(q) or _biddable(s))]
        if chip_high:
            items = [s for s in items if query.is_high_profit(s)]
        items = query.sort_items(items, sort, burden_of=burden,
                                 uncertain_of=_uncertain_of(badges))

        # (사용자 2026-07-21) 기본/추천 뷰에선 손해(효과차익 = 보수차익 − 인수금 ≤ 0) 물건을 숨긴다.
        # '차익 낮은순'(profit_asc) 정렬을 명시적으로 고르거나 전체탐색(all=1)·이름검색(q)일 때만
        # 노출한다(데이터엔 그대로 적재 — 뷰에서만 필터). 손해 물건도 정렬로 찾을 수 있게.
        if sort != "profit_asc" and not all_mode and not q:
            items = [s for s in items
                     if (query.decision_profit(s) or 0) - burden(s) > 0]

        # (2026-07-24) 기본·추천 뷰에선 당일 입찰 마감(개시+버퍼 경과) 물건을 숨긴다 — 이미 입찰
        # 불가라 추천 노출이 헛물(죽전자이2차 사례). 이름검색(q)·전체탐색(all)에선 유지(검색은 찾게).
        if not all_mode and not q:
            items = [s for s in items if _biddable(s)]

        # 모드: 전체 탐색 / 검색·칩 결과 / (필터 없음) 엄선 추천
        if all_mode:
            mode = "all"
        elif has_filter:
            mode = "results"
        else:
            mode = "recommend"
            # (사용자 2026-07-16) top-9 엄선 폐지 — 시세 평가가능 물건 '전체'를 선택 정렬 순서대로 노출.
            # items 는 위에서 evaluable 을 sort_items(기본 점수 높은순)로 이미 정렬함. 위험·차익없음·
            # 권리미확인도 est 가 있으면 is_evaluable=True 라 포함된다. 미지원유형·시세추정불가만 all=1.

        from .digest import passes_recommend_gates  # noqa: PLC0415

        def _hero_ok(s):
            if not passes_recommend_gates(s, allow_legacy=False):
                return False
            if not badges:
                # (적대감사 F7) 배지 '전체 부재'가 **로드 실패**(Supabase 400 등) 때문이면
                # fail-closed — 권리 게이트를 우회한 히어로 노출 금지. 순수 샘플 모드(권리
                # 데이터가 원래 없는 로컬)만 종전대로 통과한다.
                return not getattr(g, "_rights_failed", False)
            b = badges.get(f"{s.court}|{s.case_no}|{s.item_no}")
            return b is not None and b.is_clean
        hero = next((s for s in items if _hero_ok(s)), None) if all_mode else None
        legacy_only = all_mode and bool(items) and all(s.profit_low is None for s in items)

        # 빠른진입 칩 — 현재 쿼리에서 해당 파라미터만 토글하는 링크(다른 필터 보존).
        from urllib.parse import urlencode  # noqa: PLC0415

        from .region import matches_region  # noqa: PLC0415

        def _toggle(param, on_value="1"):
            args = {k: v for k, v in request.args.items() if k != "all"}
            if args.get(param) == on_value:
                args.pop(param, None)
            else:
                args[param] = on_value
            qs = urlencode(args)
            return "/?" + qs if qs else "/"
        seoul_ct = sum(1 for s in chip_base if matches_region(s.address, "서울"))
        chips = [
            {"label": "인수 없음", "count": chip_ct["clean"], "active": chip_clean, "href": _toggle("clean")},
            {"label": "매각기일 임박", "count": chip_ct["soon"], "active": chip_soon, "href": _toggle("soon")},
            {"label": "고차익 2억+", "count": chip_ct["high"], "active": chip_high, "href": _toggle("high")},
            {"label": "관심지역 서울", "count": seoul_ct, "active": region == "서울",
             "href": _toggle("region", "서울")},
        ]
        filters = {"q": q, "region": region, "type": ptype, "budget": budget, "sort": sort,
                   "area": area, "fails": fails,
                   "clean": chip_clean, "soon": chip_soon, "high": chip_high}
        # 페이지네이션(2026-07-16) — 전체 정렬 후 슬라이스. 정렬·필터·카운트는 전체 기준, 렌더만 페이지.
        PAGE_SIZE = 60
        total = len(items)
        try:
            page = max(1, int(request.args.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, total_pages)
        items = items[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]

        def _page_url(p):
            from urllib.parse import urlencode  # noqa: PLC0415
            a = {k: v for k, v in request.args.items() if k != "page"}
            a["page"] = str(p)
            return "/?" + urlencode(a)
        return render_template(
            "listings.html", items=items, count=total, filters=filters, chips=chips,
            page=page, total_pages=total_pages, page_url=_page_url,
            mode=mode, coverage=coverage, all_mode=all_mode,
            hero=hero, legacy_only=legacy_only, badges=badges, resales=_resale_map(),
            won=report.won, pct=report.pct,
            days_until=query.days_until,
            tax_label=tax.PROFILE.label(),
            watched=watchlist.load_watchlist(watchlist.watchlist_path()),
            data_source=getattr(g, "data_source", "n/a"))

    @app.get("/health")
    def health():
        import time as _time  # noqa: PLC0415
        src = _probe_source()
        _mark_source(src)
        # (적대감사 F7) 권리 로드 최근 실패를 노출 — 배지 전멸(스키마 드리프트 400 등)이
        # 경고 로그 한 줄로 침묵하지 않게 운영자가 헬스체크에서 바로 본다. 15분 지나면 ok 복귀.
        rights = "ok"
        if _rights_last_error["at"] and _time.time() - _rights_last_error["at"] < 900:
            rights = f"failed: {_rights_last_error['msg']}"
        return {"status": "ok", "data_source": src, "rights_source": rights}

    @app.get("/api/listings")
    def listings():
        # (재검증 감사 idx4) API 에도 인수 부담 필드 병기 — 소비자가 인수 미반영 profit 만
        # 보고 실질 음수 물건을 양수로 오인하지 않게.
        badges = _rights_badges()
        out = []
        for s in _filtered(request.args, badges=badges):
            row = s.to_row()
            b = badges.get(f"{s.court}|{s.case_no}|{s.item_no}")
            row["burden_status"] = ("clean" if b and b.is_clean
                                    else "burden" if b else "unknown")
            row["assumed_amount"] = b.assumed if b else None
            # (감사 2026-07-23 P-13) 상세 화면은 "매수인이 인수함"을 표시하는데 API 소비자는
            # assumed_amount 가 비어 있어 인수를 0으로 계산했다(표본 53/53). 금액 미상을 명시한다 —
            # CSV(report.csv_text)는 이미 '있음(금액 미상)'·유효차익 공란으로 처리하고 있었다.
            row["assumed_amount_unknown"] = bool(b and b.amount_unknown)
            out.append(row)
        return jsonify(out)

    @app.get("/export.csv")
    def export_csv():
        # 목록과 동일 필터·정렬 결과를 CSV로 다운로드(엑셀 검토용). 순수 조회.
        badges = _rights_badges()
        items = _filtered(request.args, badges=badges)
        # 저장된 CSV 파일만 봐도 출처를 알 수 있게 파일명에 각인 — 샘플 폴백(DB 장애/미적재)을
        # 라이브로 오인하는 침묵실패 방지(감사 #12 HIGH). db가 아니면 _SAMPLE 접미사.
        src = getattr(g, "data_source", "n/a")
        fname = "auction_arbitrage.csv" if src == "db" else "auction_arbitrage_SAMPLE.csv"
        # UTF-8-SIG BOM: 엑셀이 한글을 깨지 않게. report.csv_text 재사용(중복 구현 금지).
        body = "﻿" + report.csv_text(items, badges=badges)
        resp = app.response_class(body, mimetype="text/csv")
        resp.headers["Content-Disposition"] = f'attachment; filename="{fname}"'
        resp.charset = "utf-8"
        return resp

    def _sold_rows(limit: int = 300) -> list[dict]:
        """낙찰(종결) 기록 — 로컬 SQLite 우선, 클라우드는 REST. 실패=빈 리스트(페이지 정상)."""
        db_path = os.environ.get(DB_ENV)
        try:
            if db_path:
                conn = store.connect(db_path)
                try:
                    return store.load_sold(conn, limit)
                finally:
                    conn.close()
            if store_rest.enabled():
                return store_rest.fetch_sold(limit)
        except Exception as e:  # noqa: BLE001 — 낙찰 목록 실패는 비차단
            logger.warning("낙찰 목록 로드 실패: %s", e)
        return []

    def _sold_one(case_no: str) -> dict | None:
        """단건 낙찰 스냅샷 — 상세 '낙찰 종결' 모드. item/court 쿼리 파라미터로 좁힌다."""
        item = request.args.get("item") or ""
        court = request.args.get("court") or ""
        db_path = os.environ.get(DB_ENV)
        try:
            if db_path:
                conn = store.connect(db_path)
                try:
                    if court:
                        return store.load_sold_one(conn, court, case_no, item)
                    cur = conn.execute(
                        "SELECT * FROM sold_listings WHERE case_no=?"
                        + (" AND item_no=?" if item else ""),
                        (case_no, item) if item else (case_no,))
                    rows = [dict(r) for r in cur.fetchall()]
                    return rows[0] if len(rows) == 1 else None   # 다물건 모호 = 미표시(오표시 방지)
                finally:
                    conn.close()
            if store_rest.enabled() and court:
                return store_rest.fetch_sold_one(court, case_no, item)
        except Exception as e:  # noqa: BLE001
            logger.warning("낙찰 단건 로드 실패(%s): %s", case_no, e)
        return None

    @app.get("/sold")
    def sold_page():
        """(C4 2026-07-27) 최근 낙찰 기록 — 실낙찰가 보유 우선, 매각기일 최신순."""
        rows = _sold_rows(300)
        return render_template("sold.html", rows=rows, count=len(rows),
                               won=report.won,
                               data_source=getattr(g, "data_source", "n/a"))

    def _find_by_case(case_no: str):
        """(T8 감사 수정 — B14 핵심) 사건번호 매칭 물건 전부.

        T1 복합키 도입으로 같은 사건의 물건 여러 개가 공존한다 — 단건 next()는 임의의
        한 물건만 반환해 다른 물건의 수치(아파트 vs 상가)를 보여주는 침묵 오표시를 만든다.
        item(물건번호)·court 쿼리 파라미터로 좁힐 수 있다.

        (2026-07-20) 입력 정규화 — 친구가 "2025-101763"처럼 보내도 저장 포맷("2025타경101763")과
        맞도록 casesearch로 환원해 매칭한다. 파싱 불가하면 원문 완전일치로 폴백(회귀 없음).

        (A5 2026-07-27) 클라우드 상세 fast path — 전량 캐시가 콜드인 REST 서빙에서 상세가
        15k행 로드를 통과하지 않도록 사건번호 단건 REST로 조회(콜드 상세 수 초→수백 ms).
        캐시가 신선하면 기존 경로(왕복 0)가 더 싸므로 그대로 둔다. 실패 시 전체 경로 폴백.
        """
        if (not os.environ.get(DB_ENV) and store_rest.enabled()
                and not store_rest.scored_cache_fresh()):
            try:
                matches = store_rest.fetch_scored_by_case(case_no)
                if not matches:
                    q = casesearch.parse_case_query(case_no)
                    if q and q.canonical and q.canonical != case_no:
                        matches = store_rest.fetch_scored_by_case(q.canonical)
                if matches:
                    _mark_source("db")
                    from .score import market_view  # noqa: PLC0415
                    matches = [market_view(
                        s, store_rest.fetch_naver_price(s.court, s.case_no, s.item_no))
                        for s in matches]
                    item = request.args.get("item")
                    court = request.args.get("court")
                    if item is not None:
                        matches = [s for s in matches if s.item_no == item]
                    if court:
                        matches = [s for s in matches if s.court == court]
                    return matches
            except Exception as e:  # noqa: BLE001 — 단건 실패는 전량 경로로 폴백(회귀 없음)
                logger.warning("단건 조회 실패 → 전량 경로 폴백(%s): %s", case_no, e)
        scored = _scored()
        # 완전일치 우선(회귀 방지 — 합성 case_no 'LIVE-1' 등 그대로 동작).
        matches = [s for s in scored if s.case_no == case_no]
        if not matches:
            # 실패 시에만 정규화 폴백 — 친구 포맷 '2025-101763' → '2025타경101763'.
            q = casesearch.parse_case_query(case_no)
            if q:
                matches = casesearch.match_local(scored, q)
        item = request.args.get("item")
        court = request.args.get("court")
        if item is not None:
            matches = [s for s in matches if s.item_no == item]
        if court:
            matches = [s for s in matches if s.court == court]
        return matches

    @app.get("/find")
    def find_case():
        """사건번호 검색 — 로컬 큐레이션 우선, 없으면 대법원 라이브 단건 조회.

        흐름(명확한 논리, casesearch 계약):
          1) 입력 정규화(parse_case_query). 인식 실패 → 안내.
          2) 로컬 매칭(match_local): 1건이면 상세로, 여러 건이면 목록, 0건이면 3)로.
          3) 라이브: 표준형(연도O)+법원명이 있으면 case_detail로 공식정보 조회.
             연도/법원이 부족하면 입력폼(법원 드롭다운)으로 되묻는다.
        """
        raw = (request.args.get("q") or "").strip()
        court = (request.args.get("court") or "").strip()
        courts = casesearch.list_courts()
        ctx = {"raw": raw, "court_sel": court, "courts": courts,
               "won": report.won, "data_source": getattr(g, "data_source", "n/a")}
        if not raw:
            return render_template("find.html", stage="prompt", **ctx)

        q = casesearch.parse_case_query(raw)
        if q is None:
            return render_template("find.html", stage="unparsed", **ctx)
        # 힌트로 들어온 법원(입력에 섞인 것)과 드롭다운 선택을 합침(드롭다운 우선).
        court = court or (q.court or "")

        # 1) 로컬 큐레이션
        local = casesearch.match_local(_scored(), q)
        if court:
            cn = casesearch._norm_court(court)
            local = [s for s in local if cn in casesearch._norm_court(s.court)]
        if len(local) == 1:
            s = local[0]
            url = f"/property/{s.case_no}"
            if s.item_no:
                url += f"?item={s.item_no}"
            return redirect(url)
        if len(local) > 1:
            return render_template("find.html", stage="local_multi", matches=local, query=q, **ctx)

        # 2) 라이브 — 연도+법원이 있어야 관할을 특정할 수 있다.
        if not q.canonical:
            return render_template("find.html", stage="need_year", query=q, **ctx)
        if not court:
            return render_template("find.html", stage="need_court", query=q, **ctx)
        try:
            from .courtauction_client import CourtAuctionBlocked  # noqa: PLC0415
            view = casesearch.live_lookup(q, court_name=court)
        except casesearch.CaseSearchError as e:
            return render_template("find.html", stage="error", message=str(e), query=q, **ctx)
        except CourtAuctionBlocked as e:
            logger.warning("사건 라이브 조회 차단/실패: %s", e)
            return render_template("find.html", stage="error", query=q,
                                   message="대법원 접속이 일시 제한되었습니다. 잠시 후 다시 시도해 주세요.",
                                   **{k: v for k, v in ctx.items()})
        except Exception as e:  # noqa: BLE001 — 라이브 실패가 페이지를 500으로 떨구지 않게
            logger.warning("사건 라이브 조회 오류(%s): %s", type(e).__name__, e)
            return render_template("find.html", stage="error", query=q,
                                   message="사건 조회 중 오류가 발생했습니다. 법원·사건번호를 확인해 주세요.",
                                   **ctx)
        return render_template("find.html", stage="live", view=view, query=q, **ctx)

    @app.get("/api/listings.geojson")
    def listings_geojson():
        """지도용 GeoJSON — 목록과 동일 필터. 좌표는 KATEC→WGS84 캐시(coords.py) 조인."""
        from . import coords  # noqa: PLC0415
        from . import query as q
        from . import region as reg  # noqa: PLC0415
        cache = coords.load_coord_cache()
        feats = []
        skipped = 0
        geo_badges = _rights_badges()
        # 지도 3단 스코프:
        #   기본(profit) = 효과 차익(인수 차감 후) > 0 인 '차익 양수만' — 진짜 살 만한 것.
        #   scope=evaluable = 시세 추정된 것 전부(양수·음수 무관, 평가 가능).
        #   all=1 = 미지원·시세추정불가까지 전부 탐색.
        # 효과 차익·인수금액은 요청마다 권리 배지에서 계산 — 권리분석 크롤이 인수금액을
        # 채우는 대로 양수만 집합이 자동 재계산된다(정적 목록 아님).
        items = _filtered(request.args, badges=geo_badges, evaluable_default=True)
        scope = "all" if _truthy(request.args.get("all")) else request.args.get("scope", "profit")
        if scope == "profit":
            items = q.positive_only(items, burden_of=_burden_of(geo_badges),
                                    uncertain_of=_uncertain_of(geo_badges))
        for s in items:
            pt = coords.lookup(cache, s.uid, s.case_no, court=s.court)
            if not pt:
                skipped += 1
                continue
            p = q.decision_profit(s)
            # (재검증 감사 idx4) 지도 핀 차익도 인수금 차감한 유효 차익으로.
            b = geo_badges.get(f"{s.court}|{s.case_no}|{s.item_no}")
            if p is not None and b and b.assumed:
                p = p - b.assumed
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [pt[1], pt[0]]},
                "properties": {
                    "case_no": s.case_no, "item_no": s.item_no,
                    "apt_name": s.apt_name, "property_type": s.property_type,
                    "grade": s.grade, "profit": p,
                    "sido": reg.sido_of(s.address) or "기타",   # 클라이언트 지역 필터/집계용
                    # 인수 부담인데 금액 미상 → 효과 차익 마이너스일 수 있음(−α). 평가가능/전체
                    # 뷰에서 표시하고 '차익 양수만' 기본에서는 제외(query.positive_only).
                    "uncertain": bool(b and b.amount_unknown),
                    "burden": ("clean" if b and b.is_clean else "burden" if b else "unknown"),
                    "conservative": s.profit_low is not None,
                    "min_bid": s.min_bid_price,
                    "url": f"/property/{s.case_no}" + (f"?item={s.item_no}" if s.item_no else ""),
                },
            })
        # '어디에 몇 건' — 후보 전수 기준 시도별 카운트(좌표 유무 무관).
        return jsonify({"type": "FeatureCollection", "features": feats,
                        "no_coord_count": skipped,
                        "by_sido": q.count_by_sido(items)})

    @app.get("/map")
    def map_page():
        # 지도 페이지는 _scored()를 직접 안 부르므로 출처를 명시 탐지(헤더 '샘플' 오표시 방지).
        src = _probe_source()
        _mark_source(src)
        return render_template("map.html", data_source=src)

    @app.get("/api/listings/<case_no>")
    def listing_detail(case_no: str):
        matches = _find_by_case(case_no)
        if not matches:
            abort(404)
        if len(matches) > 1:
            # 같은 사건에 물건 여러 개 — 임의 1건을 주지 않고 선택지를 반환(300 Multiple Choices).
            return jsonify({
                "error": "multiple_items",
                "case_no": case_no,
                "items": [{"item_no": s.item_no, "court": s.court,
                           "property_type": s.property_type, "apt_name": s.apt_name,
                           "url": f"/api/listings/{case_no}?item={s.item_no}"}
                          for s in matches],
            }), 300
        return jsonify(matches[0].to_row())

    @app.get("/property/<case_no>")
    def property_detail(case_no: str):
        from . import tax  # noqa: PLC0415
        matches = _find_by_case(case_no)
        sold = None
        if not matches:
            # (C4 2026-07-27) 낙찰 종결 물건 — 활성 목록에 없으면 보존 스냅샷으로 동일 상세 서빙.
            # 자식 데이터(사진·권리)는 C2 프룬 보존 덕에 남아 있으면 그대로 렌더된다.
            sold = _sold_one(case_no)
            if not sold:
                abort(404)
            matches = [ScoredListing(
                case_no=sold["case_no"], court=sold.get("court") or "",
                item_no=sold.get("item_no") or "",
                apt_name=sold.get("apt_name") or "", address=sold.get("address") or "",
                property_type=sold.get("property_type") or "",
                area_m2=sold.get("area_m2") or 0.0,
                appraisal_price=sold.get("appraisal_price") or 0,
                min_bid_price=sold.get("min_bid_price") or 0,
                fail_count=sold.get("fail_count") or 0,
                sale_date=sold.get("sale_date") or "",
                est_market_price=sold.get("est_market_price"),
                market_band_low=sold.get("market_band_low"),
                profit_low=sold.get("profit_low"),
                expected_profit=sold.get("expected_profit"),
                arb_score=sold.get("arb_score"),
                grade=sold.get("grade") or "낙찰 종결",
                matched_trades=0, confidence=0.0,
                real_acquisition_cost=sold.get("min_bid_price") or 0,
                gap_rate=None, gap_score=0.0, rights_score=0.0, liquidity_score=0.0,
            )]
        if len(matches) > 1:
            # 물건 선택 페이지 — 어떤 물건인지 사용자가 고른다(잘못된 물건 수치 표시 방지).
            return render_template("choose_item.html", case_no=case_no, items=matches,
                                   won=report.won,
                                   data_source=getattr(g, "data_source", "n/a"))
        s = matches[0]
        listing = next((a for a in pipeline.load_sample_auctions() if a.case_no == case_no), None)
        if listing is None:
            # DB 서빙(courtauction 등 비-샘플) 매물 — 스코어 행에서 최소 listing 복원.
            # 권리 필드(특수권리/인수금액/점유)는 물건상세 미수집이라 기본값(게이트 비적용).
            listing = AuctionListing(
                case_no=s.case_no, court="", address=s.address, lawd_cd="", dong="",
                apt_name=s.apt_name, property_type=s.property_type, area_m2=s.area_m2,
                appraisal_price=s.appraisal_price, min_bid_price=s.min_bid_price,
                fail_count=s.fail_count, sale_date=s.sale_date,
            )
        # 권리·기일 요지 로드(물건상세 크롤분: 로컬 SQLite 우선, 클라우드는 REST) —
        # 있으면 매각물건명세서 문구로 권리 필드를 실채움해 '권리미확인'을 해제하고
        # 하드게이트가 실제 문서 기반으로 작동하게 한다.
        rights = None
        rights_row = None
        db_path = os.environ.get(DB_ENV)

        # (A3 2026-07-27) 상세의 독립 조회 5종(권리·사진·임차인·점유관계·건축물대장)을 병렬로.
        # 종전엔 순차 REST 왕복(콜당 ~400ms × 4~5)이 워밍에도 2~4초 — "클릭했는데 안 넘어간다"
        # 체감의 주범. 각 조회의 실패 폴백(개별 try + warning 로그)은 그대로 유지하고,
        # SQLite 경로는 스레드별 자체 커넥션을 열므로 병렬에도 안전하다.
        def _q_sqlite(loader):
            conn_ = store.connect(db_path)
            try:
                return loader(conn_)
            finally:
                conn_.close()

        def _load_rights_job():
            if db_path:
                return _q_sqlite(lambda c: store.load_rights(c, s.court, s.case_no, s.item_no))
            if store_rest.enabled():
                return store_rest.fetch_rights(s.court, s.case_no, s.item_no)
            return None

        def _load_photos_job():
            if db_path:
                return _q_sqlite(lambda c: store.load_photos(c, s.court, s.case_no, s.item_no))
            if store_rest.enabled():
                return store_rest.fetch_photos(s.court, s.case_no, s.item_no)
            return []

        def _load_tenants_job():
            if db_path:
                return _q_sqlite(lambda c: store.load_tenants(c, s.court, s.case_no, s.item_no))
            if store_rest.enabled():
                return store_rest.fetch_tenants(s.court, s.case_no, s.item_no)
            return []

        def _load_survey_job():
            if db_path:
                _sv_raw = _q_sqlite(
                    lambda c: store.load_detail_raw(c, s.court, s.case_no, s.item_no, "curst"))
                if _sv_raw:
                    from .courtauction_detail import curst_possession  # noqa: PLC0415
                    return curst_possession(_sv_raw)
                return None
            if store_rest.enabled():
                return store_rest.fetch_survey(s.court, s.case_no, s.item_no)
            return None

        def _load_building_job():
            if db_path:
                return _q_sqlite(lambda c: store.load_building(c, s.court, s.case_no, s.item_no))
            if store_rest.enabled() and hasattr(store_rest, "fetch_building"):
                return store_rest.fetch_building(s.court, s.case_no, s.item_no)
            return None

        from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415
        _jobs = {"rights": _load_rights_job, "photos": _load_photos_job,
                 "tenants": _load_tenants_job, "survey": _load_survey_job,
                 "building": _load_building_job}
        _got: dict = {}
        with ThreadPoolExecutor(max_workers=len(_jobs)) as _ex:
            _futs = {k: _ex.submit(fn) for k, fn in _jobs.items()}
            for _k, _f in _futs.items():
                try:
                    _got[_k] = _f.result()
                except Exception as e:  # noqa: BLE001 — 개별 조회 실패는 상세 페이지를 막지 않음
                    logger.warning("%s 로드 실패(%s %s): %s", _k, s.court, s.case_no, e)
                    _got[_k] = None

        rights_row = _got.get("rights")
        photos: list[str] = _got.get("photos") or []
        tenants: list[dict] = _got.get("tenants") or []
        _brow_prefetched = _got.get("building")

        # (2026-07-25) 현황조사서 '부동산의 점유관계' — 사용자 요구("최선순위만 보여주면
        # 내가 뭘 보고 판단하냐"). 크롤 시 보존한 curst 원본(listing_detail_raw)에서 집행관
        # 조사 원문(폐문부재/전입세대확인/기타)을 요지로 파싱해 명세서 아래 섹션으로 노출.
        # 원본 미보존(구크롤·REST 클라우드 경로)이면 None → 섹션 미표시(모름≠없음).
        survey = _got.get("survey")   # (A3) 위 병렬 배치에서 로드 — 실패 폴백 동일(None=섹션 미표시)

        # (2026-07-23) 재매각 이력 — 위 rights_row 를 그대로 쓰므로 추가 조회 0.
        # 권리 요지가 비어 배지가 안 만들어지는 물건도 기일 이력은 살아 있으므로 별도로 계산한다
        # (rights_row 를 None 으로 되돌리는 아래 가드보다 **먼저** 뽑아야 한다).
        from .courtauction_detail import CaseRights as _CR  # noqa: PLC0415
        from .courtauction_detail import resale_history  # noqa: PLC0415
        resale = None
        if rights_row:
            try:
                resale = resale_history(_CR.from_row(rights_row).schedule)
            except Exception as e:  # noqa: BLE001 — 재매각 표시는 부가 정보, 페이지를 막지 않음
                logger.warning("재매각 이력 판정 실패(%s %s): %s", s.court, s.case_no, e)

        # (UX 감사 U-01, 2026-07-23) 입찰보증금 — 통상 최저가의 10%지만 **재매각·특별매각조건은
        # 20~30%** 이고 법원이 그 비율을 명세서 비고에 문장으로 준다(실측 609건). 종전엔 화면이 늘
        # 10%로 계산해 재매각 물건의 필요 현금을 **절반으로** 알려줬다 → 그대로 법정에 가면 입찰 무효.
        # ⚠️ 재매각과 같은 이유로 **가드보다 먼저** 계산한다 — 비율은 비고에 있는데, 자유기술란이
        # 비면 아래에서 rights_row 가 None 이 되어 비고까지 함께 사라진다.
        # 명시가 없으면 stated=False → 화면이 '통상 10% 가정'임을 밝힌다(모름을 확정으로 바꾸지 않음).
        from .courtauction_rights import bid_deposit  # noqa: PLC0415
        _rr = rights_row or {}
        deposit_amount, deposit_rate, deposit_stated = bid_deposit(
            s.min_bid_price, _rr.get("remark") or "", _rr.get("lien_note") or "",
            _rr.get("surviving_rights") or "")

        badge = None
        priority = None
        if rights_row:
            import dataclasses  # noqa: PLC0415

            from .courtauction_detail import (  # noqa: PLC0415
                CaseRights,
                ambiguous_moveins,
                analyze_priority,
                opposable_deposit,
                summarize,
                tenant_moveins,
            )
            _cr = CaseRights.from_row(rights_row)
            tmoveins = tenant_moveins(tenants)   # 임차인(소유자 전입 제외) 전입일 실데이터
            amoveins = ambiguous_moveins(tenants)  # 관계 미상 전입세대(대항력 여지 후보 — 소유자 단정 금지)
            # (서빙감사 2026-07-12 #13) 빈/부분 명세서는 판정 근거 0 — 배지·rights 둘 다 미표시로
            # 폴백해 '✓ 인수 없음/권리분석 반영됨'으로 오판하지 않는다(목록 가드와 정합).
            if _cr.is_empty:
                rights_row = None
            elif _cr.opposability_assessable or tmoveins or amoveins:
                # 판정 가능: 요지 자유텍스트가 있거나(구경로), 현황조사서 임차인 전입일 실데이터가 있거나,
                # (2026-07-23) 관계 미상 전입세대가 있어 대항력 '여지' 판정이 가능함.
                rights = _cr
                badge = summarize(_cr)
                # 대항력 근거(2026-07-13): 실전입일 있으면 항상 날짜비교, 없으면 부담물건에만(구동작).
                # (2026-07-23) 관계 미상 전입세대(amoveins)만 있어도 '여지' 판정을 위해 분석한다.
                if tmoveins or amoveins or not badge.is_clean:
                    priority = analyze_priority(_cr, tenant_moveins=tmoveins,
                                                ambiguous_moveins=amoveins)
                # 현황조사서 전입일로 대항력 확정(전입 ≤ 말소기준)이면 opposable·인수액 보정.
                confirmed = priority is not None and priority.verdict == "confirmed_opposable"
                assumed = badge.assumed
                if confirmed:
                    assumed = max(assumed, opposable_deposit(tenants, priority.senior_date))
                listing = dataclasses.replace(
                    listing,
                    special_rights=badge.special,
                    tenant_opposable=badge.opposable or confirmed,
                    assumed_amount=assumed,
                    rights_verified=True,
                )
            else:
                # (2026-07-22) 자유기술란 전부 빈 요지 + 임차인표도 미크롤 = 대항력 판정근거 0 →
                # 배지·판정 미생성으로 '발견 안 됨' 초록 오표시를 막되(삼환 2022타경3289), 참고정보
                # (명세서 요지 말소기준·기일·감정요항)는 그대로 보여준다.
                rights = _cr
                rights_row = None
        gated = score.is_hard_gated(listing)
        gate_reasons = []
        if gated:
            ratio = listing.assumed_amount / listing.min_bid_price if listing.min_bid_price else 1.0
            if ratio > score.CONFIG.assumed_ratio_gate:
                gate_reasons.append(f"인수금액 비율 {ratio * 100:.0f}% (>{score.CONFIG.assumed_ratio_gate * 100:.0f}%)")
            fatal = [r for r in listing.special_rights if r in score.CONFIG.fatal_special]
            if fatal:
                gate_reasons.append("·".join(fatal) + " 신고")
        tax_parts = tax.acquisition_tax_breakdown(s.min_bid_price, s.property_type, s.area_m2)
        # (T5) 표본 게이트 상태 — 실기반 표본이 추천 기준 미만이면 '낮은 신뢰' 경고 노출.
        # (T8 감사 수정) 시세 자체가 없는 물건(미지원유형·시세추정불가)에는 "시세·차익은 참고만"
        # 경고가 무의미·혼란 — est가 있을 때만 발동.
        from .matcher import band_confident_basis  # noqa: PLC0415
        sample_gate_low = (s.est_market_price is not None
                           and s.market_sample_basis is not None
                           and s.market_sample_basis < band_confident_basis())
        # (T6) 호가 스텁 — 수동 입력 파일에 있으면 점으로 표시, 없으면 완전 무표시.
        from . import asking as asking_mod  # noqa: PLC0415
        askings = asking_mod.load_asking_prices().get(case_no, [])
        ask_points = asking_mod.asking_points(askings, s.market_band_low, s.market_band_high)
        ask_overstated = asking_mod.band_overstated(askings, s.market_band_low)
        # 가격-시간 차트(세로/시간축 개편) — 개별 실거래(월별)·호가 시점·유찰 저감·롤링 밴드.
        # (구 pricemap 가로 스냅샷은 이 차트로 교체·제거됨 — 2026-07-13 정리)
        # 기일 이력(schedule)은 권리 요지에서, 개별 실거래는 s.market_comps에서.
        from . import pricechart  # noqa: PLC0415
        chart = pricechart.build_timechart(
            s, s.market_comps, rights.schedule if rights else None, ask_points,
            assumed=(badge.assumed if badge else 0))
        # 현장 탭 인라인 지도용 좌표(KATEC→WGS84 캐시). 없으면 None → 템플릿이 주소검색 폴백.
        from . import coords  # noqa: PLC0415
        coord = None
        try:
            pt = coords.lookup(coords.load_coord_cache(), s.uid, s.case_no, court=s.court)
            if pt:
                coord = [pt[0], pt[1]]  # [lat, lon]
        except Exception as e:  # noqa: BLE001 — 좌표 실패는 지도 폴백, 페이지는 정상
            logger.warning("좌표 조회 실패(%s %s): %s", s.court, s.case_no, e)
        # (E 배선 2026-07-20) 건축물대장 요약 — 준공연도(노후도)·위반건축물 리스크.
        # 키 미설정·실패는 None → 카드 미표시(페이지 정상). 건물형 유형만 조회
        # (감사 2026-07-20: 토지·임야 상세에서 무의미한 외부 2콜 차단).
        from . import building_info  # noqa: PLC0415
        _BLDG_TYPES = ("아파트", "오피스텔", "연립", "다세대", "단독", "다가구", "근린주택", "빌라")
        bldg = None
        if any(t in (s.property_type or "") for t in _BLDG_TYPES):
            # (2026-07-22) 배치 precompute 캐시(listing_building) 우선 — 페이지뷰마다 VWorld+대장
            # 라이브 3초 왕복을 없애고 쿼터 소진에도 견딘다. 캐시 미스/미ok면 라이브 폴백(그리고
            # deploy/enrich_building 배치가 다음 회차에 채운다).
            brow = _brow_prefetched   # (A3) 위 병렬 배치에서 로드 — 실패=None(라이브 폴백 동일)
            if brow and brow.get("status") == "ok":
                bldg = brow
            else:
                bldg = building_info.get_building_summary(s.address)
        # (2026-07-23) 입찰가 시뮬레이터 초기값 — 서버에서 한 벌 계산해 넘긴다(JS 없어도 값이 보이게).
        # 이후 슬라이더 조작은 /api/bidsim 이 같은 _sim_payload 로 계산 — 두 경로가 갈리지 않는다.
        # 매도가 기본 = 검증 하한가(보수) → 추정시세 → 없으면 0(사용자가 직접 입력).
        sim_input = bidsim.SimInput(
            # (C4) 낙찰 종결 물건은 실낙찰가로 고정 시작 — "그 가격에 샀다면"의 손익.
            # 미공개(sold_price None)면 최저가 그대로(값 지어내기 금지).
            bid_price=(sold["sold_price"] if sold and sold.get("sold_price")
                       else s.min_bid_price),
            property_type=s.property_type,
            area_m2=s.area_m2 or 0.0,
            sell_price=s.market_band_low or s.est_market_price or 0,
            assumed_amount=(badge.assumed if badge else 0),
        )
        html = render_template(
            "detail.html", s=s, listing=listing, chart=chart, rights=rights, badge=badge,
            survey=survey, tenants=tenants, sold=sold,
            deposit_amount=deposit_amount, deposit_rate=deposit_rate,
            deposit_stated=deposit_stated,
            sim=_sim_payload(sim_input), sim_in=sim_input, bidsim_cfg=bidsim, resale=resale,
            coord=coord, days_until=query.days_until,
            bldg=bldg, vworld_key=os.environ.get("VWORLD_API_KEY", "").strip(),
            priority=priority,
            # 상세 본문은 원 단위 콤마 표기(사용자 결정 2026-07-24) — 홈/리스트는 억 유지.
            # won_fine(계산서)도 콤마로: 컨텍스트 인자가 jinja 전역보다 우선한다.
            won=report.won_num, won_fine=report.won_num, pct=report.pct,
            gated=gated, gate_reason=", ".join(gate_reasons),
            tax_parts=tax_parts, tax_label=tax.PROFILE.label(),
            watching=watchlist.is_watched(
                watchlist.load_watchlist(watchlist.watchlist_path()), s),
            data_source=getattr(g, "data_source", "n/a"),
            sample_gate_low=sample_gate_low, band_confident=band_confident_basis(),
            ask_points=ask_points, ask_overstated=ask_overstated, photos=photos,
        )
        # (B1 2026-07-27) 목록의 터치-프리페치가 탭 시점에 재사용되도록 짧은 private 캐시.
        # 데이터는 일 1회 새로고침이라 45초 스테일은 무해. 관심 토글 복귀는 _safe_back이
        # 캐시버스터(_r)를 붙여 최신 별 상태를 강제한다(짝 계약 — 함께 수정할 것).
        resp = app.make_response(html)
        resp.headers["Cache-Control"] = "private, max-age=45"
        return resp

    @app.get("/api/bidsim")
    def bidsim_api():
        """입찰가 시뮬레이터 — 순수 계산(DB 무접근). 상세페이지 슬라이더가 입력마다 호출한다.

        물건 식별자가 아니라 **가정 전부를 쿼리로** 받는다 — DB 왕복이 없어 빠르고,
        계산 로직이 파이썬 한 곳에만 존재한다(JS에 세율을 복제하지 않는다 = 단일 출처).
        """
        a = request.args
        inp = bidsim.SimInput(
            bid_price=_clamp_int(a.get("bid"), 0, _MAX_WON),
            property_type=(a.get("type") or "")[:40],
            area_m2=_clamp_float(a.get("area"), 0.0, 100_000.0, 0.0),
            sell_price=_clamp_int(a.get("sell"), 0, _MAX_WON),
            holding_months=_clamp_int(a.get("months"), 0, 600,
                                      bidsim.DEFAULT_HOLDING_MONTHS),
            assumed_amount=_clamp_int(a.get("assumed"), 0, _MAX_WON),
            eviction_cost=_clamp_int(a.get("eviction"), 0, _MAX_COST,
                                     bidsim.DEFAULT_EVICTION),
            repair_cost=_clamp_int(a.get("repair"), 0, _MAX_COST),
            unpaid_fees=_clamp_int(a.get("unpaid"), 0, _MAX_COST),
            registry_cost=_clamp_int(a.get("registry"), 0, _MAX_COST,
                                     bidsim.DEFAULT_REGISTRY),
            loan_ltv=_clamp_float(a.get("ltv"), 0.0, 1.0, bidsim.DEFAULT_LTV),
            loan_rate=_clamp_float(a.get("rate"), 0.0, 0.30, bidsim.DEFAULT_LOAN_RATE),
        )
        return jsonify(_sim_payload(inp))

    @app.get("/digest")
    def digest_page():
        from . import tax  # noqa: PLC0415
        n = request.args.get("n", default=10, type=int)
        min_profit_eok = request.args.get("min_profit", type=float)
        min_profit = int(min_profit_eok * 1e8) if min_profit_eok else None
        digest_badges = _rights_badges()
        # (2026-07-24) 추천 다이제스트도 당일 입찰 마감 물건 제외 — 홈 추천과 동일 기준(리뷰 HIGH).
        _now = query.now_kst()
        _biddable_items = [s for s in _scored() if not query.bidding_closed(s, _now)]
        items = digest.top_listings(_biddable_items, n=n, min_profit=min_profit,
                                    badges=digest_badges)
        filters = {"min_profit": request.args.get("min_profit", ""), "type": "", "region": "",
                   "sort": query.DEFAULT_SORT, "clean": ""}
        return render_template(
            "listings.html", items=items, count=len(items), filters=filters,
            badges=digest_badges, days_until=query.days_until,
            won=report.won, pct=report.pct,
            tax_label=tax.PROFILE.label(),
            data_source=getattr(g, "data_source", "n/a"))

    def _case_exists(case_no: str) -> bool:
        return any(s.case_no == case_no for s in _scored())

    def _safe_back() -> str:
        """토글 후 복귀 경로 — 같은 호스트의 referrer만 허용(open redirect 방지).

        (B1 2026-07-27) 상세가 private max-age=45 캐시를 갖게 되어(프리페치 재사용),
        토글 직후 복귀가 캐시본(옛 별 상태)을 쓰지 않도록 캐시버스터 _r 를 붙인다.
        """
        ref = request.referrer or ""
        if ref.startswith(request.host_url):
            import time as _time  # noqa: PLC0415
            sep = "&" if "?" in ref else "?"
            return f"{ref}{sep}_r={int(_time.time())}"
        return "/watchlist"

    @app.get("/watchlist")
    def watchlist_page():
        from . import tax  # noqa: PLC0415
        items = _scored()
        wl, wl_corrupt = watchlist.load_watchlist_status(watchlist.watchlist_path())
        # 복합키(court|case_no|item_no) 우선 매칭 + 레거시(bare case_no) 하위호환.
        watched = query.sort_items([s for s in items if watchlist.is_watched(wl, s)])
        present = ({watchlist.wl_key(s.court, s.case_no, s.item_no) for s in items}
                   | {s.case_no for s in items})
        missing = sorted(wl - present)
        snap_path = watchlist.snapshot_path()
        prev, snap_corrupt = watchlist.load_snapshot_status(snap_path)
        events = (watchlist.detect_changes(prev, watchlist.snapshot_from_scored(items), wl)
                  if prev else [])
        # 침묵실패 방지: '변동 없음'의 진짜 이유를 구분해 전달(손상 vs 스냅샷없음 vs 실제무변동).
        # snapshot_missing은 파일 존재로 판정 — 빈 스냅샷({}, 매물 0건 새로고침으로 정상 저장)을
        # 'prev가 falsy'라는 이유로 '없음'으로 오판하지 않도록(B9). 손상은 corrupted 배너가 별도 처리.
        return render_template(
            "watchlist.html", watched=watched, missing=missing, events=events,
            won=report.won, pct=report.pct, tax_label=tax.PROFILE.label(),
            corrupted=(wl_corrupt or snap_corrupt),
            snapshot_missing=(not snap_path.exists() and not snap_corrupt),
            data_source=getattr(g, "data_source", "n/a"))

    @app.get("/api/watchlist")
    def watchlist_api_list():
        return jsonify(sorted(watchlist.load_watchlist(watchlist.watchlist_path())))

    def _wl_key_from_request(case_no: str) -> str:
        """요청의 court/item 파라미터로 복합키 구성 — 없으면(레거시 클라이언트) case_no 단독.

        동명 사건(복수 법원)·다물건 사건에서 정확한 물건 하나만 등록/해제되게 한다(감사 2026-07-10).
        """
        court = request.values.get("court", "")
        item = request.values.get("item", "")
        if court:
            return watchlist.wl_key(court, case_no, item)
        return case_no

    @app.post("/api/watchlist/<case_no>")
    def watchlist_api_add(case_no: str):
        if not _case_exists(case_no):
            abort(404)
        watchlist.add_watch(_wl_key_from_request(case_no), watchlist.watchlist_path())
        return {"ok": True, "watching": True}

    @app.delete("/api/watchlist/<case_no>")
    def watchlist_api_remove(case_no: str):
        p = watchlist.watchlist_path()
        # 복합키·레거시 둘 다 제거(어느 쪽으로 등록됐든 해제되게)
        watchlist.remove_watch(_wl_key_from_request(case_no), p)
        watchlist.remove_watch(case_no, p)
        return {"ok": True, "watching": False}

    @app.post("/watchlist/toggle/<case_no>")
    def watchlist_toggle(case_no: str):
        p = watchlist.watchlist_path()
        wl = watchlist.load_watchlist(p)
        key = _wl_key_from_request(case_no)
        if key in wl or case_no in wl:
            watchlist.remove_watch(key, p)
            watchlist.remove_watch(case_no, p)   # 레거시 엔트리도 함께 해제
        else:
            if not _case_exists(case_no):
                abort(404)
            watchlist.add_watch(key, p)
        return redirect(_safe_back())

    @app.get("/calendar")
    def calendar_page():
        import re as _re  # noqa: PLC0415
        from datetime import date as _date  # noqa: PLC0415
        items = _scored()
        show_all = request.args.get("all") == "1"
        today = _date.today().isoformat()
        upcoming, past = sale_calendar.split_upcoming(items, today)
        months_all = sale_calendar.month_groups(items if show_all else upcoming)
        # (2026-07-24 QA HIGH②) 전체 월 일괄 렌더는 HTML 6MB·DOM 15만 노드 — 선택한 한 달만
        # 렌더하고 나머지 달은 카운트 링크로 노출한다. month 미지정 시 오늘이 속한(이후 첫) 달.
        sel = request.args.get("month", "")
        if not _re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", sel):
            sel = ""
        if not sel and months_all:
            sel = next((m["month"] for m in months_all if m["month"] >= today[:7]),
                       months_all[-1]["month"])
        months = [m for m in months_all if m["month"] == sel]
        month_index = [{"month": m["month"], "count": m["count"]} for m in months_all]
        # 한 달에 수천 건이 몰리는 분포(기일이 근월 집중)라 월 분리만으론 부족 — 월 안에서도
        # 400건 단위로 끊는다(날짜 경계 유지). 400건 ≈ 200KB — 모바일 안전권.
        CAL_PAGE = 400
        cal_page = None
        if months:
            m0 = months[0]
            flat = [(day["date"], s) for day in m0["days"] for s in day["items"]]
            try:
                pageno = max(1, int(request.args.get("page", 1)))
            except (TypeError, ValueError):
                pageno = 1
            pages_m = max(1, (len(flat) + CAL_PAGE - 1) // CAL_PAGE)
            pageno = min(pageno, pages_m)
            days: list[dict] = []
            for d, s in flat[(pageno - 1) * CAL_PAGE:pageno * CAL_PAGE]:
                if not days or days[-1]["date"] != d:
                    days.append({"date": d, "items": []})
                days[-1]["items"].append(s)
            months = [{"month": m0["month"], "count": len(flat), "days": days}]
            if pages_m > 1:
                cal_page = {"page": pageno, "pages": pages_m, "total": len(flat)}
        return render_template(
            "calendar.html", months=months, today=today, show_all=show_all,
            month_index=month_index, sel_month=sel, cal_page=cal_page,
            upcoming_count=len(upcoming), past_count=len(past),
            unknown_count=sale_calendar.unknown_date_count(items),
            won=report.won, weekday=sale_calendar.weekday_kr,
            data_source=getattr(g, "data_source", "n/a"))

    @app.get("/stats")
    def stats_page():
        items = _scored()
        d = stats.summarize(items)
        # 권리 도넛 정직화(재검증 감사 idx2 HIGH): 과거 '전체 − 권리미확인 = 확인 완료(96%)'는
        # 명세서를 한 번도 안 본 물건(차익없음·미지원유형 등)까지 '확인 완료'로 세는 왜곡.
        # 실제 확인(=법원 명세서 크롤) 여부는 badges 존재로 센다.
        badges = _rights_badges()
        keys = {f"{s.court}|{s.case_no}|{s.item_no}" for s in items}
        crawled = [k for k in keys if k in badges]
        rights_stats = {
            "crawled": len(crawled),
            "burden": sum(1 for k in crawled if not badges[k].is_clean),
            "clean": sum(1 for k in crawled if badges[k].is_clean),
            "uncrawled": len(keys) - len(crawled),
        }
        return render_template("stats.html", d=d, rights=rights_stats,
                               won=report.won, pct=report.pct,
                               data_source=getattr(g, "data_source", "n/a"))

    @app.get("/compare")
    def compare_page():
        from . import tax  # noqa: PLC0415
        cases = request.args.getlist("case")[:compare.MAX_COMPARE]  # 입력 개수 하드캡(방어)
        items = compare.select_for_compare(_scored(), cases)
        # 요청했으나 조회 결과에 없는 사건(매각·취하 등으로 목록에서 사라짐) — 침묵 드롭 방지.
        # 식별자는 복합키/레거시 혼재 — 둘 다 found 로 인정.
        found = ({s.case_no for s in items}
                 | {f"{s.court}|{s.case_no}|{s.item_no}" for s in items})
        missing_cases = [c.split("|")[1] if "|" in c else c
                         for c in cases if c not in found]
        taxes = {s.case_no: tax.acquisition_tax_breakdown(s.min_bid_price, s.property_type, s.area_m2)
                 for s in items}
        return render_template("compare.html", items=items, taxes=taxes,
                               requested=len(cases), missing_cases=missing_cases,
                               won=report.won, pct=report.pct, tax_label=tax.PROFILE.label(),
                               data_source=getattr(g, "data_source", "n/a"))

    @app.get("/api/stats")
    def stats_api():
        return jsonify(stats.summarize(_scored()))

    @app.get("/guide")
    def guide():
        # 정적 경매 지식(권리분석·세금·체크리스트). 헤더 데이터배지는 실제 출처 반영(일관성).
        _mark_source(_probe_source())
        return render_template("guide.html",
                               data_source=getattr(g, "data_source", "n/a"))

    @app.get("/methodology")
    def methodology():
        from . import tax  # noqa: PLC0415
        rows = backtest.evaluate()
        cal = backtest.calibration(rows)
        prec = {t: backtest.precision_at(rows, t) for t in (80, 60, 40)}
        # 헤더 데이터배지는 실제 출처 반영(백테스트 표본과 별개 — 다른 페이지와 일관).
        _mark_source(_probe_source())
        return render_template("methodology.html", cfg=score.CONFIG, cal=cal, prec=prec,
                               won=report.won, tax_label=tax.PROFILE.label(),
                               data_source=getattr(g, "data_source", "n/a"))

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
        import threading  # noqa: PLC0415
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
