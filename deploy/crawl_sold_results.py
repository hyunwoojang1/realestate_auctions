"""매각결과검색 → 실낙찰가 백필 (sold_listings.sold_price).

배경(2026-08-24): 실낙찰가 보유율이 2.3%(409/17,865)에 머물던 이유를 "법원이 정상
낙찰가를 비공개"라고 문서화해 왔는데, 이는 **활성 목록 API 만 본 결론**이었다.
사용자 반문("정말 못 구해오는 게 맞나")으로 재정찰 → 매각결과검색 화면(PGJ158M01/M02)의
전용 API(`selectDspslSchdRsltSrch.on`)가 최근 매각기일의 **정상 낙찰가(maeAmt)** 를
공개한다는 것을 확인했다(라이브 실측: 매각(04) 필터 시 전 행 maeAmt > 0).

동작:
  1) data/court_codes.json 의 법원 57곳을 순회 — 법원당 1콜(+대형 법원 추가 페이지).
  2) saNo(14자리) → 'YYYY타경N' 변환(로컬 raw 500/500 검증된 규칙)으로
     sold_listings (court, case_no, item_no) 에 조인.
  3) **sold_price 가 비어 있는 행만** 채운다(기존 값 불변 = 낙찰가 정직성 계약).
     sold_evidence='dspslRslt' 로 출처를 구분한다('maeAmt'=재매각 이력 경로와 의미가 다름).
  4) --mirror 시 갱신된 행 전체를 Supabase(auction_sold_listings)에 병합.

밴 회피: CourtAuctionClient 재사용 — 지터·403 즉시중단·일일예산·COURTAUCTION_STOP
킬스위치 전부 상속. 하루 총 콜 ≈ 57 + α(대형 법원 페이지) ≈ 60~70.

exit: 0 정상 / 2 차단·킬스위치(부분 수집분은 이미 반영됨) / 1 그 외 실패.
사용:  python -m deploy.crawl_sold_results --db auction.db [--mirror] [--limit-courts N] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import store  # noqa: E402
from src.casesearch import load_court_codes  # noqa: E402
from src.courtauction_client import CourtAuctionBlocked, CourtAuctionClient  # noqa: E402

# 대형 법원 안전 상한 — 매각(04)만 조회라 실측 1페이지(40행) 내가 보통이지만,
# 기일 몰림(월초 등)에 2~3페이지가 될 수 있다. 5페이지=200행이면 충분·과요청 차단.
MAX_PAGES_PER_COURT = 5

EVIDENCE = "dspslRslt"   # 매각결과검색 출처 — 'maeAmt'(재매각 이력 경로)와 구분


def sa_to_case_no(sa_no: str) -> str | None:
    """saNo 14자리('20230130002726') → 저장 포맷('2023타경2726').

    구조 = YYYY + 사건구분코드(0130=타경) + 일련번호. 로컬 raw_listings 500행 대조
    500/500 일치 검증(2026-08-24). 타경(0130)이 아니면 None(부동산 임의·강제경매 외).
    """
    if not sa_no or len(sa_no) != 14 or not sa_no.isdigit():
        return None
    if sa_no[4:8] != "0130":
        return None
    return f"{sa_no[:4]}타경{int(sa_no[8:])}"


def rows_to_updates(rows: list[dict], court_name: str) -> list[dict]:
    """API 행 → 백필 후보. maeAmt>0 만, (사건·물건) 중복은 최초 1건(응답에 중복 행 실측)."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for r in rows:
        case_no = sa_to_case_no(r.get("saNo") or "")
        try:
            amt = int(r.get("maeAmt") or 0)
        except (TypeError, ValueError):
            amt = 0
        item_no = str(r.get("maemulSer") or "").strip()
        if not case_no or amt <= 0 or not item_no:
            continue
        key = (case_no, item_no)
        if key in seen:
            continue
        seen.add(key)
        out.append({"court": court_name, "case_no": case_no, "item_no": item_no,
                    "sold_price": amt})
    return out


def apply_updates(conn, updates: list[dict]) -> tuple[int, int, int]:
    """빈 sold_price 만 채움. 반환 (채움, 이미 있음, 로컬에 행 없음).

    기존 값(재매각 maeAmt 경로)은 절대 덮지 않는다 — 낙찰가 정직성 계약.
    '로컬에 행 없음'은 정상일 수 있다(보존 diff 가 아직 안 돈 당일 낙찰 등) —
    다음날 보존 후 재실행되면 채워지므로 카운트만 노출한다.
    """
    filled = existing = missing = 0
    for u in updates:
        row = conn.execute(
            "SELECT sold_price FROM sold_listings WHERE court=? AND case_no=? AND item_no=?",
            (u["court"], u["case_no"], u["item_no"])).fetchone()
        if row is None:
            missing += 1
            continue
        if row["sold_price"]:
            existing += 1
            continue
        conn.execute(
            "UPDATE sold_listings SET sold_price=?, sold_evidence=? "
            "WHERE court=? AND case_no=? AND item_no=?",
            (u["sold_price"], EVIDENCE, u["court"], u["case_no"], u["item_no"]))
        filled += 1
    conn.commit()
    return filled, existing, missing


def _mirror_filled(conn, updates: list[dict]) -> int:
    """이번에 채워진 행을 Supabase 병합 — 전체 컬럼(_SOLD_COLS)으로 읽어 부분행 삽입 방지."""
    from src import store_rest  # noqa: PLC0415
    if not store_rest.enabled():
        print("  (mirror) SUPABASE 미구성 — 스킵")
        return 0
    rows = []
    for u in updates:
        r = conn.execute(
            f"SELECT {','.join(store._SOLD_COLS)} FROM sold_listings "  # noqa: S608 — 상수 컬럼
            "WHERE court=? AND case_no=? AND item_no=? AND sold_evidence=?",
            (u["court"], u["case_no"], u["item_no"], EVIDENCE)).fetchone()
        if r:
            rows.append(dict(r))
    if not rows:
        return 0
    return store_rest.upsert_sold(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="auction.db")
    ap.add_argument("--mirror", action="store_true", help="채워진 행을 Supabase 에도 병합")
    ap.add_argument("--limit-courts", type=int, default=0, help="앞 N개 법원만(검증용)")
    ap.add_argument("--dry-run", action="store_true", help="DB 미변경 — 수집·매칭 수만 보고")
    args = ap.parse_args()

    from deploy.migrate_to_supabase import _load_env  # noqa: PLC0415 — crawl_rights 와 동일 경로
    _load_env()

    codes = load_court_codes()
    if not codes:
        print("court_codes.json 없음 — 중단", file=sys.stderr)
        return 1
    courts = sorted(codes.items())
    if args.limit_courts:
        courts = courts[:args.limit_courts]

    conn = store.connect(args.db)
    client = CourtAuctionClient()
    total_updates: list[dict] = []
    blocked = False
    try:
        for name, code in courts:
            try:
                total, rows = client.sold_results(code)
                pages = min(MAX_PAGES_PER_COURT, -(-total // 40)) if total else 1
                for p in range(2, pages + 1):
                    _, more = client.sold_results(code, page_no=p)
                    if not more:
                        break
                    rows.extend(more)
                ups = rows_to_updates(rows, name)
                if ups:
                    print(f"  {name}: 매각 {total}건 → 후보 {len(ups)}건")
                total_updates.extend(ups)
            except CourtAuctionBlocked as e:
                # 차단·킬스위치 — 즉시 전체 중단(우회 금지 원칙). 수집분은 아래서 반영.
                print(f"⛔ 차단/중단 신호({name}): {e} — 순회 중단", file=sys.stderr)
                blocked = True
                break
        if args.dry_run:
            print(f"[dry-run] 후보 {len(total_updates)}건 — DB 미변경")
            return 2 if blocked else 0
        filled, existing, missing = apply_updates(conn, total_updates)
        print(f"낙찰가 백필: 채움 {filled} · 기존값 보존 {existing} · 로컬 행 없음 {missing} "
              f"(후보 {len(total_updates)}건, 법원 {len(courts)}곳)")
        if args.mirror:
            # filled=0 이어도 돌린다 — 지난 실행이 로컬만 채우고 미러에 실패했을 수 있다
            # (멱등: _mirror_filled 가 sold_evidence=dspslRslt 행만 병합).
            mn = _mirror_filled(conn, total_updates)
            print(f"  Supabase 병합: {mn}건")
    finally:
        conn.close()
    return 2 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
