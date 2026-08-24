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

# 주소 정제 — VWorld 지번/도로명 해석 적중률을 높인다.
# 지번:  '부산 사하구 다대동 120-10 삼환아파트'        → '부산 사하구 다대동 120-10'
# 도로명:'부산 사하구 다대로429번길 20 205동 20층2004호 (다대동,삼환아파트)' → '부산 사하구 다대로429번길 20'
_PAREN_RE = re.compile(r"\s*\(.*$")                       # (다대동,삼환아파트) 꼬리
_UNIT_RE = re.compile(r"\s+(제?\s*\d+동|지하\d*층?|\d+층|\d+호).*$")  # 건물 동/층/호 꼬리
_JIBUN_TAIL_RE = re.compile(r"^(.*?[동리]\s*산?\s*\d+(?:-\d+)?)(?:\s|$)")  # 지번 뒤 건물명 절단


def _jibun_only(addr: str) -> str:
    a = _PAREN_RE.sub("", addr or "").strip()
    a = _UNIT_RE.sub("", a).strip()          # 먼저 '205동 20층2004호' 제거(도로명 보존)
    m = _JIBUN_TAIL_RE.search(a)             # 남은 게 지번형이면 건물명 꼬리 절단
    return (m.group(1).strip() if m else a).strip()


def _summarize_addr(jibun: str, now: str) -> dict:
    """워커 스레드 — 건물(정제주소) 단위 요약. PK(court/case/item)는 호출측에서 붙인다.

    순수 조회만(네트워크). DB 쓰기는 메인에서. 같은 건물의 여러 세대가 이 결과를 공유한다.
    """
    base = {"jibun_addr": jibun or "", "fetched_at": now}
    if not jibun:
        return {**base, "status": "no_addr"}
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
    ap.add_argument("--workers", type=int, default=4,
                    help="병렬 스레드 수. 건축물대장 API는 순간 한도(429)가 있어 4 권장(과하면 429).")
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
    # ⚡ 건물 단위 dedup — 같은 건물의 여러 세대는 정제주소가 동일하므로 API를 1회만 친다.
    # (일일 무료 쿼터 < 전체 세대수 이므로 필수. 15,509 세대 → 고유 건물 수만 조회.)
    by_addr: dict[str, list[dict]] = {}
    for t in targets:
        by_addr.setdefault(_jibun_only(t["address"]), []).append(t)
    uniq = list(by_addr.items())
    print(f"[*] 고유 건물 {len(uniq)}개 (세대 {len(targets)} → API {len(uniq)}회로 절감)")

    ok = fail = 0
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_summarize_addr, addr, now): (addr, ts)
                for addr, ts in uniq}
        for fut in as_completed(futs):
            addr, ts = futs[fut]
            base = fut.result()          # 건물 단위 요약(court/case/item 없음)
            for t in ts:                 # 같은 건물의 모든 세대에 복제 저장
                row = {**base, "court": t["court"], "case_no": t["case_no"],
                       "item_no": t["item_no"]}
                store.save_building(conn, row)   # sqlite 쓰기는 메인 스레드에서만
                if row["status"] == "ok":
                    ok += 1
                else:
                    fail += 1
                done += 1
            if done % 100 < len(ts) or done == len(targets):
                print(f"  [{done}/{len(targets)}] ok={ok} 실패/미확인={fail}")

    print(f"[+] 완료: 적재 {ok}건 · 미확인/실패 {fail}건 (API {len(uniq)}회)")

    # Supabase 미러(테이블 있으면). 없거나 실패해도 로컬은 유지하되 **종료코드로는 드러낸다** —
    # 서빙(Vercel)이 읽는 건 클라우드라, 미러가 죽으면 로컬만 최신이고 화면은 옛 데이터다.
    # crawl_rights·run.py 는 MirrorReporter 로 전환됐는데 여기만 인라인 try/except 로 남아
    # exit 0 이었다(2026-08-05 세트3 재감사: "미전환 마지막 호출부").
    from src.mirror_report import MirrorReporter  # noqa: PLC0415
    mirror = MirrorReporter()
    if not args.no_cloud:
        from src import store_rest  # noqa: PLC0415
        if store_rest.enabled() and hasattr(store_rest, "upsert_building"):
            mirror.upsert("Supabase 건축물대장 미러링", "건",
                          lambda: store_rest.upsert_building(store.load_all_building(conn)))
        elif store_rest.enabled():
            print("[!] store_rest.upsert_building 미구현 — 로컬만 갱신(서빙은 로컬 폴백).",
                  file=sys.stderr)

    conn.close()
    if mirror.fail_count:
        print(f"[!] 클라우드 미러링 {mirror.fail_count}건 실패 — 로컬은 갱신됐지만 서빙 화면에는"
              " 반영되지 않았다. 비정상 종료(4)로 알린다.", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
