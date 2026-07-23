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
from .models import AuctionListing

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


def _rights_badges() -> dict:
    """(court|case_no|item_no) → RightsBadge. 크롤된 물건만 담긴다(없으면 '미확인' 렌더).

    목록의 '인수 부담' 칩·예상 투입 계산용. rights 테이블은 수백 행 수준이라 요청당
    로드해도 가볍고, 클라우드는 store_rest 가 TTL 캐시로 왕복을 줄인다.
    """
    from .courtauction_detail import CaseRights, summarize  # noqa: PLC0415
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
    except Exception as e:  # noqa: BLE001 — 배지 실패는 목록을 막지 않음(미확인으로 폴백)
        logger.warning("권리 배지 로드 실패 → 전량 '미확인' 폴백: %s", e)
        return {}
    out = {}
    for r in rows:
        cr = CaseRights.from_row(r)
        # (서빙감사 2026-07-12 #13) 빈/부분 응답(작성일·최선순위·인수권리 전무)은 판정 근거가
        # 0 이므로 배지를 만들지 않는다 — '✓ 인수 없음'으로 오판하지 않고 '미확인'으로 폴백.
        # (2026-07-22) 자유기술란 3칸이 전부 빈 요지도 대항력 판정근거 0 → 배지 미생성(초록 오표시 방지).
        if cr.is_empty or not cr.opposability_assessable:
            continue
        out[f"{cr.court}|{cr.case_no}|{cr.item_no}"] = summarize(cr)
    return out


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
        import datetime as _dt  # noqa: PLC0415

        from . import tax  # noqa: PLC0415
        # (2026-07-20) 사건번호 라우팅 — 홈 검색창에 "2025-101763"처럼 사건번호를 넣으면
        # 단지명 검색이 아니라 사건 조회(/find)로 보낸다. 판별은 엄격(이름검색 오탈취 방지).
        _q0 = request.args.get("q", "").strip()
        if _q0 and casesearch.looks_like_case_no(_q0):
            from urllib.parse import quote  # noqa: PLC0415
            return redirect(f"/find?q={quote(_q0)}")
        badges = _rights_badges()
        all_scored = _scored()   # 전체 채점 결과(출처 표시는 _scored 내부에서)
        today = _dt.date.today()

        def _clean(s):
            b = badges.get(f"{s.court}|{s.case_no}|{s.item_no}")
            return bool(b and b.is_clean)

        # 평가 가능(시세 추정된) 물건 = 검색 우선 홈의 기본 모수. 89% 노이즈(미지원·시세추정불가)는
        # 여기서 빠지고 '전체 탐색'(all=1)에서만 보인다.
        evaluable = [s for s in all_scored if query.is_evaluable(s)]
        # 커버리지·칩 카운트(활성 필터와 무관하게 고정) — 정직한 밀도 노출.
        coverage = {
            "total": len(all_scored),
            "eval": len(evaluable),
            "noise": len(all_scored) - len(evaluable),
            "pos": sum(1 for s in evaluable if (query.decision_profit(s) or 0) > 0),
            "soon": sum(1 for s in evaluable if query.is_soon(s, today)),
            "clean": sum(1 for s in evaluable if _clean(s)),
            "high": sum(1 for s in evaluable if query.is_high_profit(s)),
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

        burden = _burden_of(badges)
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
            items = [s for s in items if query.is_soon(s, today)]
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
                return True
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
        seoul_ct = sum(1 for s in evaluable if matches_region(s.address, "서울"))
        chips = [
            {"label": "인수 없음", "count": coverage["clean"], "active": chip_clean, "href": _toggle("clean")},
            {"label": "매각기일 임박", "count": coverage["soon"], "active": chip_soon, "href": _toggle("soon")},
            {"label": "고차익 2억+", "count": coverage["high"], "active": chip_high, "href": _toggle("high")},
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
            hero=hero, legacy_only=legacy_only, badges=badges,
            won=report.won, pct=report.pct, meter=report.gap_meter_html,
            days_until=query.days_until,
            tax_label=tax.PROFILE.label(),
            watched=watchlist.load_watchlist(watchlist.watchlist_path()),
            data_source=getattr(g, "data_source", "n/a"))

    @app.get("/health")
    def health():
        src = _probe_source()
        _mark_source(src)
        return {"status": "ok", "data_source": src}

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

    def _find_by_case(case_no: str):
        """(T8 감사 수정 — B14 핵심) 사건번호 매칭 물건 전부.

        T1 복합키 도입으로 같은 사건의 물건 여러 개가 공존한다 — 단건 next()는 임의의
        한 물건만 반환해 다른 물건의 수치(아파트 vs 상가)를 보여주는 침묵 오표시를 만든다.
        item(물건번호)·court 쿼리 파라미터로 좁힐 수 있다.

        (2026-07-20) 입력 정규화 — 친구가 "2025-101763"처럼 보내도 저장 포맷("2025타경101763")과
        맞도록 casesearch로 환원해 매칭한다. 파싱 불가하면 원문 완전일치로 폴백(회귀 없음).
        """
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
        if not matches:
            abort(404)
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
        try:
            if db_path:
                rconn = store.connect(db_path)
                try:
                    rights_row = store.load_rights(rconn, s.court, s.case_no, s.item_no)
                finally:
                    rconn.close()
            elif store_rest.enabled():
                rights_row = store_rest.fetch_rights(s.court, s.case_no, s.item_no)
        except Exception as e:  # noqa: BLE001 — 권리 요지 실패는 상세 페이지를 막지 않음
            logger.warning("권리 요지 로드 실패(%s %s): %s", s.court, s.case_no, e)
        # 물건 사진 썸네일(히어로) — 권리와 동일 경로(로컬 SQLite / 클라우드 REST).
        photos: list[str] = []
        try:
            if db_path:
                pconn = store.connect(db_path)
                try:
                    photos = store.load_photos(pconn, s.court, s.case_no, s.item_no)
                finally:
                    pconn.close()
            elif store_rest.enabled():
                photos = store_rest.fetch_photos(s.court, s.case_no, s.item_no)
        except Exception as e:  # noqa: BLE001 — 사진 실패는 히어로 생략, 페이지는 정상
            logger.warning("사진 로드 실패(%s %s): %s", s.court, s.case_no, e)
        # 임차인 현황(현황조사서 crawl) — 대항력 실판정(전입일 vs 말소기준) 원천. 미크롤이면 빈 리스트.
        tenants: list[dict] = []
        try:
            if db_path:
                tconn = store.connect(db_path)
                try:
                    tenants = store.load_tenants(tconn, s.court, s.case_no, s.item_no)
                finally:
                    tconn.close()
            elif store_rest.enabled():
                tenants = store_rest.fetch_tenants(s.court, s.case_no, s.item_no)
        except Exception as e:  # noqa: BLE001 — 임차인 로드 실패는 상세 페이지를 막지 않음
            logger.warning("임차인 현황 로드 실패(%s %s): %s", s.court, s.case_no, e)

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
            brow = None
            try:
                if db_path:
                    bconn = store.connect(db_path)
                    try:
                        brow = store.load_building(bconn, s.court, s.case_no, s.item_no)
                    finally:
                        bconn.close()
                elif store_rest.enabled() and hasattr(store_rest, "fetch_building"):
                    brow = store_rest.fetch_building(s.court, s.case_no, s.item_no)
            except Exception as e:  # noqa: BLE001 — 캐시 실패는 라이브 폴백
                logger.warning("건축물대장 캐시 조회 실패(%s %s): %s", s.court, s.case_no, e)
            if brow and brow.get("status") == "ok":
                bldg = brow
            else:
                bldg = building_info.get_building_summary(s.address)
        return render_template(
            "detail.html", s=s, listing=listing, chart=chart, rights=rights, badge=badge,
            coord=coord, days_until=query.days_until,
            bldg=bldg, vworld_key=os.environ.get("VWORLD_API_KEY", "").strip(),
            priority=priority,
            meter=report.gap_meter_html(s, askings=ask_points), won=report.won, pct=report.pct,
            gated=gated, gate_reason=", ".join(gate_reasons),
            tax_parts=tax_parts, tax_label=tax.PROFILE.label(),
            watching=watchlist.is_watched(
                watchlist.load_watchlist(watchlist.watchlist_path()), s),
            data_source=getattr(g, "data_source", "n/a"),
            sample_gate_low=sample_gate_low, band_confident=band_confident_basis(),
            ask_points=ask_points, ask_overstated=ask_overstated, photos=photos,
        )

    @app.get("/digest")
    def digest_page():
        from . import tax  # noqa: PLC0415
        n = request.args.get("n", default=10, type=int)
        min_profit_eok = request.args.get("min_profit", type=float)
        min_profit = int(min_profit_eok * 1e8) if min_profit_eok else None
        digest_badges = _rights_badges()
        items = digest.top_listings(_scored(), n=n, min_profit=min_profit,
                                    badges=digest_badges)
        filters = {"min_profit": request.args.get("min_profit", ""), "type": "", "region": "",
                   "sort": query.DEFAULT_SORT, "clean": ""}
        return render_template(
            "listings.html", items=items, count=len(items), filters=filters,
            badges=digest_badges, days_until=query.days_until,
            won=report.won, pct=report.pct, meter=report.gap_meter_html,
            tax_label=tax.PROFILE.label(),
            data_source=getattr(g, "data_source", "n/a"))

    def _case_exists(case_no: str) -> bool:
        return any(s.case_no == case_no for s in _scored())

    def _safe_back() -> str:
        """토글 후 복귀 경로 — 같은 호스트의 referrer만 허용(open redirect 방지)."""
        ref = request.referrer or ""
        if ref.startswith(request.host_url):
            return ref
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
        from datetime import date as _date  # noqa: PLC0415
        items = _scored()
        show_all = request.args.get("all") == "1"
        today = _date.today().isoformat()
        upcoming, past = sale_calendar.split_upcoming(items, today)
        months = sale_calendar.month_groups(items if show_all else upcoming)
        return render_template(
            "calendar.html", months=months, today=today, show_all=show_all,
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

    return app


def _truthy(val: str | None) -> bool:
    """env flag → bool. 미설정/빈값/0/false/no/off 는 False."""
    return (val or "").strip().lower() in {"1", "true", "yes", "on"}


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
