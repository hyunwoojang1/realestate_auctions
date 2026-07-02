"""채점 결과 필터·정렬 (순수 함수 — 테스트 용이, CLI·웹 양쪽에서 재사용).

기본 정렬 = 예상차익 금액(profit). 갭률(gap) 토글 지원. 점수(score)는 UI에서
제거됐지만 내부/백테스트 호환을 위해 정렬 키로는 남겨둔다(사용자 결정 #7).
"""
from __future__ import annotations

from .models import ScoredListing
from .region import matches_region

SORT_KEYS = ("profit", "gap", "score")
DEFAULT_SORT = "profit"


def apply_filters(items: list[ScoredListing], min_score: float | None = None,
                  property_type: str | None = None, region: str | None = None,
                  min_profit: int | None = None) -> list[ScoredListing]:
    """물건종류·지역·최소차익(원)·(내부용) 최소점수 필터."""
    out = items
    if min_profit is not None:
        out = [s for s in out if s.expected_profit is not None and s.expected_profit >= min_profit]
    if min_score is not None:   # 내부/API 하위호환용 — UI는 min_profit 사용
        out = [s for s in out if s.arb_score is not None and s.arb_score >= min_score]
    if property_type:
        out = [s for s in out if s.property_type == property_type]
    if region:
        out = [s for s in out if matches_region(s.address, region)]
    return out


def sort_items(items: list[ScoredListing], key: str = DEFAULT_SORT) -> list[ScoredListing]:
    """예상차익 금액(기본)/갭률/점수 내림차순. None은 맨 뒤."""
    if key == "gap":
        return sorted(items, key=lambda s: (s.gap_rate is None, -(s.gap_rate or 0)))
    if key == "score":
        return sorted(items, key=lambda s: (s.arb_score is None, -(s.arb_score or 0)))
    return sorted(items, key=lambda s: (s.expected_profit is None, -(s.expected_profit or 0)))
