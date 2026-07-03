"""호가(매물 부르는 값) 스텁 — 밴드 검증 보조자료 (T6).

호가는 실제 체결가가 아니라 매도자의 희망 가격이다(문서 11장). 따라서:
  - 밴드(실거래 기반)의 주재료가 아니라 **점으로 표시하는 검증 보조자료**로만 쓴다.
  - 호가가 밴드보다 아래면 "실거래 밴드가 과대일 수 있음"을 경고한다.
  - 호가가 밴드보다 위면 "매도자 기대가 높다"는 참고 정보일 뿐이다.

데이터 수급: 네이버 부동산 등은 약관상 무단 수집 금지(문서 12장) — **크롤하지 않는다**.
합법적 수급 경로(제휴/API/수동 입력)는 운영자 결정 대기(harness/QUESTIONS.md Q2).
그 전까지는 로컬 JSON 파일(data/asking_prices.json)에 수동 입력된 값만 읽으며,
파일이 없으면 조용히 빈 상태 — UI는 아무것도 표시하지 않는다(스텁의 계약).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_ASKING_PATH = Path(__file__).resolve().parent.parent / "data" / "asking_prices.json"

POS_BELOW = "below"    # 밴드 하한 아래 — 밴드 과대 가능성 신호
POS_INSIDE = "inside"  # 밴드 안
POS_ABOVE = "above"    # 밴드 위 — 매도자 기대 높음(참고)


@dataclass(frozen=True)
class AskingPrice:
    """호가 1건 — 수동 입력 스텁. observed_at은 관측일(YYYY-MM-DD)."""
    price: int
    label: str = ""        # 예: "3층 남향", "부동산 A"
    observed_at: str = ""


def load_asking_prices(path: str | Path | None = None) -> dict[str, list[AskingPrice]]:
    """case_no → 호가 리스트. 파일 없음/손상 시 빈 dict(스텁 계약: 없으면 무표시).

    파일 형식: {"2025타경1234": [{"price": 850000000, "label": "...", "observed_at": "..."}]}
    """
    p = Path(path) if path else DEFAULT_ASKING_PATH
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        # 손상 파일은 조용히 무시하지 않고 로그를 남긴다(침묵실패 방지) — 단 UI는 빈 상태.
        logger.error("호가 파일 손상/읽기 실패(%s): %s", p, e)
        return {}
    if not isinstance(raw, dict):
        logger.error("호가 파일 형식 오류(%s): 최상위가 dict가 아님", p)
        return {}
    out: dict[str, list[AskingPrice]] = {}
    for case_no, rows in raw.items():
        if not isinstance(rows, list):
            logger.warning("호가 파일(%s) %s: 리스트가 아님 — 건너뜀", p, case_no)
            continue
        items = []
        for r in rows:
            price = r.get("price") if isinstance(r, dict) else None
            # 수동 JSON 입력 친화: int·float 허용(bool은 int 서브클래스라 명시 배제).
            if isinstance(price, (int, float)) and not isinstance(price, bool) and price > 0:
                items.append(AskingPrice(price=int(price), label=str(r.get("label", "")),
                                         observed_at=str(r.get("observed_at", ""))))
            else:
                # 운영자가 수동 입력한 행이 왜 안 뜨는지 알 수 있게 로그(침묵 skip 방지).
                logger.warning("호가 파일(%s) %s: 무효 행 건너뜀 %r", p, case_no, r)
        if items:
            out[case_no] = items
    return out


def classify_vs_band(price: int, band_low: int, band_high: int) -> str:
    """호가 1건의 밴드 대비 위치 — below / inside / above."""
    if price < band_low:
        return POS_BELOW
    if price > band_high:
        return POS_ABOVE
    return POS_INSIDE


def asking_points(askings: list[AskingPrice], band_low: int | None,
                  band_high: int | None) -> list[dict]:
    """상세 화면용 점 목록 — [{price, label, observed_at, position}]. 밴드 없으면 위치 미판정."""
    pts = []
    for a in sorted(askings, key=lambda x: x.price, reverse=True):
        pos = (classify_vs_band(a.price, band_low, band_high)
               if band_low is not None and band_high is not None else "")
        pts.append({"price": a.price, "label": a.label,
                    "observed_at": a.observed_at, "position": pos})
    return pts


def band_overstated(askings: list[AskingPrice], band_low: int | None) -> bool:
    """실거래 밴드 과대 가능성 — 최저 호가가 검증 하한가보다 낮으면 True(문서 11장 3항).

    지금 시장에서 부르는 값조차 실거래 하한가보다 낮다면, 과거 실거래로 만든 밴드가
    현재 시장보다 높게 잡혀 있을 수 있다 → 차익이 과대평가될 위험 경고.
    """
    if band_low is None or not askings:
        return False
    return min(a.price for a in askings) < band_low
