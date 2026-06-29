#!/usr/bin/env python
"""백테스트 실행 — 차익 스코어가 실제 수익으로 이어졌는지 검증.

  python run_backtest.py
결과: 콘솔(물건별 실현차익·구간별 캘리브레이션·precision) + evidence/backtest.csv
※ 현재는 합성 낙찰결과(data/backtest_outcomes.json). 실데이터 들어오면 그 파일만 교체.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src import backtest, report  # noqa: E402

EVID = ROOT / "evidence"


def main() -> int:
    rows = backtest.evaluate()
    if not rows:
        print("백테스트할 데이터가 없습니다 (outcomes 매칭 0건).")
        return 0

    print("=== 백테스트: 차익 스코어 → 실현 수익 ===\n")
    print(f"{'단지':<18}{'스코어':>5} {'등급':<10}{'낙찰가':>9}{'매도가':>9}{'실현차익':>10}  적중")
    print("-" * 74)
    for r in sorted(rows, key=lambda x: -x["arb_score"]):
        print(f"{r['apt_name'][:16]:<18}{r['arb_score']:>5.0f} {r['grade']:<10}"
              f"{report.won(r['actual_nakchal']):>9}{report.won(r['realized_sale']):>9}"
              f"{report.won(r['realized_profit']):>10}  {'O' if r['hit'] else 'X'}")

    print("\n=== 스코어 구간별 캘리브레이션 ===")
    print(f"{'구간':<18}{'건수':>5}{'적중률':>8}{'평균실현차익':>13}")
    for c in backtest.calibration(rows):
        hr = "-" if c["hit_rate"] is None else f"{c['hit_rate'] * 100:.0f}%"
        ap = "-" if c["avg_profit"] is None else report.won(c["avg_profit"])
        print(f"{c['bucket']:<18}{c['n']:>5}{hr:>8}{ap:>13}")

    print("\n=== Precision @ 스코어 임계 (그 이상 추천했을 때 실제 수익 비율) ===")
    for t in (80, 60, 40):
        p = backtest.precision_at(rows, t)
        print(f"  스코어 ≥{t}: {'-' if p is None else f'{p * 100:.0f}%'}")

    EVID.mkdir(exist_ok=True)
    csv_path = EVID / "backtest.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n증거: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
