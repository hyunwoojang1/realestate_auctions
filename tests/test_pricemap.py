"""pricemap(상세 가격 지도 좌표) 유닛 테스트 — UX 개편의 핵심 시각화 데이터.

불변식: 축은 모든 가격을 포함(0~100 안), 순서 보존(최저입찰가<취득원가<밴드),
gain 은 보수 기준(취득원가→검증 하한)이며 음수 차익이면 없음, 시세 정보가 없으면 None.
"""
from __future__ import annotations

from src import pricemap
from src.models import ScoredListing
from src.store import _COLS


def _sl(**over) -> ScoredListing:
    base = {c: None for c in _COLS}
    base.update({
        "case_no": "2025타경1", "apt_name": "테스트", "address": "서울", "property_type": "아파트",
        "area_m2": 84.0, "appraisal_price": 500_000_000, "min_bid_price": 320_000_000,
        "fail_count": 2, "sale_date": "2026-08-01", "est_market_price": 430_000_000,
        "matched_trades": 8, "confidence": 0.9, "real_acquisition_cost": 330_000_000,
        "expected_profit": 100_000_000, "gap_rate": 0.23, "gap_score": 50.0,
        "rights_score": 30.0, "liquidity_score": 20.0, "arb_score": 88.0, "grade": "차익 유력",
        "court": "서울중앙", "item_no": "1", "doc_id": "", "market_scope": "same_complex_same_area",
        "market_band_low": 400_000_000, "market_band_high": 430_000_000,
        "profit_low": 70_000_000, "profit_high": 100_000_000, "market_sample_basis": 8,
    })
    base.update(over)
    return ScoredListing(**{c: base[c] for c in _COLS})


class _Ask:
    def __init__(self, price):
        self.price = price


def test_band_case_orders_and_bounds():
    m = pricemap.build(_sl())
    assert m is not None
    # 모든 좌표가 축(0~100) 안
    pts = [m["appraisal"]["pct"], m["minbid"]["pct"], m["cost"]["pct"],
           m["band"]["lo_pct"], m["band"]["lo_pct"] + m["band"]["w_pct"]]
    assert all(0 <= p <= 100 for p in pts)
    # 가격 순서 보존: 최저입찰가 < 취득원가 < 밴드 하한 < 감정가
    assert m["minbid"]["pct"] < m["cost"]["pct"] < m["band"]["lo_pct"]
    # 보수 gain: 취득원가에서 시작, 폭 = 검증 하한까지
    assert m["gain"] is not None
    assert m["gain"]["lo_pct"] == m["cost"]["pct"]
    assert m["gain"]["amount"] == 400_000_000 - 330_000_000
    # 유찰 저감률
    assert abs(m["minbid"]["cut"] - (1 - 320 / 500)) < 1e-9


def test_no_gain_when_cost_above_band_low():
    m = pricemap.build(_sl(real_acquisition_cost=410_000_000, profit_low=-10_000_000))
    assert m["gain"] is None  # 음수 차익 — 초록 구간 없음(과장 금지)


def test_est_fallback_without_band():
    m = pricemap.build(_sl(market_band_low=None, market_band_high=None, profit_low=None))
    assert m["band"] is None and m["est"] is not None
    assert m["gain"]["amount"] == 430_000_000 - 330_000_000  # est − cost


def test_none_without_any_market_price():
    m = pricemap.build(_sl(market_band_low=None, market_band_high=None,
                           est_market_price=None, profit_low=None))
    assert m is None  # 시세 정보 없음 → 축을 그리지 않음(폴백 문구)


def test_asks_included():
    m = pricemap.build(_sl(), ask_points=[_Ask(420_000_000)])
    assert len(m["asks"]) == 1 and 0 <= m["asks"][0]["pct"] <= 100


def test_label_clamped_into_view():
    # 극단값(축 맨 끝)이어도 라벨 anchor 는 10~90 으로 클램프
    m = pricemap.build(_sl())
    for key in ("appraisal", "minbid", "cost"):
        assert 10 <= m[key]["lab"] <= 90
