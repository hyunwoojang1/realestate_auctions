"""채점 결과 필터·정렬 (순수 함수 — 테스트 용이, CLI·웹 양쪽에서 재사용)."""
from __future__ import annotations

from .models import ScoredListing

SORT_KEYS = ("score", "profit", "gap")


def apply_filters(items: list[ScoredListing], min_score: float | None = None,
                  property_type: str | None = None, region: str | None = None) -> list[ScoredListing]:
    """min_score 이상 + 물건종류 일치 + 주소 prefix(지역) 일치만 남긴다."""
    out = items
    if min_score is not None:
        out = [s for s in out if s.arb_score is not None and s.arb_score >= min_score]
    if property_type:
        out = [s for s in out if s.property_type == property_type]
    if region:
        out = [s for s in out if s.address.startswith(region)]
    return out


def sort_items(items: list[ScoredListing], key: str = "score") -> list[ScoredListing]:
    """차익 스코어(기본)/예상차익/갭률 내림차순. None은 맨 뒤."""
    if key == "profit":
        return sorted(items, key=lambda s: (s.expected_profit is None, -(s.expected_profit or 0)))
    if key == "gap":
        return sorted(items, key=lambda s: (s.gap_rate is None, -(s.gap_rate or 0)))
    return sorted(items, key=lambda s: (s.arb_score is None, -(s.arb_score or 0)))
