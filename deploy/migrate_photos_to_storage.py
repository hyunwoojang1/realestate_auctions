"""기존 base64 사진(listing_photos.thumb_b64) → Supabase Storage 이전.

각 사진을 Storage에 업로드하고 local·cloud 모두 photo_url 설정 + thumb_b64 비움(DB 경량화).
멱등·이어받기: photo_url 이미 있으면 skip. 정지: PHOTO_MIGRATE_STOP 파일.

사용: PYTHONUTF8=1 AUCTION_DB=auction.db .venv/Scripts/python.exe -m deploy.migrate_photos_to_storage [--limit N]
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import photo_store, store, store_rest  # noqa: E402

STOP = "PHOTO_MIGRATE_STOP"


def _load_env():
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("AUCTION_DB", "auction.db"))
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    _load_env()
    # (2026-08-05) 사진은 R2 로 전량 이전됐고 로컬 thumb_b64 는 0행이다. 이 스크립트는 base64 →
    # 오브젝트 스토리지 이전용 레거시라 더 이상 할 일이 없다. 그런데 백엔드가 잘못 잡힌 상태에서
    # 누가 이걸 돌리면 사진을 다시 Supabase 로 올려 문제를 키운다 — 명시 허용 없이는 막는다.
    if os.environ.get("AUCTION_LEGACY_PHOTO_MIGRATE") != "1":
        print("[!] 레거시 스크립트다(base64 → 스토리지). 2026-08-05 R2 이전으로 역할이 끝났다.\n"
              "    현행 도구는 `python -m deploy.migrate_photos_to_r2` 다.\n"
              "    그래도 돌리려면 AUCTION_LEGACY_PHOTO_MIGRATE=1 을 설정하라. 중단")
        return 1
    if photo_store.backend() != "r2":
        print(f"[!] 백엔드가 '{photo_store.backend() or '미설정'}' 다 — Supabase 로 되올릴 위험. 중단")
        return 1
    if not photo_store.enabled() or not photo_store.ensure_bucket():
        print("[!] Storage 미설정/버킷실패 — 중단")
        return 1
    cloud = store_rest.enabled()
    conn = store.connect(args.db)
    rows = conn.execute(
        "SELECT rowid,court,case_no,item_no,seq,thumb_b64,fetched_at FROM listing_photos "
        "WHERE photo_url='' AND thumb_b64!=''" + (f" LIMIT {args.limit}" if args.limit else "")
    ).fetchall()
    total = len(rows)
    print(f"[*] 이전 대상 {total}장", flush=True)
    done = fail = 0
    cloud_batch = []
    for i, r in enumerate(rows, 1):
        if Path(STOP).exists():
            print("[STOP] 중단")
            break
        try:
            jpeg = base64.b64decode(r["thumb_b64"])
            url = photo_store.upload_photo(jpeg, r["court"], r["case_no"], r["item_no"], r["seq"])
        except Exception as e:  # noqa: BLE001
            url = None
            print(f"  [{i}] 오류 {type(e).__name__}", flush=True)
        if not url:
            fail += 1
            continue
        with conn:
            conn.execute("UPDATE listing_photos SET photo_url=?, thumb_b64='' WHERE rowid=?",
                         (url, r["rowid"]))
        done += 1
        if cloud:
            cloud_batch.append({"court": r["court"], "case_no": r["case_no"],
                                "item_no": r["item_no"], "seq": r["seq"], "photo_url": url,
                                "thumb_b64": "", "fetched_at": r["fetched_at"] or ""})
        if i % 100 == 0 or i == total:
            if cloud and cloud_batch:
                try:
                    store_rest.upsert_photos(cloud_batch)
                except Exception as e:  # noqa: BLE001
                    print(f"  클라우드 미러 경고: {str(e)[:60]}", flush=True)
                cloud_batch = []
            pct = 100 * i // total
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            print(f"  [{bar}] {pct}% ({i}/{total}) 성공 {done}·실패 {fail}", flush=True)
    if cloud and cloud_batch:
        try:
            store_rest.upsert_photos(cloud_batch)
        except Exception:  # noqa: BLE001
            pass
    print(f"[완료] 이전 {done}장·실패 {fail}. thumb_b64 정리는 VACUUM 별도.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
