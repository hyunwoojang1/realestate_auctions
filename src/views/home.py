"""홈(/) — 검색 우선 목록·엄선 추천·빠른진입 칩·페이지네이션.

web.py 1,872줄 blueprint 분리(2026-08-24 감사 코드품질 CRITICAL). 로직은 이동만 —
공유 헬퍼는 `web.` 모듈 속성으로 늦게 바인딩한다(테스트 monkeypatch(web._scored) 보존).
"""
from __future__ import annotations

from flask import Blueprint, g, redirect, render_template, request

from .. import casesearch, query, report, store_rest, watchlist, web

bp = Blueprint("home", __name__)


@bp.get("/")
def index():
    from .. import tax  # noqa: PLC0415
    # (2026-07-20) 사건번호 라우팅 — 홈 검색창에 "2025-101763"처럼 사건번호를 넣으면
    # 단지명 검색이 아니라 사건 조회(/find)로 보낸다. 판별은 엄격(이름검색 오탈취 방지).
    _q0 = request.args.get("q", "").strip()
    if _q0 and casesearch.looks_like_case_no(_q0):
        from urllib.parse import quote  # noqa: PLC0415
        return redirect(f"/find?q={quote(_q0)}")
    # (감사 HIGH 2026-07-28) 세 데이터셋을 먼저 동시에 받아 캐시를 채운다 — 아래
    # 두 호출은 그 캐시를 쓰므로 순서·flask.g 의미는 그대로다(콜드에서만 이득).
    store_rest.warm_caches()
    badges = web._rights_badges()
    all_scored = web._scored()   # 전체 채점 결과(출처 표시는 _scored 내부에서)
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
    burden = web._burden_of(badges)
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
            # (감사 HIGH 2026-07-28) float('inf')·'1e400' 은 ValueError 를 내지 않고 inf 를
            # 만들고, int(inf) 가 OverflowError 로 터져 500 이 됐다(프로덕션 재현 확인).
            # NaN 도 ValueError 를 내므로 함께 잡는다.
            import math  # noqa: PLC0415
            _b = float(budget)
            if not math.isfinite(_b):
                raise ValueError(budget)
            max_bid = int(_b * 1e8)
        except (ValueError, OverflowError):
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
                             uncertain_of=web._uncertain_of(badges))

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

    from ..digest import passes_recommend_gates  # noqa: PLC0415

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

    from ..region import matches_region  # noqa: PLC0415

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
        a = {k: v for k, v in request.args.items() if k != "page"}
        a["page"] = str(p)
        return "/?" + urlencode(a)
    return render_template(
        "listings.html", items=items, count=total, filters=filters, chips=chips,
        page=page, total_pages=total_pages, page_url=_page_url,
        mode=mode, coverage=coverage, all_mode=all_mode,
        hero=hero, legacy_only=legacy_only, badges=badges, resales=web._resale_map(),
        won=report.won, pct=report.pct,
        days_until=query.days_until,
        tax_label=tax.PROFILE.label(),
        watched=watchlist.load_watchlist(watchlist.watchlist_path()),
        data_source=getattr(g, "data_source", "n/a"))
