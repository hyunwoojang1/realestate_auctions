"""채점 결과 필터·정렬 (순수 함수 — 테스트 용이, CLI·웹 양쪽에서 재사용).

기본 정렬 = 보수 차익(profit_low, 없으면 기준 차익 폴백 — T7). 갭률(gap) 토글 지원.
점수(score)는 UI에서 제거됐지만 내부/백테스트 호환을 위해 정렬 키로는 남겨둔다(사용자 결정 #7).
"""
from __future__ import annotations

from .models import ScoredListing
from .region import matches_region

SORT_KEYS = ("profit", "gap", "score")
DEFAULT_SORT = "profit"

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
                  min_profit: int | None = None, burden_of=None) -> list[ScoredListing]:
    """물건종류·지역·최소차익(원, 보수 기준)·(내부용) 최소점수 필터.

    burden_of: 물건 → 인수금액(원). 주어지면 최소차익 비교도 인수 차감 후 값으로
    (감사 2026-07-10: 필터·정렬은 저장 차익, 화면은 차감 차익 — 불일치 해소).
    """
    out = items
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
