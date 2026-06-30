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
LIVE_MONTHS = 3   # 라이브 시세는 최근 3개월 실거래를 모아 표본 확대


def load_sample_auctions(path: Path | None = None) -> list[AuctionListing]:
    p = path or (DATA / "sample_auctions.json")
    raw = json.loads(p.read_text(encoding="utf-8"))
    return [AuctionListing(**r) for r in raw]


def load_courtauction_auctions(cash_won: int | None = None, sido_cd: str = "",
                               appraisal_buffer: float = 3.0, max_pages: int = 10,
                               client=None, extra=None) -> list[AuctionListing]:
    """대법원 courtauction 라이브 검색 → AuctionListing 리스트(차익 파이프라인 입력).

    - cash_won 지정: affordable_search(서버 감정가버퍼로 볼륨축소 + 로컬 '최저가≤현금' 정밀필터).
    - 미지정: 일반 search(지역 등 필터만).
    - client/extra는 테스트 주입용. 개인정보는 courtauction_fields.sanitize_row가 제거.
    저빈도·안전장치는 CourtAuctionClient가 담당(지터·회로차단기·일일상한 등).
    """
    from .courtauction_client import CourtAuctionClient, SearchFilter  # noqa: PLC0415
    from .courtauction_fields import to_auction_listing  # noqa: PLC0415

    c = client or CourtAuctionClient()
    flt = extra if extra is not None else SearchFilter(sido_cd=sido_cd)
    if cash_won:
        recs = c.affordable_search(cash_won, appraisal_buffer=appraisal_buffer,
                                   extra=flt, max_pages=max_pages)
    else:
        recs = list(c.search(flt, max_pages=max_pages))
    listings = [to_auction_listing(r) for r in recs]
    logger.info("courtauction 실매물 %d건 → AuctionListing 변환", len(listings))
    return listings


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


def load_live_trades(listings: list[AuctionListing], api_key: str,
                     deal_ymd: str) -> list[Trade]:
    """물건들의 법정동코드(LAWD_CD)별로 아파트+연립다세대 실거래를 라이브 수집."""
    trades: list[Trade] = []
    seen: set[str] = set()
    for lst in listings:
        if lst.lawd_cd in seen:
            continue
        seen.add(lst.lawd_cd)
        ymds = recent_ymds(deal_ymd, LIVE_MONTHS)
        for kind in ("apt", "rh", "officetel"):
            try:
                trades.extend(fetch_trades_months(kind, lst.lawd_cd, ymds, api_key))
            except Exception as e:  # noqa: BLE001 — 한 지역/유형 실패가 전체를 막지 않게
                logger.warning("라이브 호출 실패 kind=%s lawd=%s: %s", kind, lst.lawd_cd, e)
    return trades


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
