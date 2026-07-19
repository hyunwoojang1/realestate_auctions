# -*- coding: utf-8 -*-
"""T3: naver_cache.json(37MB, 7/15 크롤 원본) 재파싱 → naver_complexes·naver_kb_history 적재.

크롤 0번 — 이미 저장된 원본 응답에서 그동안 버리던 필드(세대수·사용승인일·용적률·주차·
전세가율·매물수·KB 시계열)를 회수한다. C1(원본 전량 저장) 원칙의 실증.

실행: PYTHONUTF8=1 .venv/Scripts/python.exe scripts/reparse_naver_cache.py [--db auction.db]
멱등(upsert) — 여러 번 실행해도 안전. 캐시는 read-only.
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
    ap.add_argument("--db", default=None, help="대상 SQLite(기본 auction.db)")
    ap.add_argument("--cache", default=str(CACHE))
    args = ap.parse_args()

    cache_p = Path(args.cache)
    if not cache_p.exists():
        print(f"캐시 없음: {cache_p}")
        return 1
    blob = json.loads(cache_p.read_text(encoding="utf-8"))
    fetched_at = f"reparse:{datetime.now().strftime('%Y-%m-%d %H:%M')}"

    conn = ns.connect(args.db)
    try:
        # 1) complex_detail → naver_complexes (단지 메타 전체)
        details = blob.get("complex_detail") or {}
        n_cx = 0
        for _cno, det in details.items():
            if ns.upsert_complex(conn, det or {}, fetched_at):
                n_cx += 1
        print(f"naver_complexes: {n_cx}/{len(details)} 적재")

        # 2) kb 캐시 → naver_kb_history 시계열 전체 (키 실측: 'complexNo:areaNo')
        kb = blob.get("kb") or {}
        n_kb = skipped = 0
        for key, prices in kb.items():
            if not prices:
                skipped += 1
                continue
            cno, _, ano = str(key).partition(":")
            if not cno or not ano:
                skipped += 1
                continue
            n_kb += ns.upsert_kb_history(conn, cno, ano, prices, fetched_at)
        print(f"naver_kb_history: {n_kb}행 적재 (빈/이형 키 {skipped}건 스킵)")

        # 3) arts 캐시 → naver_articles (호가 원본 회수 — page=1분이지만 있는 만큼)
        arts = blob.get("arts") or {}
        n_art = 0
        for key, lst in arts.items():
            cno = str(key).split(":", 1)[0]
            if cno and lst:
                n_art += ns.upsert_articles(conn, cno, lst, fetched_at)
        print(f"naver_articles: {n_art}행 적재 ({len(arts)}키)")

        # 요약 통계
        r = conn.execute("SELECT COUNT(*) c, SUM(lease_per_deal_rate != '') lr, "
                         "SUM(household_count > 0) hh FROM naver_complexes").fetchone()
        print(f"검증: 단지 {r['c']}개 (전세가율 보유 {r['lr']}, 세대수 보유 {r['hh']})")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
