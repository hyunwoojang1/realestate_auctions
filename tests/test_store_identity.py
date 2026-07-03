"""T1 식별자 구조 — 복합 PK·마이그레이션·raw 보존 회귀 테스트.

핵심 계약: "한 사건번호 안의 물건 여러 개가 조용히 사라지지 않는다."
(근거: 데이터_신뢰도_문제의식_및_개선방향.md 3장 — 단일 PK면 물건번호 2가 1을 덮음)
"""
import sqlite3

from src import store
from src.models import ScoredListing

# 마이그레이션 테스트용 v1(구) 스키마 — 당시 store.py DDL 사본
_V1_DDL = """
CREATE TABLE scored_listings (
    case_no TEXT PRIMARY KEY,
    apt_name TEXT, address TEXT, property_type TEXT, area_m2 REAL,
    appraisal_price INTEGER, min_bid_price INTEGER, fail_count INTEGER, sale_date TEXT,
    est_market_price INTEGER, matched_trades INTEGER, confidence REAL,
    real_acquisition_cost INTEGER, expected_profit INTEGER, gap_rate REAL,
    gap_score REAL, rights_score REAL, liquidity_score REAL, arb_score REAL, grade TEXT
);
"""


def _sl(case_no="2025타경12345", item_no="", court="서울중앙지방법원", doc_id="",
        apt_name="행복아파트", ptype="아파트"):
    return ScoredListing(
        case_no=case_no, apt_name=apt_name, address="서울 노원구 상계동",
        property_type=ptype, area_m2=84.0, appraisal_price=800_000_000,
        min_bid_price=520_000_000, fail_count=1, sale_date="2026-08-01",
        est_market_price=810_000_000, matched_trades=6, confidence=1.0,
        real_acquisition_cost=550_000_000, expected_profit=260_000_000,
        gap_rate=0.32, gap_score=100.0, rights_score=80.0, liquidity_score=70.0,
        arb_score=90.0, grade="양호", court=court, item_no=item_no, doc_id=doc_id,
    )


def test_same_case_different_items_both_survive():
    """같은 사건번호 + 다른 물건번호 → 2건 모두 남는다(덮어쓰기 금지)."""
    conn = store.connect(":memory:")
    store.upsert(conn, [
        _sl(item_no="1", ptype="아파트"),
        _sl(item_no="2", ptype="상가", apt_name="행복상가"),
    ])
    rows = store.fetch_ranked(conn)
    assert len(rows) == 2
    assert {r["item_no"] for r in rows} == {"1", "2"}


def test_same_composite_key_still_replaces():
    """같은 (court, case_no, item_no)는 갱신(중복 증식 금지)."""
    conn = store.connect(":memory:")
    store.upsert(conn, [_sl(item_no="1")])
    store.upsert(conn, [_sl(item_no="1")])
    assert len(store.fetch_ranked(conn)) == 1


def test_different_court_same_case_no_both_survive():
    """사건번호는 법원 간 중복 가능 — 법원이 다르면 별개 물건."""
    conn = store.connect(":memory:")
    store.upsert(conn, [
        _sl(court="서울중앙지방법원", item_no="1"),
        _sl(court="부산지방법원", item_no="1"),
    ])
    assert len(store.fetch_ranked(conn)) == 2


def test_migration_preserves_v1_rows(tmp_path):
    """구스키마(case_no 단일 PK) DB가 connect()로 자동 이관되고 데이터가 보존된다."""
    db = str(tmp_path / "old.db")
    raw = sqlite3.connect(db)
    raw.execute(_V1_DDL)
    raw.execute(
        "INSERT INTO scored_listings (case_no, apt_name, grade) VALUES (?,?,?)",
        ("2024타경999", "구아파트", "관심"),
    )
    raw.commit()
    raw.close()

    conn = store.connect(db)
    rows = store.fetch_ranked(conn)
    assert len(rows) == 1
    assert rows[0]["case_no"] == "2024타경999"
    assert rows[0]["item_no"] == ""          # v1엔 물건번호가 원래 없었음
    assert rows[0]["court"] == ""
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    assert ver == store.SCHEMA_VERSION
    # 이관 후 복합키 동작: 같은 case_no의 다른 물건 추가가 기존 행을 덮지 않는다
    store.upsert(conn, [_sl(case_no="2024타경999", court="", item_no="2")])
    assert len(store.fetch_ranked(conn)) == 2


def test_migration_idempotent(tmp_path):
    """v2 DB에 connect()를 반복해도 데이터·스키마 불변."""
    db = str(tmp_path / "v2.db")
    conn = store.connect(db)
    store.upsert(conn, [_sl(item_no="1")])
    conn.close()
    conn2 = store.connect(db)
    assert len(store.fetch_ranked(conn2)) == 1


def test_load_scored_roundtrip_uid():
    """load_scored 복원 후 uid(복합 식별자) 정상 동작."""
    conn = store.connect(":memory:")
    store.upsert(conn, [_sl(item_no="1", doc_id="DOC123")])
    s = store.load_scored(conn)[0]
    assert s.uid == "DOC123"                 # doc_id 있으면 원천 고유키 우선
    s2 = _sl(item_no="1", doc_id="")
    assert s2.uid == "서울중앙지방법원|2025타경12345|1"


class _FakeRec:
    def __init__(self, doc_id="", court="법원", case_no="2025타경1", item_no="1", raw=None):
        self.doc_id, self.court, self.case_no, self.item_no = doc_id, court, case_no, item_no
        self.raw = raw or {"srnSaNo": case_no, "maemulSer": item_no}


def test_save_raw_records_roundtrip():
    """원본 raw row가 uid 키로 보존되고 JSON 왕복 가능."""
    conn = store.connect(":memory:")
    n = store.save_raw_records(
        conn,
        [_FakeRec(item_no="1"), _FakeRec(item_no="2"), _FakeRec(doc_id="D1", item_no="3")],
        fetched_at="2026-07-03 13:00:00",
    )
    assert n == 3
    rows = conn.execute("SELECT * FROM raw_listings ORDER BY uid").fetchall()
    assert len(rows) == 3
    import json
    parsed = json.loads(rows[0]["raw_json"])
    assert parsed["srnSaNo"] == "2025타경1"
    assert all(r["fetched_at"] == "2026-07-03 13:00:00" for r in rows)


def test_save_raw_records_same_uid_replaces():
    """같은 uid 재수집은 최신본으로 갱신(증식 금지)."""
    conn = store.connect(":memory:")
    store.save_raw_records(conn, [_FakeRec(item_no="1", raw={"v": 1})])
    store.save_raw_records(conn, [_FakeRec(item_no="1", raw={"v": 2})])
    rows = conn.execute("SELECT raw_json FROM raw_listings").fetchall()
    assert len(rows) == 1
    assert '"v": 2' in rows[0]["raw_json"]
