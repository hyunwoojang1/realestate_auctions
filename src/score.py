"""차익 스코어 엔진 (0~100).

score = ( 가격갭×0.50 + 권리×0.30 + 환금성×0.20 ) × 신뢰계수(0.6~1.0)
하드게이트: 인수금액비율 > 30% 또는 치명 유치권 → 권리=0
실질취득원가 = 최저가 + 취득세 + 명도비 + 수리비 + 인수금액
가격갭 = (추정시세 − 실질취득원가) / 추정시세
"""
from __future__ import annotations

from typing import Optional

from .models import AuctionListing, ScoredListing

# ---- 가중치 ----
W_GAP, W_RIGHTS, W_LIQ = 0.50, 0.30, 0.20

# ---- 부대비용 상수 (PoC 추정치, 보수적) ----
EVICTION_COST = {"공실": 0, "임차인": 3_000_000, "소유자점유": 5_000_000, "다수점유": 10_000_000}
REPAIR_PER_M2 = 100_000  # 전용면적당 수리비 추정

# ---- 권리 페널티 ----
SPECIAL_PENALTY = {"유치권": 30, "법정지상권": 25, "지분": 20, "분묘기지권": 20, "대지권미등기": 15, "위반건축물": 15}
FATAL_SPECIAL = {"유치권"}            # 확정 시 하드게이트
ASSUMED_RATIO_GATE = 0.30            # 인수금액/최저가 임계
OCCUPANT_PENALTY = {"공실": 0, "임차인": 10, "소유자점유": 15, "다수점유": 25}
TENANT_OPPOSABLE_PENALTY = 30        # 대항력 임차인(배당 불가)

# ---- 환금성 ----
TYPE_BASE = {"아파트": 90, "오피스텔": 75, "다세대": 60, "빌라": 60, "연립": 60, "상가": 45, "토지": 35}
TYPE_BASE_DEFAULT = 30               # 특수/기타

# ---- 신뢰계수 (시세 추정 표본 밀도) ----
def confidence_from_matches(n: int) -> float:
    if n >= 3:
        return 1.0
    if n == 2:
        return 0.85
    if n == 1:
        return 0.70
    return 0.60


# ---- 취득세 (유상취득·주택 기준 단순화) ----
def acquisition_tax(price: int) -> int:
    if price <= 600_000_000:
        rate = 0.011
    elif price <= 900_000_000:
        rate = 0.022
    else:
        rate = 0.033
    return round(price * rate)


def repair_cost(area_m2: float) -> int:
    return round(area_m2 * REPAIR_PER_M2)


def real_acquisition_cost(listing: AuctionListing) -> int:
    """실질취득원가 = 최저가 + 취득세 + 명도비 + 수리비 + 인수금액."""
    return (
        listing.min_bid_price
        + acquisition_tax(listing.min_bid_price)
        + EVICTION_COST.get(listing.occupant_type, 5_000_000)
        + repair_cost(listing.area_m2)
        + listing.assumed_amount
    )


# ---- 가격갭 점수 (구간 선형보간) ----
_GAP_POINTS = [(-1.0, 0.0), (0.0, 0.0), (0.10, 35.0), (0.20, 60.0), (0.30, 80.0), (0.40, 100.0), (1.0, 100.0)]


def gap_score_from_rate(gap_rate: float) -> float:
    pts = _GAP_POINTS
    if gap_rate <= pts[0][0]:
        return pts[0][1]
    if gap_rate >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= gap_rate <= x1:
            t = (gap_rate - x0) / (x1 - x0) if x1 != x0 else 0
            return round(y0 + t * (y1 - y0), 1)
    return 0.0


# ---- 권리 점수 ----
def rights_score(listing: AuctionListing) -> float:
    # 하드게이트
    ratio = listing.assumed_amount / listing.min_bid_price if listing.min_bid_price else 1.0
    if ratio > ASSUMED_RATIO_GATE:
        return 0.0
    if any(s in FATAL_SPECIAL for s in listing.special_rights):
        return 0.0

    score = 100.0
    score -= ratio * 100                                  # 인수금액 비율만큼 직접 차감
    score -= sum(SPECIAL_PENALTY.get(s, 10) for s in listing.special_rights)
    if listing.tenant_opposable:
        score -= TENANT_OPPOSABLE_PENALTY
    score -= OCCUPANT_PENALTY.get(listing.occupant_type, 15)
    return max(0.0, min(100.0, round(score, 1)))


# ---- 환금성 점수 ----
def liquidity_score(listing: AuctionListing, matched_trades: int = 0) -> float:
    base = TYPE_BASE.get(listing.property_type, TYPE_BASE_DEFAULT)
    addr = listing.address
    if addr.startswith("서울"):
        region = 1.10
    elif addr.startswith("경기"):
        region = 1.05
    elif any(addr.startswith(c) for c in ("부산", "대구", "인천", "광주", "대전", "울산")):
        region = 1.00
    else:
        region = 0.85
    # 거래회전 보정: 매칭된 실거래가 많을수록 시장 수요 신호 (+0~10)
    turnover_bonus = min(10, matched_trades * 2)
    return max(0.0, min(100.0, round(base * region + turnover_bonus, 1)))


def grade_of(arb: Optional[float]) -> str:
    if arb is None:
        return "시세추정불가"
    if arb >= 80:
        return "확실한 차익"
    if arb >= 60:
        return "양호"
    if arb >= 40:
        return "관심"
    return "주의"


def score_listing(listing: AuctionListing, est_market_price: Optional[int], matched_trades: int) -> ScoredListing:
    """한 물건을 채점해 ScoredListing 반환."""
    conf = confidence_from_matches(matched_trades)
    cost = real_acquisition_cost(listing)
    r = rights_score(listing)
    liq = liquidity_score(listing, matched_trades)

    if est_market_price is None or est_market_price <= 0:
        # 시세 추정 불가 — 정직하게 차익을 계산하지 않는다.
        return ScoredListing(
            case_no=listing.case_no, apt_name=listing.apt_name, address=listing.address,
            property_type=listing.property_type, area_m2=listing.area_m2,
            appraisal_price=listing.appraisal_price, min_bid_price=listing.min_bid_price,
            fail_count=listing.fail_count, sale_date=listing.sale_date,
            est_market_price=None, matched_trades=matched_trades, confidence=conf,
            real_acquisition_cost=cost, expected_profit=None, gap_rate=None,
            gap_score=0.0, rights_score=r, liquidity_score=liq, arb_score=None,
            grade=grade_of(None),
        )

    gap_rate = (est_market_price - cost) / est_market_price
    gap = gap_score_from_rate(gap_rate)
    raw = gap * W_GAP + r * W_RIGHTS + liq * W_LIQ
    arb = round(raw * conf, 1)
    profit = est_market_price - cost

    # 순차익이 0 이하면 권리·환금이 좋아도 '차익'은 없다 — 큐레이션 엔진이므로 솔직히 표기.
    grade = grade_of(arb)
    if gap_rate <= 0:
        grade = "차익없음"

    return ScoredListing(
        case_no=listing.case_no, apt_name=listing.apt_name, address=listing.address,
        property_type=listing.property_type, area_m2=listing.area_m2,
        appraisal_price=listing.appraisal_price, min_bid_price=listing.min_bid_price,
        fail_count=listing.fail_count, sale_date=listing.sale_date,
        est_market_price=est_market_price, matched_trades=matched_trades, confidence=conf,
        real_acquisition_cost=cost, expected_profit=profit, gap_rate=round(gap_rate, 4),
        gap_score=gap, rights_score=r, liquidity_score=liq, arb_score=arb,
        grade=grade,
    )
