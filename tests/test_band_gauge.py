"""밴드 게이지(시그니처 비주얼) — 하한~기준가 밴드 + 취득원가 ▼ + 호가 점.

근거 문서 11장 다이어그램의 가로판. 밴드 없는 레거시 행은 기존 갭미터 폴백.
"""
import re

from src.matcher import SCOPE_SAME_COMPLEX_SAME_AREA
from src.models import AuctionListing
from src.report import gap_meter_html
from src.score import score_listing


def _scored(min_bid=520_000_000, band_low=760_000_000, band_high=800_000_000, basis=5):
    lst = AuctionListing(
        case_no="G1", court="법원", address="서울 노원구 상계동", lawd_cd="11350",
        dong="상계동", apt_name="행복아파트", property_type="아파트", area_m2=84.0,
        appraisal_price=900_000_000, min_bid_price=min_bid, fail_count=1,
        sale_date="2026-08-01", rights_verified=True, occupant_type="공실")
    return score_listing(lst, band_high, basis + 2, market_scope=SCOPE_SAME_COMPLEX_SAME_AREA,
                         band_low=band_low, band_high=band_high, band_basis=basis)


def _legacy():
    lst = AuctionListing(
        case_no="L1", court="법원", address="서울", lawd_cd="11350", dong="상계동",
        apt_name="구아파트", property_type="아파트", area_m2=84.0,
        appraisal_price=900_000_000, min_bid_price=520_000_000, fail_count=1,
        sale_date="2026-08-01")
    return score_listing(lst, 800_000_000, 5)   # 밴드 미전달 → 레거시


def _positions(html):
    return [float(m) for m in re.findall(r'left:([\d.]+)%', html)]


# ---- 디스패치 ----

def test_band_row_renders_gauge():
    html = gap_meter_html(_scored())
    assert "bandgauge" in html and "bg-band" in html and "bg-cost" in html
    assert "gapmeter" in html                     # 기존 래퍼 클래스 유지(호환)
    assert "밴드" in html and "원가" in html


def test_legacy_row_falls_back_to_gap_meter():
    html = gap_meter_html(_legacy())
    assert "bandgauge" not in html
    assert "gm-min" in html                       # 기존 미터


# ---- 원가 위치별 3상태 ----

def test_cost_below_band_shows_profit_segment():
    html = gap_meter_html(_scored(min_bid=520_000_000))     # 원가 < 하한
    assert "bg-profit" in html
    assert "보수 차익" in html
    assert "bg-inband" not in html and "bg-overcost" not in html


def test_cost_inside_band_shows_inband():
    html = gap_meter_html(_scored(min_bid=750_000_000))     # 하한 < 원가 < 기준
    assert "bg-inband" in html and "보수 차익 없음" in html
    assert "bg-profit" not in html


def test_cost_above_band_shows_overcost():
    html = gap_meter_html(_scored(min_bid=820_000_000))     # 원가 > 기준
    assert "bg-overcost" in html and "기준가 초과" in html


# ---- 좌표 무결성 ----

def test_positions_within_track():
    for mb in (520_000_000, 750_000_000, 850_000_000):
        for p in _positions(gap_meter_html(_scored(min_bid=mb))):
            assert 0.0 <= p <= 100.0


def test_band_edges_ordered():
    html = gap_meter_html(_scored())
    m = re.search(r'bg-band" style="left:([\d.]+)%;width:([\d.]+)%', html)
    assert m and float(m.group(2)) > 0            # 밴드 폭 양수


# ---- 호가 점(검증 보조) ----

def test_asking_dots_rendered():
    asks = [{"price": 700_000_000}, {"price": 850_000_000}]
    html = gap_meter_html(_scored(), askings=asks)
    assert html.count("bg-ask") == 2
    assert "호가" in html


def test_no_asking_no_dots():
    assert "bg-ask" not in gap_meter_html(_scored())


# ---- (웹 통합 테스트 2건 삭제 — 2026-07-23) ----
# 종전의 test_detail/listing_page_renders_band_gauge 는 "bandgauge" 문자열이 페이지에 있는지
# 검사했는데, 실제로는 템플릿 어디서도 meter 를 호출하지 않아(맥시멀 대시보드 개편으로 배선 소멸)
# base.html **인라인 CSS 의 클래스 정의 텍스트**에 매칭돼 통과해온 허상 검사였다.
# CSS 외부화(base.css)로 허상이 드러나 삭제. 함수 자체의 마크업 계약은 위 단위 테스트가 지킨다.
