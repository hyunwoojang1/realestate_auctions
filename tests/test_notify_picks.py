"""추천 푸시(deploy/notify_picks.py) 계약 테스트 — 네트워크 0.

계약: ①선별은 홈 추천과 동일 게이트(digest.top_listings) 위에 당일 마감·기발송만
추가로 거른다 ②한 줄 요약은 지역·감정가→최저가(유찰)·보수차익·기일을 담는다
③클릭 URL 은 1위 물건 상세로(한글 법원명 인코딩) ④기발송 이력은 30일 보관.
"""
import datetime as dt

from deploy.notify_picks import _eok, _region, compose, item_line, pick
from src.models import ScoredListing


def _s(case_no="2025타경100", **kw):
    d = dict(
        case_no=case_no, apt_name="상계주공3", address="서울 노원구 상계동 666",
        property_type="아파트", area_m2=84.9, appraisal_price=520_000_000,
        min_bid_price=332_800_000, fail_count=2, sale_date="2026-09-12",
        est_market_price=510_000_000, matched_trades=9, confidence=0.9,
        real_acquisition_cost=350_000_000, expected_profit=160_000_000,
        gap_rate=0.3, gap_score=80.0, rights_score=90.0, liquidity_score=70.0,
        arb_score=85.0, grade="차익 유력", court="서울북부지방법원", item_no="1",
        market_scope="same_complex_same_area", market_band_low=480_000_000,
        profit_low=130_000_000, market_sample_basis=7,
    )
    d.update(kw)
    return ScoredListing(**d)


def test_eok_formats():
    assert _eok(520_000_000) == "5.2억"
    assert _eok(85_000_000) == "8,500만"
    assert _eok(None) == "?"


def test_region_two_tokens():
    assert _region("서울 노원구 상계동 666") == "서울 노원구"
    assert _region("") == "지역 미상"


def test_item_line_has_user_requested_fields():
    line = item_line(_s(), dt.date(2026, 8, 25))
    assert "서울 노원구" in line and "상계주공3" in line
    assert "감정 5.2억" in line and "최저 3.3억" in line and "유찰 2회" in line
    assert "보수차익 +1.3억" in line          # decision_profit = profit_low
    assert "09-12" in line and "D-18" in line
    assert "평평" not in line   # report.pyeong 이 단위를 포함 반환 — 이중 표기 회귀 방지


def test_pick_excludes_sent_and_closed(monkeypatch):
    from src import query
    a, b, c = _s("2025타경1"), _s("2025타경2"), _s("2025타경3")
    monkeypatch.setattr(query, "bidding_closed", lambda s, now: s.case_no == "2025타경2")
    now = dt.datetime(2026, 8, 25, 9, 0)
    picks = pick([a, b, c], {}, sent={a.uid: "2026-08-24"}, n=3, now=now)
    assert [s.case_no for s in picks] == ["2025타경3"]   # a=기발송, b=마감


def test_compose_click_points_to_top_item():
    title, message, click = compose([_s(), _s("2025타경200", item_no="2")],
                                    dt.date(2026, 8, 25))
    assert title == "오늘의 경매 추천 2건"
    assert message.startswith("1. ") and "\n2. " in message
    assert click.startswith(
        "https://auction-arbitrage-hyunwoo-jang-s-projects.vercel.app/property/")
    assert "item=1" in click and "%" in click            # 한글(타경·법원명) 인코딩
