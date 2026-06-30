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
        arb_score=arb, grade="확실한 차익" if arb else "시세추정불가",
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
