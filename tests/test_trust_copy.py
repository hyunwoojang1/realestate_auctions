"""T7 UI 메시지·포지셔닝 — 보수 차익 중심 표기·근거 병기·단정 표현 제거.

근거: 데이터_신뢰도_문제의식_및_개선방향.md 6장(포지셔닝)·15장 7단계(UI 메시지).
나쁜 메시지: "예상 차익 2.3억" / 좋은 메시지: "보수 가격 기준 차익 1.4억 · 실거래 6건 기준
· 권리 확인 완료 전까지 최종 판단 금지".
"""
from pathlib import Path

from src.matcher import SCOPE_SAME_COMPLEX_SAME_AREA
from src.models import AuctionListing
from src.query import decision_profit, sort_items
from src.score import score_listing

ROOT = Path(__file__).resolve().parent.parent


def _listing(case_no="2025타경1", min_bid=520_000_000, **kw):
    d = dict(
        case_no=case_no, court="서울중앙지방법원", address="서울 노원구 상계동",
        lawd_cd="11350", dong="상계동", apt_name="행복아파트", property_type="아파트",
        area_m2=84.0, appraisal_price=900_000_000, min_bid_price=min_bid,
        fail_count=1, sale_date="2026-08-01", rights_verified=True, occupant_type="공실",
    )
    d.update(kw)
    return AuctionListing(**d)


def _scored(case_no="2025타경1", band_low=760_000_000, band_high=800_000_000,
            basis=5, min_bid=520_000_000):
    return score_listing(_listing(case_no, min_bid=min_bid), band_high, basis + 2,
                         market_scope=SCOPE_SAME_COMPLEX_SAME_AREA,
                         band_low=band_low, band_high=band_high, band_basis=basis)


# ---- 정렬·필터가 보수 차익 기준 ----

def test_sort_items_uses_conservative_profit():
    """기준 차익이 커도 보수 차익이 작으면 뒤로 — 목록 기본 정렬."""
    a = _scored("A", band_low=650_000_000, band_high=800_000_000)   # 보수차익 작음
    b = _scored("B", band_low=780_000_000, band_high=790_000_000)   # 보수차익 큼
    assert a.expected_profit > b.expected_profit
    assert decision_profit(a) < decision_profit(b)
    assert [s.case_no for s in sort_items([a, b], "profit")] == ["B", "A"]


def test_apply_filters_min_profit_conservative():
    from src.query import apply_filters
    a = _scored("A", band_low=650_000_000, band_high=800_000_000)
    assert decision_profit(a) < 200_000_000 < a.expected_profit
    assert apply_filters([a], min_profit=200_000_000) == []


# ---- 추천 표면: '위험' 제외 ----

def test_digest_excludes_hard_gated_risk():
    """하드게이트 '위험' 물건은 보수 차익이 커도 추천 TOP에서 제외(T7 — 3회 반복 지적)."""
    from src.digest import top_listings
    risky = score_listing(
        _listing("RISK", min_bid=450_000_000, special_rights=["유치권"]),
        800_000_000, 7, market_scope=SCOPE_SAME_COMPLEX_SAME_AREA,
        band_low=780_000_000, band_high=800_000_000, band_basis=5)
    ok = _scored("OK", min_bid=450_000_000)
    assert risky.grade == "위험"
    top = top_listings([risky, ok], n=10)
    assert [s.case_no for s in top] == ["OK"]


def test_digest_markdown_conservative_columns():
    from src.digest import to_markdown
    md = to_markdown([_scored()])
    assert "보수 기준 차익" in md
    assert "실거래 5건" in md          # 근거 병기
    assert "권리 확인 완료 전까지 최종 판단 금지" in md
    assert "확실" not in md            # 단정 표현 금지


# ---- 웹 표면 문구 ----

def _client():
    from src.web import create_app
    return create_app().test_client()


def test_listing_page_conservative_copy():
    html = _client().get("/").get_data(as_text=True)
    assert "1차 필터" in html
    assert "보수 기준 차익" in html
    assert "확실한 차익" not in html


def test_listing_hero_is_conservative_and_not_risky():
    """(T7 재작업) 히어로 스포트라이트 — '위험' 물건 금지 + 보수 기준 라벨 + 근거 병기.

    이전 결함: 히어로가 items[0](위험 물건)의 expected_profit을 '예상 차익'으로 헤드라인.
    """
    html = _client().get("/").get_data(as_text=True)
    # 히어로 블록 추출(첫 hero div ~ 다음 tablewrap 전)
    start = html.find('<div class="hero2')
    end = html.find('<div class="rank-head')
    assert start != -1 and end > start
    hero_html = html[start:end]
    assert "보수 기준 차익" in hero_html          # 보수 라벨
    assert "건 기준" in hero_html                 # 표본 근거 병기
    assert "예상 차익 ·" not in hero_html         # 구 라벨 금지(밴드 있는 샘플 기준)
    assert 'chip risk' not in hero_html           # 위험 칩이 히어로에 없음
    # 위험 물건(해운대)은 목록 테이블에는 남는다(배제 아님)
    table_html = html[end:]
    assert "해운대마린시티자이" in table_html


def test_listing_footer_formula_conservative():
    html = _client().get("/").get_data(as_text=True)
    assert "보수 기준 차익 = 검증 하한가" in html
    assert "예상 차익 = 추정시세" not in html      # 구 산식이 대표 산식으로 남지 않음


def test_detail_page_rights_notice():
    c = _client()
    case = c.get("/api/listings").get_json()[0]["case_no"]
    html = c.get(f"/property/{case}").get_data(as_text=True)
    assert "권리 확인 완료 전까지 최종 판단 금지" in html
    assert "보수 가격 기준 차익" in html
    assert "실거래" in html and "건 기준" in html   # 표본 근거 병기


def test_compare_page_conservative_row():
    c = _client()
    cases = [r["case_no"] for r in c.get("/api/listings").get_json()[:2]]
    html = c.get(f"/compare?case={cases[0]}&case={cases[1]}").get_data(as_text=True)
    assert "보수 기준 차익" in html
    assert "근거 표본" in html


# ---- README 포지셔닝 ----

def test_readme_no_certainty_language():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "차익이 확실한" not in text
    assert "사라고 콕 집어주는 엔진." not in text.replace("아닙니다", "")  # 부정문 인용만 허용
    assert "1차 필터" in text
    # 등급명 '확실한 차익'(구명칭) 잔존 금지 — 현행 '차익 유력'
    assert "확실한 차익" not in text
