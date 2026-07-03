"""T3 비교군 scope — 시세가 "어떤 집합"에서 나왔는지 저장·게이트 테스트.

근거: 데이터_신뢰도_문제의식_및_개선방향.md 13장 — 비교군이 틀리면 통계 방식이 아무리
좋아도 결과는 틀린다. v1 추천 인정은 same_complex_same_area만(15장 3단계).
"""
import sqlite3

from src import store
from src.matcher import (
    SCOPE_NO_COMPS,
    SCOPE_SAME_COMPLEX_NEAR_AREA,
    SCOPE_SAME_COMPLEX_SAME_AREA,
    SCOPE_SAME_DONG_FALLBACK,
    SCOPE_UNSUPPORTED,
    estimate_market,
)
from src.models import AuctionListing, Trade
from src.score import score_listing


def _listing(ptype="아파트", apt_name="행복아파트", area=84.0, **kw):
    d = dict(
        case_no="2025타경1", court="서울중앙지방법원", address="서울 노원구 상계동",
        lawd_cd="11350", dong="상계동", apt_name=apt_name, property_type=ptype,
        area_m2=area, appraisal_price=800_000_000, min_bid_price=520_000_000,
        fail_count=1, sale_date="2026-08-01", rights_verified=True, occupant_type="공실",
    )
    d.update(kw)
    return AuctionListing(**d)


def _trade(name="행복아파트", area=84.0, price=810_000_000, dong="상계동", kind="apt"):
    return Trade(apt_name=name, area_m2=area, price=price, deal_ym="202605",
                 dong=dong, kind=kind)


# ---- scope 판정 4분기 + no_comps ----

def test_scope_same_complex_same_area():
    trades = [_trade(area=84.0), _trade(area=84.9), _trade(area=83.5)]  # ±3% 이내
    m = estimate_market(_listing(area=84.0), trades)
    assert m.scope == SCOPE_SAME_COMPLEX_SAME_AREA
    assert m.est is not None and m.matched == 3


def test_scope_same_complex_near_area():
    """같은 단지지만 평형이 다르면(3%초과 10%이내) near_area — 같은 평형과 구분."""
    trades = [_trade(area=90.5), _trade(area=91.0), _trade(area=90.0)]  # 84 대비 +7~8%대
    m = estimate_market(_listing(area=84.0), trades)
    assert m.scope == SCOPE_SAME_COMPLEX_NEAR_AREA
    assert m.est is not None


def test_scope_same_dong_fallback():
    """단지명 불일치 → 같은 법정동 폴백 — 참고치."""
    trades = [_trade(name="다른아파트", area=84.0), _trade(name="딴단지", area=85.0),
              _trade(name="세번째단지", area=83.5)]
    m = estimate_market(_listing(area=84.0), trades)
    assert m.scope == SCOPE_SAME_DONG_FALLBACK
    assert m.est is not None


def test_scope_unsupported():
    m = estimate_market(_listing("상가"), [_trade(kind="nrg")])
    assert m.scope == SCOPE_UNSUPPORTED
    assert m.est is None and m.matched == 0


def test_scope_no_comps():
    m = estimate_market(_listing(dong="없는동", apt_name="없는단지"), [_trade(dong="딴동")])
    assert m.scope == SCOPE_NO_COMPS
    assert m.est is None


def test_same_area_preferred_over_near_area():
    """같은 평형 표본이 있으면 인접 평형을 섞지 않는다(좁고 강한 집합 우선)."""
    trades = [_trade(area=84.0), _trade(area=84.5), _trade(area=91.0)]
    m = estimate_market(_listing(area=84.0), trades)
    assert m.scope == SCOPE_SAME_COMPLEX_SAME_AREA
    assert m.matched == 2  # 91.0(인접 평형)은 제외


# ---- 추천 게이트: same_complex_same_area만 상위 등급 인정 ----

def _high_gap_listing(**kw):
    # 갭이 커서 스코어가 '차익 유력/양호'권에 들어가는 물건
    return _listing(min_bid_price=450_000_000, **kw)


def test_fallback_scope_caps_grade():
    """법정동 폴백 표본으로는 '차익 유력'/'양호'가 나올 수 없다 → '관심' 캡."""
    s = score_listing(_high_gap_listing(), 810_000_000, 6,
                      market_scope=SCOPE_SAME_DONG_FALLBACK)
    assert s.grade == "관심"


def test_near_area_scope_caps_grade():
    s = score_listing(_high_gap_listing(), 810_000_000, 6,
                      market_scope=SCOPE_SAME_COMPLEX_NEAR_AREA)
    assert s.grade == "관심"


def test_same_area_scope_keeps_grade():
    """같은 단지·같은 평형 표본이면 등급 유지(게이트 미발동)."""
    s = score_listing(_high_gap_listing(), 810_000_000, 6,
                      market_scope=SCOPE_SAME_COMPLEX_SAME_AREA)
    assert s.grade in ("차익 유력", "양호")


def test_legacy_empty_scope_not_gated():
    """레거시 호출(scope 미전달)은 기존 동작 유지 — 하위호환."""
    s = score_listing(_high_gap_listing(), 810_000_000, 6)
    assert s.grade in ("차익 유력", "양호")


def test_digest_excludes_fallback_scope():
    """추천 TOP은 same_complex_same_area(+레거시 '')만."""
    from src.digest import top_listings
    good = score_listing(_high_gap_listing(), 810_000_000, 6,
                         market_scope=SCOPE_SAME_COMPLEX_SAME_AREA)
    ref = score_listing(_high_gap_listing(apt_name="폴백단지", case_no="2025타경2"),
                        810_000_000, 6, market_scope=SCOPE_SAME_DONG_FALLBACK)
    top = top_listings([ref, good], n=10)
    assert any(s.case_no == good.case_no for s in top)
    assert all(s.market_scope != SCOPE_SAME_DONG_FALLBACK for s in top)


# ---- 저장: v3 스키마 + v2→v3 마이그레이션 ----

_V2_DDL = """
CREATE TABLE scored_listings (
    case_no TEXT NOT NULL,
    apt_name TEXT, address TEXT, property_type TEXT, area_m2 REAL,
    appraisal_price INTEGER, min_bid_price INTEGER, fail_count INTEGER, sale_date TEXT,
    est_market_price INTEGER, matched_trades INTEGER, confidence REAL,
    real_acquisition_cost INTEGER, expected_profit INTEGER, gap_rate REAL,
    gap_score REAL, rights_score REAL, liquidity_score REAL, arb_score REAL, grade TEXT,
    court TEXT NOT NULL DEFAULT '',
    item_no TEXT NOT NULL DEFAULT '',
    doc_id TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (court, case_no, item_no)
);
"""


def test_market_scope_roundtrip():
    conn = store.connect(":memory:")
    s = score_listing(_listing(), 810_000_000, 6, market_scope=SCOPE_SAME_COMPLEX_SAME_AREA)
    store.upsert(conn, [s])
    loaded = store.load_scored(conn)[0]
    assert loaded.market_scope == SCOPE_SAME_COMPLEX_SAME_AREA


def test_v2_to_v3_migration(tmp_path):
    """v2 DB(복합 PK, market_scope 없음) → connect() 시 컬럼 추가·데이터 보존."""
    db = str(tmp_path / "v2.db")
    raw = sqlite3.connect(db)
    raw.execute(_V2_DDL)
    raw.execute("INSERT INTO scored_listings (court, case_no, item_no, apt_name, grade) "
                "VALUES ('법원','2024타경1','1','기존단지','관심')")
    raw.commit()
    raw.close()

    conn = store.connect(db)
    rows = store.fetch_ranked(conn)
    assert len(rows) == 1
    assert rows[0]["market_scope"] == ""   # 레거시 → 빈값(다음 새로고침이 채움)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION


def test_v1_to_v3_direct_migration(tmp_path):
    """v1(단일 PK) DB도 한 번에 v3까지 — market_scope 포함 재생성."""
    db = str(tmp_path / "v1.db")
    raw = sqlite3.connect(db)
    raw.execute("""
CREATE TABLE scored_listings (
    case_no TEXT PRIMARY KEY,
    apt_name TEXT, address TEXT, property_type TEXT, area_m2 REAL,
    appraisal_price INTEGER, min_bid_price INTEGER, fail_count INTEGER, sale_date TEXT,
    est_market_price INTEGER, matched_trades INTEGER, confidence REAL,
    real_acquisition_cost INTEGER, expected_profit INTEGER, gap_rate REAL,
    gap_score REAL, rights_score REAL, liquidity_score REAL, arb_score REAL, grade TEXT
);
""")
    raw.execute("INSERT INTO scored_listings (case_no, apt_name, grade) VALUES ('2023타경9','옛단지','관심')")
    raw.commit()
    raw.close()

    conn = store.connect(db)
    rows = store.fetch_ranked(conn)
    assert len(rows) == 1 and rows[0]["market_scope"] == ""
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
