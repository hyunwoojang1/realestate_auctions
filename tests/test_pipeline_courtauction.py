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


def test_save_full_records_roundtrip_replays_real_data(tmp_path):
    """라이브 수집분을 save_full_records로 저장하면 --from-cache가 그 실데이터를 재생한다.

    (run.py 라이브 경로 → 오프라인 dry-run 경로의 핵심 계약 — fixture 폴백이 아님을 보장.)
    """
    from src import courtauction_cache as cc

    full = tmp_path / "full_cache.json"
    src_recs = _records()                         # fixture 26건을 '수집분'으로 사용
    n = cc.save_full_records(src_recs, full)
    assert n == len(src_recs)

    recs = pipeline.load_courtauction_from_cache(cache_path=full)
    # fixture 폴백(26 우연 일치)이 아니라 저장한 실데이터를 그대로 재생하는지 case_no로 확인.
    assert [r.case_no for r in recs] == [r.case_no for r in src_recs]
    # 저장 파일에 PII 없음(rec.raw는 sanitize된 dict) — 개인정보 키가 파일에 없어야 한다.
    blob = json.loads(full.read_text(encoding="utf-8"))
    assert "records" in blob and blob["records"]


def test_enrich_listings_with_rights_sets_verified():
    """물건상세 텍스트 페처 → 권리 파싱·반영 + rights_verified=True (D 배선)."""
    base = AuctionListing(
        case_no="2025타경1", court="", address="서울 강남구 역삼동", lawd_cd="11680",
        dong="역삼동", apt_name="X", property_type="아파트", area_m2=84.0,
        appraisal_price=1_000_000_000, min_bid_price=700_000_000, fail_count=1,
        sale_date="2026-08-01")
    assert base.rights_verified is False

    def fake_fetch(lst):
        return ("대항력 있는 임차인이 있어 매수인에게 인수됨. 인수 금150,000,000원", "임차인 점유", "")

    out = pipeline.enrich_listings_with_rights([base], fake_fetch)
    assert len(out) == 1
    e = out[0]
    assert e.rights_verified is True
    assert e.tenant_opposable is True
    assert e.assumed_amount == 150_000_000
    assert e.occupant_type == "임차인"


def test_enrich_keeps_unverified_when_no_text():
    """물건상세 미수집(빈 텍스트)이면 권리미확인 유지(rights_verified=False)."""
    base = AuctionListing(
        case_no="2025타경2", court="", address="서울", lawd_cd="11680", dong="역삼동",
        apt_name="Y", property_type="아파트", area_m2=84.0, appraisal_price=1,
        min_bid_price=1, fail_count=0, sale_date="2026-08-01")
    out = pipeline.enrich_listings_with_rights([base], lambda lst: ("", "", ""))
    assert out[0].rights_verified is False


# --- 감사 2026-07-15: 권리 배선(apply_rights_from_rows) — batch가 권리를 보게 한다 ---------


def _listing(case_no: str = "2025타경9", court: str = "테스트지원", item_no: str = "1",
             min_bid: int = 97_300_000) -> AuctionListing:
    return AuctionListing(
        case_no=case_no, court=court, item_no=item_no, address="강원 강릉시", lawd_cd="42150",
        dong="교동", apt_name="테스트오피스텔", property_type="오피스텔", area_m2=30.0,
        appraisal_price=190_000_000, min_bid_price=min_bid, fail_count=3,
        sale_date="2026-08-01")


def _rights_row(court: str = "테스트지원", case_no: str = "2025타경9", item_no: str = "1",
                surviving: str = "") -> dict:
    return {
        "court": court, "case_no": case_no, "item_no": item_no,
        "surviving_rights": surviving, "senior_lien": "2025.12.15.경매개시결정",
        "lien_note": "", "remark": "", "claim_amt": 100_000_000, "demand_end": "2026-03-01",
        "spec_write_ymd": "2026-07-01", "court_dept": "경매1계", "schedule": "[]",
        "appraisal_notes": "[]", "fetched_at": "2026-07-15",
    }


def test_apply_rights_from_rows_gates_assumed_deposit():
    """상세 요지의 인수금액이 채점 전에 반영돼 하드게이트가 발동해야 한다.

    (감사 2026-07-15) 이 배선이 없어 batch 8,245건 중 79.7%가 rights_score=85.0 상수였다.
    실측 사례: 강릉지원 2025타경30912 — 최저가 9,730만원 / 인수 보증금 1.1억 → 인수비율 113%.
    배선 후 실측 rights_score 85.0 → 0.0 확인.
    """
    from src.score import is_hard_gated, rights_score

    surviving = ("매수인에게 대항할 수 있는 을구 순위 3번 임차권등기(2024. 3. 26.등기) 있음"
                 "(임대차보증금 11000만 원, 전입일 2019. 1. 17.). 배당에서 보증금이 전액"
                 " 변제되지 아니하면 잔액을 매수인이 인수함")
    out, stats = pipeline.apply_rights_from_rows(
        [_listing()], [_rights_row(surviving=surviving)])

    e = out[0]
    assert e.rights_verified is True
    assert e.tenant_opposable is True
    assert e.assumed_amount == 110_000_000
    assert is_hard_gated(e) is True
    assert rights_score(e) == 0.0
    assert stats == {"total": 1, "matched": 1, "empty": 0, "gated": 1}


def test_apply_rights_from_rows_leaves_uncrawled_unverified():
    """미크롤 물건은 손대지 않는다 — '인수 없음'이 아니라 '권리미확인'으로 남아야 한다."""
    out, stats = pipeline.apply_rights_from_rows([_listing()], [])
    assert out[0].rights_verified is False
    assert out[0].assumed_amount == 0
    assert stats["matched"] == 0


def test_apply_rights_from_rows_requires_exact_item_no():
    """물건번호가 다르면 형제 물건의 명세서를 끌어다 쓰지 않는다(감사 idx16 회귀 방지)."""
    out, _ = pipeline.apply_rights_from_rows(
        [_listing(item_no="1")], [_rights_row(item_no="2", surviving="매수인이 인수함 금 5억원")])
    assert out[0].rights_verified is False
    assert out[0].assumed_amount == 0


def test_apply_rights_from_rows_preserves_maejibun_share_label():
    """(2026-07-24 게이트 FAIL 3건 회귀 방지) maejibun 검출 '지분'은 권리 병합에 지워지면 안 된다.

    실사고: 죽전자이2차 — 리스트 maejibun('갑구 2번 2분의 1 지분')으로 '지분' 라벨을 받았는데,
    권리 재크롤 후 apply_rights_from_rows 가 요지 기반 badge.special 로 **대체**하면서 라벨이
    소실 → 온전가 시세로 '차익 유력 6.5억' 부활(재채점 실측). 요지에 지분 신호가 없어도
    리스트 원천 라벨은 보존해야 한다.
    """
    import dataclasses as _dc
    lst = _dc.replace(_listing(), special_rights=["지분"])
    surviving = "등기된 부동산에 관한 권리는 매각으로 모두 말소됨"   # 요지에 지분 신호 없음
    out, stats = pipeline.apply_rights_from_rows([lst], [_rights_row(surviving=surviving)])
    assert stats["matched"] == 1
    assert "지분" in out[0].special_rights
    assert out[0].rights_verified is True


def test_courtauction_listings_flow_through_scoring():
    """실매물을 pipeline.run에 넣어 ScoredListing까지 — 샘플 시세로 채점(시세 없으면 추정불가)."""
    fake = FakeClient(_records())
    listings = pipeline.load_courtauction_auctions(cash_won=100_000_000, client=fake)
    scored = pipeline.run(auctions=listings, trades=[])   # 매칭 0 → 전부 시세추정불가지만 흐름 검증
    assert len(scored) == len(listings)
    assert all(s.case_no for s in scored)
    # 시세 0건이면 arb_score None(=시세추정불가)로 안전 처리
    assert all(s.arb_score is None for s in scored)
