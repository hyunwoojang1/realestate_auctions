"""T4 가격 밴드 — 검증 하한가/기준가 2선, 보수 차익(profit_low) 기준 추천.

근거: 데이터_신뢰도_문제의식_및_개선방향.md 7~10장.
- 단일 중앙값은 '정답 가격'처럼 보여 과신 유발 → 두 선(하한가/기준가)으로 말한다.
- P25/P50 분위수는 표본 2~4건에서 통계 흉내 → 트림 후 최저/중앙값 방식 채택.
- 최종 추천 여부는 profit_low(하한가 − 취득원가) 기준.
"""
import sqlite3

from src import store
from src.matcher import SCOPE_SAME_COMPLEX_SAME_AREA, estimate_market
from src.models import AuctionListing, Trade
from src.score import score_listing


def _listing(area=84.0, min_bid=520_000_000, **kw):
    d = dict(
        case_no="2025타경1", court="서울중앙지방법원", address="서울 노원구 상계동",
        lawd_cd="11350", dong="상계동", apt_name="행복아파트", property_type="아파트",
        area_m2=area, appraisal_price=900_000_000, min_bid_price=min_bid,
        fail_count=1, sale_date="2026-08-01", rights_verified=True, occupant_type="공실",
    )
    d.update(kw)
    return AuctionListing(**d)


def _trade(price, area=84.0):
    return Trade(apt_name="행복아파트", area_m2=area, price=price, deal_ym="202605",
                 dong="상계동", kind="apt")


# ---- 밴드 산출 ----

def test_band_low_is_min_high_is_median_after_trim():
    """5건 → 상·하단 1건 트림 → 하한=남은 최저, 기준=남은 중앙값."""
    trades = [_trade(p) for p in
              (700_000_000, 760_000_000, 800_000_000, 840_000_000, 920_000_000)]
    m = estimate_market(_listing(), trades)
    # 트림 후 {760, 800, 840} → 하한 7.6억, 기준 8.0억 (같은 면적이라 평단가 비례)
    assert m.band_low == 760_000_000
    assert m.band_high == 800_000_000 == m.est   # 기준가 = est (호환)
    assert m.matched == 5                        # 표본수는 트림 전


def test_outlier_does_not_drag_band_low():
    """가족거래급 저가 이상치(1건)가 하한가를 끌어내리지 못한다(트림 방어)."""
    trades = [_trade(p) for p in
              (400_000_000, 780_000_000, 800_000_000, 820_000_000, 850_000_000)]
    m = estimate_market(_listing(), trades)
    assert m.band_low == 780_000_000   # 4.0억 이상치는 트림됨


def test_small_sample_band_uses_min():
    """3건(트림 미발동) → 하한 = 표본 최저값. T5에서 표본 게이트로 추가 방어."""
    trades = [_trade(p) for p in (760_000_000, 800_000_000, 830_000_000)]
    m = estimate_market(_listing(), trades)
    assert m.band_low == 760_000_000
    assert m.band_high == 800_000_000


def test_no_estimate_no_band():
    m = estimate_market(_listing(property_type="상가"), [_trade(800_000_000)])
    assert m.band_low is None and m.band_high is None


# ---- profit_low/high 계산·저장 ----

def test_profit_band_computed():
    s = score_listing(_listing(), 800_000_000, 5,
                      market_scope=SCOPE_SAME_COMPLEX_SAME_AREA,
                      band_low=760_000_000, band_high=800_000_000)
    assert s.market_band_low == 760_000_000
    assert s.market_band_high == 800_000_000
    assert s.profit_low == 760_000_000 - s.real_acquisition_cost
    assert s.profit_high == 800_000_000 - s.real_acquisition_cost
    assert s.profit_high == s.expected_profit   # 기준가 차익 = 기존 예상차익(호환)


def test_legacy_call_without_band():
    """레거시 호출(밴드 미전달)은 기존 동작 그대로 — 골든셋 무회귀."""
    s = score_listing(_listing(), 800_000_000, 5)
    assert s.market_band_low is None and s.profit_low is None
    assert s.expected_profit == 800_000_000 - s.real_acquisition_cost


def test_band_does_not_change_score():
    """밴드는 점수(gap/arb)를 바꾸지 않는다 — 점수는 기준가 기준 유지(무회귀)."""
    without = score_listing(_listing(), 800_000_000, 5)
    with_band = score_listing(_listing(), 800_000_000, 5,
                              band_low=760_000_000, band_high=800_000_000)
    assert without.arb_score == with_band.arb_score
    assert without.gap_score == with_band.gap_score


# ---- 보수 기준 추천 판정 ----

def test_conservative_no_profit_gate():
    """기준가로는 차익이 나도 하한가로 안 나면 '차익없음' — 보수 기준 추천 제외."""
    # 취득원가가 하한가(7.6억)와 기준가(8.0억) 사이가 되도록 최저가 설정
    lst = _listing(min_bid=750_000_000)   # 취득세 포함 원가 > 7.6억
    s = score_listing(lst, 800_000_000, 5, band_low=760_000_000, band_high=800_000_000)
    assert s.real_acquisition_cost > 760_000_000   # 전제 확인
    assert s.expected_profit > 0                   # 기준가 차익은 존재
    assert s.profit_low <= 0
    assert s.grade == "차익없음"


def test_conservative_profit_keeps_grade():
    """하한가 기준으로도 차익이 충분하면 등급 유지."""
    s = score_listing(_listing(min_bid=520_000_000), 800_000_000, 5,
                      market_scope=SCOPE_SAME_COMPLEX_SAME_AREA,
                      band_low=760_000_000, band_high=800_000_000)
    assert s.profit_low > 0
    assert s.grade not in ("차익없음",)


def test_digest_ranks_by_profit_low():
    """추천 TOP 정렬이 보수 차익 기준 — 기준가 차익이 커도 하한 차익이 작으면 뒤."""
    from src.digest import top_listings
    # A: 기준차익 2.0억 / 보수차익 0.5억,  B: 기준차익 1.5억 / 보수차익 1.2억
    a = score_listing(_listing(case_no="A", min_bid=560_000_000), 800_000_000, 5,
                      market_scope=SCOPE_SAME_COMPLEX_SAME_AREA,
                      band_low=650_000_000, band_high=800_000_000)
    b = score_listing(_listing(case_no="B", min_bid=560_000_000), 750_000_000, 5,
                      market_scope=SCOPE_SAME_COMPLEX_SAME_AREA,
                      band_low=720_000_000, band_high=750_000_000)
    assert a.profit_low < b.profit_low
    top = top_listings([a, b], n=2)
    assert [s.case_no for s in top] == ["B", "A"]


def test_digest_excludes_nonpositive_conservative_profit():
    """보수 차익 ≤ 0이면 기준가 차익이 있어도 추천 TOP에서 제외(문서 11장)."""
    from src.digest import top_listings
    s = score_listing(_listing(min_bid=750_000_000), 800_000_000, 5,
                      market_scope=SCOPE_SAME_COMPLEX_SAME_AREA,
                      band_low=760_000_000, band_high=800_000_000)
    assert s.profit_low <= 0 < s.expected_profit
    assert top_listings([s], n=10) == []


def test_digest_min_profit_uses_profit_low():
    from src.digest import top_listings
    a = score_listing(_listing(case_no="A", min_bid=560_000_000), 800_000_000, 5,
                      market_scope=SCOPE_SAME_COMPLEX_SAME_AREA,
                      band_low=650_000_000, band_high=800_000_000)
    # 보수 차익이 min_profit 미만이면 기준 차익이 넘어도 제외
    assert a.profit_low < 100_000_000 < a.expected_profit
    assert top_listings([a], min_profit=100_000_000) == []


# ---- 저장: v4 마이그레이션 ----

def test_band_roundtrip_db():
    conn = store.connect(":memory:")
    s = score_listing(_listing(), 800_000_000, 5,
                      market_scope=SCOPE_SAME_COMPLEX_SAME_AREA,
                      band_low=760_000_000, band_high=800_000_000)
    store.upsert(conn, [s])
    loaded = store.load_scored(conn)[0]
    assert loaded.market_band_low == 760_000_000
    assert loaded.profit_low == s.profit_low


def test_v3_to_v4_migration(tmp_path):
    """v3 DB(밴드 컬럼 없음) → connect() 시 컬럼 4개 추가·데이터 보존·레거시 None."""
    db = str(tmp_path / "v3.db")
    raw = sqlite3.connect(db)
    raw.execute("""
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
    market_scope TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (court, case_no, item_no)
);
""")
    raw.execute("INSERT INTO scored_listings (court, case_no, item_no, apt_name, grade) "
                "VALUES ('법원','2024타경1','1','기존단지','관심')")
    raw.commit()
    raw.close()

    conn = store.connect(db)
    rows = store.fetch_ranked(conn)
    assert len(rows) == 1
    assert rows[0]["market_band_low"] is None    # 레거시 → 밴드 없음
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
    loaded = store.load_scored(conn)[0]
    assert loaded.profit_low is None
