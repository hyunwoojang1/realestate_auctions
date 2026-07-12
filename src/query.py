"""채점 결과 필터·정렬 (순수 함수 — 테스트 용이, CLI·웹 양쪽에서 재사용).

기본 정렬 = 보수 차익(profit_low, 없으면 기준 차익 폴백 — T7). 갭률(gap) 토글 지원.
점수(score)는 UI에서 제거됐지만 내부/백테스트 호환을 위해 정렬 키로는 남겨둔다(사용자 결정 #7).
"""
from __future__ import annotations

import datetime as _dt

from .models import ScoredListing
from .region import matches_region

SORT_KEYS = ("profit", "gap", "score")
DEFAULT_SORT = "profit"

# 검색 우선 홈(2026-07): 기본 화면은 '평가 가능한' 물건만 — 시세 추정치가 있는 것.
# 미지원유형(빌라·상가·토지 ~70%)·시세추정불가(~20%)는 값이 전부 '—'라 기본에서 숨기고
# '전체 탐색'(all=1)에서만 노출한다(노이즈에 신호가 묻히지 않게).
HIGH_PROFIT_THRESHOLD = 200_000_000   # '고차익' 빠른진입 칩 기준(2억)
SOON_DAYS = 7                         # '매각기일 임박' 빠른진입 칩 기준(일)


def is_evaluable(s: ScoredListing) -> bool:
    """이 도구가 시세를 추정한(=평가 가능한) 물건인가. 검색 우선 홈의 기본 노출 기준."""
    return s.est_market_price is not None


def days_until(sale_date: str, today: _dt.date | None = None) -> int | None:
    """매각기일까지 남은 일수. 파싱 실패 시 None(임박 필터에서 제외)."""
    today = today or _dt.date.today()
    try:
        d = _dt.date.fromisoformat((sale_date or "")[:10])
    except ValueError:
        return None
    return (d - today).days


def is_soon(s: ScoredListing, today: _dt.date | None = None, within: int = SOON_DAYS) -> bool:
    """매각기일이 오늘부터 within일 이내(지난 기일 제외)."""
    d = days_until(s.sale_date, today)
    return d is not None and 0 <= d <= within


def is_high_profit(s: ScoredListing, threshold: int = HIGH_PROFIT_THRESHOLD) -> bool:
    """보수 기준 차익이 threshold(기본 2억) 이상."""
    p = decision_profit(s)
    return p is not None and p >= threshold

# 비교군 신뢰 티어(감사 2026-07-10 MEDIUM): T3 정책상 same_dong_fallback 은 '참고치 — 추천
# 금지'인데 기본 정렬이 scope 를 안 봐 상위 50 의 84%를 fallback 허상 차익이 독식했다.
# 기본(차익) 정렬은 [검증 비교군 → 참고치 → 시세 없음] 티어 안에서 차익 내림차순.
# 0 = 같은 단지(추천 인정·인접 평형), 1 = 레거시(스코프 미기록 — 구 DB 하위호환),
# 2 = 동 폴백(참고치), 3 = 시세 없음/무효.
_SCOPE_TIER = {
    "same_complex_same_area": 0,
    "same_complex_near_area": 0,
    "": 1,
    "same_dong_fallback": 2,
}


def scope_tier(s: ScoredListing) -> int:
    if decision_profit(s) is None:
        return 3
    return _SCOPE_TIER.get(s.market_scope, 3)


def decision_profit(s: ScoredListing) -> int | None:
    """판단용 차익 — 보수 차익(profit_low) 우선, 없으면(레거시 행) 기준 차익.

    (T7, 문서 15장 7단계) 사용자에게 보이는 '차익 큰 순'과 필터는 보수 가격 기준이다.
    """
    return s.profit_low if s.profit_low is not None else s.expected_profit


def apply_filters(items: list[ScoredListing], min_score: float | None = None,
                  property_type: str | None = None, region: str | None = None,
                  min_profit: int | None = None, burden_of=None,
                  max_bid: int | None = None, min_bid: int | None = None,
                  evaluable_only: bool = False) -> list[ScoredListing]:
    """물건종류·지역·최소차익(원, 보수 기준)·예산(최저입찰가 상/하한)·평가가능 필터.

    burden_of: 물건 → 인수금액(원). 주어지면 최소차익 비교도 인수 차감 후 값으로
    (감사 2026-07-10: 필터·정렬은 저장 차익, 화면은 차감 차익 — 불일치 해소).
    max_bid/min_bid: 예산 필터(최저입찰가 상한/하한, 원). evaluable_only: 시세 추정된 물건만.
    """
    out = items
    if evaluable_only:
        out = [s for s in out if is_evaluable(s)]
    if min_profit is not None:
        def _eff(s):
            p = decision_profit(s)
            return None if p is None else p - (burden_of(s) if burden_of else 0)
        out = [s for s in out if _eff(s) is not None and _eff(s) >= min_profit]
    if min_score is not None:   # 내부/API 하위호환용 — UI는 min_profit 사용
        out = [s for s in out if s.arb_score is not None and s.arb_score >= min_score]
    if property_type:
        out = [s for s in out if s.property_type == property_type]
    if region:
        out = [s for s in out if matches_region(s.address, region)]
    if max_bid is not None:
        out = [s for s in out if s.min_bid_price and s.min_bid_price <= max_bid]
    if min_bid is not None:
        out = [s for s in out if s.min_bid_price and s.min_bid_price >= min_bid]
    return out


def sort_items(items: list[ScoredListing], key: str = DEFAULT_SORT,
               burden_of=None, uncertain_of=None) -> list[ScoredListing]:
    """정렬 — 기본(profit)은 [비교군 신뢰 티어 + 인수 불확실 티어] → 유효 차익 내림차순.

    검증 비교군(같은 단지) 물건이 폴백 참고치보다 항상 위 — '추천 금지 참고치'의 부풀린
    차익이 첫 화면 헤드라인을 차지하지 않게 한다(감사 2026-07-10).
    burden_of 가 주어지면 정렬 차익에서 인수금을 차감 — 화면 표시(p_adj)와 순위 일치.
    uncertain_of(서빙감사 2026-07-12 #1·#9): 인수 부담인데 금액 미상(+α)이라 차감 못 한
    물건은 별도 하위 티어로 강등 — 유찰 다회·임차권 미소멸 물건이 검증 clean 물건 위에
    무차감으로 랭크되던 문제 해소('−α' 표시와 정합).
    """
    if key == "gap":
        return sorted(items, key=lambda s: (s.gap_rate is None, -(s.gap_rate or 0)))
    if key == "score":
        return sorted(items, key=lambda s: (s.arb_score is None, -(s.arb_score or 0)))

    def _eff(s):
        p = decision_profit(s)
        return None if p is None else p - (burden_of(s) if burden_of else 0)
    return sorted(items, key=lambda s: (
        scope_tier(s),
        1 if (uncertain_of and uncertain_of(s)) else 0,
        -(_eff(s) or 0)))
