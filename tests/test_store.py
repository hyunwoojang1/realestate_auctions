"""SQLite 저장/복원 테스트 — 웹이 DB에서 라이브 결과를 읽는 경로 검증."""
from src import store
from src.models import ScoredListing


def _scored(case_no: str, arb: float | None) -> ScoredListing:
    return ScoredListing(
        case_no=case_no, apt_name="상계주공", address="서울 노원구 상계동",
        property_type="아파트", area_m2=84.9, appraisal_price=620_000_000,
        min_bid_price=397_000_000, fail_count=2, sale_date="2026-07-01",
        est_market_price=818_000_000, matched_trades=3, confidence=1.0,
        real_acquisition_cost=420_000_000, expected_profit=398_000_000,
        gap_rate=0.5, gap_score=50.0, rights_score=30.0, liquidity_score=20.0,
        arb_score=arb, grade="차익 유력" if arb else "시세추정불가",
    )


def test_upsert_and_load_roundtrip():
    conn = store.connect(":memory:")
    store.upsert(conn, [_scored("A", 90.0), _scored("B", 40.0)])
    loaded = store.load_scored(conn)
    assert len(loaded) == 2
    assert all(isinstance(s, ScoredListing) for s in loaded)
    assert loaded[0].case_no == "A" and loaded[0].arb_score == 90.0  # 차익순 내림차순
    assert loaded[1].case_no == "B"


def test_load_preserves_null_score_last():
    conn = store.connect(":memory:")
    store.upsert(conn, [_scored("N", None), _scored("H", 88.0)])
    loaded = store.load_scored(conn)
    assert loaded[0].case_no == "H"      # 점수 있는 것 먼저
    assert loaded[-1].arb_score is None  # 시세추정불가 맨 뒤


def test_has_rows():
    conn = store.connect(":memory:")
    assert store.has_rows(conn) is False
    store.upsert(conn, [_scored("A", 90.0)])
    assert store.has_rows(conn) is True


def _rights_row(case_no: str) -> dict:
    return {"court": "", "case_no": case_no, "item_no": "", "surviving_rights": "",
            "senior_lien": "", "lien_note": "", "remark": "", "claim_amt": None,
            "demand_end": "", "spec_write_ymd": "2026-06-01", "court_dept": "",
            "schedule": "[]", "appraisal_notes": "[]", "fetched_at": ""}


def test_prune_orphan_rights_removes_only_unmatched():
    conn = store.connect(":memory:")
    store.upsert(conn, [_scored("A", 90.0)])              # scored: case_no 'A'
    store.save_rights(conn, [_rights_row("A"), _rights_row("ORPHAN")])
    deleted = store.prune_orphan_rights(conn)
    assert deleted == 1                                   # ORPHAN 만 삭제
    assert store.load_rights(conn, "", "A") is not None   # 매칭 권리 보존
    assert store.load_rights(conn, "", "ORPHAN") is None  # 고아 제거


def test_photos_roundtrip_and_replace():
    conn = store.connect(":memory:")
    # base64 저장 → load_photos는 렌더용 data URI로 감싸 반환(듀얼모드)
    store.save_photos(conn, "", "A", "", ["aaa", "bbb", "ccc"])
    assert store.load_photos(conn, "", "A") == [
        "data:image/jpeg;base64,aaa", "data:image/jpeg;base64,bbb", "data:image/jpeg;base64,ccc"]
    # Storage URL 저장 → URL 그대로 반환(전량 교체)
    store.save_photo_urls(conn, "", "A", "", ["http://x/1.jpg", "http://x/2.jpg"])
    assert store.load_photos(conn, "", "A") == ["http://x/1.jpg", "http://x/2.jpg"]
    store.save_photos(conn, "", "A", "", ["xxx"])         # 재저장 = 전량 교체(stale 방지)
    assert store.load_photos(conn, "", "A") == ["data:image/jpeg;base64,xxx"]
    assert store.load_photos(conn, "", "MISSING") == []


def test_estimable_keys_only_priced():
    conn = store.connect(":memory:")
    store.upsert(conn, [_scored("PRICED", 90.0), _scored("NOPRICE", None)])
    # _scored: arb None → est_market_price 설정됨(818M)이라 둘 다 est 있음 → est 없는 케이스 구성
    conn.execute("UPDATE scored_listings SET est_market_price=NULL WHERE case_no='NOPRICE'")
    conn.commit()
    keys = store.estimable_keys(conn)
    assert ("", "PRICED", "") in keys
    assert ("", "NOPRICE", "") not in keys


def test_market_comps_roundtrip():
    """시간축 차트용 개별 실거래 comps가 JSON으로 저장·복원된다(v6)."""
    conn = store.connect(":memory:")
    s = _scored("C", 80.0)
    s.market_comps = [["202605", 510_000_000], ["202410", 560_000_000]]
    store.upsert(conn, [s])
    loaded = store.load_scored(conn)[0]
    assert loaded.market_comps == [["202605", 510_000_000], ["202410", 560_000_000]]


def test_market_comps_default_empty_when_absent():
    """comps 미설정 물건은 빈 리스트로 복원(레거시·무점 안전)."""
    conn = store.connect(":memory:")
    store.upsert(conn, [_scored("D", 70.0)])
    assert store.load_scored(conn)[0].market_comps == []


def test_migration_v5_to_v6_adds_market_comps():
    """구스키마(market_comps 없는 v5) DB를 열면 컬럼이 자동 추가되고 로드가 깨지지 않는다."""
    import sqlite3
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row  # connect()와 동일 — _migrate가 r["name"]을 읽는다
    # v5 상당 최소 테이블(market_comps 없음)로 위조
    raw.execute(
        "CREATE TABLE scored_listings (case_no TEXT NOT NULL, apt_name TEXT, address TEXT, "
        "property_type TEXT, area_m2 REAL, appraisal_price INTEGER, min_bid_price INTEGER, "
        "fail_count INTEGER, sale_date TEXT, est_market_price INTEGER, matched_trades INTEGER, "
        "confidence REAL, real_acquisition_cost INTEGER, expected_profit INTEGER, gap_rate REAL, "
        "gap_score REAL, rights_score REAL, liquidity_score REAL, arb_score REAL, grade TEXT, "
        "court TEXT NOT NULL DEFAULT '', item_no TEXT NOT NULL DEFAULT '', "
        "doc_id TEXT NOT NULL DEFAULT '', market_scope TEXT NOT NULL DEFAULT '', "
        "market_band_low INTEGER, market_band_high INTEGER, profit_low INTEGER, "
        "profit_high INTEGER, market_sample_basis INTEGER, "
        "PRIMARY KEY (court, case_no, item_no))"
    )
    raw.execute(
        "INSERT INTO scored_listings (case_no, apt_name, arb_score, grade) VALUES ('E','X',50.0,'양호')"
    )
    raw.commit()
    cols = {r[1] for r in raw.execute("PRAGMA table_info(scored_listings)")}
    assert "market_comps" not in cols  # 위조 v5엔 없음
    store._migrate(raw)  # 마이그레이션 실행
    cols2 = {r[1] for r in raw.execute("PRAGMA table_info(scored_listings)")}
    assert "market_comps" in cols2  # v6 컬럼 추가됨
    raw.close()


def test_replace_all_purges_absent_rows():
    """전량 교체: 이전 크롤에만 있던 매물(팔림/취하)은 제거된다(만료 매물 추천 방지)."""
    conn = store.connect(":memory:")
    store.upsert(conn, [_scored("OLD", 90.0), _scored("KEEP", 80.0)])
    n = store.replace_all(conn, [_scored("KEEP", 80.0), _scored("NEW", 70.0)])
    assert n == 2
    cases = {s.case_no for s in store.load_scored(conn)}
    assert cases == {"KEEP", "NEW"}   # OLD는 사라짐


def test_upsert_keeps_existing_rows():
    """병합 적재: 기존 행 유지(부분 크롤이 다른 지역을 지우지 않게)."""
    conn = store.connect(":memory:")
    store.upsert(conn, [_scored("A", 90.0)])
    store.upsert(conn, [_scored("B", 80.0)])
    cases = {s.case_no for s in store.load_scored(conn)}
    assert cases == {"A", "B"}
