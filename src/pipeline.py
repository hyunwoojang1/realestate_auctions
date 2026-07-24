"""end-to-end 파이프라인: 수집 → 매칭 → 스코어 → 정렬.

PoC 기본은 샘플 fixture. --live + 키가 있으면 국토부 라이브 호출(F10).
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
from pathlib import Path

from .matcher import estimate_market
from .models import AuctionListing, ScoredListing, Trade
from .molit_client import (
    fetch_trades,
    parse_apt_trades_xml,
    parse_offi_trades_xml,
    parse_rh_trades_xml,
    recent_ymds,
)
from .score import score_listing

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
def _live_months() -> int:
    """현재 유효 수집 개월수. 단일 출처=config.SAMPLE.live_months(기본 12).

    (2026-07-17) 옛 pipeline.LIVE_MONTHS 모듈상수는 config와 값이 갈리는 중구난방이라 제거.
    config를 유일 출처로 삼고, monkeypatch(SAMPLE 교체)도 그대로 반영된다.
    """
    from . import config as _cfg  # noqa: PLC0415 — 런타임 monkeypatch(SAMPLE 교체) 반영
    return _cfg.SAMPLE.live_months if _cfg.SAMPLE else _cfg.SampleConfig().live_months


def load_sample_auctions(path: Path | None = None) -> list[AuctionListing]:
    p = path or (DATA / "sample_auctions.json")
    raw = json.loads(p.read_text(encoding="utf-8"))
    return [AuctionListing(**r) for r in raw]


def collect_courtauction_records(cash_won: int | None = None, sido_cd: str = "",
                                 appraisal_buffer: float = 3.0, max_pages: int = 10,
                                 client=None, extra=None, warm: bool = True) -> list:
    """courtauction 라이브 검색 → CourtAuctionRecord 리스트(원본 전체 보존, 캐시 diff용).

    - cash_won 지정: affordable_search(서버 감정가버퍼로 볼륨축소 + 로컬 '최저가≤현금' 정밀필터).
    - 미지정: 일반 search(지역 등 필터만).
    개인정보는 courtauction_fields.sanitize_row가 제거. 안전장치는 CourtAuctionClient가 담당.
    """
    from .courtauction_client import CourtAuctionClient, SearchFilter  # noqa: PLC0415

    c = client or CourtAuctionClient()
    flt = extra if extra is not None else SearchFilter(sido_cd=sido_cd)
    if cash_won:
        return c.affordable_search(cash_won, appraisal_buffer=appraisal_buffer,
                                   extra=flt, max_pages=max_pages, warm=warm)
    return list(c.search(flt, max_pages=max_pages, warm=warm))


def load_courtauction_auctions(cash_won: int | None = None, sido_cd: str = "",
                               appraisal_buffer: float = 3.0, max_pages: int = 10,
                               client=None, extra=None) -> list[AuctionListing]:
    """단일 검색 → AuctionListing 리스트(차익 파이프라인 입력). client/extra는 테스트 주입용."""
    from .courtauction_fields import to_auction_listing  # noqa: PLC0415

    recs = collect_courtauction_records(cash_won, sido_cd, appraisal_buffer, max_pages, client, extra)
    listings = [to_auction_listing(r) for r in recs]
    logger.info("courtauction 실매물 %d건 → AuctionListing 변환", len(listings))
    return listings


# (감사 2026-07-20 CRITICAL) 직전 load_courtauction_nationwide 실행이 중간 차단으로
# '부분 수집'이었는지 신호. run.py가 True를 보면 전량 교체(replace_all)를 병합(upsert)으로
# 강등한다 — 부분 스냅샷 전량교체는 미수집 시도 물건·권리를 로컬·클라우드에서 삭제한다.
NATIONWIDE_PARTIAL = False


def load_courtauction_nationwide(cash_won: int | None = None, appraisal_buffer: float = 3.0,
                                 max_pages_per_sido: int = 10, client=None,
                                 sidos: list[str] | None = None) -> list:
    """전국 17개 시도를 샤딩 수집 → CourtAuctionRecord 리스트(docid 기준 중복제거).

    한 client를 공유해 일일상한·지터·세션이 시도 전체에 누적 적용된다(밴 회피).
    중간에 차단(CourtAuctionBlocked) 시 그때까지 모은 부분결과를 반환하고 중단한다
    (모듈 플래그 NATIONWIDE_PARTIAL=True — 호출부가 전량교체를 강등해야 함).
    시도별로 페이지를 나눠 '1→700 순차순회' 봇 패턴을 피하고 구간을 작게 유지한다.
    """
    global NATIONWIDE_PARTIAL
    from .courtauction_client import CourtAuctionBlocked, CourtAuctionClient  # noqa: PLC0415
    from .courtauction_fields import SIDO_CODES  # noqa: PLC0415

    codes = sidos if sidos is not None else list(SIDO_CODES)
    # (감사 2026-07-20 CRITICAL) 요청예산 불변식 복원 — max_pages 상향(25→80) 시 기본
    # daily_cap 500으로는 중간 차단(부분수집)이 데이터 의존적으로 발생한다. 예산을
    # '시도수 × (페이지+워밍업) + 재시도 여유'로 산출해 상한이 페이지 캡보다 먼저 끊지 않게 한다.
    budget = max(500, len(codes) * (max_pages_per_sido + 2) + 100)
    c = client or CourtAuctionClient(daily_cap=budget)
    NATIONWIDE_PARTIAL = False
    merged: dict[str, object] = {}
    for i, sd in enumerate(codes):
        try:
            recs = collect_courtauction_records(
                cash_won=cash_won, sido_cd=sd, appraisal_buffer=appraisal_buffer,
                max_pages=max_pages_per_sido, client=c, warm=(i == 0))
        except CourtAuctionBlocked as e:
            logger.warning("시도 %s(%s)에서 중단(%s) — 부분수집 %d건 반환",
                           sd, SIDO_CODES.get(sd, ""), e, len(merged))
            NATIONWIDE_PARTIAL = True
            break
        for r in recs:
            # T1: doc_id 없을 때 case_no 단일 폴백은 같은 사건의 다른 물건번호를 삼킨다 → 복합키.
            merged[r.doc_id or f"{r.court}|{r.case_no}|{r.item_no}"] = r
        logger.info("시도 %s(%s): +%d → 누적 %d건", sd, SIDO_CODES.get(sd, ""), len(recs), len(merged))
    return list(merged.values())


def load_courtauction_from_cache(cache_path: str | Path | None = None,
                                 fixture_path: Path | None = None) -> list:
    """오프라인 dry-run용: 네트워크 호출 0으로 CourtAuctionRecord 리스트를 만든다.

    우선순위:
      1) full-record 캐시(`{"records": [<raw dict>, ...]}` 형식)가 있으면 그걸 parse_row.
      2) 없거나 비어 있으면 `data/sample_courtauction.json` fixture의 dlt_srchResult로 폴백.
    어느 경우에도 courtauction 실서버를 호출하지 않는다(밤샘 오프라인 정책).
    """
    from .courtauction_fields import parse_row  # noqa: PLC0415

    recs = _records_from_full_cache(cache_path)
    if recs:
        logger.info("courtauction 캐시(full-record) %d건 로드 — 오프라인", len(recs))
        return recs

    fx = fixture_path or (DATA / "sample_courtauction.json")
    if not fx.exists():
        logger.warning("오프라인 폴백 fixture 없음(%s) — 빈 목록 반환", fx)
        return []
    j = json.loads(fx.read_text(encoding="utf-8"))
    rows = (j.get("data") or {}).get("dlt_srchResult") or []
    recs = [parse_row(r) for r in rows]
    logger.info("courtauction 샘플 fixture %d건 로드 — 오프라인 폴백", len(recs))
    return recs


def _records_from_full_cache(cache_path: str | Path | None) -> list:
    """full-record 캐시 파일에서 CourtAuctionRecord 복원. 없거나 스냅샷 전용이면 빈 목록."""
    from . import courtauction_cache as cc  # noqa: PLC0415
    from .courtauction_fields import parse_row  # noqa: PLC0415

    p = Path(cache_path or cc.DEFAULT_FULL_CACHE)
    if not p.exists():
        return []
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    rows = blob.get("records") if isinstance(blob, dict) else None
    if not rows:
        return []
    return [parse_row(r) for r in rows if isinstance(r, dict)]


def load_sample_trades() -> list[Trade]:
    """샘플 실거래: 아파트 + 연립다세대(빌라) + 오피스텔 fixture 합본."""
    trades: list[Trade] = []
    sources = [
        ("sample_molit_apt.xml", parse_apt_trades_xml),
        ("sample_rh_trades.xml", parse_rh_trades_xml),
        ("sample_offi_trades.xml", parse_offi_trades_xml),
    ]
    for fname, parser in sources:
        p = DATA / fname
        if p.exists():
            trades.extend(parser(p.read_text(encoding="utf-8")))
    return trades


def _extra_to_trade(et, lawd_cd: str = "") -> Trade:
    """확장 실거래(ExtraTrade: 단독/상업/토지)를 매칭용 Trade로 정규화.

    단지명이 없으므로 apt_name은 비워 두고(법정동+면적 매칭으로만 사용), kind로 유형을 분리한다.
    lawd_cd 태깅으로 타지역 동명 혼입을 막는다(감사 2026-07-10).
    """
    return Trade(apt_name="", area_m2=et.area_m2, price=et.price,
                 deal_ym=et.deal_ym, dong=et.dong, floor=et.floor, kind=et.kind,
                 lawd_cd=lawd_cd)


def load_live_trades(listings: list[AuctionListing], api_key: str,
                     deal_ymd: str) -> list[Trade]:
    """법정동코드(LAWD_CD)별 실거래 라이브 수집.

    기본 아파트/연립다세대/오피스텔 + 필요 시 단독/다가구(sh)·상업업무(nrg)·토지(land) 확장(E).
    확장 유형은 해당 물건이 실제로 있는 법정동에만 호출한다(불필요한 API 부하 회피).
    """
    from datetime import datetime  # noqa: PLC0415

    from . import molit_cache  # noqa: PLC0415
    from .matcher import expected_kind  # noqa: PLC0415
    from .molit_extra_client import fetch_extra_trades  # noqa: PLC0415

    # 시세추정은 아파트·오피스텔만 지원(matcher.SUPPORTED_ESTIMATION_KINDS). 그 유형이 실제로 있는
    # 법정동만 국토부를 호출한다 — 토지·상가만 있는 지역까지 긁으면 est엔 안 쓰이면서 쿼터만 태워 429.
    supported_lawd = {lst.lawd_cd for lst in listings
                      if expected_kind(lst.property_type) in ("apt", "officetel")}
    # 확장 유형(sh/nrg/land)은 현재 시세추정 미지원 → 기본 비수집(쿼터 낭비·429 방지).
    # 향후 유형 확장 시 AUCTION_FETCH_EXTRA=1 로 활성.
    fetch_extra = os.environ.get("AUCTION_FETCH_EXTRA") == "1"
    extra_by_lawd: dict[str, set[str]] = {}
    if fetch_extra:
        for lst in listings:
            k = expected_kind(lst.property_type)
            if k in ("sh", "nrg", "land"):
                extra_by_lawd.setdefault(lst.lawd_cd, set()).add(k)

    ymds = recent_ymds(deal_ymd, _live_months())
    # 열린 달(이번 달+직전 달)은 지연등록 반영 위해 매번 재수집, 그 이전은 캐시 영구재사용.
    open_months = set(ymds[:2])
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cache = molit_cache.connect()

    # 작업목록 (kind, lawd, ymd, is_extra) — 아파트·오피스텔 있는 법정동만.
    districts = [d for d in dict.fromkeys(lst.lawd_cd for lst in listings) if d in supported_lawd]
    tasks = [(k, d, y, False) for d in districts
             for k in ("apt", "rh", "officetel") for y in ymds]
    if fetch_extra:
        for d, kinds in extra_by_lawd.items():
            if d in supported_lawd:
                tasks += [(k, d, y, True) for k in sorted(kinds) for y in ymds]

    def _do_fetch(kind, lawd, ymd, is_extra):
        if is_extra:
            return [_extra_to_trade(t, lawd_cd=lawd)
                    for t in fetch_extra_trades(kind, lawd, ymd, api_key)]
        return fetch_trades(kind, lawd, ymd, api_key)

    trades: list[Trade] = []
    calls = len(tasks)
    hits = fails = 0
    try:
        # 1) 캐시 우선(닫힌 달) — 메인스레드에서 읽기
        to_fetch = []
        for (kind, lawd, ymd, is_extra) in tasks:
            if ymd not in open_months:
                cached = molit_cache._load(cache, kind, lawd, ymd)
                if cached is not None:
                    trades.extend(cached)
                    hits += 1
                    continue
            to_fetch.append((kind, lawd, ymd, is_extra))
        # 2) 나머지 병렬 fetch — 워커는 받기만, 쓰기는 메인스레드(SQLite 락 회피).
        #    국토부는 anti-bot 없어 병렬 안전(courtauction과 다름). 동시성=AUCTION_MOLIT_WORKERS.
        if to_fetch:
            from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: PLC0415
            workers = max(1, int(os.environ.get("AUCTION_MOLIT_WORKERS", "12")))

            def _fetch_one(t):
                k, lawd, y, ex_ = t
                try:
                    return t, _do_fetch(k, lawd, y, ex_)
                except Exception as e:  # noqa: BLE001
                    return t, e

            with ThreadPoolExecutor(max_workers=workers) as pool:
                for fut in as_completed([pool.submit(_fetch_one, t) for t in to_fetch]):
                    (kind, lawd, ymd, is_extra), res = fut.result()
                    if isinstance(res, Exception):
                        fails += 1
                        logger.warning("라이브 호출 실패 kind=%s lawd=%s ymd=%s: %s",
                                       kind, lawd, ymd, res)
                        continue
                    trades.extend(res)
                    if ymd not in open_months:   # 닫힌 달만 캐시(메인스레드 쓰기)
                        molit_cache._save(cache, kind, lawd, ymd, res, now)
    finally:
        cache.close()
    logger.info("라이브 시세 수집: %d콜(캐시적중 %d·실패 %d), %d개월창, 실거래 %d건",
                calls, hits, fails, len(ymds), len(trades))
    if fails:
        # 집계 경고 — '시세추정불가'가 진짜 comps 부재인지 API 실패 때문인지 구분하게 한다.
        logger.warning("라이브 시세 수집: %d/%d 호출 실패. 일부 물건은 comps 부족이 아니라 "
                       "API 실패로 시세추정불가일 수 있음(결과 신뢰도 저하).", fails, calls)
    return trades


def enrich_listings_with_rights(listings: list[AuctionListing], fetch_detail_fn) -> list[AuctionListing]:
    """물건상세 텍스트 페처로 각 물건의 권리를 파싱·반영한 새 리스트 반환(D 배선).

    `fetch_detail_fn(listing)` → (매각물건명세서, 현황조사서, 감정평가서) 텍스트 튜플.
    성공하면 courtauction_rights가 불리언·금액·유형만 추출해 반영하고 rights_verified=True가 된다
    (→ score/UI에서 '권리미확인' 해제, 하드게이트 실작동). 호출 실패·빈 텍스트면 원본 유지(권리미확인).
    개인정보 원문은 저장하지 않는다(파서가 성명 등 미추출).
    """
    from .courtauction_rights import apply_rights, parse_rights  # noqa: PLC0415

    out: list[AuctionListing] = []
    ok = 0
    for lst in listings:
        try:
            texts = fetch_detail_fn(lst)
        except Exception as e:  # noqa: BLE001 — 한 물건 실패가 전체를 막지 않게
            logger.warning("물건상세 수집 실패 %s: %s", lst.case_no, e)
            out.append(lst)
            continue
        if not texts or not any(t and t.strip() for t in texts):
            out.append(lst)   # 미수집 → 권리미확인 유지
            continue
        m, h, g = (list(texts) + ["", "", ""])[:3]
        out.append(apply_rights(lst, parse_rights(m, h, g)))
        ok += 1
    logger.info("권리 enrich: %d/%d 물건 권리분석 반영(rights_verified).", ok, len(listings))
    return out


def apply_rights_from_rows(listings: list[AuctionListing],
                           rights_rows: list[dict]) -> tuple[list[AuctionListing], dict]:
    """이미 크롤된 `listing_rights`(구조화 요지) → 채점 전 AuctionListing 권리필드 반영.

    (감사 2026-07-15) 이 배선이 없어서 batch 채점이 권리를 한 번도 보지 않았다 — 적재된
    8,245건 중 79.7%가 rights_score=85.0(=100−15 소유자점유 기본값) 상수였고, 인수비율
    하드게이트·대항력 페널티가 batch 경로에서 죽어 있었다.

    `enrich_listings_with_rights`(문서 텍스트 페처 기반)와 달리 **네트워크 0** — 이미 DB에
    있는 요지를 쓴다. 서빙(web.py)이 상세 렌더 시 쓰는 어댑터(CaseRights → summarize → badge)와
    **동일 로직**이라 목록 랭킹과 상세 페이지가 같은 사실 위에 서게 된다(종전엔 갈라져 있었다).

    매칭 실패·빈 요지는 **건드리지 않는다** — rights_verified=False("모름")로 남아 '권리미확인'
    등급을 유지한다. 없는 걸 '인수 없음'으로 단정하지 않는다.
    """
    from .courtauction_detail import CaseRights, summarize  # noqa: PLC0415 — 순환 import 회피
    from .score import is_hard_gated  # noqa: PLC0415

    by_key = {(r.get("court", ""), r.get("case_no", ""), str(r.get("item_no", "") or "")): r
              for r in rights_rows}
    out: list[AuctionListing] = []
    stats = {"total": len(listings), "matched": 0, "empty": 0, "gated": 0}
    for lst in listings:
        row = by_key.get((lst.court, lst.case_no, str(lst.item_no or "")))
        if row is None:
            out.append(lst)          # 미크롤 → 권리미확인 유지
            continue
        cr = CaseRights.from_row(row)
        # (2026-07-22) 자유기술란(인수권리·유치권·비고) 전부 빈 요지도 대항력 판정근거 0 →
        # rights_verified=False('모름') 유지. 종전엔 말소기준만 있으면 verified→초록 추천으로
        # 대항력 임차인을 놓쳤다(삼환 2022타경3289: 세 칸 null인데 추천). 랭킹·상세 동일 기준.
        if cr.is_empty or not cr.opposability_assessable:
            stats["empty"] += 1
            out.append(lst)          # 빈 요지 = 판정근거 0 → '없음'이 아니라 '모름'
            continue
        badge = summarize(cr)
        # (2026-07-24 게이트 FAIL 3건 원인) '지분'은 maejibun(리스트 원천) 검출 — 요지 기반
        # badge.special 로 **대체**하면 라벨이 소실돼 지분 물건에 온전가 시세가 부활한다
        # (죽전자이 실측: 권리 재크롤 후 '차익 유력 6.5억' 재발). apply_rights 와 동일 보존.
        special = badge.special
        if "지분" in (lst.special_rights or []) and "지분" not in special:
            special = [*special, "지분"]
        enriched = dataclasses.replace(
            lst,
            special_rights=special,
            tenant_opposable=badge.opposable,
            assumed_amount=badge.assumed,
            # (감사 2026-07-23 P-01) 인수는 명시됐는데 금액을 못 읽은 상태를 등급 판정까지 전달.
            # 종전엔 assumed=0으로 뭉개져 '부담 없음'과 구분되지 않았다.
            burden_amount_unknown=badge.amount_unknown,
            rights_verified=True,
        )
        stats["matched"] += 1
        if is_hard_gated(enriched):
            stats["gated"] += 1
        out.append(enriched)
    logger.info("권리 배선: %d/%d 반영(빈요지 %d·하드게이트 %d)",
                stats["matched"], stats["total"], stats["empty"], stats["gated"])
    return out, stats


def run(use_live: bool = False, deal_ymd: str | None = None,
        auctions: list[AuctionListing] | None = None,
        trades: list[Trade] | None = None,
        real_trades_lookup=None) -> list[ScoredListing]:
    """채점 파이프라인.

    real_trades_lookup (2026-07-19 T7): callable(listing) -> naver_real_trades 행 리스트 | None.
    네이버 complexNo로 확정된 같은 단지·같은 평형 실거래가 있으면 **이름 매칭을 우회**하고
    그것으로 추정한다(estimate_from_complex_trades — scope 직부여·창 계층화). 확정 comps가
    표본 게이트 미달이면 종전 국토부 이름매칭 경로로 폴백. 감정가 괴리로 무효화됐으면
    (확정 comps에서 괴리 = 강한 적신호) 이름매칭 폴백도 하지 않는다.
    """
    from . import molit_bridge  # noqa: PLC0415
    from .matcher import (
        SCOPE_APPRAISAL_MISMATCH,  # noqa: PLC0415
        estimate_from_complex_trades,  # noqa: PLC0415
    )

    listings = auctions if auctions is not None else load_sample_auctions()

    if trades is not None:
        trade_pool = trades
    elif use_live:
        key = os.environ.get("MOLIT_API_KEY", "").strip()
        if not key or key.startswith("여기에"):
            raise RuntimeError("MOLIT_API_KEY 미설정 — .env에 국토부 API 키를 넣어야 라이브 가능(F10).")
        ymd = deal_ymd or os.environ.get("MOLIT_DEAL_YMD") or _prev_month()
        trade_pool = load_live_trades(listings, key, ymd)
    else:
        trade_pool = load_sample_trades()

    # (감사 2026-07-19 H5) 국토부 병렬 하이브리드 브리지 — 확정쌍의 과거 이력을 지문으로
    # 국토부 aptNm 그룹 대응을 확정하고, 네이버가 아직 못 본 최근 거래만 메모리 보충한다
    # (순차 크롤 신선도 의존 완화). 지문 미달이면 보충 0건 = 종전 동작과 동일.
    bridge = None
    n_bridge_l = n_bridge_rows = 0
    if real_trades_lookup is not None and trade_pool and molit_bridge.enabled():
        bridge = molit_bridge.BridgeIndex(trade_pool)

    scored: list[ScoredListing] = []
    n_naver = n_widened = 0
    for lst in listings:
        m = None
        mult = 1.0
        if real_trades_lookup is not None:
            rows = real_trades_lookup(lst)
            if rows and bridge is not None:
                extra = bridge.topup(lst, rows)
                if extra:
                    rows = list(rows) + extra
                    n_bridge_l += 1
                    n_bridge_rows += len(extra)
            if rows:
                nm, nmult = estimate_from_complex_trades(lst, rows)
                if nm.est is not None:
                    m, mult = nm, nmult
                    n_naver += 1
                    if nmult < 1.0:
                        n_widened += 1
                elif nm.scope == SCOPE_APPRAISAL_MISMATCH:
                    m = nm   # 확정 comps 감정가 괴리 — 이름매칭 폴백 금지(적신호 유지)
        if m is None:
            m = estimate_market(lst, trade_pool)
        s = score_listing(lst, m.est, m.matched, market_scope=m.scope,
                          band_low=m.band_low, band_high=m.band_high,
                          band_basis=m.basis, comps=m.comps,
                          floor_mult=m.floor_mult)
        if mult < 1.0 and s.arb_score is not None:
            s = _apply_window_mult(s, lst, m, mult)
        scored.append(s)
    if n_naver:
        logger.info("네이버 확정 실거래 추정: %d건 (창 확장 %d건)", n_naver, n_widened)
    if n_bridge_l:
        logger.info("국토부 브리지 보충: %d물건 +%d건(지문 확정분만)", n_bridge_l, n_bridge_rows)

    scored.sort(key=lambda s: (s.arb_score is None, -(s.arb_score or 0)))
    return scored


def _apply_window_mult(s: ScoredListing, lst: AuctionListing, m, mult: float) -> ScoredListing:
    """창 확장(24/60개월) 신뢰 하향 — arb·confidence 축소 후 등급 재파생(단일 출처 derive_grade).

    근거(실측 S2 2026-07-19): 옛 거래로 표본이 불어나면 게이트를 통과해 등급이 '상향'되는
    신뢰 부풀림이 생긴다. 확장 창으로 얻은 추정은 명시적으로 하향해 그 부풀림을 상쇄한다.
    """
    from .score import derive_grade, is_hard_gated  # noqa: PLC0415

    arb = round(s.arb_score * mult, 1)
    grade = derive_grade(
        arb, gated=is_hard_gated(lst), rights_verified=lst.rights_verified,
        gap_rate=s.gap_rate, p_low=s.profit_low, market_scope=m.scope,
        band_basis=m.basis, matched_trades=m.matched, apply_scope_sample_gates=True,
        # (버그수정 2026-07-23) 이 경로가 플래그를 안 넘겨 인수-미상 상한이 무력화됐다 —
        # 재채점 실측 55건이 여기로 새어 '관심'으로 남았다. derive_grade 호출부는 **전부**
        # burden_amount_unknown 을 넘겨야 한다(현재 호출부 3곳: 여기·score_listing·_apply_market_price).
        burden_amount_unknown=lst.burden_amount_unknown)
    return dataclasses.replace(s, arb_score=arb, grade=grade,
                               confidence=round(s.confidence * mult, 2))


def _prev_month() -> str:
    import datetime as _dt
    today = _dt.date.today().replace(day=1)
    prev = today - _dt.timedelta(days=1)
    return f"{prev.year}{prev.month:02d}"
