"""입찰가 시뮬레이터 — 낙찰~매도 전체 현금흐름·세후 순익·손익분기 입찰가.

docs/tax-auction-knowledge.md **§5와 1:1**. 세율·요율을 바꾸려면 그 문서를 먼저 고친다.

이 모듈이 존재하는 이유:
  목록·스코어의 '예상 차익'은 **취득 시점 표면차익**이다(최저입찰가 + 취득세만 반영, 사용자 확정 정의).
  명도비·대출이자·양도세는 "매도가·보유기간 가정이 필요해 주관이 개입된다"는 이유로 빠져 있었다(§3).
  시뮬레이터는 그 가정을 **사용자가 직접 입력**하게 만들어 벽을 넘는다. 대신 결과는 상세페이지에만
  머무르고 목록·스코어·등급에는 절대 영향을 주지 않는다(회귀 차단 — 표면차익 정의 유지).

설계 원칙:
  · **순수 함수**(I/O·전역상태 없음) — 테스트가 값을 직접 고정한다.
  · **보수적**: 불확실하면 비용은 크게, 공제는 작게. 과대 수익 추정이 이 도구의 최대 위험이다.
  · **입찰가 연동**: 취득세는 입력 입찰가로 재계산한다(목록의 최저입찰가 고정과 다른 지점).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from . import tax
from .tax import BuyerProfile

# ── 기본 가정값(§5-1·§5-3) — UI 슬라이더의 초기값이자, 사용자가 전부 조절할 수 있다. ──
DEFAULT_EVICTION = 3_000_000      # 명도비(이사합의금·강제집행) — 물건별 편차 큼
DEFAULT_REGISTRY = 500_000        # 법무사 보수 + 국민주택채권 할인 등 등기 부대비
DEFAULT_LTV = 0.70                # 경락잔금대출 통상 수준
DEFAULT_LOAN_RATE = 0.05          # 연 이자율
DEFAULT_HOLDING_MONTHS = 24       # 보유기간(2년 = 단기세율 회피 하한)
MAX_LTV = 0.80                    # 방공제·신용 감안 상한 캡

# ── 양도소득세(§5-5) ──
BASIC_DEDUCTION = 2_500_000       # 양도소득 기본공제(연 250만원)
LOCAL_TAX_RATE = 0.10             # 지방소득세 = 산출세액의 10%
LTD_RATE_PER_YEAR = 0.02          # 장기보유특별공제 연 2%
LTD_MIN_YEARS = 3                 # 3년 이상부터 적용
LTD_MAX_RATE = 0.30               # 최대 30%(15년)

# 기본세율 누진표 (상한 과세표준, 세율, 누진공제). 상한 None = 무제한.
BASIC_RATES: tuple[tuple[int | None, float, int], ...] = (
    (14_000_000, 0.06, 0),
    (50_000_000, 0.15, 1_260_000),
    (88_000_000, 0.24, 5_760_000),
    (150_000_000, 0.35, 15_440_000),
    (300_000_000, 0.38, 19_940_000),
    (500_000_000, 0.40, 25_940_000),
    (1_000_000_000, 0.42, 35_940_000),
    (None, 0.45, 65_940_000),
)

# 단기 양도세율 — (주택·입주권, 그 외 부동산)
SHORT_TERM_UNDER_1Y = (0.70, 0.50)
SHORT_TERM_UNDER_2Y = (0.60, 0.40)

# 인지세 구간표(§5-2) — (상한 기재금액, 세액). 상한 None = 초과분.
STAMP_BRACKETS: tuple[tuple[int | None, int], ...] = (
    (10_000_000, 0),
    (30_000_000, 20_000),
    (50_000_000, 40_000),
    (100_000_000, 70_000),
    (1_000_000_000, 150_000),
    (None, 350_000),
)

# 매도 중개보수 상한요율(§5-4) — 주택: (상한 매매가, 요율, 한도액). 한도 None = 없음.
AGENT_FEE_HOUSING: tuple[tuple[int | None, float, int | None], ...] = (
    (50_000_000, 0.006, 250_000),
    (200_000_000, 0.005, 800_000),
    (900_000_000, 0.004, None),
    (1_200_000_000, 0.005, None),
    (1_500_000_000, 0.006, None),
    (None, 0.007, None),
)
AGENT_FEE_RESIDENTIAL_OFFICETEL = 0.005   # 주거용 오피스텔(전용 85㎡ 이하) 매매
AGENT_FEE_NONHOUSING = 0.009              # 상가·토지·85㎡ 초과 오피스텔(협의 상한)
RESIDENTIAL_OFFICETEL_MAX_M2 = 85.0


@dataclass(frozen=True)
class SimInput:
    """시뮬레이션 가정 한 벌. 전부 사용자가 조절 가능하다."""
    bid_price: int                              # 입찰가(슬라이더 — 이 값이 모든 계산의 축)
    property_type: str
    area_m2: float = 0.0
    sell_price: int = 0                         # 예상 매도가(기본=검증 하한가)
    holding_months: int = DEFAULT_HOLDING_MONTHS
    assumed_amount: int = 0                     # 권리분석상 인수금(대항력 보증금 등)
    eviction_cost: int = DEFAULT_EVICTION
    repair_cost: int = 0
    unpaid_fees: int = 0                        # 미납관리비(공용부분만 승계)
    registry_cost: int = DEFAULT_REGISTRY       # 법무사·채권 등
    loan_ltv: float = DEFAULT_LTV
    loan_rate: float = DEFAULT_LOAN_RATE
    profile: BuyerProfile | None = None         # None = tax.PROFILE(전역 기본)


@dataclass(frozen=True)
class SimResult:
    """시뮬레이션 결과. 금액은 전부 원 단위 정수, 비율은 소수(0.15 = 15%)."""
    acquisition_tax: int        # 취득세(본세+교육세+농특세) — 입찰가 기준 재계산
    stamp_tax: int              # 인지세
    registry_cost: int          # 법무사·채권 등
    other_costs: int            # 인수금 + 명도 + 수리 + 미납관리비
    total_acquisition: int      # 취득 단계 총투입(입찰가 포함)
    loan_amount: int
    equity: int                 # 실투자금(자기자본) = 총투입 − 대출
    interest_total: int
    agent_fee: int              # 매도 중개보수
    capital_gain: int           # 양도차익(음수 가능)
    transfer_tax: int           # 양도세 + 지방소득세
    transfer_detail: dict       # 양도세 계산 내역(UI 펼침용)
    net_profit: int             # 세후 순익
    roi: float | None           # 자기자본수익률 (자기자본 ≤ 0 이면 None)
    roi_annual: float | None    # 연환산 (보유기간 0 이면 None)


def replace_bid(inp: SimInput, bid_price: int) -> SimInput:
    """입찰가만 바꾼 새 입력 — 손익분기 탐색·민감도 계산용(불변 유지)."""
    return dataclasses.replace(inp, bid_price=int(bid_price))


def stamp_tax(price: int) -> int:
    """인지세(§5-2) — 부동산 소유권이전 증서 기재금액 기준 정액."""
    for cap, amount in STAMP_BRACKETS:
        if cap is None or price <= cap:
            return amount
    return STAMP_BRACKETS[-1][1]


def _is_residential_officetel(property_type: str, area_m2: float) -> bool:
    return "오피스텔" in (property_type or "") and 0 < area_m2 <= RESIDENTIAL_OFFICETEL_MAX_M2


def agent_fee(sell_price: int, property_type: str, area_m2: float = 0.0) -> int:
    """매도 중개보수(§5-4) — 상한요율 기준. 실제로는 협의로 낮아질 수 있다(보수적)."""
    if sell_price <= 0:
        return 0
    if tax.is_housing(property_type):
        for cap, rate, limit in AGENT_FEE_HOUSING:
            if cap is None or sell_price < cap:
                fee = round(sell_price * rate)
                return min(fee, limit) if limit is not None else fee
    if _is_residential_officetel(property_type, area_m2):
        return round(sell_price * AGENT_FEE_RESIDENTIAL_OFFICETEL)
    return round(sell_price * AGENT_FEE_NONHOUSING)


def is_housing_for_transfer(property_type: str) -> bool:
    """양도세 단기세율에서 '주택'으로 볼 것인가.

    취득세는 오피스텔을 비주택(4.6%)으로 보지만(§2), 양도세는 주거용이면 실질과세로 주택 판정을
    받을 수 있다. 주택 단기세율(70/60%)이 비주택(50/40%)보다 **높으므로** 보수 원칙에 따라
    오피스텔을 주택으로 간주한다(§5-5 주석). 업무용 확정이면 실세금은 이보다 작다.
    """
    t = (property_type or "").strip()
    return tax.is_housing(t) or "오피스텔" in t


def long_term_deduction_rate(holding_months: int) -> float:
    """장기보유특별공제율(§5-5 표1) — 3년 이상 연 2%, 최대 30%. 3년 미만 0."""
    years = holding_months // 12
    if years < LTD_MIN_YEARS:
        return 0.0
    return min(LTD_MAX_RATE, years * LTD_RATE_PER_YEAR)


def _transfer_rate(holding_months: int, housing: bool) -> tuple[str, float | None]:
    """(세율 라벨, 단일세율). 2년 이상이면 단일세율 None → 누진표 사용."""
    idx = 0 if housing else 1
    if holding_months < 12:
        return ("1년 미만 단기", SHORT_TERM_UNDER_1Y[idx])
    if holding_months < 24:
        return ("1~2년 단기", SHORT_TERM_UNDER_2Y[idx])
    return ("2년 이상 기본세율", None)


def _progressive_tax(base: int) -> int:
    """기본세율 누진표 적용(§5-5)."""
    for cap, rate, deduction in BASIC_RATES:
        if cap is None or base <= cap:
            return max(0, round(base * rate) - deduction)
    return 0


def transfer_tax(sell_price: int, acquire_price: int, expenses: int,
                 holding_months: int, property_type: str) -> dict:
    """양도소득세 + 지방소득세(§5-5).

    expenses = 필요경비(취득세·인지세·등기부대비·매도중개보수). **명도비·수리비·미납관리비·
    대출이자는 포함하지 않는다** — 실무상 불인정이거나 자본적/수익적 지출 구분이 입력만으로
    불가하므로 보수적으로 미공제(세금을 크게 잡는다).
    """
    gain = sell_price - acquire_price - expenses
    if gain <= 0:
        # 손해 보고 파는데 세금이 붙지는 않는다. 양도차익은 실제 음수값을 그대로 노출(정직).
        return {"양도차익": gain, "장기보유특별공제": 0, "양도소득금액": max(0, gain),
                "과세표준": 0, "세율": "해당 없음(양도차익 없음)",
                "산출세액": 0, "지방소득세": 0, "합계": 0}

    housing = is_housing_for_transfer(property_type)
    label, flat_rate = _transfer_rate(holding_months, housing)
    # 장특공은 단기세율 구간에 적용하지 않는다(§5-5).
    ltd_rate = 0.0 if flat_rate is not None else long_term_deduction_rate(holding_months)
    ltd = round(gain * ltd_rate)
    income = gain - ltd
    base = max(0, income - BASIC_DEDUCTION)

    if flat_rate is not None:
        calculated = round(base * flat_rate)
        label = f"{label} {flat_rate * 100:.0f}%"
    else:
        calculated = _progressive_tax(base)
    local = round(calculated * LOCAL_TAX_RATE)
    return {"양도차익": gain, "장기보유특별공제": ltd, "양도소득금액": income,
            "과세표준": base, "세율": label,
            "산출세액": calculated, "지방소득세": local, "합계": calculated + local}


def simulate(inp: SimInput) -> SimResult:
    """가정 한 벌 → 낙찰~매도 전체 손익(§5-6)."""
    bid = max(0, int(inp.bid_price))
    acq_tax = tax.acquisition_tax(bid, inp.property_type, inp.area_m2, inp.profile)
    stamp = stamp_tax(bid)
    other = (max(0, inp.assumed_amount) + max(0, inp.eviction_cost)
             + max(0, inp.repair_cost) + max(0, inp.unpaid_fees))
    total_acq = bid + acq_tax + stamp + max(0, inp.registry_cost) + other

    ltv = min(MAX_LTV, max(0.0, inp.loan_ltv))
    loan = round(bid * ltv)
    equity = total_acq - loan
    months = max(0, int(inp.holding_months))
    interest = round(loan * max(0.0, inp.loan_rate) * months / 12)

    sell = max(0, int(inp.sell_price))
    fee = agent_fee(sell, inp.property_type, inp.area_m2)
    # 필요경비 = 취득 부대비(취득세·인지세·등기) + 매도 중개보수. 명도·수리·미납·이자는 제외(보수적).
    expenses = acq_tax + stamp + max(0, inp.registry_cost) + fee
    t = transfer_tax(sell, bid, expenses, months, inp.property_type)

    net = sell - total_acq - interest - fee - t["합계"]
    roi = (net / equity) if equity > 0 else None
    roi_annual = (roi * 12 / months) if (roi is not None and months > 0) else None

    return SimResult(
        acquisition_tax=acq_tax, stamp_tax=stamp, registry_cost=max(0, inp.registry_cost),
        other_costs=other, total_acquisition=total_acq,
        loan_amount=loan, equity=equity, interest_total=interest,
        agent_fee=fee, capital_gain=t["양도차익"], transfer_tax=t["합계"], transfer_detail=t,
        net_profit=net, roi=roi, roi_annual=roi_annual,
    )


def breakeven_bid(inp: SimInput) -> int | None:
    """세후 순익 = 0 이 되는 입찰가. 이 위로 쓰면 손해다 — 입찰표에 쓰는 숫자.

    세후 순익은 입찰가에 대해 **단조감소**한다(입찰가 1원↑ → 취득세·이자↑, 양도차익↓.
    양도세 최고 실효율 77%가 1을 넘지 못하므로 절감분이 증가분을 이길 수 없다) → 이분탐색.
    입찰가 0에서도 이익이 안 나면(시세 없음·비용 과다) 손익분기 자체가 없어 None.
    """
    if simulate(replace_bid(inp, 0)).net_profit <= 0:
        return None
    lo = 0
    hi = max(inp.sell_price, inp.bid_price) * 2 + 100_000_000
    if simulate(replace_bid(inp, hi)).net_profit > 0:
        return hi   # 방어적 — 정상 파라미터에선 도달하지 않는다
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if simulate(replace_bid(inp, mid)).net_profit > 0:
            lo = mid
        else:
            hi = mid
    return lo
