"""로컬 SQLite(scored_listings) → Supabase 초기 이관 스크립트.

사용:
    # .env 의 SUPABASE_* 로드 후 실행 (auction.db 3,751건 → 클라우드)
    PYTHONUTF8=1 AUCTION_DB=auction.db .venv/Scripts/python.exe -m deploy.migrate_to_supabase

전제:
    - deploy/supabase_setup.sql 을 Supabase SQL Editor 에서 먼저 Run(테이블 생성) 했을 것.
    - .env 에 SUPABASE_URL / SUPABASE_SECRET_KEY / SUPABASE_TABLE 설정.
REST(service key)만 사용 — Postgres 직접 접속(비번) 불필요.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from src import store, store_rest


def _load_env(path: str | None = None) -> None:
    """의존성 없이 .env 를 os.environ 에 로드(이미 설정된 값은 덮지 않음).

    경로는 **레포 루트 기준**으로 고정한다. 종전엔 상대경로 ".env" 라서 작업 디렉터리가 루트가
    아니면 조용히 아무것도 안 읽었고, 그 결과 크롤러가 자격증명 없는 상태로 돌아
    사진을 base64 로 DB에 쌓는 옛 경로로 되돌아갈 수 있었다(2026-08-05 리뷰에서 실측 재현).
    """
    if path is None:
        path = str(Path(__file__).resolve().parent.parent / ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    _load_env()
    if not store_rest.enabled():
        print("[!] SUPABASE_URL/SECRET_KEY 미설정 — .env 확인.", file=sys.stderr)
        return 2

    db_path = os.environ.get("AUCTION_DB", "auction.db")
    conn = store.connect(db_path)
    try:
        items = store.load_scored(conn)
    finally:
        conn.close()
    print(f"[*] 로컬 {db_path}: {len(items)}건 로드")

    # 테이블 존재/접근 확인(없으면 REST 가 에러 → DDL 먼저 Run 안내).
    try:
        before = store_rest.has_rows()
    except Exception as e:  # noqa: BLE001
        print(f"[!] Supabase 테이블 조회 실패 — deploy/supabase_setup.sql 을 SQL Editor 에서 "
              f"먼저 Run 했는지 확인. 원인: {e}", file=sys.stderr)
        return 3
    print(f"[*] 클라우드 테이블 접근 OK (기존 데이터 있음={before})")

    n = store_rest.upsert(items)
    print(f"[+] Supabase 업서트 완료: {n}건")

    # 검증: 다시 읽어 개수 대조.
    store_rest.invalidate()
    cloud = store_rest.load_scored(use_cache=False)
    print(f"[=] 클라우드 재조회: {len(cloud)}건 (로컬 {len(items)}건과 대조)")
    if len(cloud) < len(items):
        print("[!] 클라우드 건수가 로컬보다 적음 — 재실행 또는 로그 확인 필요.", file=sys.stderr)
        return 4
    print("[OK] 이관 성공.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
