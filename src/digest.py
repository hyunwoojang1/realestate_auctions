"""주간 차익 TOP N 다이제스트 (V3).

차익 스코어순 상위 N개를 골라 markdown / HTML 다이제스트를 만든다.
HTML은 report.to_html(갭미터·스코어뱃지)을 재사용한다.
"""
from __future__ import annotations

from . import query, report
from .models import ScoredListing


def top_listings(scored: list[ScoredListing], n: int = 10,
                 min_score: float | None = None) -> list[ScoredListing]:
    """차익 스코어 있는 물건을 스코어순 정렬해 상위 N개. min_score 지정 시 하한 필터."""
    items = [s for s in scored if s.arb_score is not None]
    if min_score is not None:
        items = [s for s in items if s.arb_score >= min_score]
    return query.sort_items(items, "score")[:n]


def to_markdown(items: list[ScoredListing], title: str = "이번 주 차익 매물 TOP") -> str:
    lines = [f"# {title} {len(items)}", ""]
    lines.append("| # | 스코어 | 등급 | 단지 | 유형 | 최저가 | 추정시세 | 예상차익 | 갭 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for i, s in enumerate(items, 1):
        lines.append(
            f"| {i} | {s.arb_score:.0f} | {s.grade} | {s.apt_name} | {s.property_type} | "
            f"{report.won(s.min_bid_price)} | {report.won(s.est_market_price)} | "
            f"{report.won(s.expected_profit)} | {report.pct(s.gap_rate)} |"
        )
    lines.append("")
    lines.append("> 차익·권리는 투자판단 보조이며 전문가 상담을 대체하지 않습니다. (PoC 샘플 데이터)")
    return "\n".join(lines)
