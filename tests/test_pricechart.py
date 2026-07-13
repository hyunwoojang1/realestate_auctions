"""가격-시간 차트 데이터 빌더 테스트 (TDD).

pricechart.build_timechart 는 순수함수 — 개별 실거래(comps)·기일 이력(schedule)·호가(asks)와
스코어 물건(감정가/최저가/취득원가/밴드)을 받아, 상세 페이지의 시간축 차트가 그릴
JSON-직렬화 가능한 dict 를 만든다. I/O 없음 → 완전 단위 검증.
"""
from __future__ import annotations

import json

from src.models import ScoredListing
from src.pricechart import build_timechart


def _listing(**kw) -> ScoredListing:
    base = dict(
        case_no="2024타경51234", apt_name="상계주공", address="서울 노원구 상계동",
        property_type="아파트", area_m2=84.9, appraisal_price=620_000_000,
        min_bid_price=397_000_000, fail_count=2, sale_date="2026-07-15",
        est_market_price=545_000_000, matched_trades=8, confidence=0.9,
        real_acquisition_cost=414_000_000, expected_profit=131_000_000,
        gap_rate=0.25, gap_score=80.0, rights_score=90.0, liquidity_score=70.0,
        arb_score=82.0, grade="양호",
        market_band_low=510_000_000, market_band_high=545_000_000,
        profit_low=96_000_000, profit_high=131_000_000, market_sample_basis=6,
    )
    base.update(kw)
    return ScoredListing(**base)


# 최근 창(12개월) 안 5건 + 창 밖(맥락) 3건
COMPS = [
    ("202605", 510_000_000), ("202602", 518_000_000), ("202512", 525_000_000),
    ("202509", 530_000_000), ("202506", 538_000_000),
    ("202410", 560_000_000), ("202112", 642_000_000), ("202006", 478_000_000),
]
SCHEDULE = [
    {"ymd": "2025-02-10", "kind": "매각기일", "price": 620_000_000, "result": "유찰"},
    {"ymd": "2025-04-14", "kind": "매각기일", "price": 496_000_000, "result": "유찰"},
    {"ymd": "2026-07-15", "kind": "매각기일", "price": 397_000_000, "result": "진행"},
]
ASKS = [{"price": 560_000_000, "observed_at": "2026-06-10", "label": "현재 매물", "position": "above"}]


def test_returns_json_serializable_dict():
    out = build_timechart(_listing(), COMPS, SCHEDULE, ASKS)
    # 직렬화 가능해야 템플릿에 임베드할 수 있다
    json.dumps(out)
    assert isinstance(out, dict)


def test_levels_carry_auction_prices():
    out = build_timechart(_listing(), COMPS, SCHEDULE, ASKS)
    lv = out["levels"]
    assert lv["appraisal"] == 620_000_000
    assert lv["cost"] == 414_000_000
    assert lv["minbid"] == 397_000_000


def test_splits_comps_by_recency_window():
    out = build_timechart(_listing(), COMPS, SCHEDULE, ASKS, recency_months=12)
    trade_yms = {t["ym"] for t in out["trades"]}
    hist_yms = {h["ym"] for h in out["hist"]}
    # 최근 거래월(2026-05) 기준 12개월 안 = 밴드 산정 대상(trades)
    assert "2026-05" in trade_yms
    assert "2025-06" in trade_yms
    # 창 밖 = 맥락(hist)
    assert "2024-10" in hist_yms
    assert "2021-12" in hist_yms
    assert "2020-06" in hist_yms
    # 겹치지 않는다
    assert not (trade_yms & hist_yms)


def test_trades_and_hist_prices_are_actual():
    out = build_timechart(_listing(), COMPS, SCHEDULE, ASKS)
    pts = {(p["ym"], p["price"]) for p in out["trades"] + out["hist"]}
    # 호버 정직성: 표시 가격은 실제 체결가 그대로
    assert ("2026-05", 510_000_000) in pts
    assert ("2021-12", 642_000_000) in pts


def test_band_now_from_listing():
    out = build_timechart(_listing(), COMPS, SCHEDULE, ASKS)
    assert out["band_now"] == {"lo": 510_000_000, "hi": 545_000_000}


def test_rolling_envelope_nonempty_and_ordered():
    out = build_timechart(_listing(), COMPS, SCHEDULE, ASKS)
    env = out["band_env"]
    assert len(env) >= 2
    # 시간순 정렬 + lo<=hi
    yms = [e["ym"] for e in env]
    assert yms == sorted(yms)
    for e in env:
        assert e["lo"] <= e["hi"]


def test_gain_positive_when_band_above_cost():
    out = build_timechart(_listing(), COMPS, SCHEDULE, ASKS)
    g = out["gain"]
    assert g is not None
    assert g["amount"] == 510_000_000 - 414_000_000  # band_low - cost


def test_gain_none_when_cost_at_or_above_band():
    out = build_timechart(_listing(real_acquisition_cost=520_000_000), COMPS, SCHEDULE, ASKS)
    assert out["gain"] is None


def test_cheap_pct_vs_band_mid():
    out = build_timechart(_listing(), COMPS, SCHEDULE, ASKS)
    # 저가율 = 1 - 최저가/밴드중앙. 중앙=(510+545)/2=527.5M → 약 25%
    assert out["cheap_pct"] is not None
    assert 23 <= out["cheap_pct"] <= 27


def test_step_descends_to_current_minbid():
    out = build_timechart(_listing(), COMPS, SCHEDULE, ASKS)
    step = out["step"]
    assert len(step) >= 2
    prices = [s["price"] for s in step]
    assert prices == sorted(prices, reverse=True)  # 단조 하강
    assert step[-1]["price"] == 397_000_000        # 현재 유찰가로 끝
    assert step[-1]["label"] == "현재"


def test_asks_carry_date_and_price():
    out = build_timechart(_listing(), COMPS, SCHEDULE, ASKS)
    assert out["asks"][0]["price"] == 560_000_000
    assert out["asks"][0]["date"] == "2026-06-10"


def test_empty_comps_still_valid():
    out = build_timechart(_listing(), [], SCHEDULE, ASKS)
    assert out["trades"] == []
    assert out["hist"] == []
    assert out["band_env"] == []
    assert out["levels"]["minbid"] == 397_000_000


def test_no_schedule_falls_back_to_current_only():
    out = build_timechart(_listing(), COMPS, None, ASKS)
    # 기일 이력 없으면 계단은 감정가·현재 최저가 2점(중간 유찰일 미상)
    step = out["step"]
    assert step[-1]["price"] == 397_000_000
    assert step[0]["price"] == 620_000_000


def test_timechart_gain_reflects_assumed():
    """서빙감사 #19(재발): 인수금액이 밴드-원가 차를 넘으면 차트 차익 구간 없음(히어로 음수와 정합)."""
    from src.pricechart import build_timechart
    from src.models import ScoredListing
    from src.store import _COLS
    base = {c: None for c in _COLS}
    base.update(dict(case_no="T", apt_name="테스트", address="서울", property_type="아파트",
        area_m2=84.0, appraisal_price=330_000_000, min_bid_price=27_000_000, fail_count=7,
        sale_date="2026-08-01", est_market_price=280_000_000, matched_trades=12, confidence=1.0,
        real_acquisition_cost=27_500_000, expected_profit=252_000_000, gap_rate=0.9,
        gap_score=50.0, rights_score=30.0, liquidity_score=20.0, arb_score=88.0, grade="관심",
        court="청주지방법원", item_no="1", doc_id="", market_scope="same_complex_same_area",
        market_band_low=265_000_000, market_band_high=280_000_000,
        profit_low=237_500_000, profit_high=252_500_000, market_sample_basis=10))
    s = ScoredListing(**{c: base[c] for c in _COLS})
    # 인수금 0 → 차익 구간 있음
    assert build_timechart(s, [], assumed=0)["gain"] is not None
    # 인수금 3.3억(밴드하한 2.65억 − 원가 0.275억 = 2.375억 초과) → 차익 없음
    ch = build_timechart(s, [], assumed=330_000_000)
    assert ch["gain"] is None and ch["assumed_neg"] is True
