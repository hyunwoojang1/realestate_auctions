"""네이버 증분 크롤 수정 4종 회귀 테스트 (2026-08-07 실사고).

실사고 요약 — 매일 05:30 사이클이 8/4~8/7 나흘 연속 [4/5] 네이버 단계에서 강제 종료돼
뒤따르는 [5/5] 재채점이 통째로 실행되지 않았다. 원인 4개를 각각 고정한다:

F1 조기 종료  : 증분인데 20년치를 매번 다시 받았다 — 325쌍/34,614행 중 최근 14일 새 거래는
                53행(0.15%). 쌍당 32.7초의 거의 전부가 보유분 재수신 대기였다.
F2 시간 예산  : 예산 개념이 없어 작업 스케줄러 5시간 제한에 강제 종료(exit 267014/0xC000013A).
F3 crashed    : 'Target crashed' 가 드라이버 사망 목록('target closed')에 없어 _launch 무한 대기.
F4 날짜칸 오염: last_checked='reprocess:...' 가 문자열 비교에서 어떤 날짜보다 크게 판정돼
                1,096행이 영구히 '신선'으로 분류(그중 대상 모집단 잔존 407쌍).
F5 만기 지터  : 7/20 에 853쌍을 한 번에 확인 → 14일 뒤 같은 날 동시 만기(하루 105→716건).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from src import naver_store as ns
from src.naver_client import NaverClient, batch_has_new, is_driver_dead

# ---------------------------------------------------------------------------
# F3 — 드라이버 사망 판정
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("msg", [
    "Error: Page.evaluate: Target crashed",       # ← 실사고 문구(8/4·8/5·8/6 각 1회)
    "Page crashed",
    "Target closed",
    "Connection closed while reading from the driver",
    "Browser has been closed",
])
def test_driver_dead_signals_detected(msg):
    assert is_driver_dead(msg) is True


@pytest.mark.parametrize("msg", [
    "HTTP 429 Too Many Requests",
    "net::ERR_CONNECTION_RESET",
    "",
    None,
])
def test_non_driver_errors_not_misclassified(msg):
    """일시 오류를 드라이버 사망으로 오판하면 매번 playwright 전체를 재시작해 느려진다."""
    assert is_driver_dead(msg) is False


# ---------------------------------------------------------------------------
# F1 — 조기 종료 판정
# ---------------------------------------------------------------------------


def _row(y, m, d=1):
    return {"tradeYear": str(y), "tradeMonth": str(m), "tradeDate": str(d)}


def test_batch_has_new_without_baseline_always_true():
    """처음 보는 쌍(stop_before_ymd=None)은 전량 수집 — 조기 종료 금지."""
    assert batch_has_new([_row(2010, 1)], None) is True


def test_batch_has_new_detects_newer_trade():
    assert batch_has_new([_row(2026, 8, 5), _row(2020, 1)], "20260731") is True


def test_batch_has_new_false_when_all_older_or_equal():
    assert batch_has_new([_row(2026, 7, 31), _row(2019, 3)], "20260731") is False


def test_batch_has_new_treats_undated_row_as_possibly_new():
    """날짜를 못 읽은 행은 '모름' — 없음으로 바꾸지 않는다(보수적)."""
    assert batch_has_new([{"tradeMonth": "7"}], "20260731") is True


class _StubClient(NaverClient):
    """네트워크·playwright 없이 real_prices 페이지네이션만 검증하는 스텁."""

    def __init__(self, pages):
        self._pages = pages
        self.fetched = 0

    def fetch(self, path):            # noqa: D102 - 스텁
        if self.fetched >= len(self._pages):
            return {"realPriceOnMonthList": [], "totalRowCount": 0}
        batch = self._pages[self.fetched]
        self.fetched += 1
        return {"realPriceOnMonthList": [{"realPriceList": batch}],
                "totalRowCount": 999, "addedRowCount": self.fetched * 10}

    def _log(self, *a):               # noqa: D102 - 스텁
        pass


def _descending_pages(n_pages=25, per_page=4):
    """최신 → 과거 순 페이지들(실측 캐시 2,786쌍 전수에서 역전 0쌍)."""
    pages, y, m = [], 2026, 8
    for _ in range(n_pages):
        page = []
        for _ in range(per_page):
            page.append(_row(y, m))
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        pages.append(page)
    return pages


def test_early_stop_reads_only_first_pages_when_nothing_new():
    """보유 최신 거래일이 최신이면 1~2페이지만 읽고 멈춘다(안전마진 1페이지 포함)."""
    c = _StubClient(_descending_pages())
    rows, meta = c.real_prices("1", "1", max_pages=25, stop_before_ymd="20260901")
    assert meta["early_stop"] is True
    assert c.fetched == 2, "안전마진 1페이지까지만 더 보고 멈춰야 한다"
    assert len(rows) == 8


def test_early_stop_keeps_reading_while_new_trades_appear():
    """새 거래가 이어지면 계속 읽는다 — 조기 종료가 새 거래를 잘라먹지 않는다."""
    c = _StubClient(_descending_pages())
    # 2024-01 이후가 전부 '새 거래' → 앞 페이지들을 계속 읽어야 한다
    rows, meta = c.real_prices("1", "1", max_pages=25, stop_before_ymd="20240101")
    assert c.fetched > 4
    assert len(rows) > 16


def test_no_baseline_reads_everything():
    """신규 쌍은 종전 동작(전량 수집) — 회귀 방지."""
    c = _StubClient(_descending_pages(n_pages=5))
    rows, meta = c.real_prices("1", "1", max_pages=25, stop_before_ymd=None)
    assert meta["early_stop"] is False
    assert len(rows) == 20


def test_page_cap_still_reports_truncation():
    """상한 도달은 여전히 exhausted=False(잘림)로 드러내야 한다 — 침묵 절단 금지."""
    c = _StubClient(_descending_pages(n_pages=30))
    _rows, meta = c.real_prices("1", "1", max_pages=3, stop_before_ymd=None)
    assert meta["exhausted"] is False
    assert meta["early_stop"] is False


# ---------------------------------------------------------------------------
# F4 · F5 — 증분 대상 선정
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    ns.ensure_schema(c)
    return c


def _mark(c, cno, ano, checked, latest="20260701", exhausted=1):
    ns.record_pair_status(c, cno, ano, 10, latest, bool(exhausted), checked)


def test_non_date_last_checked_is_not_treated_as_fresh(conn):
    """F4: 'reprocess:...' 는 날짜가 아니므로 '신선'으로 분류되면 안 된다(영구 스킵 방지)."""
    _mark(conn, "100", "1", "reprocess:2026-07-19 21:30")
    fresh = ns.fresh_pairs(conn, stale_days=14, jitter_days=1)
    assert ("100", "1") not in fresh
    # checked_pairs(stale_before=) 경로도 같은 규칙
    cutoff = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
    assert ("100", "1") not in ns.checked_pairs(conn, stale_before=cutoff)


def test_recent_pair_is_fresh_and_old_pair_is_not(conn):
    now = datetime(2026, 8, 7, 5, 30, 0)
    _mark(conn, "200", "1", (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"))
    _mark(conn, "300", "1", (now - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S"))
    fresh = ns.fresh_pairs(conn, stale_days=14, jitter_days=1, now=now)
    assert ("200", "1") in fresh
    assert ("300", "1") not in fresh


def test_jitter_spreads_same_day_bulk_over_multiple_days(conn):
    """F5: 같은 날 확인된 쌍 다수가 한 날에 몰려 만기되지 않는다(8/4 실사고)."""
    now = datetime(2026, 8, 7, 5, 30, 0)
    checked = (now - timedelta(days=14, hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    for i in range(200):
        _mark(conn, str(1000 + i), "1", checked)
    fresh = ns.fresh_pairs(conn, stale_days=14, jitter_days=7, now=now)
    # 지터가 없으면 200쌍 전부 동시에 만기(fresh=0). 지터가 있으면 일부는 아직 신선하다.
    assert 0 < len(fresh) < 200, f"만기가 분산되지 않았다(fresh={len(fresh)})"


def test_jitter_is_stable_across_calls(conn):
    """내장 hash()는 프로세스마다 달라 만기일이 흔들린다 — crc32 고정 확인."""
    a = ns._pair_jitter_days("12345", "7", 7)
    b = ns._pair_jitter_days("12345", "7", 7)
    assert a == b and 0 <= a < 7


def test_pair_status_map_exposes_latest_ymd(conn):
    """F1 배선: 조기 종료는 이 값(보유 최신 거래일)을 기준선으로 쓴다."""
    _mark(conn, "400", "2", "2026-08-01 05:30:00", latest="20260725", exhausted=0)
    m = ns.pair_status_map(conn)
    assert m[("400", "2")]["latest_ymd"] == "20260725"
    assert m[("400", "2")]["exhausted"] is False
