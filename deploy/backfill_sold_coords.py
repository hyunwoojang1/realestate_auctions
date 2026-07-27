"""낙찰 기록(sold_listings)의 좌표를 원본에서 되살려 좌표 캐시에 **병합**한다.

왜 필요한가:
  네이버 단지 매칭(deploy/crawl_naver Phase A)은 이름만으로는 동명 단지를 가려낼 수 없어
  좌표로 후보를 좁힌다. 그런데 낙찰 기록 281건은 법원 원문에서 되살린 것이라 좌표 캐시에
  키가 없다(실측 66건 중 1건만 보유) — 그대로 크롤하면 대부분 no_coord 로 떨어진다.
  raw_listings 에 원본 레코드가 그대로 남아 있으므로(281/281) 거기서 KATEC 좌표를 꺼내
  변환해 채워 넣는다.

왜 build_coord_cache 를 쓰지 않는가:
  그 함수는 파일을 **통째로 새로 쓴다**. 낙찰분만 넘기면 활성 물건 수천 건의 좌표가 사라진다.
  여기서는 기존 캐시를 읽어 **없는 키만 추가**하고 되쓴다(기존 값 덮어쓰기 없음).

사용: python -m deploy.backfill_sold_coords [--db auction.db] [--dry-run]
멱등 — 여러 번 돌려도 같은 결과(이미 있는 키는 건너뜀).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import coords, store  # noqa: E402
from src.courtauction_fields import parse_row  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="낙찰 기록 좌표 캐시 병합")
    ap.add_argument("--db", default="auction.db")
    ap.add_argument("--dry-run", action="store_true", help="쓰지 않고 통계만")
    args = ap.parse_args(argv)

    conn = store.connect(args.db)
    # ⚠ raw_listings 는 크롤 회차마다 다른 doc_id 로 여러 행이 쌓인다(한 물건 최대 12행 실측).
    # 좌표는 물건당 하나면 되므로 **최신 1행만** 쓴다 — 옛 회차 doc_id 키까지 넣으면 캐시가
    # 쓸데없이 부풀고, 크롤러가 참조하는 최신 doc_id 와도 어긋날 수 있다.
    rows = conn.execute(
        "SELECT s.court, s.case_no, s.item_no, s.address, "
        "  (SELECT r.doc_id FROM raw_listings r WHERE r.court=s.court "
        "     AND r.case_no=s.case_no AND r.item_no=s.item_no "
        "   ORDER BY r.fetched_at DESC LIMIT 1) AS doc_id, "
        "  (SELECT r.raw_json FROM raw_listings r WHERE r.court=s.court "
        "     AND r.case_no=s.case_no AND r.item_no=s.item_no "
        "   ORDER BY r.fetched_at DESC LIMIT 1) AS raw_json "
        "FROM sold_listings s"
    ).fetchall()
    conn.close()

    cache_path = coords.DEFAULT_COORD_CACHE
    cache = coords.load_coord_cache()
    before = len(cache)

    added = skipped = no_coord = bad_bbox = parse_fail = 0
    for r in rows:
        uid = r["doc_id"] or f"{r['court']}|{r['case_no']}|{r['item_no']}"
        if uid in cache:
            skipped += 1
            continue
        try:
            rec = parse_row(json.loads(r["raw_json"]))
        except Exception:  # noqa: BLE001 — 원본 1건 파싱 실패가 전체를 막지 않게
            parse_fail += 1
            continue
        try:
            x, y = float(rec.x_proj), float(rec.y_proj)
        except (TypeError, ValueError):
            no_coord += 1
            continue
        if not x or not y:
            no_coord += 1
            continue
        lat, lon = coords.to_wgs84(x, y)
        # 시도 bbox 검증 — 좌표계 오인·원본 오류로 엉뚱한 지역 핀이 박히는 것을 막는다.
        if not coords._valid(lon, lat, coords.sido_of(rec.address or r["address"] or "")):
            bad_bbox += 1
            continue
        cache[uid] = [round(lat, 6), round(lon, 6)]
        cache.setdefault(f"case:{rec.court}|{rec.case_no}", [round(lat, 6), round(lon, 6)])
        added += 1

    print(f"낙찰 원본 {len(rows)}건 → 좌표 추가 {added} · 이미보유 {skipped} · "
          f"좌표없음 {no_coord} · bbox탈락 {bad_bbox} · 파싱실패 {parse_fail}")
    print(f"캐시 키 {before} → {len(cache)}")
    if args.dry_run:
        print("(dry-run — 저장하지 않음)")
        return 0
    if added:
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        print(f"저장: {cache_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
