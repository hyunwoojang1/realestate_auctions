"""pipeline.load_courtauction_auctions — 실매물→AuctionListing→스코어 연결(네트워크 없음)."""
from __future__ import annotations

import json
from pathlib import Path

from src import pipeline
from src.courtauction_fields import parse_row
from src.models import AuctionListing

DATA = Path(__file__).resolve().parent.parent / "data"


def _records():
    j = json.loads((DATA / "sample_courtauction.json").read_text(encoding="utf-8"))
    return [parse_row(r) for r in j["data"]["dlt_srchResult"]]


class FakeClient:
    """affordable_search/search를 fixture 레코드로 흉내내는 주입용 더블."""
    def __init__(self, records):
        self.records = records
        self.affordable_args = None

    def affordable_search(self, cash_won, appraisal_buffer=3.0, extra=None,
                          max_pages=25, warm=True):
        self.affordable_args = (cash_won, appraisal_buffer, max_pages)
        return [r for r in self.records if 0 < r.min_bid_price <= cash_won]

    def search(self, flt, max_pages=25, warm=True):
        return iter(self.records)


def test_load_courtauction_affordable_returns_listings():
    fake = FakeClient(_records())
    listings = pipeline.load_courtauction_auctions(cash_won=80_000_000, client=fake)
    assert listings
    assert all(isinstance(x, AuctionListing) for x in listings)
    # affordable: 모든 최저가 <= 현금
    assert all(x.min_bid_price <= 80_000_000 for x in listings)
    assert fake.affordable_args[0] == 80_000_000


def test_load_courtauction_search_mode_when_no_cash():
    fake = FakeClient(_records())
    listings = pipeline.load_courtauction_auctions(cash_won=None, client=fake)
    assert len(listings) == 26      # 일반 search = 전체 fixture
    assert listings[0].case_no == "2025타경1352"


def test_courtauction_listings_flow_through_scoring():
    """실매물을 pipeline.run에 넣어 ScoredListing까지 — 샘플 시세로 채점(시세 없으면 추정불가)."""
    fake = FakeClient(_records())
    listings = pipeline.load_courtauction_auctions(cash_won=100_000_000, client=fake)
    scored = pipeline.run(auctions=listings, trades=[])   # 매칭 0 → 전부 시세추정불가지만 흐름 검증
    assert len(scored) == len(listings)
    assert all(s.case_no for s in scored)
    # 시세 0건이면 arb_score None(=시세추정불가)로 안전 처리
    assert all(s.arb_score is None for s in scored)
