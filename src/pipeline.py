"""end-to-end 파이프라인: 수집 → 매칭 → 스코어 → 정렬.

PoC 기본은 샘플 fixture. --live + 키가 있으면 국토부 라이브 호출(F10).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .matcher import estimate_market_price
from .models import AuctionListing, ScoredListing, Trade
from .molit_client import (
    fetch_trades_months,
    parse_apt_trades_xml,
    parse_offi_trades_xml,
    parse_rh_trades_xml,
    recent_ymds,
)
from .score import score_listing

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LIVE_MONTHS = 3   # 기본값(무회귀); 실제 사용값은 config.SAMPLE.live_months


def _live_months() -> int:
    """현재 유효 수집 개월수. config.SAMPLE로 튜닝 가능(기본=LIVE_MONTHS)."""
    from . import config as _cfg  # noqa: PLC0415 — 런타임 monkeypatch(SAMPLE 교체) 반영
    return _cfg.SAMPLE.live_months if _cfg.SAMPLE else LIVE_MONTHS


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


def load_courtauction_nationwide(cash_won: int | None = None, appraisal_buffer: float = 3.0,
                                 max_pages_per_sido: int = 10, client=None,
                                 sidos: list[str] | None = None) -> list:
    """전국 17개 시도를 샤딩 수집 → CourtAuctionRecord 리스트(docid 기준 중복제거).

    한 client를 공유해 일일상한·지터·세션이 시도 전체에 누적 적용된다(밴 회피).
    중간에 차단(CourtAuctionBlocked) 시 그때까지 모은 부분결과를 반환하고 중단한다.
    시도별로 페이지를 나눠 '1→700 순차순회' 봇 패턴을 피하고 구간을 작게 유지한다.
    """
    from .courtauction_client import CourtAuctionBlocked, CourtAuctionClient  # noqa: PLC0415
    from .courtauction_fields import SIDO_CODES  # noqa: PLC0415

    c = client or CourtAuctionClient()
    codes = sidos if sidos is not None else list(SIDO_CODES)
    merged: dict[str, object] = {}
    for i, sd in enumerate(codes):
        try:
            recs = collect_courtauction_records(
                cash_won=cash_won, sido_cd=sd, appraisal_buffer=appraisal_buffer,
                max_pages=max_pages_per_sido, client=c, warm=(i == 0))
        except CourtAuctionBlocked as e:
            logger.warning("시도 %s(%s)에서 중단(%s) — 부분수집 %d건 반환",
                           sd, SIDO_CODES.get(sd, ""), e, len(merged))
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


def _extra_to_trade(et) -> Trade:
    """확장 실거래(ExtraTrade: 단독/상업/토지)를 매칭용 Trade로 정규화.

    단지명이 없으므로 apt_name은 비워 두고(법정동+면적 매칭으로만 사용), kind로 유형을 분리한다.
    """
    return Trade(apt_name="", area_m2=et.area_m2, price=et.price,
                 deal_ym=et.deal_ym, dong=et.dong, floor=et.floor, kind=et.kind)


def load_live_trades(listings: list[AuctionListing], api_key: str,
                     deal_ymd: str) -> list[Trade]:
    """법정동코드(LAWD_CD)별 실거래 라이브 수집.

    기본 아파트/연립다세대/오피스텔 + 필요 시 단독/다가구(sh)·상업업무(nrg)·토지(land) 확장(E).
    확장 유형은 해당 물건이 실제로 있는 법정동에만 호출한다(불필요한 API 부하 회피).
    """
    from .matcher import expected_kind  # noqa: PLC0415
    from .molit_extra_client import fetch_extra_trades  # noqa: PLC0415

    extra_by_lawd: dict[str, set[str]] = {}
    for lst in listings:
        k = expected_kind(lst.property_type)
        if k in ("sh", "nrg", "land"):
            extra_by_lawd.setdefault(lst.lawd_cd, set()).add(k)

    trades: list[Trade] = []
    seen: set[str] = set()
    calls = 0
    fails = 0
    for lst in listings:
        if lst.lawd_cd in seen:
            continue
        seen.add(lst.lawd_cd)
        ymds = recent_ymds(deal_ymd, _live_months())
        for kind in ("apt", "rh", "officetel"):
            calls += 1
            try:
                trades.extend(fetch_trades_months(kind, lst.lawd_cd, ymds, api_key))
            except Exception as e:  # noqa: BLE001 — 한 지역/유형 실패가 전체를 막지 않게
                fails += 1
                logger.warning("라이브 호출 실패 kind=%s lawd=%s: %s", kind, lst.lawd_cd, e)
        for kind in sorted(extra_by_lawd.get(lst.lawd_cd, ())):
            for ymd in ymds:
                calls += 1
                try:
                    trades.extend(_extra_to_trade(t)
                                  for t in fetch_extra_trades(kind, lst.lawd_cd, ymd, api_key))
                except Exception as e:  # noqa: BLE001
                    fails += 1
                    logger.warning("확장 라이브 호출 실패 kind=%s lawd=%s ymd=%s: %s",
                                   kind, lst.lawd_cd, ymd, e)
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


def run(use_live: bool = False, deal_ymd: str | None = None,
        auctions: list[AuctionListing] | None = None,
        trades: list[Trade] | None = None) -> list[ScoredListing]:
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

    scored: list[ScoredListing] = []
    for lst in listings:
        est, n = estimate_market_price(lst, trade_pool)
        scored.append(score_listing(lst, est, n))

    scored.sort(key=lambda s: (s.arb_score is None, -(s.arb_score or 0)))
    return scored


def _prev_month() -> str:
    import datetime as _dt
    today = _dt.date.today().replace(day=1)
    prev = today - _dt.timedelta(days=1)
    return f"{prev.year}{prev.month:02d}"
