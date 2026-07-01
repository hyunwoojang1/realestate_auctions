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


class ShardFakeClient:
    """시도별로 다른 record를 주는 더블. block_at 시도에서 CourtAuctionBlocked."""
    def __init__(self, per_sido, block_at=None):
        self.per_sido = per_sido            # {sido_cd: [records]}
        self.block_at = block_at
        self.warmed = []

    def affordable_search(self, cash_won, appraisal_buffer=3.0, extra=None,
                          max_pages=25, warm=True):
        from src.courtauction_client import CourtAuctionBlocked
        sd = extra.sido_cd if extra else ""
        self.warmed.append(warm)
        if sd == self.block_at:
            raise CourtAuctionBlocked(f"차단 시뮬 {sd}")
        return self.per_sido.get(sd, [])


def _mini(docid, case):
    return parse_row({"docid": docid, "srnSaNo": case, "gamevalAmt": "100000000",
                      "minmaePrice": "40000000", "yuchalCnt": "1", "maemulSer": "1"})


def test_nationwide_shards_and_dedupes():
    # 서울 2건, 부산 2건(그중 하나는 서울과 같은 docid=중복) → 합쳐서 3건
    per = {
        "11": [_mini("S1", "2025타경1"), _mini("S2", "2025타경2")],
        "26": [_mini("S2", "2025타경2"), _mini("B1", "2025타경3")],
    }
    fake = ShardFakeClient(per)
    recs = pipeline.load_courtauction_nationwide(cash_won=80_000_000, client=fake, sidos=["11", "26"])
    assert {r.doc_id for r in recs} == {"S1", "S2", "B1"}     # 중복 S2 제거
    # 첫 시도만 warm=True(세션 재사용)
    assert fake.warmed == [True, False]


def test_nationwide_partial_on_block():
    per = {"11": [_mini("S1", "2025타경1")], "26": [_mini("B1", "2025타경3")]}
    fake = ShardFakeClient(per, block_at="26")
    recs = pipeline.load_courtauction_nationwide(cash_won=80_000_000, client=fake,
                                                 sidos=["11", "26", "27"])
    # 11 수집 후 26에서 차단 → 부분(서울만) 반환
    assert {r.doc_id for r in recs} == {"S1"}


def test_from_cache_falls_back_to_sample_fixture(tmp_path):
    """--from-cache: 캐시가 없으면 샘플 fixture로 폴백(네트워크 호출 0)."""
    missing = tmp_path / "no_such_cache.json"
    recs = pipeline.load_courtauction_from_cache(cache_path=missing)
    assert len(recs) == 26                       # 샘플 fixture 전량
    assert recs[0].case_no == "2025타경1352"


def test_from_cache_uses_full_record_cache(tmp_path):
    """full-record 캐시({'records':[raw,...]})가 있으면 그걸 우선 로드."""
    cache = tmp_path / "full_cache.json"
    cache.write_text(json.dumps({"records": [
        {"docid": "C1", "srnSaNo": "2025타경9", "gamevalAmt": "100000000",
         "minmaePrice": "50000000", "yuchalCnt": "1", "maemulSer": "1"},
    ]}, ensure_ascii=False), encoding="utf-8")
    recs = pipeline.load_courtauction_from_cache(cache_path=cache)
    assert len(recs) == 1
    assert recs[0].doc_id == "C1"
    assert recs[0].min_bid_price == 50_000_000


def test_from_cache_snapshot_only_cache_falls_back(tmp_path):
    """스냅샷 전용 캐시(records 키 없음)는 복원 불가 → 샘플 fixture 폴백."""
    snap = tmp_path / "snapshot.json"
    snap.write_text(json.dumps({"C1-1": {"case_no": "x", "min_bid_price": 1}}),
                    encoding="utf-8")
    recs = pipeline.load_courtauction_from_cache(cache_path=snap)
    assert len(recs) == 26                       # 폴백


def test_courtauction_listings_flow_through_scoring():
    """실매물을 pipeline.run에 넣어 ScoredListing까지 — 샘플 시세로 채점(시세 없으면 추정불가)."""
    fake = FakeClient(_records())
    listings = pipeline.load_courtauction_auctions(cash_won=100_000_000, client=fake)
    scored = pipeline.run(auctions=listings, trades=[])   # 매칭 0 → 전부 시세추정불가지만 흐름 검증
    assert len(scored) == len(listings)
    assert all(s.case_no for s in scored)
    # 시세 0건이면 arb_score None(=시세추정불가)로 안전 처리
    assert all(s.arb_score is None for s in scored)
