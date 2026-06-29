"""관심물건(워치리스트) + 차익 변동 알림 (V2).

직전 스냅샷(score_snapshot.json) 대비 현재 채점 결과를 비교해
 - 차익 임계 돌파(스코어가 임계 미만→이상)
 - 스코어 상승
 - 최저가 하락(유찰)
을 감지한다. detect_changes는 순수함수(테스트 용이). 워치리스트가 있으면 관심물건만 대상.
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import ScoredListing

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
WATCHLIST_PATH = DATA / "watchlist.json"
SNAPSHOT_PATH = DATA / "score_snapshot.json"
DEFAULT_THRESHOLD = 80.0


def snapshot_from_scored(scored: list[ScoredListing]) -> dict:
    return {
        s.case_no: {
            "arb_score": s.arb_score, "min_bid_price": s.min_bid_price,
            "apt_name": s.apt_name, "grade": s.grade,
        }
        for s in scored
    }


def load_snapshot(path: str | Path | None = None) -> dict:
    p = Path(path) if path else SNAPSHOT_PATH
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_snapshot(snap: dict, path: str | Path | None = None) -> None:
    p = Path(path) if path else SNAPSHOT_PATH
    p.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")


def load_watchlist(path: str | Path | None = None) -> set[str]:
    p = Path(path) if path else WATCHLIST_PATH
    return set(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else set()


def add_watch(case_no: str, path: str | Path | None = None) -> set[str]:
    wl = load_watchlist(path)
    wl.add(case_no)
    p = Path(path) if path else WATCHLIST_PATH
    p.write_text(json.dumps(sorted(wl), ensure_ascii=False, indent=2), encoding="utf-8")
    return wl


def remove_watch(case_no: str, path: str | Path | None = None) -> set[str]:
    wl = load_watchlist(path)
    wl.discard(case_no)
    p = Path(path) if path else WATCHLIST_PATH
    p.write_text(json.dumps(sorted(wl), ensure_ascii=False, indent=2), encoding="utf-8")
    return wl


def _score(d: dict) -> float:
    return d.get("arb_score") or 0.0


def detect_changes(prev: dict, current: dict, watchlist: set[str] | None = None,
                   threshold: float = DEFAULT_THRESHOLD) -> list[dict]:
    """직전(prev) 대비 현재(current) 변동 이벤트 목록. 둘 다 case_no→{arb_score,min_bid_price,...}."""
    events: list[dict] = []
    cases = [c for c in current if (watchlist is None or c in watchlist)]
    for c in cases:
        cur = current[c]
        p = prev.get(c)
        name = cur.get("apt_name", c)
        if p is None:
            continue
        ps, cs = _score(p), _score(cur)
        if ps < threshold <= cs:
            events.append({"case_no": c, "apt_name": name, "type": "차익 임계 돌파",
                           "detail": f"스코어 {ps:.0f}→{cs:.0f} (≥{threshold:.0f})"})
        elif cs > ps:
            events.append({"case_no": c, "apt_name": name, "type": "스코어 상승",
                           "detail": f"스코어 {ps:.0f}→{cs:.0f}"})
        pm, cm = p.get("min_bid_price", 0), cur.get("min_bid_price", 0)
        if cm and pm and cm < pm:
            events.append({"case_no": c, "apt_name": name, "type": "최저가 하락(유찰)",
                           "detail": f"{pm / 1e8:.2f}억→{cm / 1e8:.2f}억"})
    return events
