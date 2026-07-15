#!/usr/bin/env python
"""차익 변동 알림 — 직전 스냅샷 대비 차익 임계 돌파/스코어 상승/유찰을 감지.

  python run_alerts.py
첫 실행은 기준선(스냅샷) 저장만. 이후 실행부터 직전 대비 변동을 콘솔+evidence/alerts.txt로.
워치리스트(data/watchlist.json)가 있으면 관심물건만 대상.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

from src import store, watchlist  # noqa: E402

EVID = ROOT / "evidence"


def _scored() -> list:
    # (감사 2026-07-15) 알림은 실제 서빙 DB를 봐야 한다. pipeline.run() 무인자는 샘플 fixture
    # 6건만 읽어, 기준선 저장 후 매 실행이 동일 결과 → detect_changes 가 영구 '변동 0건'이었다.
    conn = store.connect(os.environ.get("AUCTION_DB", "auction.db"))
    try:
        return store.load_scored(conn)
    finally:
        conn.close()


def main() -> int:
    current = watchlist.snapshot_from_scored(_scored())
    prev = watchlist.load_snapshot()
    wl = watchlist.load_watchlist() or None

    if not prev:
        watchlist.save_snapshot(current)
        print("기준선(스냅샷) 저장 완료. 다음 실행부터 변동 알림이 표시됩니다.")
        return 0

    events = watchlist.detect_changes(prev, current, wl)
    scope = f"관심물건 {len(wl)}건" if wl else "전체"
    lines = [f"[차익 알림] 대상: {scope} · 감지 {len(events)}건"]
    for e in events:
        lines.append(f"  • {e['type']} — {e['apt_name']} ({e['case_no']}): {e['detail']}")
    if not events:
        lines.append("  (변동 없음)")
    out = "\n".join(lines)
    print(out)

    EVID.mkdir(exist_ok=True)
    (EVID / "alerts.txt").write_text(out + "\n", encoding="utf-8")
    watchlist.save_snapshot(current)
    print(f"\n증거: {EVID / 'alerts.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
