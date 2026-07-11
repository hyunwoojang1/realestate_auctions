"""주간 차익 TOP N 다이제스트 (V3).

예상차익 금액순 상위 N개를 골라 markdown / HTML 다이제스트를 만든다(사용자 결정 #5).
HTML은 report.to_html(갭미터)을 재사용한다.
"""
from __future__ import annotations

from . import report
from .matcher import SCOPE_SAME_COMPLEX_SAME_AREA, band_confident_basis
from .models import ScoredListing


def _enough_basis(s: ScoredListing) -> bool:
    """(T5) 표본 게이트 — 밴드 실기반 표본수가 추천 기준(기본 5건) 이상인가.

    None(레거시, 게이트 정보 없음)은 하위호환 통과 — 다음 전량 새로고침이 채운다.
    """
    return s.market_sample_basis is None or s.market_sample_basis >= band_confident_basis()


def _decision_profit(s: ScoredListing) -> int | None:
    """추천 판단용 차익 — 보수 차익(profit_low) 우선, 없으면(레거시) 기준 차익.

    (T4, 문서 15장 4단계) 최종 추천 여부는 profit_low 기준으로 판단한다.
    """
    return s.profit_low if s.profit_low is not None else s.expected_profit


def passes_recommend_gates(s: ScoredListing, allow_legacy: bool = True) -> bool:
    """추천 표면 공용 게이트 — digest TOP과 목록 히어로가 같은 기준을 쓴다(T8 감사 HIGH 수정).

    조건: 보수 차익 양수 + '위험' 아님 + 같은 단지·같은 평형 scope + 근거 표본 충분(basis≥5).
    allow_legacy=True: scope=''·basis None(구 DB, 게이트 정보 없음)을 하위호환 통과 — digest용.
    allow_legacy=False: 게이트 정보가 실제로 있고 전부 통과한 물건만 — 히어로(최대 노출 표면)용.
    구 DB에서는 히어로가 아예 안 뜨는 게 맞다(검증 안 된 수치를 헤드라인으로 올리지 않는다).
    """
    p = _decision_profit(s)
    if p is None or p <= 0 or s.grade == "위험":
        return False
    if allow_legacy:
        return s.market_scope in ("", SCOPE_SAME_COMPLEX_SAME_AREA) and _enough_basis(s)
    return (s.profit_low is not None
            and s.market_scope == SCOPE_SAME_COMPLEX_SAME_AREA
            and s.market_sample_basis is not None
            and s.market_sample_basis >= band_confident_basis())


def top_listings(scored: list[ScoredListing], n: int = 10,
                 min_profit: int | None = None,
                 badges: dict | None = None) -> list[ScoredListing]:
    """추천 TOP N — 보수 차익(profit_low) 큰 순. min_profit(원)도 보수 차익에 적용.

    (T3) 추천 표면이므로 비교군 scope 게이트 적용 — '같은 단지·같은 평형' 표본으로 추정한
    물건만 인정한다. 인접평형·법정동 폴백 시세는 참고치일 뿐, 차익 추천의 근거가 될 수 없다.
    ""(레거시, scope 미기록 구 데이터)는 하위호환으로 통과시킨다.
    (T4) 정렬·하한 필터 모두 보수 차익 기준 — 기준가 차익이 커 보여도 하한가 차익이 작으면 뒤로.
    (T5) 표본 게이트 — 실기반 표본 5건 미만은 낮은 신뢰라 추천에서 제외.
    (T7) '위험'(하드게이트) 등급은 추천 표면에서 제외.
    (재검증 감사 2026-07-11 idx0 CRITICAL) badges 가 주어지면 **명세서 인수 부담(burden)
    물건도 제외** — 실측: TOP10 중 9건이 burden, 확정 인수금 차감만으로 7건 음수 전환.
    '추천'이라는 안전 신호를 인수 위험 물건에 줄 수 없다. 미크롤 물건은 통과(하위호환).
    """
    items = [s for s in scored if passes_recommend_gates(s)]
    if badges:
        items = [s for s in items
                 if (b := badges.get(f"{s.court}|{s.case_no}|{s.item_no}")) is None
                 or b.is_clean]
    if min_profit is not None:
        items = [s for s in items if _decision_profit(s) >= min_profit]
    return sorted(items, key=lambda s: -_decision_profit(s))[:n]


def _basis_note(s: ScoredListing) -> str:
    """근거 문구 — '실거래 N건 기준'. basis 없으면(레거시) 매칭수로 폴백."""
    n = s.market_sample_basis if s.market_sample_basis is not None else s.matched_trades
    return f"실거래 {n}건"


def to_markdown(items: list[ScoredListing], title: str = "이번 주 차익 매물 TOP") -> str:
    """(T7) 보수 가격 기준 차익 중심 표기 + 표본 근거 병기 — 단정 표현 금지."""
    lines = [f"# {title} {len(items)}", ""]
    lines.append("| # | 보수 기준 차익 | 기준 차익 | 갭 | 단지 | 유형 | 최저가 | 근거 | 경고 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    warn_grades = {"권리미확인", "위험", "시세추정불가", "차익없음", "미지원유형"}
    for i, s in enumerate(items, 1):
        warn = s.grade if s.grade in warn_grades else ""
        low = report.won(s.profit_low) if s.profit_low is not None else "—"
        lines.append(
            f"| {i} | {low} | {report.won(s.expected_profit)} | {report.pct(s.gap_rate)} | "
            f"{s.apt_name} | {s.property_type} | {report.won(s.min_bid_price)} | "
            f"{_basis_note(s)} | {warn} |"
        )
    lines.append("")
    lines.append("> 보수 기준 차익 = 검증 하한가 − (최저입찰가+취득세). 변동비(명도·수리·인수) 제외. "
                 "권리 확인 완료 전까지 최종 판단 금지. 투자판단 보조이며 전문가 상담을 대체하지 않습니다.")
    return "\n".join(lines)
