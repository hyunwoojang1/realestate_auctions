"""신뢰계수 표본 개선 검증 (GOAL_DEPLOY C).

핵심 주장 3가지를 오프라인 fixture로만 검증한다(외부호출 0):
  1) 신뢰계수는 표본수에 대해 '단조 비감소'다 (1건→낮음, 다수→높음).
  2) 라이브 매칭 표본이 빈약했던 원인 = 좁은 면적밴드 + 짧은 수집 개월수.
     → config.SAMPLE(area_band / live_months)를 넓히면 표본수가 실제로 늘어난다.
  3) 표본수 증가가 신뢰계수 증가로 이어져(estimate_market_price → confidence) 스코어에 반영된다.

이 파일이 만드는 표본수별 신뢰계수 표는 evidence/confidence_samples.txt 로 덤프된다.
"""
from __future__ import annotations

import pytest

from src import config, matcher, pipeline
from src.matcher import estimate_market_price, match_trades
from src.models import AuctionListing, Trade
from src.molit_client import recent_ymds
from src.score import confidence_from_matches, score_listing


def _lst(area_m2: float = 84.9, **kw) -> AuctionListing:
    d = dict(case_no="T", court="c", address="서울 노원구 상계동", lawd_cd="11350",
             dong="상계동", apt_name="상계주공", property_type="아파트", area_m2=area_m2,
             appraisal_price=620_000_000, min_bid_price=397_000_000, fail_count=2,
             sale_date="2026-07-01")
    d.update(kw)
    return AuctionListing(**d)


def _trade(area_m2: float, ym: str, price: int = 630_000_000) -> Trade:
    return Trade(apt_name="상계주공", area_m2=area_m2, price=price, deal_ym=ym,
                 dong="상계동", kind="apt")


# ── 다월 fixture: 4개월치(202606..202603) 실거래 ─────────────────────────────
# 각 월에 정확히 매칭되는(84.9㎡) 거래 1건씩 → 수집 개월수를 늘리면 표본수가 는다.
MULTI_MONTH_TRADES = [
    _trade(84.9, "202606", 640_000_000),
    _trade(84.9, "202605", 635_000_000),
    _trade(84.9, "202604", 628_000_000),
    _trade(84.9, "202603", 622_000_000),
]

# 인접 평형(±10% 밖, ±15% 안): 밴드를 넓혀야만 잡히는 comps.
NEAR_BAND_TRADES = [
    _trade(74.0, "202606", 560_000_000),   # 84.9 대비 -12.8% → ±10% 밖, ±15% 안
    _trade(96.0, "202605", 720_000_000),   # +13.1% → ±10% 밖, ±15% 안
]


@pytest.fixture(autouse=True)
def _reset_sample():
    """각 테스트 후 config.SAMPLE 기본값 복원(전역 오염 방지)."""
    saved = config.SAMPLE
    yield
    config.SAMPLE = saved


# ── 1) 신뢰계수는 표본수에 단조 비감소 ────────────────────────────────────────

def test_confidence_is_monotonic_nondecreasing_in_samples():
    """n=0..8까지 신뢰계수가 절대 감소하지 않는다(단조 비감소)."""
    prev = -1.0
    for n in range(0, 9):
        c = confidence_from_matches(n)
        assert c >= prev, f"n={n}에서 신뢰계수 감소({c} < {prev})"
        prev = c


def test_confidence_strictly_increases_across_ladder_rungs():
    """1건은 낮고 다수(≥3)는 최댓값 — 표본이 많을수록 신뢰가 높다."""
    c1 = confidence_from_matches(1)
    c2 = confidence_from_matches(2)
    c3 = confidence_from_matches(3)
    assert c1 < c2 < c3, f"사다리 단조 증가 위반: {c1}, {c2}, {c3}"
    assert confidence_from_matches(0) < c1


def test_more_matches_never_lowers_final_score_confidence():
    """estimate → score 경로에서도 표본이 많을수록 confidence가 감소하지 않는다."""
    prev = -1.0
    for k in range(1, len(MULTI_MONTH_TRADES) + 1):
        est, n = estimate_market_price(_lst(), MULTI_MONTH_TRADES[:k])
        s = score_listing(_lst(), est, n)
        assert s.confidence >= prev, f"표본 {k}건에서 confidence 감소"
        prev = s.confidence


# ── 2) 빈약 원인 = 좁은 밴드 + 짧은 개월수 (넓히면 표본↑) ─────────────────────

def test_wider_area_band_increases_sample_count():
    """면적밴드 ±10%→±15%로 넓히면 인접 평형이 comps에 포함돼 표본이 는다."""
    pool = MULTI_MONTH_TRADES + NEAR_BAND_TRADES

    config.SAMPLE = config.SampleConfig(area_band=0.10)
    narrow = len(match_trades(_lst(), pool))

    config.SAMPLE = config.SampleConfig(area_band=0.15)
    wide = len(match_trades(_lst(), pool))

    assert wide > narrow, f"밴드 확대가 표본을 늘리지 못함(narrow={narrow}, wide={wide})"
    assert narrow == len(MULTI_MONTH_TRADES)   # 좁은 밴드는 정확 평형만
    assert wide == len(pool)                    # 넓은 밴드는 인접 평형까지


def test_more_months_collect_more_ymds():
    """live_months를 늘리면 수집 대상 연월(recent_ymds) 수가 그만큼 는다(수집 폭↑)."""
    config.SAMPLE = config.SampleConfig(live_months=3)
    assert len(recent_ymds("202606", pipeline._live_months())) == 3
    config.SAMPLE = config.SampleConfig(live_months=6)
    assert len(recent_ymds("202606", pipeline._live_months())) == 6


def test_area_band_config_wires_into_matcher():
    """config.SAMPLE.area_band가 matcher._area_band()에 실제로 반영된다."""
    config.SAMPLE = config.SampleConfig(area_band=0.22)
    assert matcher._area_band() == pytest.approx(0.22)


def test_sample_config_env_override():
    """환경변수 AUCTION_LIVE_MONTHS / AUCTION_AREA_BAND가 적용된다."""
    cfg = config.load_sample_config(
        path="__nonexistent__",
        env={"AUCTION_LIVE_MONTHS": "5", "AUCTION_AREA_BAND": "0.18"},
    )
    assert cfg.live_months == 5
    assert cfg.area_band == pytest.approx(0.18)


def test_sample_config_defaults_match_legacy():
    """오버라이드 없으면 기존 동작과 동일한 기본값(무회귀)."""
    cfg = config.load_sample_config(path="__nonexistent__", env={})
    assert cfg.live_months == pipeline.LIVE_MONTHS == 3
    assert cfg.area_band == pytest.approx(matcher.AREA_BAND) == pytest.approx(0.10)


def test_sample_config_guards_bad_values():
    """0/음수 개월·비양수 밴드는 안전한 하한으로 보정."""
    cfg = config.SampleConfig(live_months=0, area_band=0.0)
    assert cfg.live_months == 1
    assert cfg.area_band == pytest.approx(0.10)


# ── 3) 표본수별 신뢰계수 표 (증거 파일용) ─────────────────────────────────────

SAMPLE_COUNTS = [0, 1, 2, 3, 4, 6, 8]


def build_confidence_table() -> list[tuple[int, float]]:
    """표본수 → 신뢰계수 표(단조 비감소). evidence 덤프와 테스트가 공유."""
    return [(n, confidence_from_matches(n)) for n in SAMPLE_COUNTS]


def test_confidence_table_is_nondecreasing():
    table = build_confidence_table()
    coefs = [c for _, c in table]
    assert coefs == sorted(coefs), f"표본수별 신뢰계수 비단조: {table}"
    assert coefs[0] < coefs[-1]   # 최소표본 < 최대표본
