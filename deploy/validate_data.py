"""데이터 품질 게이트 실행기 — 수동 점검·CI·크론에서 호출.

사용:
    PYTHONUTF8=1 .venv/Scripts/python.exe -m deploy.validate_data            # auction.db 전 게이트
    PYTHONUTF8=1 .venv/Scripts/python.exe -m deploy.validate_data --db X.db

종료코드: 0=전 게이트 PASS, 1=FAIL 존재(파이프라인에서 미러 차단에 사용).
"""
from __future__ import annotations

import argparse
import sys

from src import data_gates, store


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="경매 데이터 품질 게이트")
    ap.add_argument("--db", default="auction.db")
    args = ap.parse_args(argv)
    conn = store.connect(args.db)
    try:
        results = data_gates.run_gates(conn)
    finally:
        conn.close()
    print(data_gates.report(results))
    for r in results:
        if not r.ok and r.samples:
            print(f"  ↳ {r.name} 사례: {', '.join(map(str, r.samples))}")
    return 0 if data_gates.all_pass(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
