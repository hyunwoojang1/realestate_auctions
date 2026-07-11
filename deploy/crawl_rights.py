"""물건상세 권리·기일 요지 배치 크롤 → listing_rights 적재(+Supabase 미러).

사용:
    PYTHONUTF8=1 .venv/Scripts/python.exe -m deploy.crawl_rights            # 우선순위 상위 200건
    PYTHONUTF8=1 .venv/Scripts/python.exe -m deploy.crawl_rights --limit 50
    PYTHONUTF8=1 .venv/Scripts/python.exe -m deploy.crawl_rights --all     # 전 물건(오래 걸림)

대상 우선순위: 보수 차익 양수(추천 후보) → 유찰 많은 순. 이미 크롤된 물건은 --refresh
없으면 건너뛴다(재실행 안전). 법원 코드(boCd)는 raw_listings.raw_json에서 조인.
스로틀·상한·kill-switch(COURTAUCTION_STOP)는 CourtAuctionClient가 담당.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from deploy.migrate_to_supabase import _load_env
from src import store, store_rest
from src.courtauction_client import CourtAuctionBlocked, CourtAuctionClient, CourtAuctionError
from src.courtauction_detail import normalize

_KST = timezone(timedelta(hours=9))


def _targets(conn, limit: int | None, refresh: bool) -> list[dict]:
    """크롤 대상 (boCd, case_no, item_no, 우선순위 정렬). raw_listings에서 법원코드 조인."""
    # ⚠ court 를 조인에 반드시 포함(감사 2026-07-10 CRITICAL): 사건번호는 법원별 독립 채번이라
    # court 없이 조인하면 타법원 동명 사건의 boCd 로 크롤해 '엉뚱한 사건의 권리'가 적재된다.
    rows = conn.execute(
        """
        SELECT s.court, s.case_no, s.item_no, s.fail_count,
               COALESCE(s.profit_low, s.expected_profit) AS p,
               r.raw_json
        FROM scored_listings s
        JOIN raw_listings r
          ON r.court = s.court AND r.case_no = s.case_no AND r.item_no = s.item_no
        """
    ).fetchall()
    out = []
    done = set()
    if not refresh:
        done = {(x["court"], x["case_no"], x["item_no"])
                for x in conn.execute("SELECT court, case_no, item_no FROM listing_rights")}
    for r in rows:
        if (r["court"], r["case_no"], r["item_no"]) in done:
            continue
        try:
            bo = json.loads(r["raw_json"]).get("boCd") or ""
        except json.JSONDecodeError:
            bo = ""
        if not bo:
            continue
        out.append({"court": r["court"], "case_no": r["case_no"], "item_no": r["item_no"],
                    "bo_cd": bo, "fail": r["fail_count"] or 0, "p": r["p"]})
    # 우선순위: 보수차익 양수 먼저(값 큰 순) → 그 외는 유찰 많은 순
    out.sort(key=lambda x: (-(x["p"] or 0) if (x["p"] or 0) > 0 else 0, -x["fail"]))
    return out[:limit] if limit else out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="물건상세 권리 요지 배치 크롤")
    ap.add_argument("--db", default=os.environ.get("AUCTION_DB", "auction.db"))
    ap.add_argument("--limit", type=int, default=200, help="크롤 물건 수 상한(기본 200)")
    ap.add_argument("--all", action="store_true", help="전 물건(limit 무시)")
    ap.add_argument("--refresh", action="store_true", help="이미 있는 물건도 재크롤")
    ap.add_argument("--no-cloud", action="store_true", help="Supabase 미러링 생략")
    args = ap.parse_args(argv)
    _load_env()

    conn = store.connect(args.db)
    targets = _targets(conn, None if args.all else args.limit, args.refresh)
    print(f"[*] 대상 {len(targets)}건 (DB={args.db}, 기존 크롤분 제외={not args.refresh})")
    if not targets:
        return 0

    client = CourtAuctionClient()
    ok, fail, skipped_empty = 0, 0, 0
    batch: list[dict] = []
    now = datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S")
    try:
        for i, t in enumerate(targets, 1):
            try:
                dma = client.case_detail(t["bo_cd"], t["case_no"], t["item_no"] or "1")
            except CourtAuctionBlocked:
                raise  # 차단 신호는 즉시 전체 중단(우회 금지)
            except CourtAuctionError as e:
                fail += 1
                print(f"  [{i}/{len(targets)}] {t['case_no']} 실패: {e}")
                continue
            cr = normalize(dma, court=t["court"], case_no=t["case_no"],
                           item_no=t["item_no"], fetched_at=now)
            if cr.is_empty:
                # (재검증 감사 idx17) 빈/부분 응답은 저장하지 않는다 — 저장하면 clean 배지로
                # 오판돼 '낙찰 후 추가 인수 없음'이라는 거짓 안전 신호가 된다.
                skipped_empty += 1
                print(f"  [{i}/{len(targets)}] {t['case_no']} 빈 명세서 응답 — 스킵(미확인 유지)")
                continue
            batch.append(cr.to_row())
            ok += 1
            if i % 10 == 0:
                store.save_rights(conn, batch)
                print(f"  [{i}/{len(targets)}] 적재 누적 {ok}건 (실패 {fail}·빈응답 {skipped_empty})")
                batch = []
    except CourtAuctionBlocked as e:
        print(f"[!] 차단/상한 신호로 중단(수집분은 저장됨): {e}", file=sys.stderr)
    finally:
        # (재검증 감사 idx18) 마지막 타깃이 실패/스킵이어도 잔여 batch 는 반드시 저장 —
        # 'i == len(targets)' 조건은 continue 경로에서 건너뛰어져 최대 9건이 무경고 유실됐다.
        if batch:
            store.save_rights(conn, batch)
            print(f"  잔여 배치 저장 {len(batch)}건 (적재 총 {ok}·실패 {fail}·빈응답 {skipped_empty})")

    total = conn.execute("SELECT COUNT(*) FROM listing_rights").fetchone()[0]
    print(f"[+] listing_rights 총 {total}건")

    if not args.no_cloud and store_rest.enabled():
        # 품질 게이트: 전 배치 PASS 여야 서빙 반영(틀린 권리 요지가 조용히 상세 페이지에
        # 노출되는 것을 차단). FAIL 이면 로컬엔 남기되 미러는 건너뛴다.
        from src import data_gates  # noqa: PLC0415
        gates = data_gates.run_gates(conn)
        print(data_gates.report(gates))
        if not data_gates.all_pass(gates):
            print("[!] 품질 게이트 FAIL — Supabase 미러링 건너뜀(로컬은 저장됨).",
                  file=sys.stderr)
            conn.close()
            return 1
        try:
            rows = [dict(r) for r in conn.execute("SELECT * FROM listing_rights")]
            n = store_rest.upsert_rights(rows)
            print(f"[+] Supabase 미러링 {n}건")
        except Exception as e:  # noqa: BLE001 — 클라우드 실패는 로컬 결과를 깨지 않음
            print(f"[!] Supabase 미러링 실패(로컬은 저장됨): {e}", file=sys.stderr)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
