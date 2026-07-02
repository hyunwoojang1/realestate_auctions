"""차익 스코어 엔진 (0~100).

score = ( 가격갭×w_gap + 권리×w_rights + 환금성×w_liq ) × 신뢰계수
하드게이트: 인수금액비율 > assumed_ratio_gate 또는 치명 특수권리 → 권리=0 + 최종 상한(gate_ceiling)
실질취득원가 = 최저가 + 취득세 + 명도비 + 수리비 + 인수금액
가격갭 = (추정시세 − 실질취득원가) / 추정시세

모든 파라미터는 src/config.py의 CONFIG(ScoreConfig)에서 가져온다(코드수정 없이 튜닝 가능).
"""
from __future__ import annotations

from .config import CONFIG
from .models import AuctionListing, ScoredListing


def confidence_from_matches(n: int) -> float:
    for min_n, coef in CONFIG.confidence_ladder:   # 내림차순
        if n >= min_n:
            return coef
    return CONFIG.confidence_ladder[-1][1]


def acquisition_tax(price: int) -> int:
    """유상취득·주택 기준 단순화. config의 구간율 사용."""
    for limit, rate in CONFIG.acq_tax_brackets:
        if limit is None or price <= limit:
            return round(price * rate)
    return round(price * CONFIG.acq_tax_brackets[-1][1])


def repair_cost(area_m2: float) -> int:
    return round(area_m2 * CONFIG.repair_per_m2)


def real_acquisition_cost(listing: AuctionListing) -> int:
    """실질취득원가 = 최저가 + 취득세 + 명도비 + 수리비 + 인수금액."""
    return (
        listing.min_bid_price
        + acquisition_tax(listing.min_bid_price)
        + CONFIG.eviction_cost.get(listing.occupant_type, CONFIG.eviction_cost_default)
        + repair_cost(listing.area_m2)
        + listing.assumed_amount
    )


def gap_score_from_rate(gap_rate: float) -> float:
    pts = CONFIG.gap_points
    if gap_rate <= pts[0][0]:
        return pts[0][1]
    if gap_rate >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
        if x0 <= gap_rate <= x1:
            t = (gap_rate - x0) / (x1 - x0) if x1 != x0 else 0
            return round(y0 + t * (y1 - y0), 1)
    return 0.0


def is_hard_gated(listing: AuctionListing) -> bool:
    """인수금액 비율 > 임계 또는 치명적 특수권리 → 하드게이트."""
    ratio = listing.assumed_amount / listing.min_bid_price if listing.min_bid_price else 1.0
    if ratio > CONFIG.assumed_ratio_gate:
        return True
    if any(s in CONFIG.fatal_special for s in listing.special_rights):
        return True
    return False


def rights_score(listing: AuctionListing) -> float:
    if is_hard_gated(listing):
        return 0.0
    ratio = listing.assumed_amount / listing.min_bid_price if listing.min_bid_price else 1.0
    score = 100.0
    score -= ratio * 100
    score -= sum(CONFIG.special_penalty.get(s, CONFIG.special_penalty_default) for s in listing.special_rights)
    if listing.tenant_opposable:
        score -= CONFIG.tenant_opposable_penalty
    score -= CONFIG.occupant_penalty.get(listing.occupant_type, CONFIG.occupant_penalty_default)
    return max(0.0, min(100.0, round(score, 1)))


def liquidity_score(listing: AuctionListing, matched_trades: int = 0) -> float:
    base = CONFIG.type_base.get(listing.property_type, CONFIG.type_base_default)
    addr = listing.address
    if addr.startswith("서울"):
        region = 1.10
    elif addr.startswith("경기"):
        region = 1.05
    elif any(addr.startswith(c) for c in ("부산", "대구", "인천", "광주", "대전", "울산")):
        region = 1.00
    else:
        region = 0.85
    turnover_bonus = min(10, matched_trades * 2)
    return max(0.0, min(100.0, round(base * region + turnover_bonus, 1)))


def grade_of(arb: float | None) -> str:
    if arb is None:
        return "시세추정불가"
    for min_score, label in CONFIG.grade_thresholds:   # 내림차순
        if arb >= min_score:
            return label
    return CONFIG.grade_thresholds[-1][1]


def score_listing(listing: AuctionListing, est_market_price: int | None, matched_trades: int) -> ScoredListing:
    """한 물건을 채점해 ScoredListing 반환."""
    conf = confidence_from_matches(matched_trades)
    cost = real_acquisition_cost(listing)
    r = rights_score(listing)
    liq = liquidity_score(listing, matched_trades)

    if est_market_price is None or est_market_price <= 0 or matched_trades < CONFIG.min_comps_price:
        # 시세 추정 불가 — 정직하게 차익을 계산하지 않는다.
        # 표본이 min_comps_price(기본 2건) 미만이면 1건짜리 중앙값을 '시세'로 신뢰하지 않는다
        # (이상치 1건이 허위 차익을 만드는 것을 막는다).
        return ScoredListing(
            case_no=listing.case_no, apt_name=listing.apt_name, address=listing.address,
            property_type=listing.property_type, area_m2=listing.area_m2,
            appraisal_price=listing.appraisal_price, min_bid_price=listing.min_bid_price,
            fail_count=listing.fail_count, sale_date=listing.sale_date,
            est_market_price=None, matched_trades=matched_trades, confidence=conf,
            real_acquisition_cost=cost, expected_profit=None, gap_rate=None,
            gap_score=0.0, rights_score=r, liquidity_score=liq, arb_score=None,
            grade=grade_of(None), rights_verified=listing.rights_verified,
        )

    gap_rate = (est_market_price - cost) / est_market_price
    gap = gap_score_from_rate(gap_rate)
    raw = gap * CONFIG.w_gap + r * CONFIG.w_rights + liq * CONFIG.w_liq
    arb = round(raw * conf, 1)
    profit = est_market_price - cost

    # 하드게이트: 권리 점수만 0으로는 부족하다(가격갭 50%가 커서 상위 노출 가능).
    # 최종 스코어를 상한으로 끌어내리고 '위험' 등급으로 강등한다.
    gated = is_hard_gated(listing)
    if gated:
        arb = min(arb, CONFIG.gate_ceiling)

    grade = grade_of(arb)
    top_grade = CONFIG.grade_thresholds[0][1]      # '차익 유력'
    second_grade = CONFIG.grade_thresholds[1][1]   # '양호'
    if gated:
        grade = "위험"
    elif gap_rate <= 0:
        grade = "차익없음"
    elif not listing.rights_verified:
        # 권리분석 미수행(라이브 크롤 등) → 점수는 참고로 남기되 등급은 비단정 '권리미확인'.
        # 허위 안전신호('차익 유력'·초록 안전문구)를 절대 부여하지 않는다.
        grade = "권리미확인"
    elif matched_trades < CONFIG.min_comps_confident and grade == top_grade:
        # 표본 부족(신뢰계수 1.0 미만)인데 최상위면 한 단계 강등(1~2건 표본으로 '차익 유력' 금지).
        grade = second_grade

    return ScoredListing(
        case_no=listing.case_no, apt_name=listing.apt_name, address=listing.address,
        property_type=listing.property_type, area_m2=listing.area_m2,
        appraisal_price=listing.appraisal_price, min_bid_price=listing.min_bid_price,
        fail_count=listing.fail_count, sale_date=listing.sale_date,
        est_market_price=est_market_price, matched_trades=matched_trades, confidence=conf,
        real_acquisition_cost=cost, expected_profit=profit, gap_rate=round(gap_rate, 4),
        gap_score=gap, rights_score=r, liquidity_score=liq, arb_score=arb,
        grade=grade, rights_verified=listing.rights_verified,
    )
