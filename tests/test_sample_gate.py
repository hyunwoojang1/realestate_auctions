"""T5 표본 게이트 — 실기반 표본수(최근성+트림 후) 기준 3단계 게이트.

근거: 데이터_신뢰도_문제의식_및_개선방향.md 10장 권장 기준.
  0~2건 → 밴드 생성 금지·'시세근거 부족' / 3~4건 → 낮은 신뢰(추천 제외+경고) / 5건↑ → 정상.
게이트 기준은 matched_trades(트림 전)가 아니라 밴드 실기반 표본수(basis) — 부풀려진
표본수로 게이트를 통과하지 못하게 한다(T4 평가자 권고).
"""
import pytest

from src import config
from src.matcher import SCOPE_SAME_COMPLEX_SAME_AREA, estimate_market
from src.models import AuctionListing, Trade
from src.score import score_listing


@pytest.fixture(autouse=True)
def _reset_sample():
    saved = config.SAMPLE
    yield
    config.SAMPLE = saved


def _listing(**kw):
    d = dict(
        case_no="2025타경1", court="서울중앙지방법원", address="서울 노원구 상계동",
        lawd_cd="11350", dong="상계동", apt_name="행복아파트", property_type="아파트",
        area_m2=84.0, appraisal_price=900_000_000, min_bid_price=520_000_000,
        fail_count=1, sale_date="2026-08-01", rights_verified=True, occupant_type="공실",
    )
    d.update(kw)
    return AuctionListing(**d)


def _trades(n, base=800_000_000):
    return [Trade(apt_name="행복아파트", area_m2=84.0, price=base + i * 10_000_000,
                  deal_ym="202605", dong="상계동", kind="apt") for i in range(n)]


# ---- 경계 2·3·4·5건 (트림 미발동 구간 주의: 4건↑은 트림으로 basis=n-2) ----

def test_basis_2_no_band():
    """실기반 2건 → 밴드 금지·시세근거 부족(est None)."""
    m = estimate_market(_listing(), _trades(2))
    assert m.est is None and m.band_low is None
    assert m.basis == 2
    assert m.matched == 2


def test_basis_3_band_created():
    """실기반 3건 → 밴드 생성(낮은 신뢰는 추천 층에서 처리)."""
    m = estimate_market(_listing(), _trades(3))
    assert m.est is not None and m.band_low is not None
    assert m.basis == 3


def test_matched_4_trim_makes_basis_2_no_band():
    """매칭 4건이어도 트림 후 실기반 2건 → 밴드 금지(부풀린 표본 차단)."""
    m = estimate_market(_listing(), _trades(4))
    assert m.matched == 4
    assert m.basis == 2      # 트림(상·하단 1건씩) 후
    assert m.est is None     # 게이트가 basis를 본다


def test_matched_5_trim_basis_3_band():
    m = estimate_market(_listing(), _trades(5))
    assert m.matched == 5 and m.basis == 3
    assert m.est is not None


def test_matched_7_trim_basis_5_confident():
    m = estimate_market(_listing(), _trades(7))
    assert m.matched == 7 and m.basis == 5
    assert m.est is not None


# ---- 등급 게이트: basis < confident(5) → 상위 등급 금지 ----

def _score(basis, **kw):
    lst = _listing(min_bid_price=450_000_000, **kw)   # 갭 커서 상위권 점수
    return score_listing(lst, 800_000_000, basis + 2,
                         market_scope=SCOPE_SAME_COMPLEX_SAME_AREA,
                         band_low=780_000_000, band_high=800_000_000, band_basis=basis)


def test_basis_3_caps_grade():
    s = _score(3)
    assert s.grade == "관심"
    assert s.market_sample_basis == 3


def test_basis_4_caps_grade():
    assert _score(4).grade == "관심"


def test_basis_5_keeps_grade():
    assert _score(5).grade in ("차익 유력", "양호")


def test_legacy_none_basis_not_gated():
    s = score_listing(_listing(min_bid_price=450_000_000), 800_000_000, 6,
                      market_scope=SCOPE_SAME_COMPLEX_SAME_AREA,
                      band_low=780_000_000, band_high=800_000_000)
    assert s.grade in ("차익 유력", "양호")


# ---- 추천(digest) 게이트 ----

def test_digest_excludes_low_basis():
    from src.digest import top_listings
    low = _score(3)
    ok = _score(5, case_no="2025타경2")
    top = top_listings([low, ok], n=10)
    assert [s.case_no for s in top] == ["2025타경2"]


def test_digest_legacy_none_passes():
    from src.digest import top_listings
    legacy = score_listing(_listing(min_bid_price=450_000_000), 800_000_000, 6,
                           market_scope=SCOPE_SAME_COMPLEX_SAME_AREA,
                           band_low=780_000_000, band_high=800_000_000)
    assert legacy.market_sample_basis is None
    assert top_listings([legacy], n=10) == [legacy]


# ---- config 외부화 ----

def test_gate_thresholds_configurable():
    config.SAMPLE = config.SampleConfig(band_min_basis=2, band_confident_basis=3)
    m = estimate_market(_listing(), _trades(2))
    assert m.est is not None            # min 2로 낮추면 2건도 밴드 생성
    s = _score(3)
    assert s.grade in ("차익 유력", "양호")   # confident 3이면 3건도 추천 가능


def test_gate_env_override():
    cfg = config.load_sample_config(
        path="__nonexistent__",
        env={"AUCTION_BAND_MIN_BASIS": "4", "AUCTION_BAND_CONFIDENT_BASIS": "7"},
    )
    assert cfg.band_min_basis == 4
    assert cfg.band_confident_basis == 7


def test_gate_monotonic_guard():
    """confident < min이면 min으로 끌어올림(단조 방어)."""
    cfg = config.SampleConfig(band_min_basis=6, band_confident_basis=4)
    assert cfg.band_confident_basis >= cfg.band_min_basis == 6


# ---- 저장: v5 마이그레이션 ----

def test_basis_roundtrip_db():
    from src import store
    conn = store.connect(":memory:")
    s = _score(4)
    store.upsert(conn, [s])
    assert store.load_scored(conn)[0].market_sample_basis == 4


def test_v4_to_v5_migration(tmp_path):
    import sqlite3

    from src import store
    db = str(tmp_path / "v4.db")
    raw = sqlite3.connect(db)
    raw.execute("""
CREATE TABLE scored_listings (
    case_no TEXT NOT NULL,
    apt_name TEXT, address TEXT, property_type TEXT, area_m2 REAL,
    appraisal_price INTEGER, min_bid_price INTEGER, fail_count INTEGER, sale_date TEXT,
    est_market_price INTEGER, matched_trades INTEGER, confidence REAL,
    real_acquisition_cost INTEGER, expected_profit INTEGER, gap_rate REAL,
    gap_score REAL, rights_score REAL, liquidity_score REAL, arb_score REAL, grade TEXT,
    court TEXT NOT NULL DEFAULT '', item_no TEXT NOT NULL DEFAULT '',
    doc_id TEXT NOT NULL DEFAULT '', market_scope TEXT NOT NULL DEFAULT '',
    market_band_low INTEGER, market_band_high INTEGER, profit_low INTEGER, profit_high INTEGER,
    PRIMARY KEY (court, case_no, item_no)
);
""")
    raw.execute("INSERT INTO scored_listings (court, case_no, item_no, apt_name, grade) "
                "VALUES ('법원','2024타경1','1','기존단지','관심')")
    raw.commit()
    raw.close()

    conn = store.connect(db)
    rows = store.fetch_ranked(conn)
    assert len(rows) == 1 and rows[0]["market_sample_basis"] is None
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
