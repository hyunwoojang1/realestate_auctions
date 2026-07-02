"""CLI 필터·정렬·JSON 출력 테스트."""
import json

from src import pipeline, query, report


def _scored():
    return pipeline.run()  # 샘플 6건


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


def test_default_sort_is_profit():
    assert query.DEFAULT_SORT == "profit"
    out = query.sort_items(_scored())   # 기본 정렬
    profits = [s.expected_profit for s in out if s.expected_profit is not None]
    assert profits == sorted(profits, reverse=True)


def test_sort_by_profit_desc():
    out = query.sort_items(_scored(), "profit")
    profits = [s.expected_profit for s in out if s.expected_profit is not None]
    assert profits == sorted(profits, reverse=True)


def test_to_json_is_valid_and_complete():
    items = _scored()
    data = json.loads(report.to_json(items))
    assert isinstance(data, list) and len(data) == len(items)
    assert "arb_score" in data[0] and "grade" in data[0]
