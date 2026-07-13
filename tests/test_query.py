"""CLI 필터·정렬·JSON 출력 테스트."""
import datetime as dt
import json

from src import pipeline, query, report
from src.models import ScoredListing


def _scored():
    return pipeline.run()  # 샘플 6건


def _sl(**kw) -> ScoredListing:
    base = dict(
        case_no="X", apt_name="테스트", address="서울 강남구", property_type="아파트",
        area_m2=84.9, appraisal_price=6_000_000_000 // 10, min_bid_price=400_000_000,
        fail_count=1, sale_date="2026-07-15", est_market_price=500_000_000,
        matched_trades=5, confidence=0.9, real_acquisition_cost=420_000_000,
        expected_profit=80_000_000, gap_rate=0.16, gap_score=70.0, rights_score=80.0,
        liquidity_score=60.0, arb_score=72.0, grade="양호",
        profit_low=80_000_000, market_band_low=500_000_000, market_band_high=520_000_000,
    )
    base.update(kw)
    return ScoredListing(**base)


def test_min_score_filter():
    out = query.apply_filters(_scored(), min_score=80)
    assert out  # 상계주공(95)·역삼 오피스텔(81)
    assert all(s.arb_score is not None and s.arb_score >= 80 for s in out)


def test_type_filter():
    out = query.apply_filters(_scored(), property_type="오피스텔")
    assert out and all(s.property_type == "오피스텔" for s in out)


def test_region_filter():
    out = query.apply_filters(_scored(), region="서울")
    assert out and all(s.address.startswith("서울") for s in out)


def test_combined_filter():
    out = query.apply_filters(_scored(), min_score=50, property_type="아파트", region="서울")
    assert all(s.arb_score >= 50 and s.property_type == "아파트" and s.address.startswith("서울") for s in out)


def test_min_profit_filter():
    out = query.apply_filters(_scored(), min_profit=100_000_000)
    assert out and all(s.expected_profit is not None and s.expected_profit >= 100_000_000 for s in out)


# ---- 검색 우선 홈 헬퍼 ----

def test_is_evaluable():
    assert query.is_evaluable(_sl(est_market_price=500_000_000)) is True
    assert query.is_evaluable(_sl(est_market_price=None)) is False


def test_evaluable_only_filter_hides_no_estimate():
    items = [_sl(case_no="A", est_market_price=500_000_000),
             _sl(case_no="B", est_market_price=None, grade="미지원유형")]
    out = query.apply_filters(items, evaluable_only=True)
    assert [s.case_no for s in out] == ["A"]


def test_budget_max_and_min_bid():
    items = [_sl(case_no="cheap", min_bid_price=80_000_000),
             _sl(case_no="mid", min_bid_price=400_000_000),
             _sl(case_no="pricey", min_bid_price=900_000_000)]
    assert {s.case_no for s in query.apply_filters(items, max_bid=100_000_000)} == {"cheap"}
    assert {s.case_no for s in query.apply_filters(items, max_bid=500_000_000)} == {"cheap", "mid"}
    assert {s.case_no for s in query.apply_filters(items, min_bid=800_000_000)} == {"pricey"}


def test_area_bounds_brackets():
    lo, hi = query.area_bounds("20")        # 20평대 = 66.1~99.2㎡
    assert round(lo, 1) == 66.1 and round(hi, 1) == 99.2
    lo, hi = query.area_bounds("~20")       # 상한만(20평 미만)
    assert lo is None and round(hi, 1) == 66.1
    lo, hi = query.area_bounds("50plus")    # 하한만(50평+)
    assert round(lo, 1) == 165.3 and hi is None
    assert query.area_bounds("") == (None, None) and query.area_bounds("헛값") == (None, None)


def test_area_filter_by_bracket():
    items = [_sl(case_no="s", area_m2=59.9),    # 18평
             _sl(case_no="m", area_m2=84.9),    # 25평 → 20평대
             _sl(case_no="l", area_m2=115.0)]   # 34평 → 30평대
    lo, hi = query.area_bounds("20")
    assert {s.case_no for s in query.apply_filters(items, min_area=lo, max_area=hi)} == {"m"}
    lo, hi = query.area_bounds("~20")
    assert {s.case_no for s in query.apply_filters(items, min_area=lo, max_area=hi)} == {"s"}


def test_min_fails_filter():
    items = [_sl(case_no="new", fail_count=0),
             _sl(case_no="once", fail_count=1),
             _sl(case_no="thrice", fail_count=3)]
    assert {s.case_no for s in query.apply_filters(items, min_fails=1)} == {"once", "thrice"}
    assert {s.case_no for s in query.apply_filters(items, min_fails=3)} == {"thrice"}


def test_matches_query_by_name_and_address():
    s = _sl(apt_name="롯데캐슬 골드", address="서울 송파구 신천동")
    assert query.matches_query(s, "롯데캐슬") is True
    assert query.matches_query(s, "롯데 캐슬") is True       # 공백 무시
    assert query.matches_query(s, "신천동") is True          # 주소도 대상
    assert query.matches_query(s, "래미안") is False
    assert query.matches_query(s, "") is True                # 빈 검색어는 전체 통과


def test_name_search_filter():
    items = [_sl(case_no="A", apt_name="래미안 대치팰리스", address="경기 성남시"),
             _sl(case_no="B", apt_name="롯데캐슬", address="부산 해운대구"),
             _sl(case_no="C", apt_name="힐스테이트", address="서울 래미안로 12")]
    # 단지명 일치
    assert {s.case_no for s in query.apply_filters(items, q="래미안")} == {"A", "C"}
    # 주소 일치(단지명은 힐스테이트지만 주소에 '래미안로')
    assert {s.case_no for s in query.apply_filters(items, q="롯데")} == {"B"}
    # 다른 필터와 결합 — 이름 + 지역
    assert {s.case_no for s in query.apply_filters(items, q="래미안", region="서울")} == {"C"}


def test_days_until_and_is_soon():
    today = dt.date(2026, 7, 12)
    assert query.days_until("2026-07-15", today) == 3
    assert query.days_until("bad-date", today) is None
    assert query.is_soon(_sl(sale_date="2026-07-15"), today) is True
    assert query.is_soon(_sl(sale_date="2026-08-30"), today) is False   # 7일 초과
    assert query.is_soon(_sl(sale_date="2026-07-01"), today) is False   # 지난 기일


def test_is_high_profit():
    assert query.is_high_profit(_sl(profit_low=250_000_000)) is True
    assert query.is_high_profit(_sl(profit_low=100_000_000)) is False
    assert query.is_high_profit(_sl(profit_low=None, expected_profit=None)) is False


def test_default_sort_is_profit():
    assert query.DEFAULT_SORT == "profit"
    out = query.sort_items(_scored())   # 기본 정렬
    profits = [s.expected_profit for s in out if s.expected_profit is not None]
    assert profits == sorted(profits, reverse=True)


def test_sort_by_profit_desc():
    out = query.sort_items(_scored(), "profit")
    profits = [s.expected_profit for s in out if s.expected_profit is not None]
    assert profits == sorted(profits, reverse=True)


def test_positive_only_subtracts_burden_and_drops_uncertain():
    a = _sl(case_no="clean_pos", profit_low=200_000_000)          # 인수 없음 +2억 → 통과
    b = _sl(case_no="burden_neg", profit_low=100_000_000)         # +1억이나 인수 3억 → 제외
    c = _sl(case_no="uncertain", profit_low=200_000_000)          # 금액 미상 부담 → 제외
    d = _sl(case_no="no_estimate", profit_low=None, expected_profit=None)  # 차익 없음 → 제외
    assumed = {"burden_neg": 300_000_000}
    burden_of = lambda s: assumed.get(s.case_no, 0)               # noqa: E731
    uncertain_of = lambda s: s.case_no == "uncertain"            # noqa: E731
    out = query.positive_only([a, b, c, d], burden_of=burden_of, uncertain_of=uncertain_of)
    assert [s.case_no for s in out] == ["clean_pos"]


def test_count_by_sido_groups_and_sorts_desc():
    items = [_sl(case_no="a", address="서울 강남구"),
             _sl(case_no="b", address="서울 노원구"),
             _sl(case_no="c", address="경기 성남시"),
             _sl(case_no="d", address="주소불명")]   # 시도 미상 → '기타'
    out = query.count_by_sido(items)
    assert out[0] == {"sido": "서울", "count": 2}     # 최다 먼저
    by = {e["sido"]: e["count"] for e in out}
    assert by["경기"] == 1 and by["기타"] == 1
    assert sum(e["count"] for e in out) == 4           # 전건 계상(누락 없음)


def test_to_json_is_valid_and_complete():
    items = _scored()
    data = json.loads(report.to_json(items))
    assert isinstance(data, list) and len(data) == len(items)
    assert "arb_score" in data[0] and "grade" in data[0]
