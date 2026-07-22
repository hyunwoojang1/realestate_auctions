"""건축물대장 요약 enrichment — 정부 OpenAPI 기반 '빠른 병렬' job.

⚡ courtauction 권리 크롤(deploy/crawl_rights)과 **완전히 분리**된다. 그쪽은 대법원 사이트를
긁는 직렬·throttle·밴위험 작업이라 느리지만, 이 job은 승인된 정부 OpenAPI(BldRgstHubService
+ VWorld)라 병렬 호출·무밴이 가능해 훨씬 빠르다. 그래서 느린 크롤에 얹지 않고 따로 돌린다.

하는 일: scored_listings 의 각 물건 주소 → 건축물대장 요약(사용승인·연식·주용도·층수·연면적·
위반여부·동수) 을 조회해 listing_building 에 upsert. 같은 건물을 공유하는 세대는
building_info 모듈 캐시로 중복 조회가 자동 제거된다.

사용:
  python -m deploy.enrich_building --db auction.db --all --workers 10
  python -m deploy.enrich_building --db auction.db --limit 200        # 일부만
  python -m deploy.enrich_building --db auction.db --all --retry-failed
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from deploy.migrate_to_supabase import _load_env  # noqa: E402
from src import building_info, store  # noqa: E402

_KST = timezone(timedelta(hours=9))

# '부산광역시 사하구 다대동 120-10 삼환아파트' → '부산광역시 사하구 다대동 120-10'
# (동/리 뒤 번지[-지]까지만 남겨 VWorld 지번해석 적중률을 높인다. 건물명 꼬리가 붙으면
#  NOT_FOUND 가 나므로 잘라낸다.)
_JIBUN_RE = re.compile(r"^(.*?[동리]\s*산?\s*\d+(?:-\d+)?)")


def _jibun_only(addr: str) -> str:
    m = _JIBUN_RE.search(addr or "")
    return m.group(1).strip() if m else (addr or "").strip()


def _summarize_one(court: str, case_no: str, item_no: str, address: str,
                   now: str) -> dict:
    """워커 스레드에서 실행 — 순수 조회만(네트워크). DB 쓰기는 메인에서."""
    base = {"court": court, "case_no": case_no, "item_no": item_no,
            "jibun_addr": "", "fetched_at": now}
    jibun = _jibun_only(address)
    if not jibun:
        return {**base, "status": "no_addr"}
    base["jibun_addr"] = jibun
    try:
        summary = building_info.get_building_summary(jibun)
    except Exception as e:  # noqa: BLE001 — 개별 실패는 job 전체를 막지 않는다
        return {**base, "status": "error", "violation_content": type(e).__name__}
    if not summary:
        return {**base, "status": "no_bld"}
    return {
        **base, "status": "ok",
        "approved": summary.get("approved") or "",
        "age_years": summary.get("age_years"),
        "main_purpose": summary.get("main_purpose") or "",
        "ground_floors": summary.get("ground_floors"),
        "underground_floors": summary.get("underground_floors"),
        "total_area_m2": summary.get("total_area_m2"),
        "is_violation": 1 if summary.get("is_violation") else 0,
        "violation_content": summary.get("violation_content") or "",
        "dong_count": summary.get("dong_count"),
    }


def _targets(conn, limit: int | None, retry_failed: bool) -> list[dict]:
    """미enrich 물건(court, case_no, item_no, address). 주소 없는 행은 제외."""
    done = store.building_done_keys(conn, include_failed=not retry_failed)
    rows = conn.execute(
        "SELECT court, case_no, item_no, address FROM scored_listings "
        "WHERE address IS NOT NULL AND address != ''"
    ).fetchall()
    out = []
    for r in rows:
        key = (r["court"], r["case_no"], r["item_no"])
        if key in done:
            continue
        out.append({"court": r["court"], "case_no": r["case_no"],
                    "item_no": r["item_no"], "address": r["address"]})
        if limit and len(out) >= limit:
            break
    return out


def main() -> int:
    _load_env()
    ap = argparse.ArgumentParser(description="건축물대장 요약 병렬 enrichment")
    ap.add_argument("--db", default=os.environ.get("AUCTION_DB", "auction.db"))
    ap.add_argument("--all", action="store_true", help="미enrich 전량")
    ap.add_argument("--limit", type=int, default=200, help="--all 아니면 이 개수만")
    ap.add_argument("--workers", type=int, default=10, help="병렬 스레드 수(정부API·무밴)")
    ap.add_argument("--retry-failed", action="store_true",
                    help="실패(no_addr/no_bld/error)로 저장된 행도 재조회")
    ap.add_argument("--no-cloud", action="store_true", help="Supabase 미러 생략")
    args = ap.parse_args()

    if not os.environ.get("MOLIT_API_KEY") or not os.environ.get("VWORLD_API_KEY"):
        print("[!] MOLIT_API_KEY / VWORLD_API_KEY 미설정 — .env 확인 필요.", file=sys.stderr)
        return 2

    conn = store.connect(args.db)
    limit = None if args.all else args.limit
    targets = _targets(conn, limit, args.retry_failed)
    print(f"[*] 대상 {len(targets)}건 (DB={args.db}, workers={args.workers})")
    if not targets:
        conn.close()
        return 0

    now = datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S")
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_summarize_one, t["court"], t["case_no"], t["item_no"],
                          t["address"], now): t for t in targets}
        for i, fut in enumerate(as_completed(futs), 1):
            row = fut.result()
            store.save_building(conn, row)          # sqlite 쓰기는 메인 스레드에서만
            if row["status"] == "ok":
                ok += 1
            else:
                fail += 1
            if i % 50 == 0 or i == len(targets):
                print(f"  [{i}/{len(targets)}] ok={ok} 실패/미확인={fail}")

    print(f"[+] 완료: 적재 {ok}건 · 미확인/실패 {fail}건")

    # Supabase 미러(테이블 있으면). 없거나 실패해도 로컬은 유지.
    if not args.no_cloud:
        try:
            from src import store_rest  # noqa: PLC0415
            if store_rest.enabled() and hasattr(store_rest, "upsert_building"):
                rows = store.load_all_building(conn)
                n = store_rest.upsert_building(rows)
                print(f"[+] Supabase 건축물대장 미러링 {n}건")
            elif store_rest.enabled():
                print("[!] store_rest.upsert_building 미구현 — 로컬만 갱신(서빙은 로컬 폴백).",
                      file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[!] Supabase 미러 skip: {e}", file=sys.stderr)

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
