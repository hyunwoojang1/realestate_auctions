# -*- coding: utf-8 -*-
"""T7/T8 테스트 — 확정 단지 실거래 추정(창 계층화)·폴백 밴드폭 가드·KB 괴리 플래그·파이프라인 주입."""
import dataclasses

from src import pipeline
from src.matcher import (
    FALLBACK_BAND_SPREAD_MAX,
    SCOPE_APPRAISAL_MISMATCH,
    SCOPE_BAND_TOO_WIDE,
    SCOPE_SAME_COMPLEX_SAME_AREA,
    _int_to_ym,
    _ym_to_int,
    estimate_from_complex_trades,
    estimate_market,
)
from src.models import AuctionListing, Trade
from src.score import market_view, score_listing

LST = AuctionListing(
    case_no="2025타경32139", court="대구서부지원", address="대구 달서구 진천동",
    lawd_cd="27290", dong="진천동", apt_name="진천태왕아너스1단지",
    property_type="아파트", area_m2=105.22, appraisal_price=353_000_000,
    min_bid_price=247_100_000, fail_count=1, sale_date="2026-07-21",
    rights_verified=True, item_no="1")


def _row(ymd, price, floor=5):
    return {"trade_ymd": ymd, "price": price, "floor": floor, "exclusive_area": 105.22}


# ---- 창 계층화 ----

def test_recent_window_sufficient_no_mult():
    """12개월 창에 표본 충분 → mult 1.0, scope 직부여."""
    rows = [_row("20260304", 280_000_000), _row("20251027", 250_000_000),
            _row("20250617", 268_500_000), _row("20250326", 294_500_000),
            _row("20250310", 280_000_000), _row("20250213", 295_000_000)]
    m, mult = estimate_from_complex_trades(LST, rows)
    assert mult == 1.0
    assert m.scope == SCOPE_SAME_COMPLEX_SAME_AREA
    assert m.est is not None and 260_000_000 < m.est < 300_000_000
    assert m.band_low is not None and m.band_low <= m.est
    assert len(m.comps) == 6           # 차트용 전체 보존


def test_window_ladder_expands_with_mult():
    """12개월엔 1건뿐 → 24/60개월로 확장되며 mult<1.0."""
    rows = [_row("20260304", 280_000_000),          # 최근 12개월 내 1건
            _row("20240501", 270_000_000), _row("20240301", 275_000_000),
            _row("20231101", 265_000_000), _row("20230601", 260_000_000)]
    m, mult = estimate_from_complex_trades(LST, rows)
    assert m.est is not None
    assert mult < 1.0                   # 확장 창 → 신뢰 하향
    assert m.scope == SCOPE_SAME_COMPLEX_SAME_AREA


def test_insufficient_even_at_60m():
    """60개월로도 게이트 미달 → est None(시세추정불가), 단 scope는 확정 단지로 기록."""
    rows = [_row("20260304", 280_000_000), _row("20250101", 270_000_000)]
    m, mult = estimate_from_complex_trades(LST, rows)
    assert m.est is None
    assert mult == 1.0


def test_appraisal_guard_on_complex_trades():
    """확정 comps라도 감정가 2.5배 초과면 무효화. (표본 5건 → 트림 후 3건 = 게이트 통과 후 가드 도달)"""
    rows = [_row("20260304", 1_000_000_000), _row("20251001", 1_010_000_000),
            _row("20250801", 990_000_000), _row("20250601", 1_005_000_000),
            _row("20250401", 1_002_000_000)]
    m, mult = estimate_from_complex_trades(LST, rows)
    assert m.est is None
    assert m.scope == SCOPE_APPRAISAL_MISMATCH


def test_appraisal_lower_guard_on_complex_trades():
    """(밤샘검수 2026-07-21) 확정 comps라도 est가 감정가의 0.35배 미만이면 오매칭 신호로 무효화.
    네이버가 상가·지하유닛을 소형 주거유닛에 오매칭해 est가 감정가의 0.1배로 붕괴한 실사고 방어."""
    # 감정가 3.53억인데 실거래가 0.4억대(다른 소형 유닛 오매칭) → 비율 ~0.11 → 무효화
    rows = [_row("20260304", 40_000_000), _row("20251001", 41_000_000),
            _row("20250801", 39_000_000), _row("20250601", 40_500_000),
            _row("20250401", 40_000_000)]
    m, mult = estimate_from_complex_trades(LST, rows)
    assert m.est is None
    assert m.scope == SCOPE_APPRAISAL_MISMATCH


def test_appraisal_lower_guard_allows_legit_discount():
    """하한 0.35 위의 정상 저가(감정가 0.5~0.8배)는 무효화하지 않는다 — 과도 무효화 방지."""
    # 감정가 3.53억, 실거래 2.0억대(비율 ~0.6) → 정상 통과
    rows = [_row("20260304", 210_000_000), _row("20251001", 205_000_000),
            _row("20250801", 208_000_000), _row("20250601", 212_000_000),
            _row("20250401", 207_000_000)]
    m, mult = estimate_from_complex_trades(LST, rows)
    assert m.est is not None
    assert m.scope == SCOPE_SAME_COMPLEX_SAME_AREA


def test_unsupported_type_skipped():
    villa = dataclasses.replace(LST, property_type="다세대")
    m, _ = estimate_from_complex_trades(villa, [_row("20260304", 280_000_000)])
    assert m.est is None


def test_share_sale_skipped():
    share = dataclasses.replace(LST, special_rights=["지분"])
    m, _ = estimate_from_complex_trades(share, [_row("20260304", 280_000_000)] * 5)
    assert m.est is None


def test_int_to_ym_roundtrip():
    for ym in ("202603", "202512", "202401", "201012"):
        assert _int_to_ym(_ym_to_int(ym)) == ym


# ---- T8: 폴백 밴드폭 가드 ----

def _dong_trade(name, price, area=105.0, ym="202601"):
    return Trade(apt_name=name, area_m2=area, price=price, deal_ym=ym,
                 dong="진천동", kind="apt", lawd_cd="27290")


def test_fallback_band_too_wide_invalidated():
    """같은 동 폴백에서 밴드폭 과대(>1.6배) → 시세 무효화(진천 실사고 1.748배 재현).

    감정가 가드(폴백 상한 1.5×감정가=5.3억)에 걸리지 않도록 중앙값은 4.45억으로 두고
    band_low(2.5억)와의 스프레드만 1.78배로 벌린다 — 밴드 가드가 잡아야 하는 정확한 사각.
    """
    pool = [_dong_trade("다른단지A", 240_000_000), _dong_trade("다른단지B", 250_000_000),
            _dong_trade("다른단지C", 440_000_000), _dong_trade("다른단지D", 450_000_000),
            _dong_trade("다른단지E", 460_000_000), _dong_trade("다른단지F", 470_000_000)]
    m = estimate_market(LST, pool)
    assert m.scope == SCOPE_BAND_TOO_WIDE
    assert m.est is None


def test_fallback_normal_band_passes():
    """정상 폭(≤1.75배)의 폴백은 종전대로 추정 허용."""
    pool = [_dong_trade("다른단지A", 270_000_000), _dong_trade("다른단지B", 280_000_000),
            _dong_trade("다른단지C", 285_000_000), _dong_trade("다른단지D", 290_000_000),
            _dong_trade("다른단지E", 295_000_000)]
    m = estimate_market(LST, pool)
    assert m.est is not None
    assert m.band_low is not None
    assert m.est / m.band_low <= FALLBACK_BAND_SPREAD_MAX


# ---- T8b: KB 괴리 플래그 ----

def _scored(est):
    return score_listing(LST, est, 5, market_scope=SCOPE_SAME_COMPLEX_SAME_AREA,
                         band_low=est, band_high=est, band_basis=5)


def test_kb_divergence_over():
    s = _scored(469_000_000)   # 진천 실사고 값
    naver = {"status": "matched_kb", "kb_low": 345_000_000, "kb_avg": 360_000_000,
             "kb_high": 370_000_000}
    out = market_view(s, naver)
    assert out.market_source == "molit"
    assert out.naver.get("kb_divergence") == "over"


def test_kb_divergence_within_band_no_flag():
    s = _scored(360_000_000)
    naver = {"status": "matched_kb", "kb_low": 345_000_000, "kb_avg": 360_000_000,
             "kb_high": 370_000_000}
    out = market_view(s, naver)
    assert "kb_divergence" not in out.naver


# ---- 파이프라인 주입 ----

def test_pipeline_injects_real_trades():
    rows = [_row("20260304", 280_000_000), _row("20251027", 250_000_000),
            _row("20250617", 268_500_000), _row("20250326", 294_500_000),
            _row("20250310", 280_000_000), _row("20250213", 295_000_000)]
    lookup = lambda lst: rows if lst.case_no == LST.case_no else None  # noqa: E731
    scored = pipeline.run(auctions=[LST], trades=[], real_trades_lookup=lookup)
    s = scored[0]
    assert s.market_scope == SCOPE_SAME_COMPLEX_SAME_AREA
    assert s.est_market_price is not None
    assert 260_000_000 < s.est_market_price < 300_000_000


def test_pipeline_falls_back_without_lookup():
    """lookup 없거나 빈 결과 → 종전 이름매칭 경로 그대로(무회귀)."""
    scored = pipeline.run(auctions=[LST], trades=[], real_trades_lookup=lambda lst: None)
    assert scored[0].est_market_price is None   # 빈 trade_pool → 추정불가


def test_pipeline_window_mult_downgrades():
    """확장 창 주입 시 arb·confidence 하향 실적용."""
    rows = [_row("20260304", 280_000_000),
            _row("20240501", 270_000_000), _row("20240301", 275_000_000),
            _row("20231101", 265_000_000), _row("20230601", 260_000_000)]
    scored = pipeline.run(auctions=[LST], trades=[], real_trades_lookup=lambda lst: rows)
    s = scored[0]
    assert s.est_market_price is not None
    assert s.confidence < 1.0     # 창 확장 신뢰 하향 적용됨
