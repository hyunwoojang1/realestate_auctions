"""C1 교정 재처리 — naver_cache.json의 real 원본으로 naver_real_trades·complexes를 재구축.

크롤(원본 저장, C1)과 upsert 로직이 분리돼 있어, 취소거래 판정 버그(deleteYn 'O') 수정 후
**재크롤 없이** 캐시에서 전량 재적재한다. 기존 테이블은 스키마(취소 PK 제거)가 바뀌었으므로
DROP 후 재생성한다. 멱등.

실행: PYTHONUTF8=1 .venv/Scripts/python.exe scripts/reprocess_real_trades.py [--db auction.db]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import naver_store as ns  # noqa: E402

CACHE = ROOT / "data" / "naver_cache.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--cache", default=str(CACHE))
    args = ap.parse_args()

    blob = json.loads(Path(args.cache).read_text(encoding="utf-8"))
    real = blob.get("real") or {}
    overview = blob.get("overview") or {}
    detail = blob.get("complex_detail") or {}
    fetched = f"reprocess:{datetime.now().strftime('%Y-%m-%d %H:%M')}"

    conn = ns.connect(args.db)
    try:
        # 스키마 변경(취소 PK 제거) 반영 — 실거래 테이블만 재구축(다른 테이블은 유지).
        conn.execute("DROP TABLE IF EXISTS naver_real_trades")
        ns.ensure_schema(conn)

        n_pairs = n_rows = n_cancel = n_empty = 0
        for key, entry in real.items():
            cno, _, ano = str(key).partition(":")
            if not cno or not ano:
                continue
            rows = (entry.get("rows") if isinstance(entry, dict) else entry) or []
            meta = entry.get("meta") if isinstance(entry, dict) else {}
            saved = ns.upsert_real_trades(conn, cno, ano, rows, fetched) if rows else 0
            n_rows += saved
            n_pairs += 1
            if not rows:
                n_empty += 1
            # (M3) 처리상태 기록 — 0건 쌍도 '확인함'으로 남겨 이어받기·증분 기준 확립.
            live = ns.load_real_trades(conn, cno, ano)
            latest = live[0]["trade_ymd"] if live else ""
            ns.record_pair_status(conn, cno, ano, len(live), latest,
                                  (meta or {}).get("exhausted", True), fetched)
        # 취소 집계(검증)
        n_cancel = conn.execute("SELECT COUNT(*) FROM naver_real_trades WHERE deleted=1").fetchone()[0]
        live = conn.execute("SELECT COUNT(*) FROM naver_real_trades WHERE deleted=0").fetchone()[0]
        print(f"real_trades 재구축: {n_pairs}쌍(빈쌍 {n_empty}) · 고유거래 {n_rows}행 "
              f"(정상 {live}·취소 {n_cancel})")

        # 단지 메타 — overview 우선 보강(전세가율·min/max), detail은 폴백. 보존 upsert라 파괴 없음.
        n_cx = 0
        for cno, det in detail.items():
            ns.upsert_complex(conn, det or {}, fetched, overview=overview.get(str(cno)))
            n_cx += 1
        for cno, ov in overview.items():
            # detail 없이 overview만 있는 단지도 반영
            if str(cno) not in detail:
                ns.upsert_complex(conn, {"complexDetail": {"complexNo": cno}}, fetched, overview=ov)
        lease = conn.execute("SELECT COUNT(*) FROM naver_complexes WHERE lease_per_deal_rate != ''").fetchone()[0]
        print(f"complexes 보강: {n_cx}건 처리 (전세가율 보유 {lease})")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
