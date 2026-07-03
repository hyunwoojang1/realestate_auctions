"""채점 결과 필터·정렬 (순수 함수 — 테스트 용이, CLI·웹 양쪽에서 재사용).

기본 정렬 = 보수 차익(profit_low, 없으면 기준 차익 폴백 — T7). 갭률(gap) 토글 지원.
점수(score)는 UI에서 제거됐지만 내부/백테스트 호환을 위해 정렬 키로는 남겨둔다(사용자 결정 #7).
"""
from __future__ import annotations

from .models import ScoredListing
from .region import matches_region

SORT_KEYS = ("profit", "gap", "score")
DEFAULT_SORT = "profit"


def decision_profit(s: ScoredListing) -> int | None:
    """판단용 차익 — 보수 차익(profit_low) 우선, 없으면(레거시 행) 기준 차익.

    (T7, 문서 15장 7단계) 사용자에게 보이는 '차익 큰 순'과 필터는 보수 가격 기준이다.
    """
    return s.profit_low if s.profit_low is not None else s.expected_profit


def apply_filters(items: list[ScoredListing], min_score: float | None = None,
                  property_type: str | None = None, region: str | None = None,
                  min_profit: int | None = None) -> list[ScoredListing]:
    """물건종류·지역·최소차익(원, 보수 기준)·(내부용) 최소점수 필터."""
    out = items
    if min_profit is not None:
        out = [s for s in out
               if decision_profit(s) is not None and decision_profit(s) >= min_profit]
    if min_score is not None:   # 내부/API 하위호환용 — UI는 min_profit 사용
        out = [s for s in out if s.arb_score is not None and s.arb_score >= min_score]
    if property_type:
        out = [s for s in out if s.property_type == property_type]
    if region:
        out = [s for s in out if matches_region(s.address, region)]
    return out


def sort_items(items: list[ScoredListing], key: str = DEFAULT_SORT) -> list[ScoredListing]:
    """보수 차익 금액(기본)/갭률/점수 내림차순. None은 맨 뒤."""
    if key == "gap":
        return sorted(items, key=lambda s: (s.gap_rate is None, -(s.gap_rate or 0)))
    if key == "score":
        return sorted(items, key=lambda s: (s.arb_score is None, -(s.arb_score or 0)))
    return sorted(items, key=lambda s: (decision_profit(s) is None, -(decision_profit(s) or 0)))
