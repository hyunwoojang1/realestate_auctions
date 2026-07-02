"""물건 비교(B6) 테스트 — select_for_compare 순수함수 + /compare 라우트. 라이브 0."""
import pytest

from src import compare
from src.models import ScoredListing
from src.web import create_app


def _sl(case_no, name="A단지", profit=1_000):
    return ScoredListing(
        case_no=case_no, apt_name=name, address="서울 노원구 상계동", property_type="아파트",
        area_m2=59.0, appraisal_price=500_000_000, min_bid_price=400_000_000, fail_count=1,
        sale_date="2026-07-15", est_market_price=500_000_000, matched_trades=5, confidence=0.9,
        real_acquisition_cost=404_000_000, expected_profit=profit, gap_rate=0.2, gap_score=40.0,
        rights_score=20.0, liquidity_score=15.0, arb_score=80.0, grade="권리미확인")


FIX = [_sl("A"), _sl("B"), _sl("C"), _sl("D"), _sl("E")]


def test_select_preserves_order_and_ignores_missing():
    out = compare.select_for_compare(FIX, ["B", "없음", "A"])
    assert [s.case_no for s in out] == ["B", "A"]      # 순서 보존·없는 건 무시


def test_select_dedup():
    out = compare.select_for_compare(FIX, ["A", "A", "B"])
    assert [s.case_no for s in out] == ["A", "B"]


def test_select_caps_at_max():
    out = compare.select_for_compare(FIX, ["A", "B", "C", "D", "E"], max_n=4)
    assert len(out) == 4


def test_select_empty_when_none_match():
    assert compare.select_for_compare(FIX, ["없음1", "없음2"]) == []


# ---- 라우트 ----

@pytest.fixture
def client():
    return create_app().test_client()


def test_compare_route_two_cases_renders_both(client):
    # 샘플 fixture의 실제 case_no 2건(상계주공·강남역삼)
    r = client.get("/compare?case=2024타경51234&case=2025타경60777")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "상계주공" in body and "강남역삼푸르지오시티" in body
    assert "예상차익" in body and "취득세" in body


def test_compare_route_needs_two(client):
    body = client.get("/compare?case=2024타경51234").get_data(as_text=True)
    assert "2건 이상" in body                            # 1건이면 안내


def test_compare_route_ignores_missing_case(client):
    r = client.get("/compare?case=2024타경51234&case=없는사건&case=2025타경60777")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "상계주공" in body and "강남역삼푸르지오시티" in body   # 없는 건 무시, 나머지 렌더
