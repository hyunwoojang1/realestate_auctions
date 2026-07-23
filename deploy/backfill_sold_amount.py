#!/usr/bin/env python
"""실제 낙찰가(maeAmt) → listing_rights.schedule 주입 백필 — 라이브 호출 0.

## 왜 필요한가
재매각 배너가 "그 회차 최저입찰가"만 보여주고 있었다. 물건상세(pgj15B)의 `dspslAmt`(매각금액)는
스키마에만 있고 **항상 null**이기 때문이다(실측: 매각 회차 15/15 null).

그런데 **검색결과**(`raw_listings.raw_json`)의 `maeAmt` 에 **실제 낙찰가**가 들어 있다.
정상 낙찰된 물건은 목록에서 사라지므로 이 값이 채워지는 건 **재매각 물건뿐**이고,
그래서 정확히 재매각 배너가 필요로 하는 값이다.

## 무엇을 하나
`raw_listings` 를 읽어 (court, case_no, item_no) 별 maeAmt 를 모으고,
`listing_rights.schedule` JSON 의 **가장 최근 '매각' 회차**에 `sold` 키로 주입한다.
스키마 변경이 없다(schedule 은 이미 JSON 컬럼) — 따라서 Supabase 미러도 그대로 따라온다.

## 멱등성·안전
- 같은 값이면 UPDATE 하지 않는다(무변경 행 스킵). 반복 실행해도 결과가 같다.
- `--dry-run` 으로 변경 건수만 확인 가능.
- ⚠️ 권리 재크롤(`crawl_rights.py`)이 rights 행을 덮어쓰면 `sold` 가 사라진다.
  재크롤 후에는 이 스크립트를 다시 돌려야 한다(또는 crawl_rights 가 직접 주입하도록 병합).

사용:
    python -m deploy.backfill_sold_amount [--db auction.db] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import store  # noqa: E402
from src.courtauction_detail import resale_history  # noqa: E402


def _norm(court, case_no, item_no) -> tuple[str, str, str]:
    return (str(court or ""), str(case_no or ""), str(item_no or ""))


def collect_sold_amounts(conn) -> dict[tuple[str, str, str], int]:
    """raw_listings → {(court, case_no, item_no): 낙찰가}. 값이 없거나 0이면 담지 않는다.

    raw_listings 는 재크롤 이력 때문에 같은 물건에 여러 행이 있다(실측 37,528행 / 고유 26,286).
    같은 물건에 값이 여럿이면 **가장 큰 값**을 쓴다 — 낙찰가는 회차가 진행될수록 갱신되고,
    과소 표기(0·빈값)를 유효값으로 착각하지 않기 위해서다.
    """
    out: dict[tuple[str, str, str], int] = {}
    for court, case_no, item_no, raw in conn.execute(
            "SELECT court, case_no, item_no, raw_json FROM raw_listings"):
        try:
            d = json.loads(raw)
        except (TypeError, ValueError):
            continue
        v = str(d.get("maeAmt") or "").strip()
        if not v:
            continue
        try:
            amt = int(float(v))
        except ValueError:
            continue
        if amt <= 0:
            continue
        k = _norm(court, case_no, item_no)
        if amt > out.get(k, 0):
            out[k] = amt
    return out


def inject(schedule: list, amount: int) -> tuple[list, bool]:
    """schedule 의 가장 최근 '매각' 회차에 sold=amount 주입. (새 schedule, 변경여부).

    매각 회차가 없으면 아무것도 하지 않는다 — maeAmt 가 있는데 기일 이력에 매각이 없는 건
    데이터 불일치이므로 조용히 주입하지 않고 그대로 둔다(호출부가 미스로 집계).
    """
    rows = [r for r in schedule if isinstance(r, dict)]
    idx = [i for i, r in enumerate(sorted(rows, key=lambda x: str(x.get("ymd") or "")))
           if r.get("result") == "매각"]
    if not idx:
        return schedule, False
    ordered = sorted(rows, key=lambda x: str(x.get("ymd") or ""))
    target = ordered[idx[-1]]
    if target.get("sold") == amount:
        return schedule, False
    target["sold"] = amount
    # 저장 포맷(최신순)으로 되돌린다 — parse 가 만든 순서와 동일하게 유지.
    return sorted(ordered, key=lambda x: str(x.get("ymd") or ""), reverse=True), True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="maeAmt(실제 낙찰가) → listing_rights.schedule 백필")
    ap.add_argument("--db", default="auction.db")
    ap.add_argument("--dry-run", action="store_true", help="변경 건수만 출력하고 쓰지 않음")
    args = ap.parse_args(argv)

    conn = store.connect(args.db)
    try:
        amounts = collect_sold_amounts(conn)
        print(f"raw_listings 에서 낙찰가 수집: {len(amounts):,}물건")

        rights = conn.execute(
            "SELECT court, case_no, item_no, schedule FROM listing_rights "
            "WHERE schedule IS NOT NULL").fetchall()
        updated = no_sale_row = no_amount = unchanged = 0
        for court, case_no, item_no, sched in rights:
            k = _norm(court, case_no, item_no)
            amt = amounts.get(k)
            if not amt:
                no_amount += 1
                continue
            try:
                cur = json.loads(sched)
            except (TypeError, ValueError):
                continue
            new, changed = inject(cur, amt)
            if not changed:
                if any(r.get("result") == "매각" for r in cur if isinstance(r, dict)):
                    unchanged += 1
                else:
                    no_sale_row += 1
                continue
            if not args.dry_run:
                conn.execute(
                    "UPDATE listing_rights SET schedule=? WHERE court=? AND case_no=? AND item_no=?",
                    (json.dumps(new, ensure_ascii=False), court, case_no, item_no))
            updated += 1
        if not args.dry_run:
            conn.commit()

        print(f"  주입{'(예정)' if args.dry_run else ' 완료'}: {updated:,}건")
        print(f"  이미 동일값(무변경): {unchanged:,}건")
        print(f"  낙찰가 있으나 기일이력에 '매각' 없음: {no_sale_row:,}건")
        print(f"  낙찰가 미보유: {no_amount:,}건")

        # 검증 — 주입 후 실제로 resale_history 가 낙찰가를 읽는가
        ok = 0
        for _, _, _, sched in conn.execute(
                "SELECT court, case_no, item_no, schedule FROM listing_rights "
                "WHERE schedule IS NOT NULL"):
            try:
                r = resale_history(json.loads(sched))
            except (TypeError, ValueError):
                continue
            if r and r.last_sold_price:
                ok += 1
        print(f"  검증: resale_history 가 낙찰가를 읽는 물건 {ok:,}건")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
