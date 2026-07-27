"""낙찰 기록(sold_listings)에 시세·차익·점수를 채운다 — 활성 물건과 같은 채점 파이프라인으로.

왜 필요한가:
  낙찰 기록 281건 중 280건은 법원 원문에서 되살린 것(deploy/backfill_sold_listings.py)이라
  est_market_price·market_band_low·profit_low·arb_score 가 전부 비어 있었다(2026-07-27 실측).
  그 결과 ①낙찰 결과 페이지의 차익·점수 정렬이 무동작 ②상세 시뮬레이터의 예상 매도가가
  0원이라 **모든 낙찰 물건이 손해로 표시**됐다.

어떻게:
  raw_listings 에 원본 크롤 레코드가 281/281 남아 있다 → parse_row → to_auction_listing 으로
  AuctionListing 을 복원하고, 활성 물건과 **똑같은** pipeline.run(국토부 실거래 + 네이버 확정
  실거래)으로 채점해 결과 컬럼만 sold_listings 에 되쓴다.

지켜야 할 계약:
  · sold_price·sold_evidence·snapshot_at 은 **건드리지 않는다** — 낙찰가 정직성 계약 영역이고
    이 스크립트는 시세 추정만 담당한다.
  · 시세 추정 대상은 아파트·오피스텔뿐(T2 결정, SUPPORTED_ESTIMATION_KINDS). 토지·상가 등은
    '미지원유형'으로 남는 것이 정상이며 억지로 채우지 않는다.
  · 실패(원본 없음·파싱 실패)는 건너뛰고 세어서 보고한다 — 조용히 0 으로 채우지 않는다.

사용:
  PYTHONUTF8=1 .venv/Scripts/python.exe -m deploy.rescore_sold [--db auction.db] [--dry-run] [--mirror]
멱등 — 여러 번 돌려도 같은 결과(원본과 시세 데이터가 그대로면).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import pipeline, store  # noqa: E402
from src.courtauction_fields import parse_row, to_auction_listing  # noqa: E402

# 채점 결과 중 sold_listings 로 되쓰는 컬럼. sold_price 계열은 의도적으로 제외한다.
_WRITE_COLS = ("est_market_price", "market_band_low", "profit_low",
               "expected_profit", "arb_score", "grade")


def _load_env() -> None:
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _sold_listings(conn):
    """낙찰 기록 → (키, AuctionListing) 목록. 원본 없는 건은 건너뛴다(사유 집계)."""
    rows = conn.execute(
        "SELECT s.court, s.case_no, s.item_no, "
        # raw_listings 는 크롤 회차마다 여러 행이 쌓인다 — 최신 1행만.
        "  (SELECT r.raw_json FROM raw_listings r WHERE r.court=s.court "
        "     AND r.case_no=s.case_no AND r.item_no=s.item_no "
        "   ORDER BY r.fetched_at DESC LIMIT 1) AS raw_json "
        "FROM sold_listings s").fetchall()
    out, no_raw, parse_fail = [], 0, 0
    for r in rows:
        if not r["raw_json"]:
            no_raw += 1
            continue
        try:
            lst = to_auction_listing(parse_row(json.loads(r["raw_json"])))
        except Exception:  # noqa: BLE001 — 원본 1건 파싱 실패가 전체를 막지 않게
            parse_fail += 1
            continue
        out.append(((r["court"], r["case_no"], r["item_no"]), lst))
    return out, {"no_raw": no_raw, "parse_fail": parse_fail}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="낙찰 기록 재채점(시세·차익·점수)")
    ap.add_argument("--db", default="auction.db")
    ap.add_argument("--dry-run", action="store_true", help="쓰지 않고 통계만")
    ap.add_argument("--mirror", action="store_true", help="Supabase 미러까지 갱신")
    args = ap.parse_args(argv)
    _load_env()

    conn = store.connect(args.db)
    pairs, skip = _sold_listings(conn)
    print(f"낙찰 기록 → 원본 복원 {len(pairs)}건 "
          f"(원본없음 {skip['no_raw']} · 파싱실패 {skip['parse_fail']})")
    if not pairs:
        return 1

    # 네이버 확정 실거래 배선 — 활성 채점과 같은 조인(고/중신뢰 매칭만).
    sys.path.insert(0, str(ROOT))
    from run import _load_naver_real_map  # noqa: PLC0415

    real_map = _load_naver_real_map(args.db)
    print(f"네이버 확정 실거래 배선: {len(real_map)}물건")

    keys = [k for k, _ in pairs]
    listings = [lst for _, lst in pairs]
    # 물건 키는 (court, case_no, item_no) 지만 AuctionListing 은 item_no 를 문자열로 들고 있다 —
    # run.py 의 lookup 과 같은 형태로 맞춘다.
    lookup = (lambda lst: real_map.get((lst.court, lst.case_no, str(lst.item_no or "")))) \
        if real_map else None

    scored = pipeline.run(use_live=True, auctions=listings, real_trades_lookup=lookup)
    # pipeline.run 은 점수 내림차순으로 **정렬해서** 돌려준다 — 입력 순서와 다르므로
    # 키로 다시 맞춘다(위치 대응으로 짝지으면 다른 물건의 시세를 덮어쓴다).
    by_key = {(s.court, s.case_no, str(s.item_no or "")): s for s in scored}

    updates, matched, est_ok = [], 0, 0
    for court, case_no, item_no in keys:
        s = by_key.get((court, case_no, str(item_no or "")))
        if s is None:
            continue
        matched += 1
        if s.est_market_price is not None:
            est_ok += 1
        updates.append({"court": court, "case_no": case_no, "item_no": item_no,
                        **{c: getattr(s, c) for c in _WRITE_COLS}})

    print(f"채점 매칭 {matched}건 · 시세 추정 성공 {est_ok}건 "
          f"(나머지는 미지원유형·표본부족 — 정상)")
    if args.dry_run:
        print("(dry-run — 저장하지 않음)")
        return 0

    # 기존 행을 읽어 결과 컬럼만 갈아끼운다(sold_price 계열 보존).
    n = 0
    with conn:
        for u in updates:
            sets = ", ".join(f"{c}=?" for c in _WRITE_COLS)
            conn.execute(
                f"UPDATE sold_listings SET {sets} "  # noqa: S608 — 컬럼명은 코드 상수
                "WHERE court=? AND case_no=? AND item_no=?",
                [u[c] for c in _WRITE_COLS] + [u["court"], u["case_no"], u["item_no"]])
            n += 1
    print(f"sold_listings 갱신 {n}건")

    if args.mirror:
        from src import store_rest  # noqa: PLC0415
        if not store_rest.enabled():
            print("⚠ Supabase 미설정 — 미러 건너뜀")
        else:
            rows = [dict(r) for r in conn.execute("SELECT * FROM sold_listings")]
            print(f"Supabase 미러: {store_rest.upsert_sold(rows)}건")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
