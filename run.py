#!/usr/bin/env python
"""auction-arbitrage PoC 실행 엔트리.

  python run.py                          # 샘플 데이터로 차익 큐레이션
  python run.py --min-score 80           # 차익 스코어 80+ 만
  python run.py --type 아파트 --region 서울  # 필터
  python run.py --sort profit --json     # 예상차익순, JSON 출력
  python run.py --live --ym 202605       # 국토부 라이브(키 필요, F10)
  python run.py --source courtauction --cash 60000000 --sido 11 --live --ym 202605
                                         # 대법원 실경매 매물(가용현금 6천만·서울) → 시세매칭 차익 큐레이션
  python run.py --source courtauction --from-cache --cash 100000000
                                         # 오프라인 dry-run: 실크롤/라이브 미호출, 캐시/샘플로 파이프라인 실행
결과: 콘솔 랭킹표 + evidence/result.csv + evidence/result.html (--json이면 stdout JSON)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src import pipeline, query, report, store  # noqa: E402

EVID = ROOT / "evidence"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="경매 최저가 vs 실거래 시세 차익 큐레이션 PoC")
    ap.add_argument("--live", action="store_true", help="국토부 라이브 API 사용(MOLIT_API_KEY 필요)")
    ap.add_argument("--ym", help="조회 연월 YYYYMM (라이브 전용)")
    ap.add_argument("--source", choices=["sample", "courtauction"], default="sample",
                    help="경매물건 소스: sample(기본) | courtauction(대법원 실매물 라이브 크롤)")
    ap.add_argument("--cash", type=int, default=None,
                    help="가용현금(원). courtauction 소스에서 '최저가<=현금' 매물만(감정가버퍼로 서버축소)")
    ap.add_argument("--sido", default="", help="courtauction 시도코드(11=서울 …). 미지정=전국")
    # (재검증 감사 idx21) 기본 10p=400행은 부산 등 대형 시도에서 매일 수백 건을 조용히 잘랐다
    # → 25p=1000행으로 상향(17개 시도 × 25p = 425요청, daily_cap 500 내).
    ap.add_argument("--max-pages", type=int, default=25, help="courtauction 페이지 상한(1p=40건)")
    ap.add_argument("--appraisal-buffer", type=float, default=3.0,
                    help="affordable 감정가 상한 배수(현금×버퍼). 다회유찰 저가매물 누락 방지(기본 3)")
    ap.add_argument("--nationwide", action="store_true",
                    help="courtauction 전국 17개 시도 샤딩 수집(--sido 무시)")
    ap.add_argument("--from-cache", dest="from_cache", action="store_true",
                    help="오프라인 dry-run: courtauction 실크롤 대신 캐시/샘플 fixture로 파이프라인 실행"
                         "(네트워크 호출 0, use_live 강제 off)")
    ap.add_argument("--cache", default=None,
                    help="courtauction 증분 캐시 경로(기본 data/courtauction_cache.json). 신규/변경/소멸 리포트")
    ap.add_argument("--db", default=str(ROOT / "auction.db"), help="SQLite 경로")
    ap.add_argument("--min-profit", dest="min_profit", type=int, default=None,
                    help="보수 기준 차익 하한(원) 필터 — 검증 하한가 기준(밴드 없으면 기준 차익)")
    ap.add_argument("--min-score", type=float, default=None, help="(내부용) 점수 하한 필터")
    ap.add_argument("--type", dest="ptype", default=None, help="물건종류 필터(아파트/오피스텔/다세대 등)")
    ap.add_argument("--region", default=None, help="지역 필터(주소 prefix, 예: 서울/경기/부산)")
    ap.add_argument("--sort", choices=query.SORT_KEYS, default=query.DEFAULT_SORT,
                    help="정렬 기준(profit/gap/score, 기본 profit)")
    ap.add_argument("--json", action="store_true", help="결과를 JSON으로 stdout 출력")
    ap.add_argument("--no-cloud", dest="no_cloud", action="store_true",
                    help="Supabase 클라우드 미러링 비활성(기본: 라이브+SUPABASE_URL 설정 시 자동 미러링)")
    ap.add_argument("--live-months", dest="live_months", type=int, default=None,
                    help="라이브 시세 수집 개월수(표본 폭). 미지정=config/env/기본(3)")
    ap.add_argument("--area-band", dest="area_band", type=float, default=None,
                    help="매칭 전용면적 허용밴드(±비율, 예 0.15). 미지정=config/env/기본(0.10)")
    args = ap.parse_args(argv)

    # .env 로드(있으면)
    _load_dotenv(ROOT / ".env")

    # 표본 튜닝값 CLI 오버라이드 → config.SAMPLE 갱신(pipeline/matcher가 참조).
    _apply_sample_overrides(args.live_months, args.area_band)

    # --live(국토부 MOLIT 시세)와 --from-cache(courtauction 물건 소스=캐시, 재크롤 안 함)는 독립.
    #  --from-cache 단독      = 완전 오프라인(네트워크 0, 샘플/추정 시세) → dry-run.
    #  --from-cache --live    = 캐시 물건 + 라이브 시세(재크롤 없이 실시세로 재채점).
    use_live = args.live

    if not args.json:
        src = "대법원 courtauction 실매물" if args.source == "courtauction" else "샘플 물건"
        src_mode = "캐시(재크롤X)" if args.from_cache else ("전국 실크롤" if args.nationwide else "실크롤")
        price_mode = "라이브 국토부 시세" if use_live else "샘플/추정 시세(오프라인)"
        print(f"▶ 물건: {src} · {src_mode} · 시세: {price_mode}\n")

    if args.source == "courtauction":
        from src import courtauction_cache as cc  # noqa: PLC0415
        from src.courtauction_fields import to_auction_listing  # noqa: PLC0415

        if args.from_cache:
            records = pipeline.load_courtauction_from_cache(cache_path=args.cache)
            if args.cash:   # 로컬 '최저가<=현금' 정밀필터(라이브 affordable_search 대체)
                records = [r for r in records if 0 < r.min_bid_price <= args.cash]
        elif args.nationwide:
            records = pipeline.load_courtauction_nationwide(
                cash_won=args.cash, appraisal_buffer=args.appraisal_buffer,
                max_pages_per_sido=args.max_pages)
        else:
            records = pipeline.collect_courtauction_records(
                cash_won=args.cash, sido_cd=args.sido,
                appraisal_buffer=args.appraisal_buffer, max_pages=args.max_pages)

        # 증분 캐시 diff(신규/변경/소멸) 리포트 후 현재 스냅샷 저장.
        # dry-run(--from-cache)은 프로덕션 full-record 캐시를 덮어쓰지 않도록
        # 별도 스냅샷 경로(*.dryrun.json)에 diff 스냅샷을 남긴다.
        base_cache = args.cache or cc.DEFAULT_CACHE
        cache_path = f"{base_cache}.dryrun.json" if args.from_cache else base_cache
        diff = cc.diff_records(records, cc.load_cache(cache_path))
        cc.save_cache(records, cache_path)
        # 라이브 수집분은 full-record 캐시에도 저장 → 이후 --from-cache 오프라인 dry-run이
        # fixture가 아니라 '마지막 실제 수집분'을 재생한다. dry-run은 이 캐시를 덮지 않는다.
        if not args.from_cache:
            cc.save_full_records(records)
        if not args.json:
            print(f"  courtauction 수집 {len(records)}건 — {diff.summary} (캐시 {cache_path})")

        # 목적물(mokmulSer) 다중 행 병합 — 건물행 우선(감사 2026-07-10 HIGH: 마지막 행 승리로
        # 아파트가 토지로 강등되던 순서 의존 오류). raw 보존(save_raw_records)은 전 행 유지.
        from src.courtauction_fields import merge_mokmul_rows  # noqa: E402, PLC0415
        merged_records = merge_mokmul_rows(records)
        listings = [to_auction_listing(r) for r in merged_records]
        # 만료 방어(재검증 감사 idx26): 매각기일이 지난 물건은 채점·서빙 대상에서 제외
        # (크롤 원본에 과거 기일 행이 섞여 들어옴 — 7/7 수집분에 461행 실측).
        from datetime import date as _date  # noqa: PLC0415
        _today = _date.today().isoformat()
        n_before = len(listings)
        listings = [x for x in listings if not x.sale_date or x.sale_date >= _today]
        if len(listings) < n_before and not args.json:
            print(f"  만료(기일 경과) 제외: {n_before - len(listings)}건")

        # (감사 2026-07-15) 권리 배선 — 이미 크롤된 listing_rights 요지를 **채점 전에** 반영한다.
        # 이 단계가 없어서 batch가 권리를 한 번도 안 보고 점수를 매겼다(79.7%가 rights_score=85.0
        # 상수 → 권리 가중치 30%의 변별력 0, 인수비율 하드게이트 영구 사망). 네트워크 0 —
        # 크롤 결과를 재사용할 뿐이다. 미크롤·빈요지는 손대지 않음(= '권리미확인' 유지).
        try:
            _rconn = store.connect(args.db)
            try:
                _rights_rows = store.load_all_rights(_rconn)
            finally:
                _rconn.close()
        except Exception as e:  # noqa: BLE001 — 권리 배선 실패가 채점 자체를 막지 않게
            print(f"  ⚠ 권리 배선 건너뜀(권리 요지 로드 실패: {e})")
            _rights_rows = []
        if _rights_rows:
            listings, _rstats = pipeline.apply_rights_from_rows(listings, _rights_rows)
            if not args.json:
                print(f"  권리 배선: {_rstats['matched']}/{_rstats['total']}건 반영 "
                      f"(하드게이트 {_rstats['gated']}건 · 빈요지 {_rstats['empty']}건 "
                      f"· 미크롤 {_rstats['total'] - _rstats['matched'] - _rstats['empty']}건=권리미확인)")

        # (2026-07-19 T7) 네이버 complexNo 확정 실거래 배선 — 있으면 이름매칭을 우회해
        # 같은 단지·같은 평형 실거래로 추정(폴백 오염 교정). 로드 실패해도 채점은 계속.
        _real_map = {}
        try:
            _real_map = _load_naver_real_map(args.db)
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠ 네이버 실거래 배선 건너뜀({e})")
        if _real_map and not args.json:
            print(f"  네이버 확정 실거래 배선: {len(_real_map)}물건 (naver_real_trades)")
        _lookup = (lambda lst: _real_map.get((lst.court, lst.case_no, str(lst.item_no or "")))) \
            if _real_map else None

        scored = pipeline.run(use_live=use_live, deal_ymd=args.ym, auctions=listings,
                              real_trades_lookup=_lookup)
    else:
        scored = pipeline.run(use_live=use_live, deal_ymd=args.ym)

    # 시세 출처 가드: 라이브 국토부 시세가 아닌(샘플/추정) 채점 결과는 소스와 무관하게
    # 서빙 DB에 넣지 않는다(T8 감사 HIGH — `run.py --from-cache`가 소스 기본값 sample이라
    # 샘플 fixture 6건을 서빙 DB에 적재해 X-Data-Source: db로 실매물인 척 서빙되던 구멍). 별도 dryrun DB로.
    db_path = args.db
    if not use_live:
        db_path = f"{args.db}.dryrun.db"
        if not args.json:
            print(f"  ⚠ 라이브 시세 아님(샘플/추정) → 서빙 DB 대신 {db_path} 에 저장")
    conn = store.connect(db_path)
    # T1 원본 보존: courtauction 수집분의 raw row를 채점 결과와 별도로 남긴다
    # (파싱 버그·스키마 개편 시 재처리 원천 + 수집 감사 증거).
    if args.source == "courtauction":
        store.save_raw_records(conn, records)
        # 지도 좌표 캐시(KATEC→WGS84, 시도 bbox 검증) — /map 이 조인해 핀을 찍는다.
        from src import coords  # noqa: PLC0415
        cstats = coords.build_coord_cache(records)
        if not args.json:
            print(f"  좌표 캐시: {cstats['ok']}/{cstats['total']}건 "
                  f"(좌표없음 {cstats['no_coord']}·검증탈락 {cstats['bbox_reject']})")
    # 풀스냅샷(전국 실크롤 또는 캐시 전량, 라이브 시세)은 전량 교체로 만료매물 제거. 그 외는 병합.
    full_snapshot = args.nationwide or args.from_cache
    if args.source == "courtauction" and use_live and full_snapshot and not scored:
        # 수집 0건(전 샤드 차단·전량 파싱 실패 등)에 전량 교체를 돌리면 서빙 DB가 통째로 비워지고,
        # 빈 테이블은 data_gates 를 위반 0건으로 통과해 클라우드까지 전파된다. 빈 스냅샷은 '만료'가
        # 아니라 '수집 실패'이므로 기존 서빙 DB를 보존하고 교체·클라우드 미러를 건너뛴다.
        print("  ⛔ 수집 0건 — 전량 교체·클라우드 미러 스킵(기존 서빙 DB 보존). 크롤 차단/실패 의심.",
              file=sys.stderr)
        n = 0
        args.no_cloud = True
    elif args.source == "courtauction" and use_live and full_snapshot:
        n = store.replace_all(conn, scored)
        # scored 전량교체 후 대응 물건이 사라진 고아 권리 정리(rights 무한누적 방지·중복 제거).
        pruned = store.prune_orphan_rights(conn)
        if pruned and not args.json:
            print(f"  🧹 로컬 고아 권리 {pruned}건 정리(scored 동기화)")
    else:
        n = store.upsert(conn, scored)   # 전체 저장

    # 클라우드 미러링: 라이브 적재분을 Supabase(REST)로도 반영 → Vercel 서빙이 최신을 읽는다.
    # 로컬 SQLite 적재가 끝난 뒤에 하며, 실패해도 로컬 결과는 보존(클라우드 오류가 새로고침을 깨지 않음).
    # 드라이런(비라이브)은 절대 클라우드에 쓰지 않는다(샘플을 라이브로 오인 방지).
    if use_live and not args.no_cloud:
        # 품질 게이트: 전 배치 PASS 여야 서빙 반영(침산동 나대지 사고 재발 방지 — 사람이 아닌
        # 시스템이 매 적재마다 검사). FAIL 이면 로컬엔 남기되 클라우드 미러는 차단.
        from src import data_gates  # noqa: PLC0415
        gate_results = data_gates.run_gates(conn)
        if not args.json:
            print(data_gates.report(gate_results))
        if not data_gates.all_pass(gate_results):
            print("  ⛔ 품질 게이트 FAIL — Supabase 미러링 건너뜀(로컬 적재는 유지). "
                  "deploy/validate_data.py 로 위반 사례 확인 후 수정.", file=sys.stderr)
            args.no_cloud = True
    if use_live and not args.no_cloud:
        from src import store_rest  # noqa: PLC0415
        if store_rest.enabled():
            try:
                if full_snapshot:
                    cn = store_rest.replace_all(scored)
                else:
                    cn = store_rest.upsert(scored)
                if not args.json:
                    print(f"  ☁ Supabase 미러링: {cn}건 "
                          f"({'전량교체' if full_snapshot else '병합'})")
                # 클라우드 고아 권리 정리(scored 전량교체 후 rights 동기화). RPC 함수
                # (supabase_rights.sql prune_auction_orphan_rights) 미배포면 조용히 skip.
                if full_snapshot:
                    try:
                        pr = store_rest.prune_rights()
                        if pr and not args.json:
                            print(f"  🧹 클라우드 고아 권리 {pr}건 정리")
                    except Exception as e:  # noqa: BLE001
                        if not args.json:
                            print(f"  ⚠ 클라우드 고아 정리 skip(RPC 미배포?): {e}")
            except Exception as e:  # noqa: BLE001 — 클라우드 실패는 로컬 새로고침을 깨지 않음
                if not args.json:
                    print(f"  ⚠ Supabase 미러링 실패(로컬은 정상 적재됨): {e}")

    view = query.sort_items(
        query.apply_filters(scored, args.min_score, args.ptype, args.region,
                            min_profit=args.min_profit),
        args.sort,
    )

    if args.json:
        print(report.to_json(view))
        return 0

    print(report.to_console(view))
    print(f"\n저장(전체): {n}건 · 표시(필터 후): {len(view)}건 → {db_path}")

    EVID.mkdir(exist_ok=True)
    csv_path = report.to_csv(view, EVID / "result.csv")
    html_path = report.to_html(view, EVID / "result.html")
    print(f"증거: {csv_path}")
    print(f"증거: {html_path}")
    return 0


def _load_naver_real_map(db_path: str) -> dict:
    """naver_prices 매핑 × naver_real_trades 조인 → {(court,case_no,item_no): [실거래 행]}.

    (2026-07-19 T7) 채점 전에 1회 로드 — 물건별로 그 단지·그 평형의 확정 실거래를 붙인다.
    해제거래(deleted=1) 제외. 테이블 없거나 비어 있으면 빈 dict(주입 생략).
    """
    from src import naver_store as ns  # noqa: PLC0415

    conn = store.connect(db_path)
    try:
        ns.ensure_schema(conn)
        out: dict = {}
        # (감사 2026-07-19 C2) 매칭 신뢰도 게이트 — naver_match는 휴리스틱(24건 검증)이라
        # '저신뢰' 매칭을 그대로 주입하면 "확정 같은단지"라는 자신만만한 오답이 된다.
        # 고신뢰·중신뢰만 실거래 주입, 저신뢰(match_conf='저신뢰')·NULL은 종전 이름매칭 경로로.
        # (감사 2026-07-20 정합) 주석 계약대로 '고/중신뢰만' 주입 — 저신뢰·NULL은 종전 이름매칭 경로로.
        # 종전 `match_conf IS NULL OR`는 주석·자매쿼리(data_gates._naver_verified_keys)와 모순이라 제거.
        # (현 DB엔 NULL+유효 complex_no 행 0건이라 실동작 무변 — 방어적 정합.)
        q = ("SELECT np.court, np.case_no, np.item_no, nrt.trade_ymd, nrt.price, "
             "       nrt.floor, nrt.exclusive_area "
             "FROM naver_prices np JOIN naver_real_trades nrt "
             "  ON nrt.complex_no = np.complex_no AND nrt.area_no = np.area_no "
             "WHERE nrt.deleted = 0 AND np.complex_no IS NOT NULL AND np.complex_no != '' "
             "  AND np.area_no IS NOT NULL AND np.area_no != '' "
             "  AND np.match_conf IN ('고신뢰','중신뢰')")
        for r in conn.execute(q):
            out.setdefault((r["court"], r["case_no"], str(r["item_no"] or "")), []).append(dict(r))
        return out
    finally:
        conn.close()


def _apply_sample_overrides(live_months, area_band) -> None:
    """CLI로 넘어온 표본 튜닝값을 config.SAMPLE에 반영(미지정은 기존값 유지)."""
    import dataclasses  # noqa: PLC0415

    from src import config, matcher, pipeline  # noqa: PLC0415

    if live_months is None and area_band is None:
        return
    cur = config.SAMPLE
    # 불변 패턴: 기존 인스턴스를 제자리 변경하지 않고 새 객체로 교체(replace가 __post_init__ 재실행).
    config.SAMPLE = dataclasses.replace(
        cur,
        live_months=cur.live_months if live_months is None else live_months,
        area_band=cur.area_band if area_band is None else area_band,
    )
    print(f"  표본 튜닝: live_months={pipeline._live_months()} "
          f"area_band=±{matcher._area_band():.0%}\n")


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    import os
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


if __name__ == "__main__":
    raise SystemExit(main())
