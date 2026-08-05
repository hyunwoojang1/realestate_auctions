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
from src.courtauction_client import (
    BUDGET_FILE,
    CourtAuctionBlocked,
    CourtAuctionClient,
    CourtAuctionError,
)
from src.courtauction_detail import (
    curst_has_context,
    detail_schema_drift,
    extract_photos,
    normalize,
    parse_curst_survey,
)

_KST = timezone(timedelta(hours=9))
# 물건당 저장 사진 수 상한 — 히어로 스와이프용. Supabase 공유티어(500MB) 용량 때문에 무제한은
# 지양(전물건 전사진=1GB+). 대부분 물건이 이 이하이므로 사실상 '거의 전부'. 환경변수로 조정.
PHOTO_CAP = int(os.environ.get("AUCTION_PHOTO_CAP", "12"))
# ── 증분 크롤 인프라 (2026-07-23 저녁 배선) ─────────────────────────────────────────
# 법원엔 '변경 피드' API가 없다 → 증분은 우리가 diff 로 만든다.
#   발견(리스트 전량, 싸다) → 대조(신규/변경/소멸, 공짜) → 보강(상세, 비싸니 신규+변경만)
#
# tenant_checks: 현황조사서 '시도 완료' 영속 마커. 현황조사서가 빈(공실 등) 물건은 listing_tenants
# 에 행이 안 생겨 'lt IS NULL' 필터에 영원히 걸린다 — 종전엔 당일 fetched_at 컷오프 해크로 막았는데
# (일회성), 이 테이블이 그 자리를 영속으로 대체한다. ipcheck=true 유효 응답을 받은 물건만 기록
# (소프트차단·네트워크 실패는 미기록 → 자연 재시도).


def _ensure_tenant_checks(conn) -> None:
    """tenant_checks 마커 테이블 보장 + 임차인 보유 물건 자동 시드(있음=확실히 시도됨, 멱등)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tenant_checks (
            court TEXT NOT NULL DEFAULT '',
            case_no TEXT NOT NULL,
            item_no TEXT NOT NULL DEFAULT '',
            checked_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (court, case_no, item_no)
        )""")
    conn.execute("""
        INSERT OR IGNORE INTO tenant_checks (court, case_no, item_no, checked_at)
        SELECT court, case_no, item_no, MAX(fetched_at) FROM listing_tenants
        GROUP BY court, case_no, item_no""")
    conn.commit()


def _record_tenant_check(conn, court: str, case_no: str, item_no: str, at: str) -> None:
    """현황조사서 유효 조회(ipcheck=true) 완료 기록 — 빈 결과여도 '시도 완료'로 남겨 재크롤 낭비 방지."""
    conn.execute("INSERT OR REPLACE INTO tenant_checks (court, case_no, item_no, checked_at) "
                 "VALUES (?, ?, ?, ?)", (court, case_no, str(item_no or ""), at))
    conn.commit()


# 재보강 판정에 쓰는 기일 종류 — sale_date(매각기일)와 대응되는 이벤트만 본다.
# 매각결정기일(매각기일+~1주)까지 포함하면 '기일변경으로 앞당겨진' 물건을 놓친다.
_SALE_KINDS = ("매각기일", "개찰기일")


def _stale_rights_keys(conn) -> set:
    """변경 재보강 대상: 리스트(scored)의 현재 매각기일이 저장된 요지 schedule 에 없는 물건.

    유찰→새 회차, 기일변경이 나면 리스트는 새 sale_date 를 갖지만 listing_rights.schedule 은
    옛 회차 그대로다(권리는 1회 크롤 후 방치돼 왔다 — '변경' 축의 공백). 명세서는 회차마다
    갱신(작성일·최저가·인수문구)될 수 있으므로 재크롤이 필요하다.
    판정: schedule 에 kind∈_SALE_KINDS 이고 ymd >= 현재 sale_date 인 이벤트가 **없으면** stale.
    (미래 sale_date 물건만 — 지난 기일 물건 재크롤은 낭비.)
    """
    today = datetime.now(_KST).strftime("%Y-%m-%d")
    stale: set = set()
    rows = conn.execute("""
        SELECT s.court, s.case_no, s.item_no, s.sale_date, lr.schedule
        FROM scored_listings s
        JOIN listing_rights lr
          ON lr.court = s.court AND lr.case_no = s.case_no AND lr.item_no = s.item_no
        WHERE s.sale_date >= ?
    """, (today,)).fetchall()
    for court, case_no, item_no, sale_date, sched in rows:
        try:
            events = json.loads(sched) if sched else []
        except json.JSONDecodeError:
            events = []
        covered = any((e.get("ymd") or "") >= sale_date and e.get("kind") in _SALE_KINDS
                      for e in events if isinstance(e, dict))
        if not covered:
            stale.add((court, case_no, item_no))
    return stale


def _targets(conn, limit: int | None, refresh: bool,
             estimable_only: set | None = None, skip: set | None = None,
             stale: set | None = None, only_sold: bool = False) -> list[dict]:
    """크롤 대상 (boCd, case_no, item_no, 우선순위 정렬). raw_listings에서 법원코드 조인.

    estimable_only 지정 시 사진 저장 대상(시세추정 가능)만 남긴다 — 사진 백필용.
    skip 지정 시 (court,case_no,str(item_no)) 정규화 키가 일치하는 물건을 제외한다 —
    이어받기(resume)용. 예: 이미 사진을 확보한 물건을 건너뛰어 네트워크 단절 후 재개.
    stale 지정 시(2026-07-23 변경축) 이미 크롤된 물건이라도 그 키는 done 에서 빼서 **재크롤**한다 —
    유찰 새 회차·기일변경으로 저장된 요지가 낡은 물건(_stale_rights_keys 참조).
    """
    # ⚠ court 를 조인에 반드시 포함(감사 2026-07-10 CRITICAL): 사건번호는 법원별 독립 채번이라
    # court 없이 조인하면 타법원 동명 사건의 boCd 로 크롤해 '엉뚱한 사건의 권리'가 적재된다.
    # (2026-07-28) 낙찰 기록(sold_listings)도 대상에 넣는다 — 종결 물건은 권리 요지가 없어
    # 점수(arb_score)가 영영 안 매겨지고 '권리미확인'으로 남는다. --only-sold 면 낙찰분만.
    src_table = "sold_listings" if only_sold else "scored_listings"
    p_expr = ("COALESCE(s.profit_low, s.expected_profit)" if not only_sold
              else "COALESCE(s.profit_low, s.expected_profit)")
    rows = conn.execute(
        f"""
        SELECT s.court, s.case_no, s.item_no, s.fail_count,
               {p_expr} AS p,
               r.raw_json
        FROM {src_table} s
        JOIN raw_listings r
          ON r.court = s.court AND r.case_no = s.case_no AND r.item_no = s.item_no
        """  # noqa: S608 — 테이블·식은 코드 상수(사용자 입력 아님)
    ).fetchall()
    out = []
    done = set()
    # (D2 2026-07-22 QA HIGH) JOIN(scored⋈raw) 은 raw_listings 다-docid(재크롤·이력) 때문에 같은
    # (court,case_no,item_no)에 여러 행을 낸다(실측 raw 37528 vs distinct 26286). 중복제거 없이는
    # 같은 사건에 최대 52배 상세요청이 나가 밴 예산(500/일)을 낭비하고 커버리지가 정체된다.
    seen: set = set()
    if not refresh:
        done = {(x["court"], x["case_no"], x["item_no"])
                for x in conn.execute("SELECT court, case_no, item_no FROM listing_rights")}
        if stale:
            done -= stale     # 낡은 요지(새 회차 미반영)는 '크롤됨'에서 제외 → 재보강 대상으로 환원
    for r in rows:
        key_norm = (r["court"], r["case_no"], str(r["item_no"] or ""))
        if key_norm in seen:                 # 이미 이 사건을 타깃에 넣음(팬아웃 중복) — 스킵
            continue
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
            continue                         # boCd 없는 raw 행 — 같은 키의 다른 행이 채울 수 있게 seen 미표시
        seen.add(key_norm)
        out.append({"court": r["court"], "case_no": r["case_no"], "item_no": r["item_no"],
                    "bo_cd": bo, "fail": r["fail_count"] or 0, "p": r["p"]})
    # 우선순위: 보수차익 양수 먼저(값 큰 순) → 그 외는 유찰 많은 순
    out.sort(key=lambda x: (-(x["p"] or 0) if (x["p"] or 0) > 0 else 0, -x["fail"]))
    return out[:limit] if limit else out


# P-08 추천계열 등급 — 이 물건들이 현황조사서 검증 없이 초록으로 팔리는 것이 가장 위험한 오류다.
_RECO_GRADES = ("차익 유력", "양호", "관심")


def _tenant_targets(conn, limit: int | None) -> list[dict]:
    """현황조사서(B-2) 백필 대상 — tenant_checks 미기록(=미시도) + in-scope 물건.

    공통 조건: listing_rights 존재(말소기준 有 → 여지 판정 가능) · 미지원유형 제외 ·
    매각기일이 아직 안 지남(지난 물건 크롤은 낭비) · tenant_checks 미기록(빈 결과도 기록되므로
    재시작/일일 반복에도 같은 물건을 재크롤하지 않는다 — 종전 fetched_at 컷오프 해크 대체).

    대상 클래스 2종 + 우선순위(감사 P-08 반영):
      prio 0: **추천등급 + 인수권리란 빈칸** — 요지가 '아무 말 안 함'을 근거로 안전 판정된 추천
              물건(실측 205건). 종전 필터(rights_verified=0)는 이들을 구조적으로 영원히 배제했다
              ("이미 '미확인' 표시된 안전한 물건만 겨누고, 초록으로 팔리는 물건은 검증 안 함" —
              감사 헌장 §0-① 정면 위배). 위험 검증 우선으로 예산 재정렬.
      prio 1: 권리미확인 등급(rights_verified=0) — 삼환 클래스(요지 빈칸 대항력 미해결).
      prio 2: 그 외 rights_verified=0.
    물건당 case_detail(세션컨텍스트)+현황조사서 = 2요청.
    """
    today = datetime.now(_KST).strftime("%Y-%m-%d")
    rows = conn.execute(
        """
        SELECT s.court, s.case_no, s.item_no, s.fail_count, s.grade,
               s.rights_verified, lr.surviving_rights, r.raw_json
        FROM scored_listings s
        JOIN raw_listings r
          ON r.court = s.court AND r.case_no = s.case_no AND r.item_no = s.item_no
        JOIN listing_rights lr
          ON lr.court = s.court AND lr.case_no = s.case_no AND lr.item_no = s.item_no
         AND TRIM(COALESCE(lr.senior_lien, '')) <> ''
        LEFT JOIN tenant_checks tc
          ON tc.court = s.court AND tc.case_no = s.case_no AND tc.item_no = s.item_no
        WHERE tc.case_no IS NULL
          AND s.grade <> '미지원유형'
          AND s.sale_date >= ?
          AND ( s.rights_verified = 0
                OR (s.grade IN (?, ?, ?)
                    AND TRIM(COALESCE(lr.surviving_rights, '')) = '') )
        """,
        (today, *_RECO_GRADES),
    ).fetchall()
    seen: set = set()
    out = []
    for r in rows:
        key = (r["court"], r["case_no"], str(r["item_no"] or ""))
        if key in seen:                      # scored⋈raw 다-docid 팬아웃 중복 제거(D2와 동일)
            continue
        try:
            bo = json.loads(r["raw_json"]).get("boCd") or ""
        except json.JSONDecodeError:
            bo = ""
        if not bo:
            continue
        seen.add(key)
        if r["grade"] in _RECO_GRADES:
            prio = 0                          # P-08: 추천인데 검증 원천이 막혀 있던 클래스 최우선
        elif r["grade"] == "권리미확인":
            prio = 1
        else:
            prio = 2
        out.append({"court": r["court"], "case_no": r["case_no"], "item_no": r["item_no"],
                    "bo_cd": bo, "fail": r["fail_count"] or 0, "prio": prio})
    out.sort(key=lambda x: (x["prio"], -x["fail"]))   # 추천+빈요지 → 권리미확인 → 기타
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
    ap.add_argument("--only-sold", dest="only_sold", action="store_true",
                    help="낙찰 기록(sold_listings)만 대상 — 활성 물건 큐는 건드리지 않는다")
    ap.add_argument("--tenants-backfill", action="store_true",
                    help="현황조사서(B-2) 백필 모드: 미시도(tenant_checks 無)·말소기준有·미래기일 물건. "
                         "우선순위 = 추천등급+인수권리란 빈칸(P-08) → 권리미확인 → 기타 verified=0. "
                         "현황조사서 수집 강제 ON. 물건당 case_detail+현황조사서=2요청.")
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
    _ensure_tenant_checks(conn)               # 마커 테이블 보장(+임차인 보유분 자동 시드, 멱등)
    if args.tenants_backfill:
        targets = _tenant_targets(conn, limit)
        n_reco = sum(1 for t in targets if t["prio"] == 0)
        print(f"[*] 현황조사서 백필 대상 {len(targets)}건 "
              f"(추천+빈요지 {n_reco}·권리미확인계열 {len(targets) - n_reco} · 미시도만)")
    else:
        # (2026-07-23 변경축) 유찰 새 회차·기일변경으로 요지가 낡은 물건은 재크롤 대상에 환원.
        stale = _stale_rights_keys(conn) if not refresh else set()
        targets = _targets(conn, limit, refresh, estimable_only=est_only, skip=skip,
                           stale=stale, only_sold=args.only_sold)
        n_stale = sum(1 for t in targets
                      if (t["court"], t["case_no"], t["item_no"]) in stale)
        print(f"[*] 대상 {len(targets)}건 (DB={args.db}, 기존 크롤분 제외={not args.refresh}, "
              f"기일갱신 재보강 {n_stale}건 포함/전체 stale {len(stale)}건)")
    if not targets:
        return 0

    # (D1 2026-07-22) 권리 크롤은 차단/실패로 자주 재시작되는데, 종전엔 재시작마다 daily_cap이 0에서
    # 새로 시작해 하루 6119콜(상한 12배)이 나갔다. 당일 요청수를 파일에 영속해 재시작이 예산을 이어받게.
    client_kw = {"budget_file": BUDGET_FILE}
    if args.cap:
        client_kw["daily_cap"] = args.cap
    if args.min_interval is not None:
        client_kw["min_interval"] = args.min_interval
    if args.max_interval is not None:
        client_kw["max_interval"] = args.max_interval
    client = CourtAuctionClient(**client_kw)
    # 임차인 현황(현황조사서) 동시 수집 — 물건당 +1 요청이라 밴 위험↑. 기본 OFF,
    # AUCTION_CRAWL_TENANTS=1 일 때만 켠다(대항력 실판정 원천. 크롤 완전 휴지기·소량부터 검증).
    crawl_tenants = os.environ.get("AUCTION_CRAWL_TENANTS") == "1" or args.tenants_backfill
    ok, fail, skipped_empty, photo_n, tenant_n = 0, 0, 0, 0, 0
    photo_fail = 0        # 업로드 실패 장수(부분 실패 포함) — 요약·경고용
    photo_skipped = 0     # 스토리지 불가로 저장 자체를 건너뛴 장수(base64 적재 봉쇄분)
    mismatch = 0          # (C6) 응답 사건번호 불일치로 스킵한 건(오사건 저장 차단)
    drift = 0             # (H4) 응답 스키마 드리프트(필드명 변경) 감지 건 — 침묵실패 조기경보
    blocked = False       # (C5) 차단/상한 신호로 중단됐는지 — exit code 승격용
    batch: list[dict] = []
    now = datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S")
    # 사진은 용량 때문에 '시세추정 가능' 물건에만 저장(사용자가 여는 물건 ≈ 평가 가능한 것).
    estimable = store.estimable_keys(conn)
    _backend = photo_store.backend()
    _use_storage = photo_store.enabled() and photo_store.ensure_bucket()
    # base64 폴백은 **명시 허용이 있을 때만**. 종전엔 오브젝트 스토리지가 잠깐 안 되면 조용히
    # base64 로 DB에 쌓았는데, 그게 무료티어 DB 500MB를 터뜨린 원래 원인이다(사진만 ~500MB).
    # 스토리지를 쓰기로 해놓고 못 쓰는 상태면 '사진 없이 권리만' 진행하고 끝에서 크게 경고한다.
    _allow_b64 = os.environ.get("AUCTION_ALLOW_BASE64_PHOTOS") == "1"
    if _use_storage:
        print(f"[+] 사진 업로드 모드: {_backend}")
        if _backend != "r2":
            print(f"[!] 사진 백엔드가 r2 가 아니라 '{_backend}' 다 — R2_* 5종 환경변수를 확인하라"
                  " (2026-08-05 R2 이전 완료, Supabase 사진 버킷은 더 쓰지 않는다).",
                  file=sys.stderr)
    else:
        print(f"[!] 사진 오브젝트 스토리지 사용 불가(backend={_backend or '미설정'}) — "
              + ("base64 폴백 허용(AUCTION_ALLOW_BASE64_PHOTOS=1)"
                 if _allow_b64 else "사진 저장 생략(권리 크롤은 계속). DB base64 적재 봉쇄."),
              file=sys.stderr)
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
            # (감사체계 2026-07-23) 원본 보존 — 파서를 거치기 전의 응답을 마스킹·압축 저장.
            # 블라인드 감사·사후 재파싱 재료. 실패해도 크롤은 계속(부수 기능).
            try:
                store.save_detail_raw(conn, t["court"], t["case_no"], t["item_no"],
                                      "pgj15B", dma, fetched_at=now)
            except Exception as e:  # noqa: BLE001
                print(f"  [{i}/{len(targets)}] {t['case_no']} raw 보존 실패(무시): {type(e).__name__}")
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
                    # (E3) ipcheck=false/errors = 소프트차단·컨텍스트없음 → '임차인 없음'이 아니다.
                    # 확정(ipcheck=true)일 때만 저장한다 — 빈 리스트로 save하면 기존 임차인 전량삭제.
                    if curst_has_context(survey):
                        # (감사체계 2026-07-23) 현황조사서 원본 보존 — 조사 서술문("소유자와의
                        # 관계를 알 수 없는 …")은 파싱 컬럼에 안 남으므로 raw가 유일한 기록.
                        try:
                            store.save_detail_raw(conn, t["court"], t["case_no"], t["item_no"],
                                                  "curst", survey, fetched_at=now)
                        except Exception as e:  # noqa: BLE001
                            print(f"  [{i}/{len(targets)}] {t['case_no']} curst raw 보존 실패(무시): "
                                  f"{type(e).__name__}")
                        tenants = parse_curst_survey(survey)
                        store.save_tenants(conn, t["court"], t["case_no"], t["item_no"],
                                           tenants, fetched_at=now)
                        # 유효 조회 완료 마커 — 빈 결과(공실 등)도 '시도됨'으로 남겨
                        # 일일 파이프라인이 같은 물건을 매일 재크롤하지 않게 한다.
                        _record_tenant_check(conn, t["court"], t["case_no"], t["item_no"], now)
                        tenant_n += sum(1 for x in tenants if x.get("is_tenant_like"))
                    else:
                        print(f"  [{i}/{len(targets)}] {t['case_no']} 현황조사서 미확정"
                              f"(ipcheck=false) — 임차인 저장 스킵(기존 보존)", file=sys.stderr)
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
                        # 부분 실패도 센다 — 종전엔 성공 장수만 세서 "업로드가 절반씩 실패 중"인
                        # 상황이 요약·exit code 어디에도 안 나타났다(침묵실패).
                        photo_fail += len(jpegs) - len(urls)
                    elif jpegs and _allow_b64:
                        import base64 as _b64  # noqa: PLC0415
                        thumbs = [_b64.b64encode(j).decode("ascii") for j in jpegs]
                        store.save_photos(conn, *key, thumbs, fetched_at=now)
                        photo_n += len(thumbs)
                    elif jpegs:
                        photo_skipped += len(jpegs)   # 스토리지 불가 → base64 로 흘리지 않는다
                except Exception as e:  # noqa: BLE001 — 사진 실패는 권리 크롤을 막지 않음
                    photo_fail += 1
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
    if photo_fail:
        print(f"[!] 사진 업로드 실패 {photo_fail}장(backend={_backend or '미설정'}) — "
              "오브젝트 스토리지 상태를 확인하라.", file=sys.stderr)
    if photo_skipped:
        print(f"[!] 사진 {photo_skipped}장을 저장하지 않고 건너뛰었다 — 오브젝트 스토리지 사용 불가. "
              "DB base64 적재는 의도적으로 봉쇄했다(무료티어 DB 한도 재발 방지). "
              "R2_* 설정을 고치고 재크롤하면 그 물건들이 다시 대상이 된다.", file=sys.stderr)

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
    elif (photo_fail + photo_skipped) and photo_n == 0 and (photo_fail + photo_skipped) >= 5:
        # 사진 대상이 있었는데 **한 장도** 저장되지 않았다 = 스토리지 경로가 통째로 죽은 것.
        # 권리 지표만 보면 정상이라 exit 0 으로 끝나던 침묵실패를 여기서 잡는다.
        print(f"[!] 사진이 한 장도 저장되지 않았다(실패 {photo_fail}·생략 {photo_skipped}) — "
              "오브젝트 스토리지 경로 점검 필요. 비정상 종료로 알림.", file=sys.stderr)
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
            # 이 미러는 로컬 listing_photos **전량**을 PK 기준 덮어쓰기로 올린다. 로컬에 옛
            # Supabase Storage URL 이 한 행이라도 남아 있으면, 이미 R2 로 옮겨둔 클라우드 행을
            # 죽은 URL 로 되돌린다(원본 버킷을 지운 뒤엔 복구 불가 = 사진 깨짐). 로컬이 클라우드보다
            # 항상 최신이라는 보장이 없으므로(실측: 로컬 18,535행 < 클라우드 37,850행)
            # **스테일 URL 은 아예 올리지 않는다.** 2026-08-05 R2 이전 리뷰에서 발견.
            prows, stale = photo_store.split_stale_rows(
                [dict(r) for r in conn.execute("SELECT * FROM listing_photos")])
            if stale:
                print(f"[!] 사진 미러링에서 스테일 Supabase URL {len(stale)}행 제외 — "
                      "로컬이 R2 이전을 되돌리는 것을 봉쇄. "
                      "`python -m deploy.migrate_photos_to_r2 --sync-local` 로 정리하라.",
                      file=sys.stderr)
            pn = store_rest.upsert_photos(prows)
            print(f"[+] 사진 미러링 {pn}장")
        except Exception as e:  # noqa: BLE001 — 사진 테이블 미배포/실패는 조용히 skip
            print(f"[!] 사진 미러링 skip(테이블 미배포?): {e}", file=sys.stderr)
        if crawl_tenants:
            # 현황조사서 크롤 시에만 임차인 미러(대항력 여지 원천). 테이블 미배포면 graceful skip.
            try:
                trows = [dict(r) for r in conn.execute("SELECT * FROM listing_tenants")]
                tn = store_rest.upsert_tenants(trows)
                print(f"[+] Supabase 임차인 미러링 {tn}행")
            except Exception as e:  # noqa: BLE001 — 임차인 테이블 미배포/실패는 조용히 skip
                print(f"[!] Supabase 임차인 미러링 skip(테이블 미배포?): {e}", file=sys.stderr)
            # (2026-07-25 V6) 점유관계 요지 미러 — 상세 신설 섹션의 클라우드 서빙 동등성.
            # 로컬은 curst 원본에서 즉석 파싱하지만 Vercel 은 이 테이블만 읽는다.
            try:
                svrows = store.survey_rows(conn)
                sn = store_rest.upsert_survey(svrows)
                print(f"[+] Supabase 점유관계 미러링 {sn}건")
            except Exception as e:  # noqa: BLE001 — 미배포/실패는 조용히 skip(로컬 서빙 무영향)
                print(f"[!] Supabase 점유관계 미러링 skip(테이블 미배포?): {e}", file=sys.stderr)
    conn.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
