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
from src import casesearch, photo, photo_store, store, store_rest
from src.courtauction_client import CourtAuctionBlocked, CourtAuctionClient, CourtAuctionError
from src.courtauction_detail import (
    detail_schema_drift,
    extract_photos,
    normalize,
    parse_curst_survey,
)

_KST = timezone(timedelta(hours=9))
# 물건당 저장 사진 수 상한 — 히어로 스와이프용. Supabase 공유티어(500MB) 용량 때문에 무제한은
# 지양(전물건 전사진=1GB+). 대부분 물건이 이 이하이므로 사실상 '거의 전부'. 환경변수로 조정.
PHOTO_CAP = int(os.environ.get("AUCTION_PHOTO_CAP", "12"))


def _targets(conn, limit: int | None, refresh: bool,
             estimable_only: set | None = None, skip: set | None = None) -> list[dict]:
    """크롤 대상 (boCd, case_no, item_no, 우선순위 정렬). raw_listings에서 법원코드 조인.

    estimable_only 지정 시 사진 저장 대상(시세추정 가능)만 남긴다 — 사진 백필용.
    skip 지정 시 (court,case_no,str(item_no)) 정규화 키가 일치하는 물건을 제외한다 —
    이어받기(resume)용. 예: 이미 사진을 확보한 물건을 건너뛰어 네트워크 단절 후 재개.
    """
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
        key_norm = (r["court"], r["case_no"], str(r["item_no"] or ""))
        if (r["court"], r["case_no"], r["item_no"]) in done:
            continue
        if estimable_only is not None and key_norm not in estimable_only:
            continue
        if skip is not None and key_norm in skip:
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
    ap.add_argument("--estimable", action="store_true",
                    help="사진 저장 대상(시세추정 가능)만 크롤 — 사진 백필용(limit 무시, refresh 함의). "
                         "기본은 이어받기: 이미 사진 있는 물건 건너뜀(--force로 전량 재크롤)")
    ap.add_argument("--force", action="store_true",
                    help="--estimable 이어받기 무시하고 사진 있는 물건도 전량 재크롤")
    ap.add_argument("--refresh", action="store_true", help="이미 있는 물건도 재크롤")
    ap.add_argument("--no-cloud", action="store_true", help="Supabase 미러링 생략")
    ap.add_argument("--cap", type=int, default=None,
                    help="일일 요청 상한 오버라이드(기본 500=안티밴 서킷). 대량 백필 시 상향. "
                         "403/위장차단 감지는 이 값과 무관하게 항상 즉시 중단(우회 아님).")
    ap.add_argument("--min-interval", type=float, default=None,
                    help="요청 간 최소 지연(초). 기본 3.0. 낮추면 빠르지만 밴 위험↑(단일IP 유지, 프록시 금지).")
    ap.add_argument("--max-interval", type=float, default=None,
                    help="요청 간 최대 지연(초). 기본 8.0.")
    args = ap.parse_args(argv)
    _load_env()

    conn = store.connect(args.db)
    # --estimable: 사진 대상만 재크롤(기존 rights 있어도 사진 백필 위해 refresh 함의).
    # 기본은 이어받기 — 이미 사진 확보한 물건은 skip(네트워크 단절 후 재개에 안전). --force 시 전량.
    est_only = store.estimable_keys(conn) if args.estimable else None
    skip = None
    if args.estimable and not args.force:
        skip = {(x["court"], x["case_no"], str(x["item_no"] or ""))
                for x in conn.execute("SELECT court, case_no, item_no FROM listing_photos")}
    refresh = args.refresh or args.estimable
    limit = None if (args.all or args.estimable) else args.limit
    targets = _targets(conn, limit, refresh, estimable_only=est_only, skip=skip)
    print(f"[*] 대상 {len(targets)}건 (DB={args.db}, 기존 크롤분 제외={not args.refresh})")
    if not targets:
        return 0

    client_kw = {}
    if args.cap:
        client_kw["daily_cap"] = args.cap
    if args.min_interval is not None:
        client_kw["min_interval"] = args.min_interval
    if args.max_interval is not None:
        client_kw["max_interval"] = args.max_interval
    client = CourtAuctionClient(**client_kw)
    # 임차인 현황(현황조사서) 동시 수집 — 물건당 +1 요청이라 밴 위험↑. 기본 OFF,
    # AUCTION_CRAWL_TENANTS=1 일 때만 켠다(대항력 실판정 원천. 크롤 완전 휴지기·소량부터 검증).
    crawl_tenants = os.environ.get("AUCTION_CRAWL_TENANTS") == "1"
    ok, fail, skipped_empty, photo_n, tenant_n = 0, 0, 0, 0, 0
    mismatch = 0          # (C6) 응답 사건번호 불일치로 스킵한 건(오사건 저장 차단)
    drift = 0             # (H4) 응답 스키마 드리프트(필드명 변경) 감지 건 — 침묵실패 조기경보
    blocked = False       # (C5) 차단/상한 신호로 중단됐는지 — exit code 승격용
    batch: list[dict] = []
    now = datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S")
    # 사진은 용량 때문에 '시세추정 가능' 물건에만 저장(사용자가 여는 물건 ≈ 평가 가능한 것).
    estimable = store.estimable_keys(conn)
    _use_storage = photo_store.enabled() and photo_store.ensure_bucket()
    if _use_storage:
        print("[+] 사진=Supabase Storage 업로드 모드")
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
            except Exception as e:  # noqa: BLE001 — (버그수정 2026-07-21) 개별 물건의 네트워크 리셋
                # (ConnectionReset 10054 등)이 _warm_session 등 retry-미포함 경로에서 안 잡혀 크롤
                # 전체를 크래시시키던 것. 한 물건 실패는 건너뛰고 계속(크래시<미탐<완주). 차단은 위에서 처리.
                fail += 1
                print(f"  [{i}/{len(targets)}] {t['case_no']} 네트워크/기타 실패(스킵): {type(e).__name__}")
                continue
            # (C6) 응답이 요청한 사건번호와 일치하는지 대조 — 서버 캐시 이상/경합으로 다른 사건
            # 응답이 와도 그대로 저장하면 '엉뚱한 사건의 권리'가 적재되고 재크롤 대상에서도 빠져
            # 진짜 권리가 영구 유실된다. 표기차(공백/하이픈/'타경')로 인한 **오거부를 막기 위해**
            # casesearch.parse_case_query 로 (연도,일련) canonical 환원 후 비교 — 둘 다 파싱돼
            # **확실히 다를 때만** 스킵한다(파싱 실패 시엔 저장 강행: 미탐<오거부).
            resp_raw = str((dma.get("csBaseInfo") or {}).get("userCsNo") or "")
            resp_q = casesearch.parse_case_query(resp_raw)
            want_q = casesearch.parse_case_query(str(t["case_no"] or ""))
            resp_c = resp_q.canonical if resp_q else None
            want_c = want_q.canonical if want_q else None
            if resp_c and want_c and resp_c != want_c:
                mismatch += 1
                print(f"  [{i}/{len(targets)}] {t['case_no']} 응답 사건번호 불일치"
                      f"(resp={resp_raw!r}) — 스킵(오사건 저장 차단)")
                continue
            # (H4) 스키마 드리프트 카나리 — 필드명이 바뀌어 요지가 통째로 빈 값이 되는 침묵실패
            # (전 물건 '발견 안 됨' 오표시)를 조기 경보. 감지돼도 저장은 진행(빈 요지는 아래 is_empty
            # 가드가 스킵)하되 건수를 집계해, 드리프트율이 높으면 아래에서 비정상 종료로 알린다.
            reason = detail_schema_drift(dma)
            if reason:
                drift += 1
                print(f"  [{i}/{len(targets)}] {t['case_no']} ⚠ 스키마 드리프트: {reason}",
                      file=sys.stderr)
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
            # 임차인 현황(현황조사서) — 대항력 실판정(전입일 vs 말소기준일) 원천. case_detail
            # '직후 같은 세션'이라 세션 컨텍스트 충족(선행 상세 없이는 빈 응답). 물건당 +1 요청.
            if crawl_tenants:
                try:
                    survey = client.case_curst_survey(t["bo_cd"], t["case_no"])
                    tenants = parse_curst_survey(survey)
                    store.save_tenants(conn, t["court"], t["case_no"], t["item_no"],
                                       tenants, fetched_at=now)
                    tenant_n += sum(1 for x in tenants if x.get("is_tenant_like"))
                except CourtAuctionBlocked:
                    raise  # 차단은 전체 중단(우회 금지)
                except Exception as e:  # noqa: BLE001 — 현황조사서 실패는 권리 크롤을 막지 않음
                    print(f"  [{i}/{len(targets)}] {t['case_no']} 현황조사서 실패(무시): "
                          f"{type(e).__name__}")
            # 사진 썸네일 — 같은 pgj15B 응답에서 추출(추가 요청 0), 시세추정 물건만 저장.
            # Storage 가능하면 업로드→URL 저장(DB 경량), 아니면 base64 폴백(로컬 개발).
            key = (t["court"], t["case_no"], str(t["item_no"] or ""))
            if key in estimable:
                # (버그수정 2026-07-21) 사진 업로드/저장 실패(Supabase Storage 연결 리셋 등)가 크롤
                # 전체를 크래시시키던 것 — 사진은 부수 기능이라 실패해도 권리 크롤은 계속돼야 한다.
                # 권리(batch)는 이미 append됐으므로 사진만 건너뛴다.
                try:
                    jpegs = [j for r in extract_photos(dma, cap=PHOTO_CAP)
                             if (j := photo.thumbnail_jpeg(r))]
                    if jpegs and _use_storage:
                        urls = [u for s, j in enumerate(jpegs)
                                if (u := photo_store.upload_photo(j, *key, s))]
                        if urls:
                            store.save_photo_urls(conn, *key, urls, fetched_at=now)
                            photo_n += len(urls)
                    elif jpegs:
                        import base64 as _b64  # noqa: PLC0415
                        thumbs = [_b64.b64encode(j).decode("ascii") for j in jpegs]
                        store.save_photos(conn, *key, thumbs, fetched_at=now)
                        photo_n += len(thumbs)
                except Exception as e:  # noqa: BLE001 — 사진 실패는 권리 크롤을 막지 않음
                    print(f"  [{i}/{len(targets)}] {t['case_no']} 사진 처리 실패(무시): {type(e).__name__}")
            if i % 10 == 0:
                store.save_rights(conn, batch)
                print(f"  [{i}/{len(targets)}] 적재 누적 {ok}건 (실패 {fail}·빈응답 {skipped_empty})")
                batch = []
    except CourtAuctionBlocked as e:
        blocked = True   # (C5) 차단은 성공으로 위장하면 안 됨 — 아래에서 exit code 비0으로 승격
        print(f"[!] 차단/상한 신호로 중단(수집분은 저장됨): {e}", file=sys.stderr)
    finally:
        # (재검증 감사 idx18) 마지막 타깃이 실패/스킵이어도 잔여 batch 는 반드시 저장 —
        # 'i == len(targets)' 조건은 continue 경로에서 건너뛰어져 최대 9건이 무경고 유실됐다.
        if batch:
            store.save_rights(conn, batch)
            print(f"  잔여 배치 저장 {len(batch)}건 (적재 총 {ok}·실패 {fail}·빈응답 {skipped_empty})")

    total = conn.execute("SELECT COUNT(*) FROM listing_rights").fetchone()[0]
    photos_total = conn.execute("SELECT COUNT(*) FROM listing_photos").fetchone()[0]
    print(f"[+] listing_rights 총 {total}건 · 사진 이번 {photo_n}장(누적 {photos_total}장)")
    if crawl_tenants:
        tenants_total = conn.execute("SELECT COUNT(*) FROM listing_tenants").fetchone()[0]
        print(f"[+] 임차인(대항력 후보) 이번 {tenant_n}명 · listing_tenants 누적 {tenants_total}행")
    if mismatch:
        print(f"[!] 응답 사건번호 불일치로 스킵 {mismatch}건(오사건 저장 차단됨).", file=sys.stderr)
    if drift:
        print(f"[!] (H4) 응답 스키마 드리프트 {drift}건 감지 — 법원 API 필드명 변경 가능성.",
              file=sys.stderr)

    # (C5) exit code 종합 — 스케줄러가 '부분 중단'을 '완전 성공'과 구분하게 한다.
    #  0=정상, 2=차단/상한으로 중단, 3=처리대상 중 실패율 과다 또는 (H4)스키마 드리프트율 과다.
    exit_code = 0
    processed = ok + skipped_empty          # 실제 응답을 받아 파싱까지 간 건(드리프트율 분모)
    if blocked:
        exit_code = 2
    elif targets and fail / len(targets) > 0.5:
        print(f"[!] 실패율 과다({fail}/{len(targets)}) — 스키마 변경/차단 의심. 비정상 종료로 알림.",
              file=sys.stderr)
        exit_code = 3
    elif processed >= 20 and drift / processed > 0.3:
        # (H4) 응답은 오는데 요지 필드가 대량으로 사라짐 = 필드명 변경 강한 신호. 조용히 전 물건을
        # '발견 안 됨'으로 적재하지 않도록 비정상 종료로 사람을 부른다(침묵실패 방어).
        print(f"[!] (H4) 스키마 드리프트율 과다({drift}/{processed}) — 파서-응답 불일치. "
              f"비정상 종료로 알림(파서 점검 필요).", file=sys.stderr)
        exit_code = 3

    if args.no_cloud or not store_rest.enabled():
        # (C5) 미러링을 건너뛰는 이유를 명시 — 서빙(Supabase)이 조용히 정체되는 침묵 실패 방지.
        if not args.no_cloud:
            print("[!] Supabase 미러링 비활성(store_rest 환경변수 미설정) — 로컬만 갱신됨. "
                  "서빙 데이터는 이번 크롤이 반영되지 않았다.", file=sys.stderr)
        conn.close()
        return exit_code

    if store_rest.enabled():
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
            print(f"[+] Supabase 권리 미러링 {n}건")
        except Exception as e:  # noqa: BLE001 — 클라우드 실패는 로컬 결과를 깨지 않음
            print(f"[!] Supabase 권리 미러링 실패(로컬은 저장됨): {e}", file=sys.stderr)
        try:
            prows = [dict(r) for r in conn.execute("SELECT * FROM listing_photos")]
            pn = store_rest.upsert_photos(prows)
            print(f"[+] Supabase 사진 미러링 {pn}장")
        except Exception as e:  # noqa: BLE001 — 사진 테이블 미배포/실패는 조용히 skip
            print(f"[!] Supabase 사진 미러링 skip(테이블 미배포?): {e}", file=sys.stderr)
    conn.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
