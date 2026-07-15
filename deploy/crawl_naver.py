"""네이버 KB시세·호가 배치 수집 — 아파트·오피스텔 물건별 매핑·저장.

파이프라인: 좌표 → 법정동(cortar) → 단지목록 → 이름·면적 매칭 → KB시세(+호가 폴백) → naver_prices 저장.
Safety: NaverClient(지터·세션갱신·429백오프·순차) + kill-switch(NAVER_STOP). 이어받기: naver_prices 기존분 skip.
캐시: cortar→단지목록, complexNo→상세 를 data/naver_cache.json 에 영구화(재fetch 최소화).

사용:
  PYTHONUTF8=1 AUCTION_DB=auction.db .venv/Scripts/python.exe -m deploy.crawl_naver [--limit N] [--refresh]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import coords, naver_match, store  # noqa: E402
from src.naver_client import NaverBlocked, NaverClient  # noqa: E402

_CACHE = ROOT / "data" / "naver_cache.json"
_KST = datetime.now().astimezone().tzinfo


def _load_env():
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _won_from_manwon(v) -> int | None:
    try:
        return int(round(float(v) * 10000))
    except (TypeError, ValueError):
        return None


def _parse_kor_price(s: str) -> int | None:
    """'3억 2,000' → 320000000, '9,500'(만원) → 95000000."""
    if not s:
        return None
    s = str(s).replace(",", "").strip()
    m = re.match(r"(?:(\d+)억)?\s*(\d+)?", s)
    if not m:
        return None
    eok = int(m.group(1)) if m.group(1) else 0
    man = int(m.group(2)) if m.group(2) else 0
    won = eok * 100_000_000 + man * 10_000
    return won or None


class Cache:
    """중복 요청 제거용 영구 캐시 — 다세대 경매(건물당 여러 세대)·같은 동네 물건이 재요청 안 하게."""
    _KEYS = ("cortar_pt", "cortar_complexes", "complex_detail", "kb", "arts")

    def __init__(self):
        for k in self._KEYS:
            setattr(self, k, {})
        if _CACHE.exists():
            try:
                d = json.loads(_CACHE.read_text(encoding="utf-8"))
                for k in self._KEYS:
                    setattr(self, k, d.get(k, {}))
            except Exception:  # noqa: BLE001
                pass

    def save(self):
        _CACHE.write_text(json.dumps({k: getattr(self, k) for k in self._KEYS},
                                     ensure_ascii=False), encoding="utf-8")


def _targets(conn, cache_coords, limit, refresh, retry_failed=False):
    if refresh:
        done = set()
    else:
        # retry_failed: 실패로 저장된 행(no_match/no_kb/no_coord)을 '미처리'로 봐 재시도 대상에 포함.
        # 코드 수정(음차맵·유형버그 등) 후 옛 실패분을 회수할 때 쓴다 — 성공분은 그대로 건너뛴다.
        done = store.naver_done_keys(conn, include_failed=not retry_failed)
    rows = conn.execute(
        "SELECT doc_id, court, case_no, item_no, apt_name, area_m2, property_type FROM scored_listings "
        "WHERE property_type IN ('아파트','오피스텔') AND apt_name != '' ORDER BY case_no").fetchall()
    out = []
    for r in rows:
        key = (r["court"], r["case_no"], r["item_no"])
        if key in done:
            continue
        pt = coords.lookup(cache_coords, r["doc_id"], r["case_no"], court=r["court"])
        out.append((r, pt))
        if limit and len(out) >= limit:
            break
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="네이버 KB시세·호가 수집")
    ap.add_argument("--db", default=os.environ.get("AUCTION_DB", "auction.db"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--refresh", action="store_true", help="기존분도 재수집")
    ap.add_argument("--retry-failed", dest="retry_failed", action="store_true",
                    help="실패로 저장된 행(no_match/no_kb/no_coord)만 재시도 — 성공분은 유지. "
                         "코드 수정(음차맵·유형버그) 후 옛 실패분 회수용")
    args = ap.parse_args(argv)
    _load_env()

    conn = store.connect(args.db)
    coord_cache = coords.load_coord_cache()
    targets = _targets(conn, coord_cache, args.limit, args.refresh, args.retry_failed)
    total = len(targets)
    mode = "전량재수집" if args.refresh else ("실패분 재시도" if args.retry_failed else "이어받기")
    print(f"[*] 대상 {total}건 (DB={args.db}, 모드={mode})", flush=True)
    if not total:
        return 0

    cache = Cache()
    mn = float(os.environ.get("AUCTION_NAVER_MIN", "1.5"))
    mx = float(os.environ.get("AUCTION_NAVER_MAX", "3.0"))
    nc = NaverClient(min_delay=mn, max_delay=mx)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stat = {"matched_kb": 0, "matched_ask": 0, "no_kb": 0, "no_match": 0, "no_coord": 0}
    try:
        for i, (r, pt) in enumerate(targets, 1):
            row = {"court": r["court"], "case_no": r["case_no"], "item_no": r["item_no"],
                   "fetched_at": now, "status": "no_match"}
            try:
                _process(nc, cache, r, pt, row)
            except NaverBlocked:
                raise
            except Exception as e:  # noqa: BLE001 — 한 건 실패가 전체를 막지 않게
                print(f"  [{i}] {r['apt_name']} 오류: {type(e).__name__}: {str(e)[:60]}", flush=True)
            stat[row["status"]] = stat.get(row["status"], 0) + 1
            store.save_naver_price(conn, row)
            if i % 25 == 0 or i == total:
                cache.save()
                pct = 100 * i // total
                bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                print(f"  [{bar}] {pct}% ({i}/{total}) "
                      f"KB {stat['matched_kb']}·호가 {stat['matched_ask']}·KB無 {stat['no_kb']}·"
                      f"매칭실패 {stat['no_match']}·좌표無 {stat['no_coord']} (콜 {nc.calls})", flush=True)
    except NaverBlocked as e:
        print(f"[중단] {e}", flush=True)
    finally:
        cache.save()
        nc.close()
    print(f"[완료] KB {stat['matched_kb']}·호가 {stat['matched_ask']}·KB無 {stat['no_kb']}·"
          f"매칭실패 {stat['no_match']}·좌표無 {stat['no_coord']}", flush=True)
    return 0


def _process(nc, cache, r, pt, row):
    if not pt:
        row["status"] = "no_coord"
        return
    lat, lng = pt
    # 네이버는 아파트(APT)·오피스텔(OPST)이 별도 타입 — 유형에 맞는 목록을 조회해야 매칭된다.
    kind = "OPST" if r["property_type"] == "오피스텔" else "APT"
    # 좌표별 cortar 캐시(~110m 반올림) — 같은 건물 세대·같은 동네 물건이 재조회 안 하게.
    ptkey = f"{lat:.3f},{lng:.3f}"
    if ptkey not in cache.cortar_pt:
        cache.cortar_pt[ptkey] = nc.cortar_for(lat, lng) or ""
    cortar = cache.cortar_pt[ptkey]
    if not cortar:
        row["status"] = "no_coord"
        return
    ckey = f"{cortar}:{kind}"
    if ckey not in cache.cortar_complexes:
        cache.cortar_complexes[ckey] = nc.complexes_in(cortar, kind=kind)
    comps = cache.cortar_complexes[ckey]
    best, bs = naver_match.best_complex(r["apt_name"], comps)
    if not best or bs < 0.70:
        row["status"] = "no_match"
        return
    cno = str(best["complexNo"])
    if cno not in cache.complex_detail:
        cache.complex_detail[cno] = nc.complex_detail(cno) or {}
    det = cache.complex_detail[cno]
    pys = det.get("complexPyeongDetailList") or []
    area = float(r["area_m2"] or 0)
    bestpy, bd = naver_match.best_area(area, pys)
    conf = naver_match.accept(bs, bd) if bestpy else None
    row.update(complex_no=cno, complex_name=best.get("complexName", ""), match_conf=conf or "저신뢰")
    if not conf:
        row["status"] = "no_match"
        return
    an = bestpy.get("pyeongNo") or bestpy.get("areaNo")
    row["area_no"] = str(an)
    kbkey = f"{cno}:{an}"          # 같은 단지·같은 면적타입 KB시세 재요청 방지
    if kbkey not in cache.kb:
        cache.kb[kbkey] = nc.kb_price(cno, an)
    prices = cache.kb[kbkey]
    if prices:
        p = prices[0]
        row.update(status="matched_kb", base_ymd=p.get("baseYearMonthDay", ""),
                   kb_low=_won_from_manwon(p.get("dealLowPriceLimit")),
                   kb_avg=_won_from_manwon(p.get("dealAveragePrice")),
                   kb_high=_won_from_manwon(p.get("dealUpperPriceLimit")),
                   lease_avg=_won_from_manwon(p.get("leaseAveragePrice")))
        return
    # KB 미등재(주상복합 등) → 호가 폴백. 단지·유형별 호가목록 캐시.
    akey = f"{cno}:{kind}"
    if akey not in cache.arts:
        cache.arts[akey] = nc.articles(cno, kind=kind)
    arts = cache.arts[akey]
    prc = [_parse_kor_price(a.get("dealOrWarrantPrc")) for a in arts
           if abs(float(a.get("area2") or a.get("area1") or 0) - area) < 8]
    prc = [x for x in prc if x]
    if prc:
        row.update(status="matched_ask", ask_min=min(prc), ask_max=max(prc), ask_count=len(prc))
    else:
        row["status"] = "no_kb"


if __name__ == "__main__":
    raise SystemExit(main())
