"""낙찰 보조 스크립트가 **아는 값을 지우지 않는지** — 2026-07-31 실사고 회귀 가드.

## 무엇이 있었나
낙찰 기록을 보강하려고 두 스크립트를 돌렸는데 둘 다 **기존 값을 덮어썼다**.

1. `deploy/backfill_sold_listings` 는 raw_listings 로 행을 만들어 `INSERT OR REPLACE` 한다.
   raw 에는 시세가 없으니 시세 컬럼이 NULL 인데, 이미 sold 에 있던 키까지 그걸로 갈아엎어
   **낙찰 중 시세 보유 398 → 356**(42건 소실)이 됐다.
2. `deploy/rescore_sold` 는 '백필로 되살린 행이 전부 비어 있어서' 만든 **채우기** 도구인데
   값이 있는 행까지 재계산으로 덮었다. 낙찰 물건은 활성에서 빠지는 순간
   `prune_orphan_rights` 로 **권리 행이 사라지므로** 재계산은 원리상 스냅샷보다 열등하다
   (권리 없이 채점 → '권리미확인'). 실측 **점수 보유 201 → 4**, 복구 경로 없음.

두 사고의 뿌리는 같다 — "모름을 지어내지 않는다"는 지켰지만 그 짝인
**"아는 것을 지우지 않는다"** 가 없었다. 이 테스트가 그 짝을 고정한다.
"""
from src import store

COLS_TO_KEEP = ("est_market_price", "market_band_low", "profit_low",
                "expected_profit", "arb_score", "grade",
                "market_scope", "matched_trades", "confidence")


def _rich_row(case_no="2024타경1"):
    """C2 diff 가 물건이 사라지던 순간 떠 둔 '온전한' 스냅샷 — 권리·시세가 살아 있을 때 값."""
    return {
        "court": "서울중앙지방법원", "case_no": case_no, "item_no": "1",
        "apt_name": "스냅단지", "address": "서울 강남구 1-2", "property_type": "아파트",
        "area_m2": 84.0, "appraisal_price": 500_000_000, "min_bid_price": 256_000_000,
        "fail_count": 2, "sale_date": "2026-07-20",
        "est_market_price": 480_000_000, "market_band_low": 470_000_000,
        "profit_low": 150_000_000, "expected_profit": 170_000_000,
        "arb_score": 88.0, "grade": "차익 유력",
        "market_scope": "same_complex_same_area", "matched_trades": 12, "confidence": 1.0,
        "sold_price": None, "sold_evidence": "disappeared", "snapshot_at": "2026-07-31 11:01",
    }


def _poor_row(case_no="2024타경1"):
    """raw_listings 로만 복원한 행 — 신원·가격은 알지만 시세·점수는 모른다."""
    r = _rich_row(case_no)
    for c in COLS_TO_KEEP:
        r[c] = None if c != "grade" else ""
    r["sold_price"] = 300_000_000
    r["sold_evidence"] = "maeAmt"
    return r


def test_upsert_sold_replaces_wholesale(tmp_path):
    """전제 확인 — store.upsert_sold 는 INSERT OR REPLACE 라 **부분 갱신이 아니다**.

    이 사실이 위 두 사고의 물리적 원인이다. 호출부가 병합 책임을 진다는 걸 명시해 둔다.
    """
    conn = store.connect(str(tmp_path / "a.db"))
    store.upsert_sold(conn, [_rich_row()])
    store.upsert_sold(conn, [_poor_row()])
    row = conn.execute("SELECT * FROM sold_listings").fetchone()
    assert row["est_market_price"] is None      # 덮어써진다 — 그래서 호출부가 막아야 한다
    assert row["arb_score"] is None


def test_backfill_carries_existing_market_columns(tmp_path, monkeypatch):
    """백필은 이미 있던 키의 시세·점수를 **물려받아야** 한다."""
    from deploy import backfill_sold_listings as bf

    db = tmp_path / "b.db"
    conn = store.connect(str(db))
    store.upsert_sold(conn, [_rich_row()])
    # raw 원본 1건 + 활성 없음 → 백필 대상이 되게 만든다
    conn.execute(
        "INSERT INTO raw_listings (uid, doc_id, court, case_no, item_no, raw_json, fetched_at)"
        " VALUES (?,?,?,?,?,?,?)",
        ("u1", "d1", "서울중앙지방법원", "2024타경1", "1",
         '{"maeAmt": "300000000"}', "2026-07-31"))
    conn.commit()

    # parse_row/to_auction_listing 은 실제 스키마를 요구하므로 최소 복원본으로 대체
    class _L:
        apt_name, address, property_type = "스냅단지", "서울 강남구 1-2", "아파트"
        area_m2, appraisal_price, min_bid_price = 84.0, 500_000_000, 256_000_000
        fail_count, sale_date = 2, "2026-07-20"

    monkeypatch.setattr(bf, "parse_row", lambda d: d)
    monkeypatch.setattr(bf, "to_auction_listing", lambda d: _L())

    rows = bf.build_rows(conn)
    assert len(rows) == 1
    got = rows[0]
    assert got["sold_price"] == 300_000_000          # 백필이 가져온 새 사실은 반영
    assert got["est_market_price"] == 480_000_000    # 알던 값은 살아남는다
    assert got["arb_score"] == 88.0
    assert got["grade"] == "차익 유력"


def test_backfill_leaves_new_keys_empty(tmp_path, monkeypatch):
    """반대편 — sold 에 없던 **신규** 키는 시세를 지어내지 않고 비워 둔다."""
    from deploy import backfill_sold_listings as bf

    conn = store.connect(str(tmp_path / "c.db"))
    conn.execute(
        "INSERT INTO raw_listings (uid, doc_id, court, case_no, item_no, raw_json, fetched_at)"
        " VALUES (?,?,?,?,?,?,?)",
        ("u9", "d9", "서울중앙지방법원", "2024타경999", "1",
         '{"maeAmt": "300000000"}', "2026-07-31"))
    conn.commit()

    class _L:
        apt_name, address, property_type = "신규", "서울", "아파트"
        area_m2, appraisal_price, min_bid_price = 84.0, 5, 2
        fail_count, sale_date = 0, "2026-07-20"

    monkeypatch.setattr(bf, "parse_row", lambda d: d)
    monkeypatch.setattr(bf, "to_auction_listing", lambda d: _L())

    rows = bf.build_rows(conn)
    assert len(rows) == 1
    assert rows[0]["est_market_price"] is None
    assert rows[0]["arb_score"] is None
