#!/usr/bin/env python
"""Supabase auction_scored_listings.sale_time 일회성 백필 — 라이브 크롤 0.

## 왜 필요한가
당일 입찰 마감 컷오프(query.bidding_closed)는 매각 개시시각(raw maeHh1 'HHMM')이 있어야
정밀 판정한다. 로컬 서빙은 load_scored 가 raw_listings 를 맵 조인해 주입하지만,
클라우드(Vercel)는 raw_listings 가 없어 sale_time="" → 전 물건 10:00 폴백 가정.
run.py 미러가 sale_time 을 싣도록 배선(2026-07-24)했으나 다음 새로고침 전까지의
기존 클라우드 행은 비어 있다 — 그 갭을 이 스크립트가 메운다.

## 무엇을 하나
1) 클라우드에서 현재 서빙 중인 (court, case_no, item_no) 키 전량을 읽고
2) 로컬 raw_listings 의 store._sale_time_map 으로 개시시각을 구해
3) 클라우드에 존재하는 키만 {PK+sale_time} 부분행 merge upsert 한다.
   (클라우드에 없는 키는 건드리지 않는다 — 점수 없는 유령 행 생성 방지)

## 멱등성·안전
- merge-duplicates 부분행 upsert 라 제공 컬럼(sale_time)만 갱신, 반복 실행 결과 동일.
- --dry-run 으로 대상 건수만 확인 가능.
- 컬럼 미배포면 400 — deploy/supabase_setup.sql 의 ALTER 선행 필요.

사용:
    python -m deploy.backfill_sale_time [--db auction.db] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from deploy.migrate_to_supabase import _load_env  # noqa: E402
from src import store, store_rest  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Supabase scored.sale_time 백필(로컬 raw 기준)")
    ap.add_argument("--db", default="auction.db")
    ap.add_argument("--dry-run", action="store_true", help="대상 건수만 출력하고 쓰지 않음")
    args = ap.parse_args(argv)

    _load_env()
    if not store_rest.enabled():
        print("SUPABASE_URL/SECRET_KEY 미설정 — 중단", file=sys.stderr)
        return 1

    conn = store.connect(args.db)
    try:
        times = store._sale_time_map(conn)
        print(f"로컬 raw_listings 개시시각 수집: {len(times):,}물건")
    finally:
        conn.close()

    cloud = store_rest.load_scored(use_cache=False)
    print(f"클라우드 서빙 행: {len(cloud):,}건")

    rows = []
    already = missing = 0
    for s in cloud:
        t = times.get((s.court, s.case_no, s.item_no), "")
        if not t:
            missing += 1
            continue
        if s.sale_time == t:
            already += 1
            continue
        rows.append({"court": s.court, "case_no": s.case_no,
                     "item_no": s.item_no, "sale_time": t})

    print(f"  주입 대상: {len(rows):,}건 / 이미 동일값: {already:,}건 / 로컬 시각 미보유: {missing:,}건")
    if args.dry_run or not rows:
        return 0

    url, key, table = store_rest._cfg()
    n = store_rest._post_upsert(url, key, table, rows)
    store_rest.invalidate()
    print(f"  ☁ 백필 완료: {n:,}건")

    # 검증 — 재로드해서 sale_time 이 실제로 채워졌는지 표본 확인
    refreshed = store_rest.load_scored(use_cache=False)
    filled = sum(1 for s in refreshed if s.sale_time)
    print(f"  검증: 클라우드 sale_time 보유 {filled:,}/{len(refreshed):,}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
