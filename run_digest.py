#!/usr/bin/env python
"""주간 차익 TOP N 다이제스트 생성.

  python run_digest.py                 # 예상차익 금액순 TOP 10
  python run_digest.py --n 5 --min-profit 50000000
결과: 콘솔(markdown) + evidence/digest.md + evidence/digest.html
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src import digest, report, store  # noqa: E402

EVID = ROOT / "evidence"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="주간 차익 TOP N 다이제스트")
    ap.add_argument("--n", type=int, default=10, help="상위 N개 (기본 10)")
    ap.add_argument("--min-profit", dest="min_profit", type=int, default=None,
                    help="예상차익 하한(원)")
    args = ap.parse_args(argv)

    # (감사 2026-07-15) 실제 서빙 DB를 대상으로 — 무인자 pipeline.run()은 샘플 6건만 봐서
    # 주간 TOP 이 항상 같은 샘플이었다.
    conn = store.connect(os.environ.get("AUCTION_DB", "auction.db"))
    try:
        scored = store.load_scored(conn)
    finally:
        conn.close()
    items = digest.top_listings(scored, n=args.n, min_profit=args.min_profit)
    md = digest.to_markdown(items)
    print(md)

    EVID.mkdir(exist_ok=True)
    (EVID / "digest.md").write_text(md, encoding="utf-8")
    report.to_html(items, EVID / "digest.html")
    print(f"\n증거: {EVID / 'digest.md'}")
    print(f"증거: {EVID / 'digest.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
