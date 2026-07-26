#!/usr/bin/env python
"""과거 낙찰 기록 백필 — raw_listings 의 실낙찰가(maeAmt) 마이닝 → sold_listings 소급 적재.

(C3 2026-07-27) C2 diff 는 앞으로의 새로고침만 잡는다 — 과거 수집분(raw 3.9만행)에 이미
실낙찰가가 있는 물건들을 소급해 '낙찰 기록'으로 되살린다.

## 무엇을 담나 (정직성 계약)
- **maeAmt 보유 물건만**: 그 회차에 실제 낙찰됐던 가격(이후 대금 미납으로 재매각된 이력).
  법원은 정상 낙찰의 최종가를 공개하지 않으므로, 가격 없는 과거 소멸 물건은 백필하지 않는다
  (채점 스냅샷도 없어 '빈 껍데기'만 남는다 — 앞으로의 소멸은 C2 가 완전한 스냅샷으로 보존).
- **현재 진행(scored 잔존) 물건 제외**: 재매각 진행 중인 물건은 활성 목록의 재매각 배지가
  담당한다 — sold 목록은 '종결/과거 기록' 전용(이중 표시 방지).
- 멱등: 이미 있는 키는 INSERT OR REPLACE 로 갱신. 반복 실행 안전.

사용:
    PYTHONUTF8=1 .venv/Scripts/python.exe -m deploy.backfill_sold_listings [--dry-run] [--cloud]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from deploy.backfill_sold_amount import collect_sold_amounts  # noqa: E402 — maeAmt 마이닝 재사용
from src import store  # noqa: E402
from src.courtauction_fields import parse_row, to_auction_listing  # noqa: E402

DB = ROOT / "auction.db"


def _latest_raw_by_key(conn) -> dict[tuple[str, str, str], dict]:
    """(court, case_no, item_no) → 가장 최근 fetched_at 의 raw_json dict."""
    out: dict[tuple[str, str, str], tuple[str, dict]] = {}
    for court, case_no, item_no, raw, fetched in conn.execute(
            "SELECT court, case_no, item_no, raw_json, fetched_at FROM raw_listings"):
        k = (str(court or ""), str(case_no or ""), str(item_no or ""))
        prev = out.get(k)
        if prev is None or str(fetched) > prev[0]:
            try:
                out[k] = (str(fetched), json.loads(raw))
            except (TypeError, ValueError):
                continue
    return {k: v[1] for k, v in out.items()}


def build_rows(conn) -> list[dict]:
    amounts = collect_sold_amounts(conn)
    if not amounts:
        return []
    active = {(r[0], r[1], r[2]) for r in conn.execute(
        "SELECT court, case_no, item_no FROM scored_listings")}
    latest = _latest_raw_by_key(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows: list[dict] = []
    skipped_active = skipped_noraw = 0
    for key, amt in amounts.items():
        if key in active:
            skipped_active += 1
            continue
        raw = latest.get(key)
        if raw is None:
            skipped_noraw += 1
            continue
        try:
            lst = to_auction_listing(parse_row(raw))
        except Exception:  # noqa: BLE001 — 옛 원본 파싱 실패는 건너뜀(지어내지 않음)
            skipped_noraw += 1
            continue
        rows.append({
            "court": key[0], "case_no": key[1], "item_no": key[2],
            "apt_name": lst.apt_name, "address": lst.address,
            "property_type": lst.property_type, "area_m2": lst.area_m2,
            "appraisal_price": lst.appraisal_price, "min_bid_price": lst.min_bid_price,
            "fail_count": lst.fail_count, "sale_date": lst.sale_date,
            # 과거 채점 스냅샷은 미보존 — 시세·차익 필드는 NULL(모름을 지어내지 않음)
            "est_market_price": None, "market_band_low": None,
            "profit_low": None, "expected_profit": None, "arb_score": None, "grade": "",
            "sold_price": amt, "sold_evidence": "maeAmt", "snapshot_at": now,
        })
    print(f"[백필] maeAmt 보유 {len(amounts):,}키 → 적재 대상 {len(rows):,} "
          f"(활성 제외 {skipped_active}·원본 결손 {skipped_noraw})")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cloud", action="store_true", help="Supabase 미러까지 반영")
    args = ap.parse_args()

    conn = store.connect(args.db)
    rows = build_rows(conn)
    if args.dry_run:
        for r in rows[:5]:
            print("  예:", r["case_no"], r["apt_name"], r["sold_price"])
        print("(dry-run — 쓰지 않음)")
        return 0
    n = store.upsert_sold(conn, rows)
    print(f"[백필] 로컬 sold_listings {n:,}행 적재")
    if args.cloud and rows:
        from src import store_rest  # noqa: PLC0415
        if store_rest.enabled():
            cn = store_rest.upsert_sold(rows)
            print(f"[백필] Supabase 미러 {cn:,}행")
        else:
            print("⚠ SUPABASE env 미설정 — 클라우드 미러 건너뜀")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
