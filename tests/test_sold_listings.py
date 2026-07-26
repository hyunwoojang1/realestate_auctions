"""낙찰(종결) 보존·표시 — sold_listings 계약 (Phase C 2026-07-27).

핵심 정직성 계약: 실낙찰가는 재매각(maeAmt)에만 존재 — 미공개 물건의 sold_price 는 NULL 이며
0원·회차 최저가(last_sold_floor)·추정가로 채우지 않는다.
"""
from src import store


def _sold_row(case_no: str, price=None, evidence="disappeared", sale_date="2026-07-20"):
    return {
        "court": "서울중앙지방법원", "case_no": case_no, "item_no": "1",
        "apt_name": "낙찰단지", "address": "서울 강남구 1-2", "property_type": "아파트",
        "area_m2": 84.0, "appraisal_price": 500_000_000, "min_bid_price": 256_000_000,
        "fail_count": 2, "sale_date": sale_date, "est_market_price": 480_000_000,
        "market_band_low": 470_000_000, "profit_low": 150_000_000,
        "expected_profit": 170_000_000, "arb_score": 88.0, "grade": "차익 유력",
        "sold_price": price, "sold_evidence": evidence, "snapshot_at": "2026-07-27 14:00",
    }


def test_upsert_and_load_sold_ordering(tmp_path):
    """실낙찰가 보유가 미공개보다 먼저, 그 안에서 매각기일 최신순."""
    conn = store.connect(str(tmp_path / "s.db"))
    store.upsert_sold(conn, [
        _sold_row("2025타경1", price=None, sale_date="2026-07-25"),
        _sold_row("2025타경2", price=310_000_000, evidence="maeAmt", sale_date="2026-07-10"),
        _sold_row("2025타경3", price=220_000_000, evidence="maeAmt", sale_date="2026-07-15"),
    ])
    rows = store.load_sold(conn)
    assert [r["case_no"] for r in rows] == ["2025타경3", "2025타경2", "2025타경1"]


def test_upsert_sold_idempotent(tmp_path):
    conn = store.connect(str(tmp_path / "s.db"))
    store.upsert_sold(conn, [_sold_row("2025타경1")])
    store.upsert_sold(conn, [_sold_row("2025타경1", price=300_000_000, evidence="maeAmt")])
    rows = store.load_sold(conn)
    assert len(rows) == 1
    assert rows[0]["sold_price"] == 300_000_000       # 재적재가 갱신(멱등 병합)


def test_load_sold_one_exact_key(tmp_path):
    conn = store.connect(str(tmp_path / "s.db"))
    store.upsert_sold(conn, [_sold_row("2025타경9")])
    assert store.load_sold_one(conn, "서울중앙지방법원", "2025타경9", "1") is not None
    assert store.load_sold_one(conn, "다른법원", "2025타경9", "1") is None   # 키 폴백 금지


def _scored_obj(case_no: str, sale_date: str):
    from src.models import ScoredListing
    return ScoredListing(
        case_no=case_no, apt_name="교체단지", address="서울 강남구 1-2",
        property_type="아파트", area_m2=84.0, appraisal_price=500_000_000,
        min_bid_price=256_000_000, fail_count=2, sale_date=sale_date,
        est_market_price=480_000_000, matched_trades=3, confidence=1.0,
        real_acquisition_cost=270_000_000, expected_profit=170_000_000, gap_rate=0.4,
        gap_score=90.0, rights_score=100.0, liquidity_score=80.0, arb_score=88.0,
        grade="차익 유력", court="서울중앙지방법원", item_no="1",
    )


def test_collect_sold_snapshot_diff_rules(tmp_path):
    """(C2) diff 규칙 — 기일 지난 소멸=보존, 기일 남은 소멸=제외(취하 가능), 잔존=제외.
    재매각 이력('sold' 키)이 있으면 실낙찰가+evidence=maeAmt, 없으면 NULL+disappeared."""
    import json

    from run import _collect_sold_snapshot
    conn = store.connect(str(tmp_path / "c2.db"))
    past1 = _scored_obj("2025타경1", "2026-07-20")     # 기일 지남·소멸 → 보존(미공개)
    past2 = _scored_obj("2025타경2", "2026-07-18")     # 기일 지남·소멸·sold 이력 → 보존(실낙찰가)
    future = _scored_obj("2025타경3", "2099-01-01")    # 기일 남음·소멸 → 제외
    stay = _scored_obj("2025타경4", "2026-07-19")      # 새 스냅샷에도 있음 → 제외
    store.replace_all(conn, [past1, past2, future, stay])
    store.save_rights(conn, [{
        "court": past2.court, "case_no": past2.case_no, "item_no": past2.item_no,
        "surviving_rights": "", "senior_lien": "", "lien_note": "", "remark": "",
        "claim_amt": None, "demand_end": "", "spec_write_ymd": "", "court_dept": "",
        "schedule": json.dumps([
            {"ymd": "2026-05-01", "kind": "매각기일", "result": "매각",
             "price": 200_000_000, "sold": 231_000_000},
            {"ymd": "2026-07-18", "kind": "매각기일", "result": "", "price": 179_000_000},
        ], ensure_ascii=False),
        "appraisal_notes": "[]", "fetched_at": "x",
    }])
    rows = _collect_sold_snapshot(conn, [stay])
    got = {r["case_no"]: r for r in rows}
    assert set(got) == {"2025타경1", "2025타경2"}
    assert got["2025타경1"]["sold_price"] is None
    assert got["2025타경1"]["sold_evidence"] == "disappeared"
    assert got["2025타경2"]["sold_price"] == 231_000_000     # 'sold'(실낙찰) — price(최저가) 아님
    assert got["2025타경2"]["sold_evidence"] == "maeAmt"


def test_prune_keeps_children_of_sold(tmp_path):
    """(C2) 낙찰 보존 물건의 자식(사진 등)은 prune 에서 살아남는다 — 낙찰 상세의 아카이브."""
    conn = store.connect(str(tmp_path / "c2p.db"))
    keep = _scored_obj("2025타경대기", "2026-08-01")
    store.replace_all(conn, [keep])
    store.upsert_sold(conn, [_sold_row("2025타경낙찰")])
    store.save_photos(conn, "서울중앙지방법원", "2025타경낙찰", "1", ["YQ=="], fetched_at="x")
    store.save_photos(conn, "", "ORPH", "", ["Yg=="], fetched_at="x")
    assert store.prune_orphan_photos(conn) == 1               # 진짜 고아만 삭제
    assert len(store.load_photos(conn, "서울중앙지방법원", "2025타경낙찰", "1")) == 1


def test_unknown_price_stays_null(tmp_path):
    """미공개 낙찰가는 NULL 그대로 — 0원·최저가 대입 금지(정직성)."""
    conn = store.connect(str(tmp_path / "s.db"))
    store.upsert_sold(conn, [_sold_row("2025타경1", price=None)])
    row = store.load_sold_one(conn, "서울중앙지방법원", "2025타경1", "1")
    assert row["sold_price"] is None
    assert row["sold_price"] != row["min_bid_price"]
