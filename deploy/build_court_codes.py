#!/usr/bin/env python
"""data/court_codes.json 재생성 — 법원명 → 대법원 경매 법원코드(cortOfcCd=boCd).

## 왜 이렇게 하나 (무한수정 방지)
법원코드를 손으로 적으면 틀리고 끝없이 고치게 된다. 대신 **우리가 이미 크롤한 실데이터**
(raw_listings.raw_json 의 boCd)에서 court→boCd 를 추출한다. 전국 크롤을 돌려온 터라 실무상
모든 법원이 데이터에 등장한다. 한 법원명이 두 코드로 갈리면(충돌) 에러로 세워 사람이 확인한다.

사용:
    python deploy/build_court_codes.py            # auction.db 에서 생성 → data/court_codes.json
    python deploy/build_court_codes.py --db X.db  # 다른 DB
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "court_codes.json"


def build(db_path: str) -> dict[str, str]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    seen: dict[str, set[str]] = collections.defaultdict(set)
    try:
        for row in conn.execute("SELECT court, raw_json FROM raw_listings"):
            if not row["raw_json"]:
                continue
            try:
                rj = json.loads(row["raw_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            court = (row["court"] or "").strip()
            bo = (rj.get("boCd") or "").strip()
            if court and bo:
                seen[court].add(bo)
    finally:
        conn.close()

    conflicts = {k: sorted(v) for k, v in seen.items() if len(v) > 1}
    if conflicts:
        raise SystemExit(f"법원명↔코드 충돌(사람 확인 필요): {conflicts}")
    return {k: next(iter(v)) for k, v in sorted(seen.items())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="법원코드 매핑 재생성(raw_listings 기반)")
    ap.add_argument("--db", default=str(ROOT / "auction.db"))
    args = ap.parse_args(argv)

    codes = build(args.db)
    if not codes:
        raise SystemExit("추출된 법원코드 0건 — raw_listings 가 비었거나 boCd 미포함.")

    # KST 날짜는 외부에서 주입하지 않고 파일 갱신자가 채운다(스크립트는 값만 만든다).
    blob = {
        "_meta": {
            "purpose": "법원명 → 대법원 경매 법원코드(cortOfcCd=boCd). casesearch.live_lookup 용.",
            "source": "raw_listings 실크롤 데이터 자동추출(court→boCd, 충돌 시 에러). 재생성: python deploy/build_court_codes.py",
            "count": len(codes),
        },
        "codes": codes,
    }
    OUT.write_text(json.dumps(blob, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"court_codes.json 갱신: {len(codes)}개 법원 → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
