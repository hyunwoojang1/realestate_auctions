"""당일 입찰 마감 컷오프 (2026-07-24) — 개시시각(maeHh1)+버퍼 경과 물건을 추천에서 내린다.

죽전자이2차(2025타경55336) 사례: 매각기일 당일 오전 크롤 → 정상 수집이지만, 오후엔
입찰 마감이라 추천 노출이 헛물. 날짜 단위 필터(sale_date<today)는 당일을 못 걸러 시각 컷오프가 필요.
"""
from __future__ import annotations

import datetime as dt

from src import query, sale_calendar
from src.models import ScoredListing


def _s(sale_date: str, sale_time: str = "") -> ScoredListing:
    return ScoredListing(
        case_no="2025타경1", apt_name="T", address="A", property_type="아파트",
        area_m2=84.9, appraisal_price=100, min_bid_price=80, fail_count=1,
        sale_date=sale_date, est_market_price=None, matched_trades=0, confidence=0.6,
        real_acquisition_cost=90, expected_profit=None, gap_rate=None, gap_score=0.0,
        rights_score=0.0, liquidity_score=0.0, arb_score=None, grade="관심",
        sale_time=sale_time)


TODAY = "2026-07-24"


# ---- bidding_closed: 개시 10:00 + 버퍼 120분 = 컷 12:00 ----

def test_open_before_start_not_closed():
    """개시(10:00) 전 — 당연히 마감 아님."""
    assert query.bidding_closed(_s(TODAY, "1000"), dt.datetime(2026, 7, 24, 9, 30)) is False


def test_within_buffer_not_closed():
    """개시 후지만 버퍼(정오) 전 — 아직 입찰 유효, 숨기면 안 됨(11:30 < 12:00)."""
    assert query.bidding_closed(_s(TODAY, "1000"), dt.datetime(2026, 7, 24, 11, 30)) is False


def test_after_buffer_closed():
    """개시+버퍼(12:00) 경과 — 마감."""
    assert query.bidding_closed(_s(TODAY, "1000"), dt.datetime(2026, 7, 24, 12, 30)) is True


def test_missing_time_assumes_10am():
    """시각 미상이면 통상 개시 10:00 가정 → 12:30이면 마감."""
    assert query.bidding_closed(_s(TODAY, ""), dt.datetime(2026, 7, 24, 12, 30)) is True


def test_early_start_court_cutoff_shifts():
    """개시 09:55 법원 → 컷 11:55. 11:50은 유효, 12:00은 마감(법원별 개시차 반영)."""
    s = _s(TODAY, "0955")
    assert query.bidding_closed(s, dt.datetime(2026, 7, 24, 11, 50)) is False
    assert query.bidding_closed(s, dt.datetime(2026, 7, 24, 12, 0)) is True


def test_future_date_never_closed():
    """미래 기일은 마감 아님(오늘만 시각 판정)."""
    assert query.bidding_closed(_s("2026-07-25", "1000"), dt.datetime(2026, 7, 24, 23, 0)) is False


def test_past_date_not_handled_here():
    """과거 기일은 bidding_closed 소관 아님(False) — 날짜 필터(split_upcoming)가 과거로 보낸다."""
    assert query.bidding_closed(_s("2026-07-23", "1000"), dt.datetime(2026, 7, 24, 12, 30)) is False


def test_unknown_date_not_closed():
    """기일 미상은 마감 판정 불가 → False(침묵 제외 방지)."""
    assert query.bidding_closed(_s("", "1000"), dt.datetime(2026, 7, 24, 12, 30)) is False


# ---- split_upcoming: 시각 인지 (오늘 마감분 → 과거) ----

def test_split_moves_closed_today_to_past():
    """오늘 마감 지난 물건은 과거로, 마감 전 오늘·미래는 예정으로."""
    now = dt.datetime(2026, 7, 24, 12, 30)
    open_today = _s(TODAY, "1000")      # 개시10 컷12:00, now 12:30 → 마감 → 과거
    future = _s("2026-07-25", "1000")   # 예정
    past = _s("2026-07-20", "1000")     # 과거
    up, old = sale_calendar.split_upcoming([open_today, future, past], TODAY, now)
    assert future in up and open_today not in up
    assert open_today in old and past in old


def test_split_keeps_open_today_in_upcoming():
    """마감 전(11:00)이면 오늘 물건은 예정에 남는다."""
    now = dt.datetime(2026, 7, 24, 11, 0)
    open_today = _s(TODAY, "1000")
    up, old = sale_calendar.split_upcoming([open_today], TODAY, now)
    assert open_today in up and open_today not in old


# ---- 방어: 비정상 시각·타임존 (리뷰 MEDIUM) ----

def test_malformed_time_falls_back_no_crash():
    """4자리지만 비정상 시각('2530')은 _dt.time() ValueError 없이 10:00 가정 —
    한 물건의 이상값이 전 페이지 500을 내지 않게 한다."""
    s = _s(TODAY, "2530")   # 25시 30분 = 무효 → 10:00 가정, 컷 12:00
    assert query.bidding_closed(s, dt.datetime(2026, 7, 24, 11, 30)) is False
    assert query.bidding_closed(s, dt.datetime(2026, 7, 24, 12, 30)) is True


def test_malformed_minute_falls_back():
    """분이 60 이상('1099')도 무효 → 10:00 가정."""
    s = _s(TODAY, "1099")
    assert query.bidding_closed(s, dt.datetime(2026, 7, 24, 12, 30)) is True


def test_now_kst_is_naive_kst_walltime():
    """now_kst()는 tz-naive KST 벽시계 — 서버가 UTC로 떠도 컷오프가 밀리지 않게."""
    n = query.now_kst()
    assert n.tzinfo is None
    # UTC now와 약 9시간 차(±1분 허용) — KST 오프셋 확인
    delta = (n - dt.datetime.now(dt.UTC).replace(tzinfo=None)).total_seconds()
    assert 9 * 3600 - 60 < delta < 9 * 3600 + 60
