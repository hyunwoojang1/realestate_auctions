"""입찰가 시뮬레이터 테스트 — docs/tax-auction-knowledge.md §5 수치를 코드에 고정한다.

이 테스트가 깨지면 (a) 세법·요율 개정 반영 중이거나 (b) 실수로 산식이 변형된 것.
어느 쪽이든 지식문서 §5와 src/bidsim.py를 함께 갱신해야 한다.

핵심 계약:
  · 취득세는 **입력 입찰가 기준으로 재계산**된다(목록은 최저입찰가 고정 — 여기가 다른 점).
  · 필요경비는 보수적 범위(명도비·수리비·이자·미납관리비 미공제).
  · 손익분기 입찰가는 세후 순익 = 0 을 만족한다.
"""
import pytest

from src import bidsim
from src.tax import BuyerProfile

P1 = BuyerProfile()   # 1주택·비조정·개인(기본)


def _inp(**kw):
    """3억 아파트(84㎡) 기본 시나리오 — 개별 테스트가 필요한 필드만 덮어쓴다."""
    base = dict(
        bid_price=300_000_000, property_type="아파트", area_m2=84.0,
        sell_price=400_000_000, holding_months=24, profile=P1,
    )
    base.update(kw)
    return bidsim.SimInput(**base)


# ────────────────────────── 인지세 (§5-2) ──────────────────────────

@pytest.mark.parametrize(("price", "expected"), [
    (10_000_000, 0),           # 1천만 이하 비과세
    (10_000_001, 20_000),
    (30_000_000, 20_000),
    (30_000_001, 40_000),
    (50_000_000, 40_000),
    (50_000_001, 70_000),
    (100_000_000, 70_000),
    (100_000_001, 150_000),
    (1_000_000_000, 150_000),
    (1_000_000_001, 350_000),
])
def test_stamp_tax_brackets(price, expected):
    assert bidsim.stamp_tax(price) == expected


# ────────────────────────── 매도 중개보수 (§5-4) ──────────────────────────

def test_agent_fee_housing_brackets():
    # 2억~9억 = 0.4%, 한도 없음
    assert bidsim.agent_fee(400_000_000, "아파트", 84.0) == 1_600_000
    # 9억~12억 = 0.5%
    assert bidsim.agent_fee(1_000_000_000, "아파트", 84.0) == 5_000_000
    # 15억 이상 = 0.7%
    assert bidsim.agent_fee(2_000_000_000, "아파트", 84.0) == 14_000_000


def test_agent_fee_housing_caps():
    # 5천만 미만 = 0.6%, 한도 25만
    assert bidsim.agent_fee(30_000_000, "아파트", 60.0) == 180_000
    assert bidsim.agent_fee(45_000_000, "아파트", 60.0) == 250_000      # 27만 → 한도 25만
    # 5천만~2억 = 0.5%, 한도 80만
    assert bidsim.agent_fee(100_000_000, "아파트", 60.0) == 500_000
    assert bidsim.agent_fee(190_000_000, "아파트", 60.0) == 800_000     # 95만 → 한도 80만


def test_agent_fee_officetel_and_nonhousing():
    # 주거용 오피스텔(85㎡ 이하) = 0.5%
    assert bidsim.agent_fee(400_000_000, "오피스텔", 45.0) == 2_000_000
    # 85㎡ 초과 오피스텔·상가·토지 = 0.9%
    assert bidsim.agent_fee(400_000_000, "오피스텔", 120.0) == 3_600_000
    assert bidsim.agent_fee(400_000_000, "상가", 80.0) == 3_600_000
    assert bidsim.agent_fee(400_000_000, "토지", 0.0) == 3_600_000


# ────────────────────────── 양도소득세 (§5-5) ──────────────────────────

def test_transfer_tax_zero_when_no_gain():
    """양도차익이 0 이하면 양도세는 0 — 손해 보고 파는데 세금이 붙지 않는다."""
    t = bidsim.transfer_tax(sell_price=300_000_000, acquire_price=350_000_000,
                            expenses=5_000_000, holding_months=24, property_type="아파트")
    assert t["양도차익"] < 0
    assert t["합계"] == 0


def test_transfer_tax_short_term_housing_70pct():
    """주택 1년 미만 = 70% 단일세율(+지방소득세 10%). 장특공·누진공제 없음."""
    t = bidsim.transfer_tax(sell_price=400_000_000, acquire_price=300_000_000,
                            expenses=0, holding_months=11, property_type="아파트")
    base = 100_000_000 - bidsim.BASIC_DEDUCTION      # 기본공제 250만
    assert t["장기보유특별공제"] == 0
    assert t["과세표준"] == base
    assert t["산출세액"] == round(base * 0.70)
    assert t["합계"] == round(round(base * 0.70) * 1.1)


def test_transfer_tax_short_term_nonhousing_50pct():
    """토지·상가 1년 미만 = 50%, 1~2년 = 40%."""
    a = bidsim.transfer_tax(sell_price=400_000_000, acquire_price=300_000_000,
                            expenses=0, holding_months=11, property_type="토지")
    b = bidsim.transfer_tax(sell_price=400_000_000, acquire_price=300_000_000,
                            expenses=0, holding_months=18, property_type="토지")
    base = 100_000_000 - bidsim.BASIC_DEDUCTION
    assert a["산출세액"] == round(base * 0.50)
    assert b["산출세액"] == round(base * 0.40)


def test_transfer_tax_officetel_treated_as_housing_conservatively():
    """오피스텔은 취득세만 비주택(4.6%) — 양도세 단기세율은 주택(70%)으로 보수 처리(§5-5 주석)."""
    t = bidsim.transfer_tax(sell_price=400_000_000, acquire_price=300_000_000,
                            expenses=0, holding_months=11, property_type="오피스텔")
    base = 100_000_000 - bidsim.BASIC_DEDUCTION
    assert t["산출세액"] == round(base * 0.70)


def test_transfer_tax_basic_progressive_rates():
    """2년 이상 = 기본세율 누진표. 과세표준 9,195만 → 35% − 누진공제 1,544만."""
    t = bidsim.transfer_tax(sell_price=400_000_000, acquire_price=300_000_000,
                            expenses=5_550_000, holding_months=24, property_type="아파트")
    assert t["양도차익"] == 94_450_000
    assert t["장기보유특별공제"] == 0            # 3년 미만
    assert t["과세표준"] == 91_950_000
    assert t["산출세액"] == 16_742_500          # 91,950,000×35% − 15,440,000
    assert t["지방소득세"] == 1_674_250
    assert t["합계"] == 18_416_750


def test_transfer_tax_low_bracket_with_long_term_deduction():
    """3년 보유 → 장특공 6% 적용 후 15% 구간(누진공제 126만)."""
    t = bidsim.transfer_tax(sell_price=320_000_000, acquire_price=300_000_000,
                            expenses=0, holding_months=36, property_type="아파트")
    # 양도차익 2,000만 − 장특공 6% = 18,800,000 → 과표 16,300,000 → 15% 구간
    assert t["장기보유특별공제"] == 1_200_000
    assert t["과세표준"] == 16_300_000
    assert t["산출세액"] == 1_185_000       # 16,300,000×15% − 1,260,000


def test_transfer_tax_lowest_bracket_is_flat_6pct():
    """과세표준 1,400만 이하는 6% 단일(누진공제 0)."""
    t = bidsim.transfer_tax(sell_price=310_000_000, acquire_price=300_000_000,
                            expenses=0, holding_months=24, property_type="아파트")
    assert t["과세표준"] == 7_500_000       # 1,000만 − 기본공제 250만
    assert t["산출세액"] == 450_000         # 7,500,000×6%


def test_long_term_deduction_2pct_per_year_capped_30():
    """장특공 = 보유 3년 이상부터 연 2%, 최대 30%(15년). 3년 미만은 0."""
    assert bidsim.long_term_deduction_rate(35) == 0.0        # 2년 11개월
    assert bidsim.long_term_deduction_rate(36) == pytest.approx(0.06)
    assert bidsim.long_term_deduction_rate(120) == pytest.approx(0.20)   # 10년
    assert bidsim.long_term_deduction_rate(180) == pytest.approx(0.30)   # 15년 = 상한
    assert bidsim.long_term_deduction_rate(600) == pytest.approx(0.30)   # 상한 유지


def test_long_term_deduction_not_applied_to_short_term():
    """단기세율 구간(2년 미만)에는 장특공을 적용하지 않는다."""
    t = bidsim.transfer_tax(sell_price=400_000_000, acquire_price=300_000_000,
                            expenses=0, holding_months=18, property_type="아파트")
    assert t["장기보유특별공제"] == 0


# ────────────────────────── 전체 시뮬레이션 (§5-6) ──────────────────────────

def test_simulate_golden_scenario():
    """손계산 대조 — 3억 낙찰·4억 매도·2년 보유·LTV 70%·연 5%.

    취득세 3,300,000(1.1%) / 인지세 150,000 / 법무·채권 500,000 / 명도 3,000,000
    → 취득 총비용 306,950,000, 대출 210,000,000, 실투자금 96,950,000
    이자 21,000,000 · 중개보수 1,600,000 · 양도세 18,416,750
    → 세후 순익 52,033,250
    """
    r = bidsim.simulate(_inp())
    assert r.acquisition_tax == 3_300_000
    assert r.stamp_tax == 150_000
    assert r.total_acquisition == 306_950_000
    assert r.loan_amount == 210_000_000
    assert r.equity == 96_950_000
    assert r.interest_total == 21_000_000
    assert r.agent_fee == 1_600_000
    assert r.transfer_tax == 18_416_750
    assert r.net_profit == 52_033_250
    assert r.roi == pytest.approx(52_033_250 / 96_950_000)
    assert r.roi_annual == pytest.approx(r.roi * 12 / 24)


def test_acquisition_tax_follows_bid_price_not_min_bid():
    """시뮬레이터의 취득세는 **입력 입찰가**로 재계산된다(목록의 최저입찰가 고정과 다른 지점)."""
    low = bidsim.simulate(_inp(bid_price=500_000_000))
    high = bidsim.simulate(_inp(bid_price=700_000_000))
    assert low.acquisition_tax == round(500_000_000 * 0.011)
    # 6~9억 구간 법정 산식 → 세율 자체가 올라간다(단순 비례 아님)
    assert high.acquisition_tax > round(700_000_000 * 0.011)


def test_assumed_amount_and_costs_reduce_net_profit():
    """인수금·명도비·수리비·미납관리비는 전부 순익에서 차감된다."""
    plain = bidsim.simulate(_inp(eviction_cost=0))
    burdened = bidsim.simulate(_inp(eviction_cost=0, assumed_amount=50_000_000,
                                    repair_cost=10_000_000, unpaid_fees=2_000_000))
    assert burdened.net_profit < plain.net_profit
    # 인수금·수리비·미납관리비는 필요경비 미인정 → 양도세는 그대로(보수적)
    assert burdened.transfer_tax == plain.transfer_tax
    assert plain.net_profit - burdened.net_profit == 62_000_000


def test_no_loan_means_equity_equals_total_acquisition():
    r = bidsim.simulate(_inp(loan_ltv=0.0))
    assert r.loan_amount == 0
    assert r.interest_total == 0
    assert r.equity == r.total_acquisition


def test_loan_ltv_is_capped():
    """LTV 상한(80%) — 입력이 넘어도 캡. 음수는 0으로."""
    assert bidsim.simulate(_inp(loan_ltv=0.95)).loan_amount == round(300_000_000 * bidsim.MAX_LTV)
    assert bidsim.simulate(_inp(loan_ltv=-0.5)).loan_amount == 0


def test_roi_is_none_when_equity_not_positive():
    """자기자본이 0 이하면 ROI는 정의 불가 — None(0 나눗셈·허수익률 방지)."""
    r = bidsim.simulate(_inp(bid_price=0, sell_price=0, loan_ltv=0.0,
                             eviction_cost=0, registry_cost=0))
    assert r.equity == 0
    assert r.roi is None
    assert r.roi_annual is None
    # 대조군: 정상 시나리오는 산출된다
    ok = bidsim.simulate(_inp())
    assert ok.equity > 0 and ok.roi is not None


def test_zero_holding_months_does_not_divide_by_zero():
    r = bidsim.simulate(_inp(holding_months=0))
    assert r.interest_total == 0
    assert r.roi_annual is None      # 기간 0 → 연환산 불가


# ────────────────────────── 손익분기 입찰가 ──────────────────────────

def test_breakeven_bid_yields_zero_net_profit():
    """손익분기 입찰가로 다시 시뮬레이션하면 세후 순익이 0 근처(±1만원)에 온다."""
    inp = _inp()
    be = bidsim.breakeven_bid(inp)
    assert be is not None
    r = bidsim.simulate(bidsim.replace_bid(inp, be))
    assert abs(r.net_profit) <= 10_000


def test_breakeven_bid_is_above_current_bid_when_profitable():
    """차익이 나는 시나리오면 손익분기가 현재 입찰가보다 높다(= 더 써도 된다)."""
    inp = _inp()
    assert bidsim.simulate(inp).net_profit > 0
    assert bidsim.breakeven_bid(inp) > inp.bid_price


def test_breakeven_bid_below_current_bid_when_losing():
    """지금 입찰가로 손해면 손익분기는 그보다 낮다(= 이만큼 깎아야 산다)."""
    inp = _inp(sell_price=300_000_000)
    assert bidsim.simulate(inp).net_profit < 0
    assert bidsim.breakeven_bid(inp) < inp.bid_price


def test_net_profit_is_monotonic_decreasing_in_bid():
    """이분탐색 전제 — 입찰가가 오르면 세후 순익은 반드시 내려간다."""
    inp = _inp()
    profits = [bidsim.simulate(bidsim.replace_bid(inp, b)).net_profit
               for b in range(200_000_000, 420_000_000, 20_000_000)]
    assert all(a > b for a, b in zip(profits, profits[1:], strict=False))


def test_breakeven_none_when_hopeless():
    """매도가가 0이면(시세 추정 불가) 어떤 입찰가로도 이익이 안 나 손익분기가 없다."""
    assert bidsim.breakeven_bid(_inp(sell_price=0)) is None


# ────────────────────────── 소액 표기(won_fine) ──────────────────────────

@pytest.mark.parametrize(("value", "expected"), [
    (0, "0원"),
    (150_000, "15만원"),          # 인지세 — won()이면 '0.00억'으로 뭉개졌던 값
    (500_000, "50만원"),          # 법무·채권
    (3_000_000, "300만원"),       # 명도비
    (99_999_999, "10,000만원"),   # 1억 직전은 아직 만원 표기
    (100_000_000, "1.00억"),      # 1억부터 억 표기
    (248_500_000, "2.48억"),   # 1억 이상은 기존 won()에 그대로 위임(표기 무회귀)
    (-500_000, "-50만원"),
])
def test_won_fine_switches_unit_at_1eok(value, expected):
    from src.report import won_fine
    assert won_fine(value) == expected


def test_won_fine_rounds_half_up_like_js():
    """JS Math.round 와 동일한 half-up — 서버 렌더와 슬라이더 갱신 표기가 갈리지 않게.

    파이썬 기본 round()는 banker's rounding이라 5,000원이 '0'(→'5,000원')이 되는데,
    JS는 1(→'1만원')이 된다. 이 경계에서 첫 슬라이더 조작 시 표기가 튀면 안 된다.
    """
    from src.report import won_fine
    assert won_fine(5_000) == "1만원"
    assert won_fine(15_000) == "2만원"
    assert won_fine(4_999) == "4,999원"


# ────────────────────────── 웹 배선 (/api/bidsim · 상세페이지) ──────────────────────────

def _client():
    from src.web import create_app
    return create_app().test_client()


def test_bidsim_api_matches_engine():
    """API는 엔진과 같은 값을 준다 — 서버·JS 두 경로가 갈리지 않게 payload 단일화."""
    r = _client().get("/api/bidsim?bid=300000000&sell=400000000&months=24"
                      "&type=아파트&area=84&ltv=0.7&rate=0.05")
    assert r.status_code == 200
    d = r.get_json()
    engine = bidsim.simulate(_inp())
    assert d["net_profit"] == engine.net_profit
    assert d["acquisition_tax"] == engine.acquisition_tax
    assert d["equity"] == engine.equity
    assert d["breakeven_bid"] == bidsim.breakeven_bid(_inp())
    assert d["breakeven_headroom"] == d["breakeven_bid"] - 300_000_000


def test_bidsim_api_defaults_and_garbage_input():
    """빈 요청·쓰레기 입력에도 500이 나지 않고 기본 가정으로 계산된다(입력 방어)."""
    for qs in ("", "?bid=abc&sell=&months=NaN&ltv=999&rate=-5", "?bid=-1&type=" + "가" * 200):
        r = _client().get("/api/bidsim" + qs)
        assert r.status_code == 200, qs
        d = r.get_json()
        assert d["bid"] >= 0 and d["loan_amount"] >= 0


def test_bidsim_api_clamps_ltv_and_rate():
    """LTV·금리 상한 클램프 — 사용자 입력이 그대로 계산에 들어가지 않는다."""
    d = _client().get("/api/bidsim?bid=100000000&sell=200000000&ltv=999&rate=999").get_json()
    assert d["loan_amount"] == round(100_000_000 * bidsim.MAX_LTV)
    # 금리 상한 30%로 잘려 이자가 유한하다(2년 기준)
    assert d["interest_total"] == round(d["loan_amount"] * 0.30 * 2)


def test_detail_page_renders_simulator():
    """상세페이지에 시뮬레이터가 서버 렌더된다(JS 꺼져도 숫자가 보이는 점진적 향상)."""
    body = _client().get("/property/2024타경51234").get_data(as_text=True)   # 상계주공(샘플)
    assert 'id="bidsim"' in body
    assert "입찰가 시뮬레이터" in body
    assert "손익분기 입찰가" in body
    assert 'id="sm-bid"' in body
