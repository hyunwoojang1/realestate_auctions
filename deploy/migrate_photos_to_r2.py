"""Supabase Storage → Cloudflare R2 사진 이전 (기존 물건사진 보존용).

배경: Supabase 무료 파일스토리지 1GB를 초과(실측 1,858MB·38,101장)해 공정사용 제한 통보를
받았다. 과거 물건 사진을 **지우지 않고** 계속 보관하려면 저장 10GB·이그레스 무제한이 무료인
R2로 옮기는 것이 유일한 지속 가능한 선택지다(2026-08-05 사용자 결정).

**클라우드(Supabase 테이블)를 기준으로 돈다.** 로컬 auction.db 에는 18,535행뿐인데 클라우드엔
37,850행이 있어(2026-08-05 실측), 로컬 기준으로 돌면 절반이 이전되지 않은 채 "완료"로 보인다.
실제 서빙(Vercel)이 읽는 것도 클라우드다.

이전 루프는 로컬을 **URL 문자열 일치**로만 갱신하므로, 로컬·클라우드가 같은 PK인데 값이 달랐던
행은 옛 URL로 남는다. 그 잔재는 `--sync-local` 이 **PK 기준**으로 따로 맞춘다. 남겨두면
크롤의 사진 미러(로컬 전량 → 클라우드 덮어쓰기)가 이전을 되돌린다.

멱등·이어받기: 매 실행마다 "아직 supabase URL인 행"을 다시 조회하므로 중단 후 재실행해도
남은 것만 처리한다. 오브젝트 키(sha1.jpg)는 백엔드가 달라도 동일해 덮어쓰기가 안전하다.
정지: 레포 루트에 PHOTO_R2_STOP 파일 생성.

사용:
  # 전량 이전
  PYTHONUTF8=1 .venv/Scripts/python.exe -m deploy.migrate_photos_to_r2 [--limit N] [--workers 24]
  # 로컬 잔재 정리(PK 기준)
  PYTHONUTF8=1 .venv/Scripts/python.exe -m deploy.migrate_photos_to_r2 --sync-local
  # 원본 버킷 삭제 관문 — 클라우드·로컬 잔여 0 + 표본 도달성 200 이면 exit 0, 아니면 1
  PYTHONUTF8=1 .venv/Scripts/python.exe -m deploy.migrate_photos_to_r2 --check [--sample 30]
"""
from __future__ import annotations

import argparse
import logging
import os
import random
import re
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import photo_store, store_rest  # noqa: E402

logger = logging.getLogger(__name__)

STOP = "PHOTO_R2_STOP"
PAGE = 1000                     # PostgREST 한 페이지 행 수
KEY_RE = re.compile(r"^[0-9a-f]{40}\.jpg$")   # object_path() 산출물 형태 — 엉뚱한 키 업로드 차단


def _load_env() -> None:
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _cloud_pending(limit: int | None = None) -> list[dict]:
    """클라우드에서 아직 Supabase URL을 가리키는 사진 행을 전량 수집."""
    url, key, _ = store_rest._cfg()
    endpoint = store_rest._endpoint(url, store_rest.PHOTOS_TABLE)
    headers = store_rest._headers(key)
    out: list[dict] = []
    offset = 0
    while True:
        r = requests.get(endpoint, headers=headers, timeout=60, params={
            "select": "court,case_no,item_no,seq,photo_url,fetched_at",
            "photo_url": "like.*supabase.co*",
            "order": "court,case_no,item_no,seq",
            "limit": PAGE, "offset": offset})
        r.raise_for_status()
        rows = r.json()
        out.extend(rows)
        if len(rows) < PAGE or (limit and len(out) >= limit):
            break
        offset += PAGE
    return out[:limit] if limit else out


def _cloud_total() -> tuple[int, int]:
    """(전체 사진 행, 아직 supabase인 행) — 진행률 표시용."""
    url, key, _ = store_rest._cfg()
    endpoint = store_rest._endpoint(url, store_rest.PHOTOS_TABLE)
    h = store_rest._headers(key, {"Prefer": "count=exact", "Range-Unit": "items"})

    def _count(params):
        r = requests.get(endpoint, headers={**h, "Range": "0-0"}, params=params, timeout=30)
        r.raise_for_status()
        return int(r.headers.get("content-range", "0-0/0").split("/")[-1])

    return _count({"select": "seq"}), _count({"select": "seq", "photo_url": "like.*supabase.co*"})


def _source_prefix() -> str:
    """정상적인 원본 URL 접두사. 이것으로 시작하지 않으면 내려받지 않는다."""
    url, _, _ = store_rest._cfg()
    bucket = os.environ.get("SUPABASE_PHOTOS_BUCKET", "auction-photos")
    return f"{(url or '').rstrip('/')}/storage/v1/object/public/{bucket}/"


def _move_one(row: dict) -> tuple[dict, str | None]:
    """사진 1장 다운로드 → R2 업로드. 반환 (원본행, 새 URL 또는 None)."""
    old = row["photo_url"]
    # `like.*supabase.co*` 는 **부분 문자열** 필터라 호스트 검증이 아니다. DB 값이 어떤 경로로든
    # 오염되면 내부망 주소로 GET 을 날리고(SSRF) 그 응답을 **공개 R2 버킷에 그대로 업로드**하는
    # 체인이 된다. 접두사 정확일치로 막는다(2026-08-05 보안 리뷰).
    if not old.startswith(_source_prefix()):
        logger.warning("원본 접두사 불일치로 스킵: %s", old[:60])
        return row, None
    key = old.rsplit("/", 1)[-1]
    if not KEY_RE.match(key):
        return row, None
    try:
        # 공용 세션 — 장당 TLS 핸드셰이크를 없앤다(photo_store.session 주석 참고).
        # 리다이렉트 미추종 — 접두사 검증을 우회해 내부망으로 튀는 경로 차단.
        r = photo_store.session().get(old, timeout=30, allow_redirects=False)
        if r.status_code != 200 or not r.content:
            return row, None
        return row, photo_store.upload_bytes(r.content, key)
    except Exception as e:  # noqa: BLE001 — 개별 실패는 다음 실행에서 재시도(멱등)
        # 무음 금지: 실패가 일시적 네트워크인지 구조적 버그인지 사후에 구분할 수 있어야 한다.
        logger.warning("이전 실패 %s: %s", key, type(e).__name__)
        return row, None


def _update_local(conn: sqlite3.Connection, pairs: list[tuple[str, str]]) -> int:
    """로컬 SQLite 의 photo_url 을 old→new 로 교체. 없는 행은 그냥 0건."""
    with conn:
        cur = conn.executemany(
            "UPDATE listing_photos SET photo_url=? WHERE photo_url=?", pairs)
    return max(cur.rowcount, 0)


def _local_counts(db: str) -> tuple[int, int]:
    """(로컬 사진 행, 아직 supabase URL 인 행). 로컬을 안 세면 '100% 완료'가 거짓이 된다."""
    try:
        c = sqlite3.connect(db)
        try:
            t = c.execute("SELECT COUNT(*) FROM listing_photos").fetchone()[0]
            s = c.execute("SELECT COUNT(*) FROM listing_photos "
                          "WHERE photo_url LIKE '%supabase.co%'").fetchone()[0]
            return t, s
        finally:
            c.close()
    except sqlite3.Error:
        return 0, 0


def _reachable_sample(n: int) -> tuple[int, list[str]]:
    """클라우드 photo_url 을 무작위 n장 뽑아 실제로 응답하는지 확인. 반환 (검사수, 실패URL들).

    이 레포엔 "저장된 사진 URL 이 정말 열리는가"를 보는 장치가 하나도 없었다. 그런데 이 작업의
    목적이 '원본 버킷을 지울 수 있는 상태'를 만드는 것이라, 지운 뒤 깨졌는지 알려줄 눈이 없으면
    되돌릴 수 없는 사고가 된다 — 그래서 삭제 관문인 `--check` 안에 넣는다(2026-08-05 리뷰 권고).
    """
    if n <= 0:
        return 0, []
    url, key, _ = store_rest._cfg()
    r = requests.get(store_rest._endpoint(url, store_rest.PHOTOS_TABLE),
                     headers=store_rest._headers(key), timeout=30,
                     params={"select": "photo_url", "limit": max(n * 40, 400)})
    r.raise_for_status()
    urls = [row["photo_url"] for row in r.json() if row.get("photo_url")]
    if not urls:
        return 0, []
    picks = random.sample(urls, min(n, len(urls)))
    # DB 값을 그대로 때리는 건 _move_one 과 같은 신뢰 경계다 — 일관되게 접두사를 검증한다.
    # 우리 R2 공개 도메인이 아닌 값은 요청하지 않고 그 자체를 '실패'로 센다.
    pub = os.environ.get("R2_PUBLIC_BASE", "").strip().rstrip("/") + "/"
    s = photo_store.session()
    bad = []
    for u in picks:
        if len(pub) < 2 or not u.startswith(pub):
            bad.append(u)
            continue
        try:
            if s.head(u, timeout=20).status_code != 200:
                bad.append(u)
        except Exception:  # noqa: BLE001 — 네트워크 실패도 '도달 불가'로 센다
            bad.append(u)
    return len(picks), bad


def _sync_local_from_cloud(db: str) -> int:
    """로컬의 스테일 행을 **PK 기준**으로 클라우드 값에 맞춘다.

    이전은 클라우드 기준으로 돌고 로컬은 'URL 문자열 정확일치'로만 갱신했다. 그래서 로컬·클라우드가
    같은 PK인데 다른 URL을 갖고 있던 행은 매칭에 실패해 옛 Supabase URL로 남는다. 그 상태로
    크롤이 돌면 로컬 전량 미러가 클라우드를 되돌린다 — 그래서 문자열이 아니라 PK로 맞춘다.
    """
    url, key, _ = store_rest._cfg()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT court, case_no, item_no, seq FROM listing_photos "
            "WHERE photo_url LIKE '%supabase.co%'").fetchall()
        if not rows:
            return 0
        fixed = 0
        for r in rows:
            resp = requests.get(store_rest._endpoint(url, store_rest.PHOTOS_TABLE),
                                headers=store_rest._headers(key), timeout=30,
                                params={"select": "photo_url", "court": f"eq.{r['court']}",
                                        "case_no": f"eq.{r['case_no']}",
                                        "item_no": f"eq.{r['item_no']}", "seq": f"eq.{r['seq']}"})
            resp.raise_for_status()
            got = resp.json()
            if got and got[0].get("photo_url") and "supabase.co" not in got[0]["photo_url"]:
                with conn:
                    conn.execute(
                        "UPDATE listing_photos SET photo_url=? "
                        "WHERE court=? AND case_no=? AND item_no=? AND seq=?",
                        (got[0]["photo_url"], r["court"], r["case_no"], r["item_no"], r["seq"]))
                fixed += 1
        return fixed
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("AUCTION_DB", "auction.db"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--batch", type=int, default=400, help="DB 반영·정지확인 주기(장)")
    ap.add_argument("--check", action="store_true",
                    help="진행률·도달성 점검 후 종료. 전부 정상이면 exit 0, 아니면 1")
    ap.add_argument("--sample", type=int, default=30,
                    help="--check 시 도달성(HTTP 200)을 확인할 무작위 표본 장수")
    ap.add_argument("--sync-local", action="store_true",
                    help="로컬 SQLite 의 스테일 Supabase URL 을 PK 기준으로 클라우드 값에 맞춤")
    args = ap.parse_args(argv)
    _load_env()
    # logger.warning 이 stdout 로그에 안 남아 실패가 묻히던 것 방지(리뷰 지적).
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="  [warn] %(message)s")
    # 풀 크기는 **세션이 만들어지기 전에** 못 박아야 한다. ensure_bucket() 이 먼저 세션을 만들면
    # 뒤늦게 올린 값은 반영되지 않는다(2026-08-05 리뷰에서 dead code 로 지적된 순서 버그).
    photo_store.configure_pool(max(args.workers * 2, 16))

    if not store_rest.enabled():
        print("[!] Supabase 미설정 — 원본을 읽을 수 없다. 중단")
        return 1
    if args.check:
        total, pending = _cloud_total()
        lt, lp = _local_counts(args.db)
        print(f"[클라우드] 전체 {total:,}장 · 남은 Supabase {pending:,}장 · "
              f"이전 완료 {total - pending:,}장 ({100 * (total - pending) // max(total, 1)}%)")
        print(f"[로컬DB ] 전체 {lt:,}행 · 남은 Supabase {lp:,}행"
              + ("  ← --sync-local 로 정리 필요(크롤이 클라우드를 되돌릴 수 있음)" if lp else ""))
        checked, bad = _reachable_sample(args.sample)
        print(f"[도달성 ] 무작위 {checked}장 중 응답 실패 {len(bad)}장"
              + (f" → {bad[:3]}" if bad else ""))
        # exit code 로 게이팅 가능해야 한다 — 이 명령이 "버킷 삭제해도 되는가"의 관문이다.
        ok = pending == 0 and lp == 0 and not bad and checked > 0
        print("[판정   ] " + ("OK — 원본 버킷 정리 가능" if ok else
                              "NG — 아래 항목을 먼저 해소하라(원본 삭제 금지)"))
        return 0 if ok else 1
    if photo_store.backend() != "r2":
        print("[!] R2 백엔드가 아니다 — .env 에 R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/"
              "R2_SECRET_ACCESS_KEY/R2_BUCKET/R2_PUBLIC_BASE 5종을 넣어라. 중단")
        return 1
    if args.sync_local:
        n = _sync_local_from_cloud(args.db)
        _, lp = _local_counts(args.db)
        print(f"[동기화] 로컬 {n:,}행을 클라우드 값으로 교정 · 남은 스테일 {lp:,}행")
        return 0
    if not photo_store.ensure_bucket():
        print("[!] R2 버킷 접근 불가 — 키/버킷명/공개설정 확인. 중단")
        return 1

    rows = _cloud_pending(args.limit)
    total = len(rows)
    if not total:
        print("[완료] 이전할 사진이 없다(전부 R2).")
        return 0
    print(f"[*] 이전 대상 {total:,}장 · 동시 {args.workers}", flush=True)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    done = fail = local_n = 0
    CH = args.batch                              # 배치마다 DB 반영 + 정지신호 확인
    # 스레드풀은 루프 밖에서 한 번만 만든다 — 배치마다 재생성하면 워커 수만큼 스레드를 매번 띄운다.
    ex = ThreadPoolExecutor(max_workers=args.workers)
    try:
        return _run(args, rows, total, conn, ex, CH, done, fail, local_n)
    finally:
        # 예외·Ctrl+C 로 빠져나가도 SQLite 잠금과 스레드가 남지 않게 한다(리뷰 지적).
        ex.shutdown(wait=False, cancel_futures=True)
        conn.close()


def _run(args, rows, total, conn, ex, CH, done, fail, local_n) -> int:
    for start in range(0, total, CH):
        if Path(STOP).exists():
            print("[STOP] 중단 — 재실행하면 남은 것부터 이어간다")
            break
        chunk = rows[start:start + CH]
        results = list(ex.map(_move_one, chunk))

        cloud_rows, local_pairs = [], []
        for row, new_url in results:
            if not new_url:
                fail += 1
                continue
            done += 1
            cloud_rows.append({"court": row["court"], "case_no": row["case_no"],
                               "item_no": row["item_no"], "seq": row["seq"],
                               "photo_url": new_url, "thumb_b64": "",
                               "fetched_at": row.get("fetched_at") or ""})
            local_pairs.append((new_url, row["photo_url"]))
        if cloud_rows:
            try:
                store_rest.upsert_photos(cloud_rows)
            except Exception as e:  # noqa: BLE001 — 다음 실행에서 재시도(원본은 아직 살아있다)
                print(f"  [!] 클라우드 반영 실패 {str(e)[:80]} — 이 배치는 다음 실행에 재처리",
                      flush=True)
                done -= len(cloud_rows)
                fail += len(cloud_rows)
                continue
            local_n += _update_local(conn, local_pairs)

        pct = 100 * min(start + CH, total) // total
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        print(f"  [{bar}] {pct}% ({min(start + CH, total):,}/{total:,}) "
              f"성공 {done:,}·실패 {fail:,}·로컬 {local_n:,}", flush=True)

    print(f"[완료] 이전 {done:,}장 · 실패 {fail:,}장 · 로컬 갱신 {local_n:,}행", flush=True)
    if fail:
        print("      실패분은 원본이 그대로 남아 있다 — 재실행하면 그것만 다시 시도한다.")
    _, lp = _local_counts(args.db)
    if lp:
        print(f"      ⚠ 로컬에 스테일 Supabase URL {lp:,}행 남음 — `--sync-local` 로 정리하라. "
              "안 하면 다음 크롤의 미러링이 클라우드를 되돌린다.")
    print("      원본 Supabase 버킷 삭제는 --check 가 클라우드·로컬 모두 0 인 것 + 화면 검증 후.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
