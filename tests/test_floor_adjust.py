"""층 보정(floor_adjust) — 주소 층 파싱·배율 산출·추정기/서빙 폴백 통합 테스트.

근거 실측(2026-07-24, naver_real_trades 2021~): 같은 단지·평형 내 저층(1~2층) 중앙값
1층 −7.6% · 2층 −5.5%. 층 무시 중앙값은 저층 물건 차익을 체계적으로 과대평가한다.
"""

from __future__ import annotations

import pytest

from src import floor_adjust
from src.floor_adjust import floor_multiplier, subject_floor
from src.matcher import estimate_from_complex_trades, estimate_market
from src.models import AuctionListing, Trade


# ---------------------------------------------------------------- 주소 층 파싱
@pytest.mark.parametrize("address,expected", [
    ("서울특별시 영등포구 경인로114길 62 제5층 제510호", 5),
    ("부산광역시 동래구 수안동 32-2 5층503호", 5),
    ("부산광역시 남구 양지골로 152 3동 4층504호 (감만동,유창그린)", 4),
    ("대구광역시 동구 신서동 828 신서화성파크드림 106동 18층1805호", 18),
    ("서울특별시 중구 인현동2가 190-1", None),          # 층 표기 없음
    ("경기도 평택시 지산동 850-60 더 클래스1 601호", None),  # 호수만 — 층 추정 안 함
    ("인천광역시 부평구 부평동 XX 지하1층 비101호", 0),      # 지하 — '1층' 오인 금지
    ("서울 OO구 OO동 상가 제1층 제2층", 1),               # 복층 — 최저층(보수)
])
def test_subject_floor(address, expected):
    assert subject_floor(address) == expected


# ---------------------------------------------------------------- 배율 산출
def test_multiplier_none_for_upper_or_unknown():
    comps = [(1, 90.0), (2, 92.0), (5, 100.0), (7, 101.0)]
    assert floor_multiplier(None, comps) == (1.0, "none")
    assert floor_multiplier(10, comps) == (1.0, "none")
    assert floor_multiplier(3, comps) == (1.0, "none")   # 3층부터는 비저층


def test_multiplier_complex_ratio_when_enough_samples():
    # 저층 중앙값 90, 상층 중앙값 100 → 0.9 (양측 3건 이상)
    comps = [(1, 88.0), (1, 90.0), (2, 92.0), (5, 98.0), (7, 100.0), (10, 102.0)]
    mult, basis = floor_multiplier(1, comps)
    assert basis == "complex"
    assert mult == pytest.approx(90.0 / 100.0)


def test_multiplier_never_adjusts_upward():
    # 저층이 더 비싼 단지(실측 18%) — 상향 보정 금지, 1.0 클램프
    comps = [(1, 110.0), (1, 112.0), (2, 111.0), (5, 100.0), (7, 99.0), (9, 100.0)]
    mult, basis = floor_multiplier(2, comps)
    assert basis == "complex"
    assert mult == 1.0


def test_multiplier_clamped_at_floor():
    # 소표본 극단 비율(저층 절반가) — 0.65 하한 클램프
    comps = [(1, 50.0), (1, 50.0), (2, 50.0), (5, 100.0), (7, 100.0), (9, 100.0)]
    mult, _ = floor_multiplier(1, comps)
    assert mult == floor_adjust.MULT_MIN


def test_multiplier_default_when_insufficient():
    # 저층 표본 부족 → 전국 실측 기본계수 (층 미상 floor=0 행은 표본에서 제외)
    comps = [(0, 95.0), (5, 100.0), (7, 100.0), (9, 100.0)]
    assert floor_multiplier(1, comps) == (floor_adjust.DEFAULT_MULT[1], "default")
    assert floor_multiplier(2, comps) == (floor_adjust.DEFAULT_MULT[2], "default")
    assert floor_multiplier(0, comps) == (floor_adjust.DEFAULT_MULT[0], "default")  # 지하


# ---------------------------------------------------------------- 추정기 통합
def _listing(address: str) -> AuctionListing:
    return AuctionListing(
        case_no="2026타경1", court="테스트지법", address=address,
        lawd_cd="11110", dong="테스트동", apt_name="플로어캐슬",
        property_type="아파트", area_m2=84.0, appraisal_price=500_000_000,
        min_bid_price=350_000_000, fail_count=1, sale_date="2026-09-01",
        rights_verified=True,
    )


def _trades(floors_prices: list[tuple[int, int]]) -> list[Trade]:
    return [Trade(apt_name="플로어캐슬", area_m2=84.0, price=p, deal_ym="202606",
                  dong="테스트동", floor=f, kind="apt", lawd_cd="11110")
            for f, p in floors_prices]


def test_estimate_market_adjusts_low_floor_subject():
    fp = [(1, 450_000_000), (2, 452_000_000), (1, 448_000_000),
          (5, 500_000_000), (8, 500_000_000), (11, 502_000_000)]
    low = estimate_market(_listing("테스트동 101동 1층101호"), _trades(fp))
    up = estimate_market(_listing("테스트동 101동 10층1001호"), _trades(fp))
    assert up.floor_mult == 1.0
    assert low.floor_mult < 1.0
    assert low.est == pytest.approx(up.est * low.floor_mult, abs=2)
    assert low.band_low == pytest.approx(up.band_low * low.floor_mult, abs=2)


def test_estimate_market_no_adjust_when_floor_unknown():
    fp = [(1, 450_000_000), (5, 500_000_000), (8, 500_000_000), (11, 502_000_000)]
    m = estimate_market(_listing("테스트동 101동 101호"), _trades(fp))  # 층 표기 없음
    assert m.floor_mult == 1.0


def test_estimate_from_complex_trades_adjusts_low_floor():
    rows = [
        {"trade_ymd": "20260601", "price": 450_000_000, "floor": 1},
        {"trade_ymd": "20260510", "price": 452_000_000, "floor": 2},
        {"trade_ymd": "20260420", "price": 448_000_000, "floor": 1},
        {"trade_ymd": "20260610", "price": 500_000_000, "floor": 6},
        {"trade_ymd": "20260520", "price": 500_000_000, "floor": 9},
        {"trade_ymd": "20260410", "price": 502_000_000, "floor": 12},
    ]
    low, _ = estimate_from_complex_trades(_listing("테스트동 101동 2층201호"), rows)
    up, _ = estimate_from_complex_trades(_listing("테스트동 101동 9층901호"), rows)
    assert up.floor_mult == 1.0
    assert low.floor_mult == pytest.approx(450_000_000 / 500_000_000)
    assert low.est == pytest.approx(round(up.est * low.floor_mult), abs=2)


def test_estimate_from_complex_trades_rows_without_floor_key():
    # 하위호환: floor 키 없는 dict 행 — 미상 취급(크래시 없이 기본계수 경로)
    rows = [{"trade_ymd": f"2026{m:02d}01", "price": 500_000_000} for m in range(1, 7)]
    m, _ = estimate_from_complex_trades(_listing("테스트동 101동 1층101호"), rows)
    assert m.est is not None
    assert m.floor_mult == floor_adjust.DEFAULT_MULT[1]


# ---------------------------------------------------------------- 서빙 폴백(KB)
def test_kb_fallback_applies_default_multiplier():
    from src.score import market_view, score_listing
    naver = {"status": "matched_kb", "kb_avg": 500_000_000,
             "kb_low": 480_000_000, "kb_high": 520_000_000, "match_conf": "high"}
    s_low = score_listing(_listing("테스트동 101동 1층101호"), None, 0)
    s_up = score_listing(_listing("테스트동 101동 9층901호"), None, 0)
    v_low = market_view(s_low, naver)
    v_up = market_view(s_up, naver)
    assert v_up.floor_mult == 1.0
    assert v_low.floor_mult == floor_adjust.DEFAULT_MULT[1]
    assert v_low.est_market_price == int(v_up.est_market_price * floor_adjust.DEFAULT_MULT[1])
    assert v_low.market_band_low < v_up.market_band_low
