#!/usr/bin/env python
"""auction-arbitrage PoC 실행 엔트리.

  python run.py                          # 샘플 데이터로 차익 큐레이션
  python run.py --min-score 80           # 차익 스코어 80+ 만
  python run.py --type 아파트 --region 서울  # 필터
  python run.py --sort profit --json     # 예상차익순, JSON 출력
  python run.py --live --ym 202605       # 국토부 라이브(키 필요, F10)
결과: 콘솔 랭킹표 + evidence/result.csv + evidence/result.html (--json이면 stdout JSON)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src import pipeline, query, report, store  # noqa: E402

EVID = ROOT / "evidence"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="경매 최저가 vs 실거래 시세 차익 큐레이션 PoC")
    ap.add_argument("--live", action="store_true", help="국토부 라이브 API 사용(MOLIT_API_KEY 필요)")
    ap.add_argument("--ym", help="조회 연월 YYYYMM (라이브 전용)")
    ap.add_argument("--db", default=str(ROOT / "auction.db"), help="SQLite 경로")
    ap.add_argument("--min-score", type=float, default=None, help="차익 스코어 하한 필터")
    ap.add_argument("--type", dest="ptype", default=None, help="물건종류 필터(아파트/오피스텔/다세대 등)")
    ap.add_argument("--region", default=None, help="지역 필터(주소 prefix, 예: 서울/경기/부산)")
    ap.add_argument("--sort", choices=query.SORT_KEYS, default="score", help="정렬 기준(score/profit/gap)")
    ap.add_argument("--json", action="store_true", help="결과를 JSON으로 stdout 출력")
    args = ap.parse_args(argv)

    # .env 로드(있으면)
    _load_dotenv(ROOT / ".env")

    if not args.json:
        mode = "라이브(국토부 API)" if args.live else "샘플 데이터"
        print(f"▶ 모드: {mode}\n")

    scored = pipeline.run(use_live=args.live, deal_ymd=args.ym)

    conn = store.connect(args.db)
    n = store.upsert(conn, scored)   # 전체 저장

    view = query.sort_items(
        query.apply_filters(scored, args.min_score, args.ptype, args.region),
        args.sort,
    )

    if args.json:
        print(report.to_json(view))
        return 0

    print(report.to_console(view))
    print(f"\n저장(전체): {n}건 · 표시(필터 후): {len(view)}건 → {args.db}")

    EVID.mkdir(exist_ok=True)
    csv_path = report.to_csv(view, EVID / "result.csv")
    html_path = report.to_html(view, EVID / "result.html")
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
