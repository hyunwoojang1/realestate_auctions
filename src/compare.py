"""물건 비교(B6) — 선택 로직 순수 함수. 웹 /compare가 재사용.

레퍼런스(auction.com/Zillow) 'compare' 모방. 외부 호출 0 — 이미 채점된 결과만 나란히 놓는다.
관심물건(watchlist) case_no 집합을 그대로 넘기면 '관심물건 비교'가 된다(재사용).
"""
from __future__ import annotations

from .models import ScoredListing

MAX_COMPARE = 4   # 한 화면 비교 상한(레이아웃·인지 부하)


def select_for_compare(scored: list[ScoredListing], case_nos: list[str],
                       max_n: int = MAX_COMPARE) -> list[ScoredListing]:
    """식별자 순서대로 매칭되는 ScoredListing을 최대 max_n개 반환.

    식별자는 복합키("court|case_no|item_no" — 감사 2026-07-10: case_no 단독은 동명 사건
    67건에서 임의 법원 물건을 표시) 또는 레거시 bare case_no(하위호환) 둘 다 지원.
    - 없는 식별자는 조용히 무시(에러 아님 — 만료/오타 흔함).
    - 중복은 1회만. 입력 순서 보존(사용자가 담은 순서).
    """
    by_key = {f"{s.court}|{s.case_no}|{s.item_no}": s for s in scored}
    by_case = {s.case_no: s for s in scored}
    out: list[ScoredListing] = []
    seen: set[str] = set()
    for c in case_nos:
        s = by_key.get(c) if "|" in c else by_case.get(c)
        if s is None:
            continue
        uid = f"{s.court}|{s.case_no}|{s.item_no}"
        if uid not in seen:
            out.append(s)
            seen.add(uid)
            if len(out) >= max_n:
                break
    return out
