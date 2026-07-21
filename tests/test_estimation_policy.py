"""T2 시세 추정 정책 — v1 아파트·오피스텔 한정, 유형 불명 시 추정 금지.

근거: 데이터_신뢰도_문제의식_및_개선방향.md 5~6장.
- 유형을 모르면 비교군을 만들지 않는다(아파트/빌라/상가 혼입 → 중앙값 무의미).
- 빌라/다세대/상가/토지는 '같은 법정동+면적' 중앙값이 위험 → 시세추정불가.
- 미지원 유형은 '데이터 부족'이 아니라 '정책상 미추정'임을 등급으로 구분한다.
"""
from src.matcher import (
    _kind_ok,
    estimate_market_price,
    is_estimation_supported,
    match_trades,
)
from src.models import AuctionListing, Trade
from src.score import score_listing


def _listing(ptype="아파트", apt_name="행복아파트", area=84.0):
    return AuctionListing(
        case_no="2025타경1", court="서울중앙지방법원", address="서울 노원구 상계동",
        lawd_cd="11350", dong="상계동", apt_name=apt_name, property_type=ptype,
        area_m2=area, appraisal_price=800_000_000, min_bid_price=520_000_000,
        fail_count=1, sale_date="2026-08-01",
    )


def _trade(kind="apt", name="행복아파트", area=84.0, price=810_000_000):
    return Trade(apt_name=name, area_m2=area, price=price, deal_ym="202605",
                 dong="상계동", kind=kind)


APT_TRADES = [_trade(price=p) for p in
              (790_000_000, 800_000_000, 810_000_000, 820_000_000, 830_000_000)]


# ---- 지원 유형 판정 ----

def test_supported_types():
    assert is_estimation_supported("아파트")
    assert is_estimation_supported("오피스텔")


def test_unsupported_types():
    for t in ("다세대", "빌라", "연립", "단독주택", "다가구", "상가",
              "근린생활시설", "토지", "임야", "기타", "", "알수없는유형"):
        assert not is_estimation_supported(t), t


# ---- 유형 필터: 불명·미태깅 통과 제거 ----

def test_kind_ok_rejects_unknown_want():
    """유형 매핑 실패(want=None)면 어떤 거래도 통과하지 않는다(과거: 전부 통과)."""
    assert not _kind_ok(_trade(kind="apt"), None)
    assert not _kind_ok(_trade(kind="rh"), None)


def test_kind_ok_rejects_untagged_trade():
    """kind 미태깅("") 거래도 통과하지 않는다(과거: 통과) — 유형 혼입 방지."""
    assert not _kind_ok(_trade(kind=""), "apt")


def test_kind_ok_exact_match_only():
    assert _kind_ok(_trade(kind="apt"), "apt")
    assert not _kind_ok(_trade(kind="rh"), "apt")


# ---- 시세 추정: 미지원 유형은 comps가 있어도 추정하지 않는다 ----

def test_apartment_estimates_normally():
    est, n = estimate_market_price(_listing("아파트"), APT_TRADES)
    assert est is not None and est > 0
    assert n == 5


def test_villa_never_estimates_even_with_same_dong_trades():
    """빌라: 같은 법정동·같은 면적 rh 거래가 있어도 시세추정불가(문서 4장 fallback 위험)."""
    rh_trades = [_trade(kind="rh", name="", price=p) for p in
                 (290_000_000, 380_000_000, 450_000_000, 500_000_000)]
    est, n = estimate_market_price(_listing("다세대", apt_name="어느다세대", area=59.0),
                                   rh_trades)
    assert est is None
    assert n == 0


def test_unknown_type_never_estimates():
    """유형 불명: 모든 kind 거래가 있어도 비교군을 만들지 않는다(과거: 전부 혼입)."""
    mixed = APT_TRADES + [_trade(kind="rh", name=""), _trade(kind="officetel")]
    est, n = estimate_market_price(_listing("기타"), mixed)
    assert est is None
    assert n == 0


def test_unknown_type_match_trades_empty():
    assert match_trades(_listing("기타"), APT_TRADES) == []


# ---- 등급: 미지원 유형 vs 데이터 부족 구분 ----

def test_unsupported_grade_label():
    """미지원 유형은 '시세추정불가'(데이터 부족)가 아니라 '미지원유형'(정책상 미추정)."""
    s = score_listing(_listing("상가"), None, 0)
    assert s.grade == "미지원유형"
    assert s.est_market_price is None
    assert s.arb_score is None
    assert s.expected_profit is None


def test_apartment_no_comps_grade_stays_na():
    """아파트인데 comps가 없으면 기존 '시세추정불가' 유지(원인 구분)."""
    s = score_listing(_listing("아파트"), None, 0)
    assert s.grade == "시세추정불가"


# ---- 추천 경로에서 자동 배제 ----

def test_digest_excludes_unsupported():
    """다이제스트(추천 TOP N)는 expected_profit 기준이라 미지원 유형이 낄 수 없다."""
    from src.digest import top_listings
    apt = score_listing(_listing("아파트"), 810_000_000, 6)
    shop = score_listing(_listing("상가", apt_name="행복상가"), None, 0)
    top = top_listings([shop, apt], n=10)
    assert all(s.grade != "미지원유형" for s in top)
    assert any(s.case_no == apt.case_no for s in top)


def test_ranking_puts_unsupported_last():
    """정렬(arb_score None → 뒤) — 미지원 유형이 상위 노출되지 않는다.
    (사용자 2026-07-21) 권리 미확인은 점수 None이 되므로, '상위' 전제인 아파트는 권리 확정본으로."""
    import dataclasses
    from src import store
    from src.models import ScoredListing
    conn = store.connect(":memory:")
    apt = score_listing(dataclasses.replace(_listing("아파트"), rights_verified=True), 810_000_000, 6)
    shop = score_listing(_listing("상가", apt_name="행복상가"), None, 0)
    # 같은 사건의 물건 1(아파트)/2(상가) 시나리오 — 복합키로 공존
    shop = ScoredListing(**{**shop.to_row(), "item_no": "2"})
    store.upsert(conn, [shop, apt])
    rows = store.fetch_ranked(conn)
    assert rows[0]["grade"] != "미지원유형"
    assert rows[-1]["grade"] == "미지원유형"
