"""주간 차익 TOP N 다이제스트 (V3).

예상차익 금액순 상위 N개를 골라 markdown / HTML 다이제스트를 만든다(사용자 결정 #5).
HTML은 report.to_html(갭미터)을 재사용한다.
"""
from __future__ import annotations

from . import query, report
from .matcher import SCOPE_SAME_COMPLEX_SAME_AREA
from .models import ScoredListing


def top_listings(scored: list[ScoredListing], n: int = 10,
                 min_profit: int | None = None) -> list[ScoredListing]:
    """예상차익 있는 물건을 금액순 정렬해 상위 N개. min_profit(원) 지정 시 하한 필터.

    (T3) 추천 표면이므로 비교군 scope 게이트 적용 — '같은 단지·같은 평형' 표본으로 추정한
    물건만 인정한다. 인접평형·법정동 폴백 시세는 참고치일 뿐, 차익 추천의 근거가 될 수 없다.
    ""(레거시, scope 미기록 구 데이터)는 하위호환으로 통과시킨다.
    """
    items = [s for s in scored if s.expected_profit is not None
             and s.market_scope in ("", SCOPE_SAME_COMPLEX_SAME_AREA)]
    if min_profit is not None:
        items = [s for s in items if s.expected_profit >= min_profit]
    return query.sort_items(items, "profit")[:n]


def to_markdown(items: list[ScoredListing], title: str = "이번 주 차익 매물 TOP") -> str:
    lines = [f"# {title} {len(items)}", ""]
    lines.append("| # | 예상차익 | 갭 | 단지 | 유형 | 최저가 | 추정시세 | 경고 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    warn_grades = {"권리미확인", "위험", "시세추정불가", "차익없음", "미지원유형"}
    for i, s in enumerate(items, 1):
        warn = s.grade if s.grade in warn_grades else ""
        lines.append(
            f"| {i} | {report.won(s.expected_profit)} | {report.pct(s.gap_rate)} | "
            f"{s.apt_name} | {s.property_type} | {report.won(s.min_bid_price)} | "
            f"{report.won(s.est_market_price)} | {warn} |"
        )
    lines.append("")
    lines.append("> 예상차익 = 추정시세 − (최저입찰가+취득세). 변동비(명도·수리·인수) 제외. "
                 "투자판단 보조이며 전문가 상담을 대체하지 않습니다.")
    return "\n".join(lines)
