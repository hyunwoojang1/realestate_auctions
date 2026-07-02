"""매각·차익 집계 통계 (B4) — 순수 함수, 채점 결과 목록 기반.

레퍼런스(지지옥션·탱크옥션)의 '매각통계' 기본기능 모방. 단 외부 호출 없이
이미 적재된 채점 결과(ScoredListing)만 집계한다. 웹 /stats·/api/stats 재사용.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable

from .models import ScoredListing
from .region import sido_of

SCORE_BUCKET_WIDTH = 10   # 스코어 히스토그램 구간 폭
FAIL_CAP = 3              # 유찰 횟수 3회 이상은 "3회+"로 묶음


def _avg(vals: Iterable[float | int | None]) -> float | None:
    xs = [v for v in vals if v is not None]
    return (sum(xs) / len(xs)) if xs else None


def overview(items: list[ScoredListing]) -> dict:
    """전체 요약 — 건수·시세추정 성공률·평균 신뢰계수/갭률/차익·차익양수 건수."""
    est_ok = sum(1 for s in items if s.est_market_price is not None)
    return {
        "count": len(items),
        "est_success_rate": (est_ok / len(items)) if items else None,
        "avg_confidence": _avg(s.confidence for s in items),
        "avg_gap_rate": _avg(s.gap_rate for s in items),
        "avg_expected_profit": _avg(s.expected_profit for s in items),
        "positive_profit_count": sum(1 for s in items if (s.expected_profit or 0) > 0),
    }


def _group_rows(items: list[ScoredListing], key: Callable[[ScoredListing], str]) -> list[dict]:
    groups: dict[str, list[ScoredListing]] = defaultdict(list)
    for s in items:
        groups[key(s)].append(s)
    rows = [{
        "name": name,
        "count": len(g),
        "avg_gap_rate": _avg(s.gap_rate for s in g),
        "avg_expected_profit": _avg(s.expected_profit for s in g),
        "top_score": max((s.arb_score for s in g if s.arb_score is not None), default=None),
    } for name, g in groups.items()]
    return sorted(rows, key=lambda r: -r["count"])


def by_property_type(items: list[ScoredListing]) -> list[dict]:
    """용도(아파트/오피스텔/...)별 건수·평균 갭률·평균 차익·최고점 (건수 내림차순)."""
    return _group_rows(items, lambda s: s.property_type or "기타")


def _sido_key(s: ScoredListing) -> str:
    """시도 그룹 키. 실패 '원인'을 구분해 데이터 품질을 드러낸다(침묵실패 방지):
    주소 자체가 비어 파싱 불가('주소없음') vs 주소는 있으나 시도 미인식('기타(시도미인식)').
    """
    if not (s.address or "").strip():
        return "주소없음"
    return sido_of(s.address) or "기타(시도미인식)"


def by_sido(items: list[ScoredListing]) -> list[dict]:
    """시도(주소 prefix)별 집계. 주소없음/시도미인식을 뭉치지 않고 분리(원인 구분)."""
    return _group_rows(items, _sido_key)


def score_distribution(items: list[ScoredListing],
                       width: int = SCORE_BUCKET_WIDTH) -> dict:
    """차익 스코어 히스토그램. 마지막 구간은 상한 100 포함('90~100'). None은 별도 카운트."""
    top = 100 // width - 1   # 마지막 버킷 인덱스
    scored = [s.arb_score for s in items if s.arb_score is not None]
    counts = Counter(min(int(v // width), top) for v in scored)
    buckets = [{
        "label": f"{i * width}~{100 if i == top else i * width + width - 1}",
        "count": counts.get(i, 0),
    } for i in range(top + 1)]
    return {"buckets": buckets, "none_count": len(items) - len(scored)}


def fail_count_distribution(items: list[ScoredListing], cap: int = FAIL_CAP) -> list[dict]:
    """유찰 횟수 분포 — cap회 이상은 'cap회+'로 묶음."""
    counts = Counter(min(s.fail_count, cap) for s in items)
    return [{
        "label": f"{n}회+" if n == cap else f"{n}회",
        "count": counts.get(n, 0),
    } for n in range(cap + 1)]


def grade_distribution(items: list[ScoredListing]) -> list[dict]:
    """등급(차익 유력/양호/위험/...) 분포 — 많은 순."""
    return [{"name": g, "count": n} for g, n in Counter(s.grade for s in items).most_common()]


def summarize(items: list[ScoredListing]) -> dict:
    """/stats·/api/stats 가 쓰는 전체 묶음."""
    return {
        "overview": overview(items),
        "by_property_type": by_property_type(items),
        "by_sido": by_sido(items),
        "score_distribution": score_distribution(items),
        "fail_count_distribution": fail_count_distribution(items),
        "grade_distribution": grade_distribution(items),
    }
