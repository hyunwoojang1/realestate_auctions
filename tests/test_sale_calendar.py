"""경매 일정 캘린더(B1) 테스트 — 순수 함수 + 웹 라우트. 라이브 호출 0."""
from src import sale_calendar
from src.models import ScoredListing
from src.web import create_app


def _sl(case_no="2026타경1", sale_date="2026-07-15", score=80.0, apt="A단지"):
    return ScoredListing(
        case_no=case_no, apt_name=apt, address="서울 노원구 상계동", property_type="아파트",
        area_m2=59.0, appraisal_price=500_000_000, min_bid_price=400_000_000,
        fail_count=1, sale_date=sale_date,
        est_market_price=500_000_000, matched_trades=5, confidence=0.9,
        real_acquisition_cost=404_000_000, expected_profit=96_000_000,
        gap_rate=0.19, gap_score=40.0, rights_score=20.0, liquidity_score=15.0,
        arb_score=score, grade="차익 유력")


FIX = [
    _sl("1", "2026-07-20", score=90.0),
    _sl("2", "2026-07-20", score=50.0),
    _sl("3", "2026-07-08"),
    _sl("4", "2026-08-03"),
    _sl("5", ""),              # 기일 미상 — 캘린더 제외 대상
    _sl("6", "2026-06-30"),    # 과거 기일
]


# ---- 날짜 그룹핑 ----

def test_group_by_date_sorted_ascending_and_skips_blank():
    days = sale_calendar.group_by_date(FIX)
    assert [d["date"] for d in days] == ["2026-06-30", "2026-07-08", "2026-07-20", "2026-08-03"]
    assert len(days[2]["items"]) == 2          # 7/20 두 건


def test_group_by_date_items_sorted_by_score_desc():
    days = sale_calendar.group_by_date(FIX)
    d720 = next(d for d in days if d["date"] == "2026-07-20")
    assert [s.arb_score for s in d720["items"]] == [90.0, 50.0]


def test_unknown_date_count():
    assert sale_calendar.unknown_date_count(FIX) == 1


def test_malformed_date_treated_as_unknown_not_missorted():
    """비ISO 원문(크롤 잔여물)은 예정/과거·월 묶음에 끼지 않고 미상으로 집계."""
    bad = [_sl("9", "변경 2026.07.15")]
    assert sale_calendar.unknown_date_count(bad) == 1
    assert sale_calendar.group_by_date(bad) == []
    up, past = sale_calendar.split_upcoming(bad, today="2026-07-01")
    assert up == [] and past == []


# ---- 과거/예정 분리 ----

def test_split_upcoming_today_is_inclusive():
    up, past = sale_calendar.split_upcoming(FIX, today="2026-07-08")
    up_dates = {s.sale_date for s in up}
    assert "2026-07-08" in up_dates            # 오늘 기일은 '예정'에 포함
    assert {s.sale_date for s in past} == {"2026-06-30"}
    assert all(s.sale_date for s in up)        # 미상은 어느 쪽에도 없음


# ---- 월 묶음 ----

def test_month_groups_structure():
    months = sale_calendar.month_groups(FIX)
    assert [m["month"] for m in months] == ["2026-06", "2026-07", "2026-08"]
    july = next(m for m in months if m["month"] == "2026-07")
    assert [d["date"] for d in july["days"]] == ["2026-07-08", "2026-07-20"]
    assert july["count"] == 3                  # 7월 총 3건


def test_weekday_kr():
    assert sale_calendar.weekday_kr("2026-07-15") == "수"
    assert sale_calendar.weekday_kr("이상한값") == ""


# ---- 웹 라우트 ----

def test_calendar_page_renders():
    r = create_app().test_client().get("/calendar")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "경매 일정" in body


def test_calendar_page_links_to_detail():
    r = create_app().test_client().get("/calendar?all=1")
    body = r.get_data(as_text=True)
    assert "/property/" in body                # 물건 상세 링크
