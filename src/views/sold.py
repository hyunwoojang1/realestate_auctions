"""낙찰 결과(/sold) — 보존 스냅샷 목록·검색과 그 로더들.

로더 3종(_sold_rows·_sold_one·_sold_naver_*)은 상세(views.detail)도 함께 쓴다.
시세 정책(신뢰 출처만)은 store.apply_sold_market_policy — 여기는 조회·표시만.
"""
from __future__ import annotations

import logging
import os

from flask import Blueprint, g, render_template, request

from .. import casesearch, query, report, store, store_rest
from ..web import DB_ENV

logger = logging.getLogger(__name__)

bp = Blueprint("sold", __name__)


def _sold_naver_map() -> dict:
    """(court|case_no|item_no) → 네이버 단지번호. 시세가 없는 물건도 사용자가 네이버에서
    직접 확인할 수 있게 목록·상세에 링크를 걸기 위한 것(사용자 요청 2026-07-28)."""
    db_path = os.environ.get(DB_ENV)
    try:
        if db_path:
            conn = store.connect(db_path)
            try:
                return {f"{r['court']}|{r['case_no']}|{r['item_no']}": r["complex_no"]
                        for r in conn.execute(
                            "SELECT court, case_no, item_no, complex_no FROM naver_prices "
                            "WHERE complex_no IS NOT NULL AND complex_no != ''")}
            finally:
                conn.close()
        if store_rest.enabled():
            return {f"{r['court']}|{r['case_no']}|{r['item_no']}": r.get("complex_no")
                    for r in store_rest.load_all_naver()
                    if (r.get("complex_no") or "").strip()}
    except Exception as e:  # noqa: BLE001 — 링크 없음은 무해(페이지는 정상)
        logger.warning("네이버 단지번호 맵 로드 실패: %s", e)
    return {}


def _sold_naver_row(sold: dict) -> dict | None:
    """낙찰 물건 1건의 네이버 매핑(단지번호·KB시세 등). 없으면 None(링크 미표시)."""
    court, case_no = sold.get("court") or "", sold.get("case_no") or ""
    item_no = sold.get("item_no") or ""
    db_path = os.environ.get(DB_ENV)
    try:
        if db_path:
            conn = store.connect(db_path)
            try:
                return store.load_naver_price(conn, court, case_no, item_no)
            finally:
                conn.close()
        if store_rest.enabled():
            return store_rest.fetch_naver_price(court, case_no, item_no)
    except Exception as e:  # noqa: BLE001 — 링크 없음은 무해
        logger.warning("낙찰 네이버 매핑 로드 실패(%s): %s", case_no, e)
    return None


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


@bp.get("/sold")
def sold_page():
    """낙찰 기록 — 홈과 같은 검색 카드 + 기본 점수 높은순(사용자 결정 2026-07-27).

    홈 검색창처럼 **사건번호도 받는다**. 다만 홈과 달리 즉시 /find 로 넘기지 않고 먼저
    낙찰 기록 안에서 찾는다(여기서 찾는 사건은 대개 종결된 사건이라). 결과가 0건일 때만
    "진행 중 경매에서 찾기" 링크를 띄운다 — 자동 이동은 필터링 중 화면을 빼앗아 놀랍다.
    """
    import math  # noqa: PLC0415
    a = request.args
    q = (a.get("q") or "").strip()
    region, ptype = a.get("region", ""), a.get("type", "")
    budget, area, fails = a.get("budget", ""), a.get("area", ""), a.get("fails", "")
    sort = a.get("sort", query.SOLD_DEFAULT_SORT)
    if sort not in query.SOLD_SORT_KEYS:
        sort = query.SOLD_DEFAULT_SORT

    min_area, max_area = query.area_bounds(area)
    if area and (min_area, max_area) == (None, None):
        area = ""
    try:
        min_fails = int(fails) if fails else None
    except ValueError:
        fails, min_fails = "", None
    max_bid = min_bid = None
    if budget == "8plus":
        min_bid = 800_000_000
    elif budget:
        try:
            # (감사 HIGH 2026-07-28) float('inf')·'1e400' 은 ValueError 를 내지 않고 inf 를
            # 만들고, int(inf) 가 OverflowError 로 터져 500 이 됐다(프로덕션 재현 확인).
            # NaN 도 ValueError 를 내므로 함께 잡는다.
            _b = float(budget)
            if not math.isfinite(_b):
                raise ValueError(budget)
            max_bid = int(_b * 1e8)
        except (ValueError, OverflowError):
            budget = ""

    # 필터가 있으면 전량에서 걸러야 한다 — 300건만 읽고 거르면 뒤쪽 기록이 조용히 빠진다.
    rows = _sold_rows(5000)
    total = len(rows)
    rows = query.filter_sold(rows, q=q or None, region=region or None,
                             property_type=ptype or None, max_bid=max_bid, min_bid=min_bid,
                             min_area=min_area, max_area=max_area, min_fails=min_fails)
    # 고른 정렬이 쓰는 값(점수·차익·낙찰가)이 결과에 하나도 없으면 실제로는 정렬이
    # 일어나지 않는다 — 기일 최신순으로 강등하고 화면에 그 사실을 밝힌다(정렬된 척 금지).
    sort_unavailable = "" if query.sold_sort_available(rows, sort) else sort
    rows = query.sort_sold(rows, query.SOLD_DEFAULT_SORT if sort_unavailable else sort)
    filters = {"q": q, "region": region, "type": ptype, "budget": budget,
               "area": area, "fails": fails, "sort": sort}
    return render_template(
        "sold.html", rows=rows, count=len(rows), total=total, filters=filters,
        has_filter=bool(q or region or ptype or budget or area or fails),
        case_like=bool(q and casesearch.looks_like_case_no(q)),
        naver_map=_sold_naver_map(),
        sort_unavailable=sort_unavailable,
        sort_label={"score": "점수", "score_asc": "점수",
                    "profit": "시세 대비 차익", "profit_asc": "시세 대비 차익",
                    "price": "낙찰가", "price_asc": "낙찰가"}.get(sort_unavailable, ""),
        # 카드가 차익을 그리려면 같은 정의(시세 하한 − 낙찰가)를 써야 한다 — 정렬과 표시가
        # 다른 식을 쓰면 "위에 있는데 숫자가 더 작은" 화면이 된다. 템플릿에 함수째 넘긴다.
        sold_gap=query.sold_gap,
        # 게이트에 걸린 이유를 카드가 밝히려면 판정 함수도 함께 넘겨야 한다
        # (조용히 '시세 미추정'으로 뭉뚱그리면 왜 비었는지 알 수 없다).
        sold_comparable=query.sold_comparable,
        won=report.won, data_source=getattr(g, "data_source", "n/a"))
