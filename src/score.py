"""차익 스코어 엔진 (0~100).

score = ( 가격갭×w_gap + 권리×w_rights + 환금성×w_liq ) × 신뢰계수
하드게이트: 인수금액비율 > assumed_ratio_gate 또는 치명 특수권리 → 권리=0 + 최종 상한(gate_ceiling)
취득원가(객관) = 최저입찰가 + 취득세(본세+교육세+농특세)  ← real_acquisition_cost()
  · 명도비·수리비·권리 인수금액은 물건별 변동비라 취득원가에서 제외(객관성). 상세페이지에서
    '최대 인수(보증금 상한)'로 따로 차감해 차익 범위(최악~최선)를 보여준다.
가격갭 = (추정시세 − 취득원가) / 추정시세

모든 파라미터는 src/config.py의 CONFIG(ScoreConfig)에서 가져온다(코드수정 없이 튜닝 가능).
"""
from __future__ import annotations

from .config import CONFIG
from .matcher import SCOPE_SAME_COMPLEX_SAME_AREA as SCOPE_RECOMMENDABLE
from .matcher import band_confident_basis, is_estimation_supported
from .models import AuctionListing, ScoredListing


def confidence_from_matches(n: int) -> float:
    for min_n, coef in CONFIG.confidence_ladder:   # 내림차순
        if n >= min_n:
            return coef
    return CONFIG.confidence_ladder[-1][1]


def real_acquisition_cost(listing: AuctionListing) -> int:
    """취득원가(객관) = 최저입찰가 + 취득세(본세+교육세+농특세, tax.py).

    취득세는 docs/tax-auction-knowledge.md 기준 정밀 계산(주택 누진·다주택 중과·85㎡ 농특세·
    비주택 4.6%). 매수인 가정은 tax.PROFILE(기본 1주택·비조정·개인) 1개로 통일, UI에 명시.
    명도비·수리비·권리 인수금액 등 물건별 변동 비용은 제외(객관성) — 상세페이지 경고로만 안내.
    """
    from . import tax  # noqa: PLC0415 — 순환 없음, 지연 로드로 monkeypatch(tax.PROFILE) 반영
    return listing.min_bid_price + tax.acquisition_tax(
        listing.min_bid_price, listing.property_type, listing.area_m2)


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


def score_listing(listing: AuctionListing, est_market_price: int | None, matched_trades: int,
                  market_scope: str = "", band_low: int | None = None,
                  band_high: int | None = None, band_basis: int | None = None,
                  comps: tuple[tuple[str, int], ...] = ()) -> ScoredListing:
    """한 물건을 채점해 ScoredListing 반환.

    market_scope(T3): 시세 비교군의 출처. ""=레거시 호출(스코프 게이트 미적용).
    band_low/band_high(T4): 검증 하한가/기준가. 점수(gap)는 기준가로 유지(무회귀)하되,
    '차익없음' 판정 등 추천 여부는 보수 차익(profit_low = 하한가 − 취득원가)을 기준으로 한다.
    band_basis(T5): 밴드 실기반 표본수. band_confident_basis(기본 5) 미만이면 낮은 신뢰 —
    상위 등급 금지. None=레거시(게이트 미적용).
    """
    conf = confidence_from_matches(matched_trades)
    cost = real_acquisition_cost(listing)
    r = rights_score(listing)
    liq = liquidity_score(listing, matched_trades)

    if est_market_price is None or est_market_price <= 0 or matched_trades < CONFIG.min_comps_price:
        # 시세 추정 불가 — 정직하게 차익을 계산하지 않는다.
        # 표본이 min_comps_price(기본 2건) 미만이면 1건짜리 중앙값을 '시세'로 신뢰하지 않는다
        # (이상치 1건이 허위 차익을 만드는 것을 막는다).
        # (T2) v1 미지원 유형(빌라/상가/토지/유형불명)은 '데이터가 부족해서'가 아니라
        # '정책상 추정하지 않아서'임을 구분해 표기한다 — 사용자가 원인을 알아야 신뢰가 생긴다.
        na_grade = grade_of(None) if is_estimation_supported(listing.property_type) else "미지원유형"
        return ScoredListing(
            case_no=listing.case_no, apt_name=listing.apt_name, address=listing.address,
            property_type=listing.property_type, area_m2=listing.area_m2,
            appraisal_price=listing.appraisal_price, min_bid_price=listing.min_bid_price,
            fail_count=listing.fail_count, sale_date=listing.sale_date,
            est_market_price=None, matched_trades=matched_trades, confidence=conf,
            real_acquisition_cost=cost, expected_profit=None, gap_rate=None,
            gap_score=0.0, rights_score=r, liquidity_score=liq, arb_score=None,
            grade=na_grade, rights_verified=listing.rights_verified,
            court=listing.court, item_no=listing.item_no, doc_id=listing.doc_id,
            market_scope=market_scope, market_sample_basis=band_basis,
        )

    gap_rate = (est_market_price - cost) / est_market_price
    gap = gap_score_from_rate(gap_rate)
    raw = gap * CONFIG.w_gap + r * CONFIG.w_rights + liq * CONFIG.w_liq
    arb = round(raw * conf, 1)
    profit = est_market_price - cost
    # (T4) 밴드 차익 — 보수(하한가 기준)/기준(기준가 기준). 추천 판단은 p_low가 기준.
    p_low = band_low - cost if band_low is not None else None
    p_high = band_high - cost if band_high is not None else None

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
    elif gap_rate <= 0 or (p_low is not None and p_low <= 0):
        # (T4) 보수 기준: 검증 하한가로도 차익이 안 남으면 '차익없음' — 기준가 차익이 있어도
        # 추천하지 않는다(문서 11장 "보수 기준 차익이 충분하지 않습니다 → 추천 제외").
        grade = "차익없음"
    elif not listing.rights_verified:
        # 권리분석 미수행(라이브 크롤 등) → 점수는 참고로 남기되 등급은 비단정 '권리미확인'.
        # 허위 안전신호('차익 유력'·초록 안전문구)를 절대 부여하지 않는다.
        grade = "권리미확인"
    elif market_scope not in ("", SCOPE_RECOMMENDABLE) and grade in (top_grade, second_grade):
        # (T3) 비교군 scope 게이트 — v1 추천은 '같은 단지·같은 평형' 표본만 인정(문서 15장 3단계).
        # 인접 평형·같은 법정동 폴백 표본은 시세 참고치일 뿐 — 상위 등급('차익 유력'/'양호') 금지.
        # ""(레거시 호출·구 DB)는 게이트 미적용(하위호환).
        grade = "관심"
    elif band_basis is not None and band_basis < band_confident_basis() \
            and grade in (top_grade, second_grade):
        # (T5) 표본 게이트 — 실기반 표본 3~4건은 밴드는 만들되 '낮은 신뢰': 추천 등급 금지.
        # 소표본 중앙값·하한가는 통계 흉내일 수 있다(문서 10장 권장 기준).
        grade = "관심"
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
        court=listing.court, item_no=listing.item_no, doc_id=listing.doc_id,
        market_scope=market_scope, market_sample_basis=band_basis,
        market_band_low=band_low, market_band_high=band_high,
        profit_low=p_low, profit_high=p_high,
        market_comps=[list(c) for c in comps],
    )
