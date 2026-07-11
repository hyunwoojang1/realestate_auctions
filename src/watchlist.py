"""관심물건(워치리스트) + 차익 변동 알림 (V2).

직전 스냅샷(score_snapshot.json) 대비 현재 채점 결과를 비교해
 - 차익 임계 돌파(스코어가 임계 미만→이상)
 - 스코어 상승
 - 최저가 하락(유찰)
을 감지한다. detect_changes는 순수함수(테스트 용이). 워치리스트가 있으면 관심물건만 대상.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from .models import ScoredListing

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
WATCHLIST_PATH = DATA / "watchlist.json"
SNAPSHOT_PATH = DATA / "score_snapshot.json"
DEFAULT_THRESHOLD = 80.0


def watchlist_path() -> Path:
    """워치리스트 저장 경로 — AUCTION_WATCHLIST env로 오버라이드(테스트·운영 분리)."""
    return Path(os.environ.get("AUCTION_WATCHLIST") or WATCHLIST_PATH)


def snapshot_path() -> Path:
    """스냅샷 경로 — AUCTION_SNAPSHOT env로 오버라이드."""
    return Path(os.environ.get("AUCTION_SNAPSHOT") or SNAPSHOT_PATH)


def wl_key(court: str, case_no: str, item_no: str = "") -> str:
    """관심물건·스냅샷 식별 키 — (court|case_no|item_no) 복합.

    감사(2026-07-10) 확정: 사건번호는 법원별 독립 채번 + 한 사건에 물건 여러 개 —
    case_no 단독 키는 동명 사건 67건·다물건 사건에서 임의 물건을 표시/알림하는 구조였다.
    """
    return f"{court}|{case_no}|{item_no or ''}"


def is_watched(watched: set[str], s: ScoredListing) -> bool:
    """복합키 우선, 레거시 항목(구 파일의 bare case_no)은 하위호환 매칭."""
    return wl_key(s.court, s.case_no, s.item_no) in watched or s.case_no in watched


def snapshot_from_scored(scored: list[ScoredListing]) -> dict:
    return {
        wl_key(s.court, s.case_no, s.item_no): {
            "arb_score": s.arb_score, "min_bid_price": s.min_bid_price,
            "apt_name": s.apt_name, "grade": s.grade, "case_no": s.case_no,
        }
        for s in scored
    }


def _atomic_write(p: Path, text: str) -> None:
    """임시파일에 쓰고 os.replace로 원자 교체 — 동시 토글/크래시 중 파일 손상·유실 방지.

    같은 디렉터리에 tmp를 만들어야 os.replace가 원자적(동일 볼륨). 실패 시 tmp 정리.
    """
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _safe_load_json(p: Path, default):
    """(값, corrupted) 반환. 없으면 (default, False), 정상 (val, False),
    손상/읽기실패면 로그 남기고 (default, True) — 조용한 500 대신 원인 있는 폴백(침묵실패 방지)."""
    if not p.exists():
        return default, False
    try:
        return json.loads(p.read_text(encoding="utf-8")), False
    except (json.JSONDecodeError, OSError) as e:
        logger.error("워치리스트/스냅샷 파일 손상 — 빈 값 폴백: %s (%s)", p, e)
        return default, True


def load_snapshot(path: str | Path | None = None) -> dict:
    """직전 스냅샷 dict. 없거나 손상이면 {} (손상 시 로그). 손상여부까지 필요하면 load_snapshot_status."""
    p = Path(path) if path else SNAPSHOT_PATH
    return _safe_load_json(p, {})[0]


def load_snapshot_status(path: str | Path | None = None) -> tuple[dict, bool]:
    """(스냅샷 dict, corrupted). corrupted=True면 파일이 있으나 파싱 실패."""
    p = Path(path) if path else SNAPSHOT_PATH
    return _safe_load_json(p, {})


def save_snapshot(snap: dict, path: str | Path | None = None) -> None:
    p = Path(path) if path else SNAPSHOT_PATH
    _atomic_write(p, json.dumps(snap, ensure_ascii=False, indent=2))


def load_watchlist(path: str | Path | None = None) -> set[str]:
    """관심물건 case_no 집합. 없거나 손상이면 빈 집합(손상 시 로그, 500 대신 폴백)."""
    p = Path(path) if path else WATCHLIST_PATH
    return set(_safe_load_json(p, [])[0])


def load_watchlist_status(path: str | Path | None = None) -> tuple[set[str], bool]:
    """(case_no 집합, corrupted). 웹이 '진짜 빈 목록' vs '파일 손상'을 구분해 배너 표시."""
    p = Path(path) if path else WATCHLIST_PATH
    v, corrupt = _safe_load_json(p, [])
    return set(v), corrupt


def add_watch(case_no: str, path: str | Path | None = None) -> set[str]:
    wl = load_watchlist(path)
    wl.add(case_no)
    p = Path(path) if path else WATCHLIST_PATH
    _atomic_write(p, json.dumps(sorted(wl), ensure_ascii=False, indent=2))
    return wl


def remove_watch(case_no: str, path: str | Path | None = None) -> set[str]:
    wl = load_watchlist(path)
    wl.discard(case_no)
    p = Path(path) if path else WATCHLIST_PATH
    _atomic_write(p, json.dumps(sorted(wl), ensure_ascii=False, indent=2))
    return wl


def _score(d: dict) -> float:
    return d.get("arb_score") or 0.0


def detect_changes(prev: dict, current: dict, watchlist: set[str] | None = None,
                   threshold: float = DEFAULT_THRESHOLD) -> list[dict]:
    """직전(prev) 대비 현재(current) 변동 이벤트 목록. 둘 다 case_no→{arb_score,min_bid_price,...}."""
    events: list[dict] = []
    # watchlist 엔트리는 복합키 또는 레거시 case_no — 둘 다 매칭(하위호환).
    def _watched(key: str, cur: dict) -> bool:
        if watchlist is None:
            return True
        return key in watchlist or cur.get("case_no", key) in watchlist

    cases = [c for c in current if _watched(c, current[c])]
    for c in cases:
        cur = current[c]
        # 표시·링크용은 실제 사건번호(복합키가 아니라) — 템플릿 /property/<case_no> 링크 정합.
        case_no = cur.get("case_no", c)
        # 구 스냅샷(bare case_no 키) 폴백 — 복합키 전환 직후 첫 비교가 끊기지 않게(하위호환).
        p = prev.get(c) or prev.get(case_no)
        name = cur.get("apt_name") or case_no
        if p is None:
            continue
        ps, cs = _score(p), _score(cur)
        if ps < threshold <= cs:
            events.append({"case_no": case_no, "apt_name": name, "type": "차익 임계 돌파",
                           "detail": f"스코어 {ps:.0f}→{cs:.0f} (≥{threshold:.0f})"})
        elif cs > ps:
            events.append({"case_no": case_no, "apt_name": name, "type": "스코어 상승",
                           "detail": f"스코어 {ps:.0f}→{cs:.0f}"})
        pm, cm = p.get("min_bid_price", 0), cur.get("min_bid_price", 0)
        if cm and pm and cm < pm:
            events.append({"case_no": case_no, "apt_name": name, "type": "최저가 하락(유찰)",
                           "detail": f"{pm / 1e8:.2f}억→{cm / 1e8:.2f}억"})
    return events
