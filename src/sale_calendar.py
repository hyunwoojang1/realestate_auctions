"""경매 일정 캘린더 (B1) — 매각기일 그룹핑 순수 함수.

레퍼런스(지지옥션)의 '경매 캘린더' 모방. 이미 적재된 채점 결과의 sale_date만 사용
(외부 호출 0). 웹 /calendar 에서 재사용. stdlib calendar와의 충돌을 피해 sale_calendar.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from .models import ScoredListing

WEEKDAYS_KR = ("월", "화", "수", "목", "금", "토", "일")


def weekday_kr(d: str) -> str:
    """'2026-07-15' → '수'. 파싱 실패는 빈 문자열(캘린더 표시용 — 실패로 죽지 않음)."""
    try:
        return WEEKDAYS_KR[date.fromisoformat(d).weekday()]
    except ValueError:
        return ""


def _valid_iso(d: str) -> bool:
    """ISO(YYYY-MM-DD) 형식 검증 — 크롤 원문이 비정상 형식으로 남는 경우(ymd_to_iso 원문 통과)
    문자열 비교·월 슬라이스가 조용히 틀리는 것을 막는다."""
    try:
        date.fromisoformat(d)
    except ValueError:
        return False
    return True


def _dated(items: list[ScoredListing]) -> list[ScoredListing]:
    return [s for s in items if s.sale_date and _valid_iso(s.sale_date)]


def unknown_date_count(items: list[ScoredListing]) -> int:
    """매각기일 미상(빈 값·비ISO 형식) 건수 — 캘린더에서 빠지는 물건의 침묵 누락 방지용 표시."""
    return sum(1 for s in items if not (s.sale_date and _valid_iso(s.sale_date)))


def group_by_date(items: list[ScoredListing]) -> list[dict]:
    """기일별 그룹 [{date, items}] — 날짜 오름차순, 그룹 안은 스코어 내림차순."""
    groups: dict[str, list[ScoredListing]] = defaultdict(list)
    for s in _dated(items):
        groups[s.sale_date].append(s)
    return [{
        "date": d,
        "items": sorted(groups[d], key=lambda s: (s.arb_score is None, -(s.arb_score or 0))),
    } for d in sorted(groups)]


def split_upcoming(items: list[ScoredListing], today: str,
                   now=None) -> tuple[list[ScoredListing], list[ScoredListing]]:
    """(예정, 과거) 분리 — 오늘 기일은 입찰 마감(개시+버퍼) 전이면 예정, 지났으면 과거로.
    기일 미상은 양쪽 모두 제외. now 미지정 시 bidding_closed가 현재시각을 쓴다(당일 마감 판정)."""
    from .query import bidding_closed  # 지연 import(순환 회피)
    up: list[ScoredListing] = []
    past: list[ScoredListing] = []
    for s in _dated(items):
        if s.sale_date > today:
            up.append(s)
        elif s.sale_date < today:
            past.append(s)
        else:  # 오늘 기일 — 시각 컷오프로 예정/과거 판정
            (past if bidding_closed(s, now) else up).append(s)
    return up, past


def month_groups(items: list[ScoredListing]) -> list[dict]:
    """월 묶음 [{month, count, days:[{date, items}]}] — 캘린더 뷰 골격."""
    months: dict[str, list[dict]] = defaultdict(list)
    for day in group_by_date(items):
        months[day["date"][:7]].append(day)
    return [{
        "month": m,
        "count": sum(len(d["items"]) for d in months[m]),
        "days": months[m],
    } for m in sorted(months)]
