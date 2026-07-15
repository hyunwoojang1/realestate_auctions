"""2차 감사 수정 회귀 테스트 (2026-07-15 Ponytail+정밀 리뷰).

A 위험물건 추천(score) · C 캐시 오염/크래시(molit_cache) · F 단지혼입 오판(matcher)
G 지역코드 오매칭(region) · H 리포트 None 크래시(report).
(B run_alerts/digest·D·E store_rest 는 DB/REST 통합 경로라 여기선 순수함수 위주로 커버.)
"""
import pytest

from src import molit_cache, region, report
from src.matcher import _multi_complex
from src.models import AuctionListing, ScoredListing, Trade
from src.score import market_view, score_listing


# ── A) 하드게이트 물건이 시세추정불가 경로에서도 '위험'으로 표기 ──
def _gated() -> AuctionListing:
    # 인수금액/최저입찰 = 1.0 → 하드게이트(임계 초과). 시세추정은 없음(est None).
    return AuctionListing(
        case_no="G1", court="서울중앙지방법원", address="서울 노원구 상계동",
        lawd_cd="11350", dong="상계동",
        apt_name="위험단지", property_type="아파트", area_m2=84.0,
        appraisal_price=900_000_000, min_bid_price=500_000_000,
        fail_count=1, sale_date="2026-08-01", rights_verified=True,
        assumed_amount=500_000_000,
    )


def test_hardgated_estnone_is_wirheom():
    s = score_listing(_gated(), None, 0)          # est None → 조기반환 경로
    assert s.grade == "위험"                       # 게이트가 이 경로에서도 적용


def test_hardgated_estnone_stays_wirheom_with_kb():
    s = score_listing(_gated(), None, 0)
    naver = {"status": "matched_kb", "kb_avg": 800_000_000,
             "kb_low": 780_000_000, "kb_high": 820_000_000}
    v = market_view(s, naver)                      # 서빙 시 KB 재계산
    assert v.grade == "위험"                        # KB 붙어도 위험 유지(추천 안 됨)


# ── KB는 폴백 — 국토부 실거래 우선(감사 2026-07-16, 국토부 오버라이드 319건 교정) ──
def _clean() -> AuctionListing:
    return AuctionListing(
        case_no="C1", court="서울중앙지방법원", address="서울 노원구 상계동",
        lawd_cd="11350", dong="상계동", apt_name="정상단지", property_type="아파트",
        area_m2=84.0, appraisal_price=600_000_000, min_bid_price=400_000_000,
        fail_count=1, sale_date="2026-08-01", rights_verified=True)


def test_kb_does_not_override_molit_estimate():
    """국토부 실거래 est가 있으면 KB가 덮지 않는다(실거래 기반 원칙)."""
    s = score_listing(_clean(), 620_000_000, 6)    # 국토부 est 성공
    naver = {"status": "matched_kb", "kb_avg": 900_000_000,
             "kb_low": 880_000_000, "kb_high": 920_000_000}
    v = market_view(s, naver)
    assert v.market_source == "molit"              # KB로 안 덮임
    assert v.est_market_price == s.est_market_price  # 국토부 시세 유지


def test_kb_fallback_only_when_no_molit_comps():
    """국토부 comps 없을(est None) 때만 KB 폴백 — 신뢰계수 하향(0.75) 반영."""
    from src.config import CONFIG
    s = score_listing(_clean(), None, 0)           # 국토부 실패 → 시세추정불가
    naver = {"status": "matched_kb", "kb_avg": 800_000_000,
             "kb_low": 780_000_000, "kb_high": 820_000_000}
    v = market_view(s, naver)
    assert v.market_source == "kb"
    assert v.est_market_price == 800_000_000
    assert v.confidence == CONFIG.kb_confidence     # 신뢰 하향(1.0 아님)


# ── F) _multi_complex: 한 이름의 두 토큰은 혼입이 아님 ──
def _t(name: str) -> Trade:
    return Trade(apt_name=name, area_m2=84.0, price=1, deal_ym="202606",
                 dong="상계동", floor=5, kind="apt", lawd_cd="11350")


def test_multi_complex_single_name_two_tokens_not_multi():
    comps = [_t("힐스테이트 2차 3단지"), _t("힐스테이트 2차 3단지")]
    assert _multi_complex(comps) is False          # {2,3} 한 단지 고유표기 → 혼입 아님


def test_multi_complex_distinct_complexes_is_multi():
    comps = [_t("갑오마을 3단지"), _t("갑오마을 8단지")]
    assert _multi_complex(comps) is True           # {3} vs {8} → 이웃 단지 혼입


# ── G) region.name_to_code: 모호한 부분일치는 None ──
def test_ambiguous_partial_returns_none():
    jung = [k for k in region.LAWD if "중구" in k.replace(" ", "")]
    if len(jung) < 2:
        pytest.skip("데이터에 중구가 하나뿐 — 모호성 조건 불성립")
    assert region.name_to_code("중구") is None       # 여러 시도의 중구 → 엉뚱한 구 반환 금지


def test_exact_name_returns_code():
    k = next(iter(region.LAWD))
    assert region.name_to_code(k) == region.LAWD[k]  # 정확일치는 그대로


def test_unique_partial_returns_code():
    gn = [v for k, v in region.LAWD.items() if "강남구" in k.replace(" ", "")]
    if len(gn) != 1:
        pytest.skip("강남구가 유일하지 않음")
    assert region.name_to_code("강남구") == gn[0]      # 유일 부분일치는 채택


# ── C) molit_cache: 손상 캐시 미스 강등(#15). (#28 빈결과 미캐시는 설계상 되돌림) ──
def test_nonempty_result_is_cached(tmp_path):
    conn = molit_cache.connect(tmp_path / "m.db")
    calls = {"n": 0}

    def fetch_fn():
        calls["n"] += 1
        return [Trade(apt_name="A", area_m2=84.0, price=5, deal_ym="202506",
                      dong="역삼동", floor=1, kind="apt", lawd_cd="11680")]

    for _ in range(2):
        molit_cache.get_or_fetch(conn, "apt", "11680", "202506", fetch_fn,
                                 cacheable=True, now="2026-07-14 00:00:00", throttle_s=0)
    assert calls["n"] == 1                           # 비어있지 않으면 정상 캐시(1회만 fetch)


def test_corrupt_cache_row_degrades_to_miss(tmp_path):
    conn = molit_cache.connect(tmp_path / "m.db")
    conn.execute(
        "INSERT OR REPLACE INTO molit_trades (kind,lawd_cd,ymd,trades_json,n,fetched_at) "
        "VALUES (?,?,?,?,?,?)",
        ("apt", "11680", "202506", '[{"bogus_field": 1}]', 1, "now"))
    conn.commit()
    assert molit_cache._load(conn, "apt", "11680", "202506") is None  # 크래시 대신 미스


# ── H) report.to_html: area_m2 None 이어도 렌더가 죽지 않음 ──
def test_to_html_handles_none_area(tmp_path):
    s = ScoredListing(
        case_no="A", apt_name="상계주공", address="서울 노원구 상계동",
        property_type="아파트", area_m2=None, appraisal_price=620_000_000,
        min_bid_price=397_000_000, fail_count=2, sale_date="2026-07-01",
        est_market_price=818_000_000, matched_trades=3, confidence=1.0,
        real_acquisition_cost=420_000_000, expected_profit=398_000_000,
        gap_rate=0.5, gap_score=50.0, rights_score=30.0, liquidity_score=20.0,
        arb_score=80.0, grade="차익 유력",
    )
    out = tmp_path / "out.html"
    report.to_html([s], out)                         # 크래시하면 여기서 실패
    assert out.exists() and out.stat().st_size > 0
