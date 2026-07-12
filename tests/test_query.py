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
