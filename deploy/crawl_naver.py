"""네이버 KB시세·호가·실거래 배치 수집 — 아파트·오피스텔 물건별 매핑·저장.

Phase A(물건 단위): 좌표 → 법정동(cortar) → 단지목록 → 이름·면적 매칭 → KB시세+호가+overview →
  naver_prices 저장 + naver_store(단지메타·KB시계열·호가) upsert.
Phase B(--backfill-real, 단지·평형 단위): 이미 매칭된 (complex_no, area_no) 쌍의 prices/real
  실거래 이력을 커서 루프로 수집 → naver_real_trades. 물건 매칭 불필요 — 쌍당 ~3콜.
  우선순위: 시세추정불가(est NULL) → same_dong_fallback(오염) → 나머지. 재실행 시
  naver_real_trades 기존 쌍 skip(멱등 이어받기).

Safety: NaverClient(지터·세션갱신·429백오프·순차) + kill-switch(NAVER_STOP).
캐시(C1 원본 전량 저장): cortar→단지목록, complexNo→상세/overview, (cno:an)→kb/real 을
data/naver_cache.json 에 영구화 — 파싱 버그가 나도 재크롤 없이 회수 가능.

사용:
  PYTHONUTF8=1 AUCTION_DB=auction.db .venv/Scripts/python.exe -m deploy.crawl_naver [--limit N] [--refresh]
  PYTHONUTF8=1 .venv/Scripts/python.exe -m deploy.crawl_naver --backfill-real [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import coords, naver_match, store  # noqa: E402
from src import naver_store as ns  # noqa: E402
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
    """중복 요청 제거용 영구 캐시 — 다세대 경매(건물당 여러 세대)·같은 동네 물건이 재요청 안 하게.

    (2026-07-19) overview·real 추가 — C1 원칙(원본 전량 저장): 신규 엔드포인트 응답도 통째 보존.
    """
    _KEYS = ("cortar_pt", "cortar_complexes", "complex_detail", "kb", "arts", "overview", "real")

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


def _targets(conn, cache_coords, limit, refresh, retry_failed=False, only_sold=False):
    if refresh:
        done = set()
    else:
        # retry_failed: 실패로 저장된 행(no_match/no_kb/no_coord)을 '미처리'로 봐 재시도 대상에 포함.
        # 코드 수정(음차맵·유형버그 등) 후 옛 실패분을 회수할 때 쓴다 — 성공분은 그대로 건너뛴다.
        done = store.naver_done_keys(conn, include_failed=not retry_failed)
    # 우선순위 큐(C6): ①시세추정불가(est NULL — 네이버가 유일한 시세 희망) ②same_dong_fallback
    # (오염 의심 — 실거래 교정 대상) ③나머지. 차단으로 중간에 죽어도 가치 높은 물건부터 처리된다.
    # (2026-07-27) 낙찰 기록(sold_listings)도 대상에 포함한다 — 종전엔 활성 물건만 봐서
    # 종결 물건은 시세가 영영 비어 있었다(차익·점수 정렬 불가, 상세 시뮬 매도가 0원).
    # sold 는 scored 에 없는 컬럼(market_scope)이 없으므로 우선순위 2(나머지)로 넣는다.
    # 같은 키가 양쪽에 있으면 활성분이 먼저 나오고 done 셋이 중복을 걸러 낸다.
    rows = conn.execute(
        "SELECT doc_id, court, case_no, item_no, apt_name, area_m2, property_type, "
        "  CASE WHEN est_market_price IS NULL THEN 0 "
        "       WHEN market_scope='same_dong_fallback' THEN 1 ELSE 2 END AS pri, 0 AS is_sold "
        "FROM scored_listings "
        "WHERE property_type IN ('아파트','오피스텔') AND apt_name != '' "
        "UNION ALL "
        # doc_id 는 좌표 캐시의 1순위 키라 원본(raw_listings)에서 되찾는다 — 없으면 빈 값이라도
        # coords.lookup 이 court|case_no 폴백으로 좌표를 찾는다(회귀 없음).
        # ⚠ raw_listings 는 같은 물건에 크롤 회차마다 다른 doc_id 로 여러 행이 쌓인다(최대 12행
        # 실측). 그냥 조인하면 같은 물건을 여러 번 크롤한다 — **최신 1행만** 집어온다.
        "SELECT COALESCE(("
        "    SELECT r.doc_id FROM raw_listings r "
        "    WHERE r.court=s.court AND r.case_no=s.case_no AND r.item_no=s.item_no "
        "    ORDER BY r.fetched_at DESC LIMIT 1), '') AS doc_id, "
        "  s.court, s.case_no, s.item_no, s.apt_name, "
        "  s.area_m2, s.property_type, 0 AS pri, 1 AS is_sold "
        "FROM sold_listings s "
        "WHERE s.property_type IN ('아파트','오피스텔') AND s.apt_name != '' "
        "ORDER BY pri, case_no").fetchall()
    out = []
    for r in rows:
        # (2026-07-27) --only-sold: 낙찰 기록만. 활성 물건 수백 건은 일일 크롤의 몫이라
        # 낙찰 시세 보강을 위해 그 큐 전체를 다시 도는 것은 낭비이고 밴 리스크만 키운다.
        if only_sold and not r["is_sold"]:
            continue
        key = (r["court"], r["case_no"], r["item_no"])
        if key in done:
            continue
        pt = coords.lookup(cache_coords, r["doc_id"], r["case_no"], court=r["court"])
        out.append((r, pt))
        if limit and len(out) >= limit:
            break
    return out


def _backfill_pairs(conn, limit=None, refresh=False, stale_days=None):
    """Phase B 대상 — 매칭된 (complex_no, area_no) 쌍, 우선순위순.

    이어받기: **naver_pair_status에 이미 확인된 쌍은 skip**(실거래 0건 쌍도 확인 완료로 기록돼
    매 실행 재크롤 안 함 — M3 수정). --refresh면 전량 재수집.
    stale_days 지정(증분): 그 일수(+쌍별 지터)보다 오래 전 확인된 쌍만 재수집 대상
    — 만기 시각 계산은 naver_store.fresh_pairs 가 담당한다(날짜 파싱·지터를 한 곳에 모음).
    """
    from src import naver_store as _ns  # noqa: PLC0415

    if refresh:
        have = set()
    elif stale_days is not None:
        # 증분: stale_days(+쌍별 지터) 이내 확인된 쌍만 '완료'로 봐 제외 → 오래된 쌍은 재대상.
        # (2026-08-07) 단일 임계 → 쌍별 지터로 교체. 같은 날 대량 확인분이 14일 뒤 한꺼번에
        # 만기돼 하루 대상이 105→716건으로 튄 실사고(8/4) 재발 방지 — naver_store.fresh_pairs 주석.
        jitter = int(os.environ.get("AUCTION_NAVER_STALE_JITTER", "7"))
        have = _ns.fresh_pairs(conn, stale_days, jitter_days=jitter)
    else:
        have = _ns.checked_pairs(conn)   # 처리한 모든 쌍(0건 포함) skip
    # (2026-07-27) 활성 물건과 **낙찰 기록** 양쪽의 매칭 쌍을 모은다 — Phase A 가 sold 도
    # 매칭하므로 여기서 빼면 그 쌍의 확정 실거래를 영영 못 받아 재채점이 이름매칭으로 강등된다.
    rows = conn.execute(
        "SELECT np.complex_no, np.area_no, MAX(np.complex_name) name, "
        "  MIN(l.pri) pri, MAX(l.property_type) ptype "
        "FROM naver_prices np JOIN ("
        "  SELECT court, case_no, item_no, property_type, "
        "    CASE WHEN est_market_price IS NULL THEN 0 "
        "         WHEN market_scope='same_dong_fallback' THEN 1 ELSE 2 END AS pri "
        "  FROM scored_listings "
        "  UNION ALL "
        "  SELECT court, case_no, item_no, property_type, 0 AS pri FROM sold_listings"
        ") l ON l.court=np.court AND l.case_no=np.case_no AND l.item_no=np.item_no "
        "WHERE np.complex_no IS NOT NULL AND np.complex_no != '' "
        "  AND np.area_no IS NOT NULL AND np.area_no != '' "
        "GROUP BY np.complex_no, np.area_no ORDER BY pri, np.complex_no").fetchall()
    out = [r for r in rows if (r["complex_no"], r["area_no"]) not in have]
    return out[:limit] if limit else out


def backfill_real(args) -> int:
    """Phase B: 매칭된 쌍의 prices/real 실거래 + overview + 호가를 단지 단위로 백필.

    --incremental: naver_pair_status 기준 stale_days(기본 14, 쌍별 지터)보다 오래된 쌍 +
    미확인 신규 쌍만. 이 모드에서는 쌍마다 **보유한 최신 거래일 이후만** 받는다(조기 종료).
    매일 스케줄에서 이 모드로 돌면 안티밴 예산을 아끼며 신선도를 유지한다.
    """
    conn = store.connect(args.db)
    ns.ensure_schema(conn)
    incremental = bool(getattr(args, "incremental", False))
    stale = args.stale_days if incremental else None
    pairs = _backfill_pairs(conn, args.limit, args.refresh, stale_days=stale)
    total = len(pairs)
    # 시간 예산(분). 증분 모드 기본 90분 — 앞 단계(경매·권리) 2시간 + 뒤 단계(재채점)까지
    # 작업 스케줄러 5시간 제한 안에 반드시 들어오게. 0/음수면 무제한(수동 전량 작업용).
    max_minutes = getattr(args, "max_minutes", None)
    if max_minutes is None:
        max_minutes = int(os.environ.get("AUCTION_NAVER_MAX_MINUTES", "90")) if incremental else 0
    max_minutes = max(0, int(max_minutes))
    mode = f"증분(>{args.stale_days}일±지터)" if stale is not None else ("전량재수집" if args.refresh else "이어받기")
    budget = f"·예산 {max_minutes}분" if max_minutes else ""
    print(f"[*] backfill-real 대상 {total}쌍 (DB={args.db}, 모드={mode}{budget})", flush=True)
    if not total:
        return 0
    cache = Cache()
    mn = float(os.environ.get("AUCTION_NAVER_MIN", "1.0"))
    mx = float(os.environ.get("AUCTION_NAVER_MAX", "2.2"))
    # 실거래 페이지 캡(2026-07-19 속도튜닝): 80p는 대단지에서 쌍당 ~90콜 → 45h ETA 실측.
    # 25p(~250행)면 최근 5~10년 확보 — 창 계층화(최대 60개월)·차트에 충분, 옛 꼬리만 포기.
    real_cap = int(os.environ.get("AUCTION_NAVER_REAL_PAGES", "25"))
    nc = NaverClient(min_delay=mn, max_delay=mx)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n_rows = n_pairs = n_trunc = 0
    n_fail = n_early = 0
    # (2026-08-07 조기 종료) 쌍별 '보유한 최신 거래일' — 그보다 새로운 거래만 받고 멈춘다.
    # 증분 모드에서만 적용(전량재수집/신규 쌍은 stop 없음 → 종전대로 전량).
    prev_status = ns.pair_status_map(conn) if incremental else {}
    # (2026-08-07 시간 예산) 예산을 넘기면 남은 쌍을 **명시하고** 정상 종료한다. 종전엔 예산 개념이
    # 없어 작업 스케줄러 5시간 제한에 강제 종료됐고, 그러면 뒤따르는 [5/5] 재채점이 통째로
    # 실행되지 않았다(8/4~8/7 4일 연속). 완주 보장이 부분 수집보다 중요하다.
    deadline = (time.monotonic() + max_minutes * 60) if max_minutes else None
    n_skipped_budget = 0
    try:
        for i, p in enumerate(pairs, 1):
            cno, ano = str(p["complex_no"]), str(p["area_no"])
            if deadline is not None and time.monotonic() > deadline:
                n_skipped_budget = total - i + 1
                print(f"  [예산] {max_minutes}분 경과 — 남은 {n_skipped_budget}쌍은 다음 회차로 "
                      f"미룹니다(정상 종료: 뒤 단계 재채점이 돌아야 함)", flush=True)
                break
            try:
                # 1) 실거래(핵심) — 캐시 저장(C1) 후 upsert
                st = prev_status.get((cno, ano)) or {}
                stop_ymd = st.get("latest_ymd") or None
                rows, meta = nc.real_prices(cno, ano, max_pages=real_cap,
                                            stop_before_ymd=stop_ymd)
                early = bool(meta.get("early_stop"))
                if early:
                    n_early += 1
                cache.real[f"{cno}:{ano}"] = {"rows": rows, "meta": meta}
                if not meta["exhausted"]:
                    n_trunc += 1
                saved = ns.upsert_real_trades(conn, cno, ano, rows, now)
                n_rows += saved
                n_pairs += 1
                # (M3) 쌍 처리상태 기록 — 0건이어도 '확인함'으로 남겨 재크롤 방지·증분 기준.
                live = ns.load_real_trades(conn, cno, ano)
                latest = live[0]["trade_ymd"] if live else ""
                # 조기 종료는 '끝까지 봤다'는 뜻이 아니므로 잘림(exhausted) 판정을 갱신하지 않는다 —
                # 종전 값을 보존한다(True 로 덮으면 진짜 잘린 쌍의 신호가 사라진다).
                exhausted = meta.get("exhausted", True)
                if early and "exhausted" in st:
                    exhausted = st["exhausted"]
                ns.record_pair_status(conn, cno, ano, len(live), latest, exhausted, now)
                # 2) overview(단지당 1회) — 전세가율·매물수·세대수
                if cno not in cache.overview:
                    cache.overview[cno] = nc.overview(cno) or {}
                    det = cache.complex_detail.get(cno) or {}
                    ns.upsert_complex(conn, det, now, overview=cache.overview[cno])
            except NaverBlocked:
                raise   # 차단·kill-switch는 전체 중단(우회 금지)
            except Exception as e:  # noqa: BLE001 — 한 쌍 실패가 10시간 크롤을 죽이지 않게
                # (실측 2026-07-19) 일시 오류 미방어로 246쌍에서 전체 크래시 — 쌍 단위로 격리하고
                # 스킵을 침묵시키지 않는다(개수·로그). 스킵된 쌍은 다음 실행 이어받기가 재시도.
                n_fail += 1
                print(f"  ⚠ 쌍 실패 skip ({cno}:{ano}) {type(e).__name__}: {str(e)[:60]}", flush=True)
            # (속도튜닝) 호가는 백필에서 제외 — 실거래가 생기면 호가 폴백 중요도가 급락하고,
            # 호가 신선도는 Phase A·일일 증분 크롤 몫. 쌍당 콜 ~90→~7로 감축(45h→~6h).
            if i % 10 == 0 or i == total:
                cache.save()
                pct = 100 * i // total
                print(f"  [{pct:3d}%] {i}/{total}쌍 — 실거래 {n_rows}행"
                      f"{f'·잘림 {n_trunc}' if n_trunc else ''} (콜 {nc.calls}) {p['name'][:12]}", flush=True)
    except NaverBlocked as e:
        print(f"[중단] {e} — {n_pairs}쌍/{n_rows}행까지 저장됨(이어받기 가능)", flush=True)
    finally:
        cache.save()
        nc.close()
    # 절단은 반드시 명시한다 — 조용히 줄이면 '전량 처리됨'으로 읽힌다(하네스 원칙).
    extra = f"·조기종료 {n_early}" if n_early else ""
    if n_skipped_budget:
        extra += f"·예산초과 미처리 {n_skipped_budget}쌍(다음 회차)"
    print(f"[완료] {n_pairs}쌍 · 실거래 {n_rows}행 적재 "
          f"(잘림 {n_trunc}·실패skip {n_fail}{extra})", flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="네이버 KB시세·호가 수집")
    ap.add_argument("--db", default=os.environ.get("AUCTION_DB", "auction.db"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--refresh", action="store_true", help="기존분도 재수집")
    ap.add_argument("--retry-failed", dest="retry_failed", action="store_true",
                    help="실패로 저장된 행(no_match/no_kb/no_coord)만 재시도 — 성공분은 유지. "
                         "코드 수정(음차맵·유형버그) 후 옛 실패분 회수용")
    ap.add_argument("--backfill-real", dest="backfill_real", action="store_true",
                    help="Phase B: 매칭된 (단지,평형) 쌍의 prices/real 실거래를 단지 단위로 백필")
    ap.add_argument("--incremental", action="store_true",
                    help="증분 갱신: naver_pair_status 기준 오래된(>stale-days) 쌍+신규만 재수집(일일 스케줄용)")
    ap.add_argument("--max-minutes", dest="max_minutes", type=int, default=None,
                    help="Phase B 시간 예산(분). 초과 시 남은 쌍을 명시하고 정상 종료 — 뒤따르는 "
                         "재채점 단계가 반드시 돌게 한다. 미지정 시 증분 모드는 90분"
                         "(env AUCTION_NAVER_MAX_MINUTES), 그 외 무제한. 0=무제한")
    ap.add_argument("--stale-days", dest="stale_days", type=int, default=14,
                    help="증분 신선도 기준(일). 이보다 오래 확인 안 된 쌍만 재수집")
    ap.add_argument("--only-sold", dest="only_sold", action="store_true",
                    help="낙찰 기록(sold_listings)만 대상 — 활성 물건 큐는 건드리지 않는다")
    args = ap.parse_args(argv)
    _load_env()

    if args.backfill_real:
        return backfill_real(args)

    conn = store.connect(args.db)
    ns.ensure_schema(conn)
    coord_cache = coords.load_coord_cache()
    targets = _targets(conn, coord_cache, args.limit, args.refresh, args.retry_failed,
                       only_sold=args.only_sold)
    total = len(targets)
    mode = "전량재수집" if args.refresh else ("실패분 재시도" if args.retry_failed else "이어받기")
    if args.only_sold:
        mode += "·낙찰만"
    print(f"[*] 대상 {total}건 (DB={args.db}, 모드={mode})", flush=True)
    if not total:
        return 0

    cache = Cache()
    mn = float(os.environ.get("AUCTION_NAVER_MIN", "1.0"))
    mx = float(os.environ.get("AUCTION_NAVER_MAX", "2.2"))
    nc = NaverClient(min_delay=mn, max_delay=mx)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stat = {"matched_kb": 0, "matched_ask": 0, "no_kb": 0, "no_match": 0, "no_coord": 0}
    try:
        for i, (r, pt) in enumerate(targets, 1):
            row = {"court": r["court"], "case_no": r["case_no"], "item_no": r["item_no"],
                   "fetched_at": now, "status": "no_match"}
            try:
                _process(nc, cache, r, pt, row, conn=conn, now=now)
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


def _process(nc, cache, r, pt, row, conn=None, now=""):
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
    if not bestpy or naver_match.accept(bs, bd) is None:
        # (P0 실측 2026-07-19) 캐시된 상세가 평형 일부만 담고 있던 사례(24958: 6평형 중 1개) —
        # 캐시 불완전이 무매칭으로 굳지 않게, 면적 매칭 실패 시 상세를 1회 강제 재수집 후 재시도.
        cache.complex_detail[cno] = nc.complex_detail(cno) or {}
        det = cache.complex_detail[cno]
        pys = det.get("complexPyeongDetailList") or []
        bestpy, bd = naver_match.best_area(area, pys)
    conf = naver_match.accept(bs, bd) if bestpy else None
    row.update(complex_no=cno, complex_name=best.get("complexName", ""), match_conf=conf or "저신뢰")
    if not conf:
        row["status"] = "no_match"
        return
    an = str(bestpy.get("pyeongNo") or bestpy.get("areaNo"))
    row["area_no"] = an
    # 단지 메타(overview 포함) — 전세가율·매물수·세대수·사용승인일 (2026-07-19 개편)
    if conn is not None:
        if cno not in cache.overview:
            cache.overview[cno] = nc.overview(cno) or {}
        ns.upsert_complex(conn, det, now, overview=cache.overview[cno])
    kbkey = f"{cno}:{an}"          # 같은 단지·같은 면적타입 KB시세 재요청 방지
    if kbkey not in cache.kb:
        cache.kb[kbkey] = nc.kb_price(cno, an)
    prices = cache.kb[kbkey]
    if conn is not None and prices:
        ns.upsert_kb_history(conn, cno, an, prices, now)   # 시계열 전체 보존(종전 [0]만 쓰고 폐기)
    # 호가 — 항상 수집(C2-a: 종전 'KB 있으면 스킵'은 교차검증 불가) + 전 페이지(C2-b).
    akey = f"{cno}:{kind}"
    if akey not in cache.arts:
        arts_all, _complete = nc.articles_all(cno, kind=kind)
        cache.arts[akey] = arts_all
    arts = cache.arts[akey]
    if conn is not None and arts:
        ns.upsert_articles(conn, cno, arts, now)
    prc = [_parse_kor_price(a.get("dealOrWarrantPrc")) for a in arts
           if abs(float(a.get("area2") or a.get("area1") or 0) - area) < 8]
    prc = [x for x in prc if x]
    if prc:
        row.update(ask_min=min(prc), ask_max=max(prc), ask_count=len(prc))
    if prices:
        p = prices[0]
        row.update(status="matched_kb", base_ymd=p.get("baseYearMonthDay", ""),
                   kb_low=_won_from_manwon(p.get("dealLowPriceLimit")),
                   kb_avg=_won_from_manwon(p.get("dealAveragePrice")),
                   kb_high=_won_from_manwon(p.get("dealUpperPriceLimit")),
                   lease_avg=_won_from_manwon(p.get("leaseAveragePrice")),
                   lease_low=_won_from_manwon(p.get("leaseLowPriceLimit")),
                   lease_high=_won_from_manwon(p.get("leaseUpperPriceLimit")))
    elif prc:
        row["status"] = "matched_ask"
    else:
        row["status"] = "no_kb"


if __name__ == "__main__":
    raise SystemExit(main())
