"""기존 적재분 소급 개인정보 재마스킹 (감사 2026-07-15). 로컬 SQLite + Supabase 미러.

배경(두 경로 모두 실측 확인):
  1. `raw_listings.raw_json` — `_FREE_TEXT_FIELDS`에 `maejibun`(매각지분)이 빠져 채무자·공유자
     실명 1,870행 저장. **로컬 전용**(Supabase 미러 대상 아님, web 미서빙).
  2. `listing_rights` — normalize()가 명세서 자유텍스트(remark/lien_note/surviving_rights)를
     마스킹 없이 저장해 유치권신고인·임차인 실명 652행. **이쪽은 Supabase 로 미러돼 상세
     페이지로 서빙된다** — 클라우드 실측 remark 297행·surviving_rights 29행 오염 확인.

마스커 수정 후 **이미 적재된 행**에 소급 적용한다(신규 크롤은 sanitize_row/normalize 가 자동 처리).
멱등: 이미 마스킹된 행은 값이 안 바뀌므로 재실행 안전.

사용:
    PYTHONUTF8=1 .venv/Scripts/python.exe -m scripts.remask_existing_pii --dry-run
    PYTHONUTF8=1 .venv/Scripts/python.exe -m scripts.remask_existing_pii          # 로컬만
    PYTHONUTF8=1 .venv/Scripts/python.exe -m scripts.remask_existing_pii --cloud  # 로컬+Supabase
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.courtauction_fields import mask_personal_names, sanitize_row  # noqa: E402

DB = Path(__file__).resolve().parent.parent / "auction.db"

# listing_rights 중 자유텍스트(성명 유입 경로). senior_lien 은 '2021.4.28.근저당권' 류 공시정보뿐.
_RIGHTS_TEXT_FIELDS = ("surviving_rights", "lien_note", "remark")


def _remask_raw(conn, dry: bool) -> int:
    """raw_listings.raw_json — sanitize_row 전체 재적용(maejibun 포함)."""
    rows = conn.execute("SELECT rowid, raw_json FROM raw_listings").fetchall()
    changed: list[tuple[str, int]] = []
    for r in rows:
        try:
            before = json.loads(r["raw_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        after = sanitize_row(before)
        if after != before:
            changed.append((json.dumps(after, ensure_ascii=False), r["rowid"]))
    print(f"[raw_listings]   검사 {len(rows):,}행 → 마스킹 필요 {len(changed):,}행")
    if not dry and changed:
        conn.executemany("UPDATE raw_listings SET raw_json = ? WHERE rowid = ?", changed)
        conn.commit()
        print(f"[raw_listings]   ✅ {len(changed):,}행 갱신")
    return len(changed)


def _remask_rights(conn, dry: bool) -> list[dict]:
    """listing_rights 자유텍스트 — 변경된 행(클라우드 미러용 dict 리스트) 반환."""
    rows = [dict(r) for r in conn.execute("SELECT * FROM listing_rights")]
    changed: list[dict] = []
    for r in rows:
        new = {f: mask_personal_names(r[f] or "") for f in _RIGHTS_TEXT_FIELDS}
        if any(new[f] != (r[f] or "") for f in _RIGHTS_TEXT_FIELDS):
            changed.append({**r, **new})
    print(f"[listing_rights] 검사 {len(rows):,}행 → 마스킹 필요 {len(changed):,}행")
    if not dry and changed:
        conn.executemany(
            "UPDATE listing_rights SET surviving_rights=?, lien_note=?, remark=? "
            "WHERE court=? AND case_no=? AND item_no=?",
            [(c["surviving_rights"], c["lien_note"], c["remark"],
              c["court"], c["case_no"], c["item_no"]) for c in changed],
        )
        conn.commit()
        print(f"[listing_rights] ✅ {len(changed):,}행 갱신")
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="변경 건수만 세고 쓰지 않음")
    ap.add_argument("--cloud", action="store_true",
                    help="listing_rights 변경분을 Supabase 미러에도 반영(오염 실측된 경로)")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    _remask_raw(conn, args.dry_run)
    rights_changed = _remask_rights(conn, args.dry_run)

    if args.dry_run:
        print("(dry-run — 쓰지 않음)")
        return 0

    if args.cloud and rights_changed:
        from src import store_rest  # noqa: PLC0415
        if not store_rest.enabled():
            print("⚠ SUPABASE_URL/SECRET_KEY 미설정 — 클라우드 미러 건너뜀(로컬만 반영됨)")
            return 1
        n = store_rest.upsert_rights([
            {k: c[k] for k in c if k != "rowid"} for c in rights_changed
        ])
        print(f"[Supabase]       ✅ {n:,}행 미러 갱신")
    elif rights_changed:
        print("⚠ listing_rights 는 Supabase 미러 대상 — 클라우드에도 반영하려면 --cloud 로 재실행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
