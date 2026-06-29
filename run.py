#!/usr/bin/env python
"""auction-arbitrage PoC 실행 엔트리.

  python run.py                # 샘플 데이터로 차익 큐레이션
  python run.py --live --ym 202605   # 국토부 라이브(키 필요, F10)
결과: 콘솔 랭킹표 + evidence/result.csv + evidence/result.html
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src import pipeline, report, store  # noqa: E402

EVID = ROOT / "evidence"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="경매 최저가 vs 실거래 시세 차익 큐레이션 PoC")
    ap.add_argument("--live", action="store_true", help="국토부 라이브 API 사용(MOLIT_API_KEY 필요)")
    ap.add_argument("--ym", help="조회 연월 YYYYMM (라이브 전용)")
    ap.add_argument("--db", default=str(ROOT / "auction.db"), help="SQLite 경로")
    args = ap.parse_args(argv)

    # .env 로드(있으면)
    _load_dotenv(ROOT / ".env")

    mode = "라이브(국토부 API)" if args.live else "샘플 데이터"
    print(f"▶ 모드: {mode}\n")

    scored = pipeline.run(use_live=args.live, deal_ymd=args.ym)

    conn = store.connect(args.db)
    n = store.upsert(conn, scored)
    print(report.to_console(scored))
    print(f"\n저장: {n}건 → {args.db}")

    EVID.mkdir(exist_ok=True)
    csv_path = report.to_csv(scored, EVID / "result.csv")
    html_path = report.to_html(scored, EVID / "result.html")
    print(f"증거: {csv_path}")
    print(f"증거: {html_path}")
    return 0


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    import os
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


if __name__ == "__main__":
    raise SystemExit(main())
