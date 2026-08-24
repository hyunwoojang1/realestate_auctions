"""보조 페이지 — /map, /digest, /calendar, /stats, /compare, /guide, /methodology."""
from __future__ import annotations

from flask import Blueprint, g, render_template, request

from .. import backtest, compare, digest, query, report, sale_calendar, score, stats, web

bp = Blueprint("pages", __name__)


@bp.get("/map")
def map_page():
    # 지도 페이지는 _scored()를 직접 안 부르므로 출처를 명시 탐지(헤더 '샘플' 오표시 방지).
    src = web._probe_source()
    web._mark_source(src)
    return render_template("map.html", data_source=src)


@bp.get("/digest")
def digest_page():
    from .. import tax  # noqa: PLC0415
    n = request.args.get("n", default=10, type=int)
    min_profit_eok = request.args.get("min_profit", type=float)
    min_profit = int(min_profit_eok * 1e8) if min_profit_eok else None
    digest_badges = web._rights_badges()
    # (2026-07-24) 추천 다이제스트도 당일 입찰 마감 물건 제외 — 홈 추천과 동일 기준(리뷰 HIGH).
    _now = query.now_kst()
    _biddable_items = [s for s in web._scored() if not query.bidding_closed(s, _now)]
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


@bp.get("/calendar")
def calendar_page():
    import re as _re  # noqa: PLC0415
    from datetime import date as _date  # noqa: PLC0415
    items = web._scored()
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


@bp.get("/stats")
def stats_page():
    items = web._scored()
    d = stats.summarize(items)
    # 권리 도넛 정직화(재검증 감사 idx2 HIGH): 과거 '전체 − 권리미확인 = 확인 완료(96%)'는
    # 명세서를 한 번도 안 본 물건(차익없음·미지원유형 등)까지 '확인 완료'로 세는 왜곡.
    # 실제 확인(=법원 명세서 크롤) 여부는 badges 존재로 센다.
    badges = web._rights_badges()
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


@bp.get("/compare")
def compare_page():
    from .. import tax  # noqa: PLC0415
    cases = request.args.getlist("case")[:compare.MAX_COMPARE]  # 입력 개수 하드캡(방어)
    items = compare.select_for_compare(web._scored(), cases)
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


@bp.get("/guide")
def guide():
    # 정적 경매 지식(권리분석·세금·체크리스트). 헤더 데이터배지는 실제 출처 반영(일관성).
    web._mark_source(web._probe_source())
    return render_template("guide.html",
                           data_source=getattr(g, "data_source", "n/a"))


@bp.get("/methodology")
def methodology():
    from .. import tax  # noqa: PLC0415
    rows = backtest.evaluate()
    cal = backtest.calibration(rows)
    prec = {t: backtest.precision_at(rows, t) for t in (80, 60, 40)}
    # 헤더 데이터배지는 실제 출처 반영(백테스트 표본과 별개 — 다른 페이지와 일관).
    web._mark_source(web._probe_source())
    return render_template("methodology.html", cfg=score.CONFIG, cal=cal, prec=prec,
                           won=report.won, tax_label=tax.PROFILE.label(),
                           data_source=getattr(g, "data_source", "n/a"))
