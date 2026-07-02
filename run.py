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
    ap.add_argument("--max-pages", type=int, default=10, help="courtauction 페이지 상한(1p=40건)")
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
                    help="예상차익 하한(원) 필터")
    ap.add_argument("--min-score", type=float, default=None, help="(내부용) 점수 하한 필터")
    ap.add_argument("--type", dest="ptype", default=None, help="물건종류 필터(아파트/오피스텔/다세대 등)")
    ap.add_argument("--region", default=None, help="지역 필터(주소 prefix, 예: 서울/경기/부산)")
    ap.add_argument("--sort", choices=query.SORT_KEYS, default=query.DEFAULT_SORT,
                    help="정렬 기준(profit/gap/score, 기본 profit)")
    ap.add_argument("--json", action="store_true", help="결과를 JSON으로 stdout 출력")
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

        listings = [to_auction_listing(r) for r in records]
        scored = pipeline.run(use_live=use_live, deal_ymd=args.ym, auctions=listings)
    else:
        scored = pipeline.run(use_live=use_live, deal_ymd=args.ym)

    # 시세 출처 가드: courtauction 실매물을 '라이브 국토부 시세'가 아닌 샘플/추정 시세로 채점한 결과는
    # 서빙 DB에 넣지 않는다(웹이 X-Data-Source: db 로 '라이브인 척' 내보내는 것을 방지). 별도 dryrun DB로.
    db_path = args.db
    if args.source == "courtauction" and not use_live:
        db_path = f"{args.db}.dryrun.db"
        if not args.json:
            print(f"  ⚠ 실매물인데 라이브 시세 아님(샘플/추정) → 서빙 DB 대신 {db_path} 에 저장")
    conn = store.connect(db_path)
    # 풀스냅샷(전국 실크롤 또는 캐시 전량, 라이브 시세)은 전량 교체로 만료매물 제거. 그 외는 병합.
    full_snapshot = args.nationwide or args.from_cache
    if args.source == "courtauction" and use_live and full_snapshot:
        n = store.replace_all(conn, scored)
    else:
        n = store.upsert(conn, scored)   # 전체 저장

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
