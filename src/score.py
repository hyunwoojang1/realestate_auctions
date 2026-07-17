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
    region = CONFIG.region_multiplier_default
    for prefixes, mult in CONFIG.region_multipliers:      # 위→아래 우선, 첫 매칭 채택
        if any(addr.startswith(p) for p in prefixes):
            region = mult
            break
    turnover_bonus = min(CONFIG.turnover_bonus_cap, matched_trades * CONFIG.turnover_bonus_per_trade)
    return max(0.0, min(100.0, round(base * region + turnover_bonus, 1)))


def grade_of(arb: float | None) -> str:
    if arb is None:
        return CONFIG.grade_labels["unestimable"]
    for min_score, label in CONFIG.grade_thresholds:   # 내림차순
        if arb >= min_score:
            return label
    return CONFIG.grade_thresholds[-1][1]


def derive_grade(arb: float | None, *, gated: bool, rights_verified: bool,
                 gap_rate: float | None = None, p_low: int | None = None,
                 market_scope: str | None = None, band_basis: int | None = None,
                 matched_trades: int | None = None,
                 apply_scope_sample_gates: bool = False) -> str:
    """arb+상태 → 등급 라벨 — 채점(국토부)·서빙폴백(KB/호가/전세) **단일 출처**.

    (2026-07-17 통합) 이전엔 score_listing과 market_view._apply_market_price가 등급 파생을
    각자 중복 구현해 우선순위가 갈렸다(채점=차익없음→권리미확인, 폴백=권리미확인→차익없음).
    아래 하나의 우선순위로 통일한다:
        위험(하드게이트) → 차익없음(보수차익≤0) → 권리미확인 → [scope·표본 게이트] → 표본강등 → grade_of

    - 차익없음을 권리미확인보다 먼저: 하한밴드로도 차익이 없으면 권리와 무관하게 '추천 안 함'이
      더 정직하고 정보량이 크다(차익 있는데 권리만 미검증일 때 '권리미확인'으로 남긴다).
    - apply_scope_sample_gates=True(국토부 채점): 같은단지·표본 게이트로 상위등급을 '관심' 강등.
      폴백은 신뢰계수로 이미 하향돼 있어 게이트 없이 추천 허용(False).
    """
    L = CONFIG.grade_labels
    if gated:
        return L["risk"]
    if (gap_rate is not None and gap_rate <= 0) or (p_low is not None and p_low <= 0):
        return L["no_profit"]
    if not rights_verified:
        return L["rights_unverified"]
    grade = grade_of(arb)
    if apply_scope_sample_gates:
        top, second = L["top"], L["second"]
        if market_scope not in ("", SCOPE_RECOMMENDABLE) and grade in (top, second):
            return L["interest"]
        if band_basis is not None and band_basis < band_confident_basis() and grade in (top, second):
            return L["interest"]
        if matched_trades is not None and matched_trades < CONFIG.min_comps_confident and grade == top:
            return second
    return grade


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
        na_grade = grade_of(None) if is_estimation_supported(listing.property_type) \
            else CONFIG.grade_labels["unsupported"]
        # (감사 2026-07-15) 하드게이트(유치권 등 치명권리·인수금액 과다)는 시세추정 여부와 무관하게
        # '위험'으로 표기한다. 이 경로에서 게이트를 건너뛰면, 서빙 때 KB시세로 재계산될 때
        # market_view 가 s.grade=="위험" 신호를 못 받아 위험 물건에 추천 등급을 주게 된다.
        if is_hard_gated(listing):
            na_grade = CONFIG.grade_labels["risk"]
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

    # 등급 파생 — derive_grade 단일 출처(폴백 재계산 _apply_market_price와 동일 우선순위).
    # 국토부 채점은 scope·표본 게이트 적용(apply_scope_sample_gates=True):
    #  (T3) 같은단지·같은평형 표본만 상위등급 인정, (T5) 실표본 5건 미만 강등, 표본<3 최상위 강등.
    grade = derive_grade(
        arb, gated=gated, rights_verified=listing.rights_verified,
        gap_rate=gap_rate, p_low=p_low, market_scope=market_scope,
        band_basis=band_basis, matched_trades=matched_trades,
        apply_scope_sample_gates=True)

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


def kb_price_of(naver: dict | None) -> int | None:
    """네이버 페이로드에서 신뢰할 KB 일반가(원) — matched_kb + 양수일 때만(이중 게이트)."""
    if naver and naver.get("status") == "matched_kb":
        v = naver.get("kb_avg")
        if v and v > 0:
            return int(v)
    return None


def ask_view_of(naver: dict | None, property_type: str) -> tuple[int, int, int, float] | None:
    """호가(ask) 폴백 시세 — 매칭 평형 기준 호가 범위에 보수 할인. (mid, low, high, conf) 또는 None.

    호가는 미체결·상향 편향이라 유형별 할인계수를 곱해 하향한다. 호가가 1건뿐이면 신뢰를 더 낮춘다.
    (매칭된 area_no 기준이라 kb_avg처럼 면적 정합됨 — 별도 ㎡정규화 불필요.)
    """
    if not naver:
        return None
    lo = naver.get("ask_min") or 0
    if lo <= 0:
        return None
    hi = naver.get("ask_max") or lo
    if hi < lo:
        hi = lo
    hc = CONFIG.ask_haircut_offi if property_type == "오피스텔" else CONFIG.ask_haircut_apt
    low = int(lo * hc)
    high = int(hi * hc)
    mid = (low + high) // 2
    conf = CONFIG.ask_confidence_single if (naver.get("ask_count") or 0) <= 1 else CONFIG.ask_confidence
    return (mid, low, high, conf)


def lease_view_of(naver: dict | None, property_type: str) -> int | None:
    """전세 역산 시세(원) — 시세 = 전세 일반가 / 전세가율. 양수 전세가 있을 때만."""
    if not naver:
        return None
    lv = naver.get("lease_avg") or 0
    if lv <= 0:
        return None
    ratio = CONFIG.jeonse_ratio_offi if property_type == "오피스텔" else CONFIG.jeonse_ratio_apt
    return int(lv / ratio)


def _apply_market_price(s: ScoredListing, naver: dict, price: int, price_low: int,
                        price_high: int, confidence: float, source: str) -> ScoredListing:
    """주어진 시세(price)로 차익·등급 재계산 — KB/호가/전세 폴백 공통 로직.

    권리 하드게이트(위험)·권리미확인은 시세로 풀리지 않으므로 보존한다. 차익은 하한밴드(보수)로 판정.
    """
    import dataclasses  # noqa: PLC0415
    cost = s.real_acquisition_cost or 0
    gap_rate = (price - cost) / price if price else 0.0
    gap = gap_score_from_rate(gap_rate)
    raw = gap * CONFIG.w_gap + s.rights_score * CONFIG.w_rights + s.liquidity_score * CONFIG.w_liq
    arb = round(raw * confidence, 1)          # 폴백 시세 → 신뢰계수 하향(실거래보다 보수)
    p_low = price_low - cost
    gated = (s.grade == CONFIG.grade_labels["risk"])   # 하드게이트는 채점층이 등급 문자열로 전달
    if gated:                                 # 위험(권리)은 시세로 안 풀림 — arb 상한 유지
        arb = min(arb, CONFIG.gate_ceiling)
    # 등급 파생은 derive_grade 단일 출처(채점 score_listing과 동일 우선순위). 폴백은 scope·표본
    # 게이트 없이 추천 허용(신뢰계수로 이미 하향) → apply_scope_sample_gates=False.
    grade = derive_grade(arb, gated=gated, rights_verified=s.rights_verified,
                         gap_rate=gap_rate, p_low=p_low, apply_scope_sample_gates=False)
    return dataclasses.replace(
        s, est_market_price=price, market_band_low=price_low, market_band_high=price_high,
        expected_profit=price - cost, profit_low=p_low, profit_high=price_high - cost,
        gap_rate=round(gap_rate, 4), gap_score=gap, arb_score=arb, grade=grade,
        confidence=confidence, market_scope=SCOPE_RECOMMENDABLE, market_source=source, naver=naver)


def market_view(s: ScoredListing, naver: dict | None) -> ScoredListing:
    """서빙 시점 시세뷰 — 폴백 사다리로 시세를 산정해 차익·등급 재계산(불변).

    우선순위: 국토부 실거래(est, 최우선) → KB시세(0.75) → 네이버 호가(0.55) → 전세 역산(0.50).
    실거래(est)가 있으면 '실거래 기반' 원칙상 폴백을 쓰지 않는다(감사 2026-07-16: KB가 국토부를 덮던
    319건 교정). 폴백들은 표본·스코프 게이트 없이 추천 가능하되 신뢰계수를 낮춰 표기하며, 권리 상태
    (위험=하드게이트·권리미확인)는 폴백으로 바뀌지 않으므로 보존한다.
    """
    import dataclasses  # noqa: PLC0415
    if naver is None:
        return s
    # 국토부 실거래 최우선 — est 있으면 실거래 유지, 표시용 페이로드만 첨부.
    if s.est_market_price and s.est_market_price > 0:
        return dataclasses.replace(s, naver=naver, market_source="molit")
    # 폴백 1: KB시세.
    kb = kb_price_of(naver)
    if kb is not None:
        kb_low = naver.get("kb_low") or kb
        kb_high = naver.get("kb_high") or kb
        return _apply_market_price(s, naver, kb, kb_low, kb_high, CONFIG.kb_confidence, "kb")
    # 폴백 2: 네이버 호가(보수 할인).
    ask = ask_view_of(naver, s.property_type)
    if ask is not None:
        mid, low, high, conf = ask
        return _apply_market_price(s, naver, mid, low, high, conf, "ask")
    # 폴백 3: 전세 역산.
    lease = lease_view_of(naver, s.property_type)
    if lease is not None:
        return _apply_market_price(s, naver, lease, lease, lease, CONFIG.lease_confidence, "lease")
    # 폴백 없음 — 표시용 페이로드만.
    return dataclasses.replace(s, naver=naver, market_source="none")
