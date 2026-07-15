"""크롤링 안전 수정 회귀 테스트 (2026-07-15 Ponytail+정밀 리뷰 감사).

①수집 0건이 서빙 DB를 지우지 않음(치명 data-wipe 방지)
②네이버 안티봇 차단을 '데이터 없음'으로 오인하지 않음
③MOLIT 페이지네이션이 필터된 행 때문에 다음 페이지를 조기에 못 받는 버그 방지.
"""
import json

import pytest

from src import molit_client, store
from src.models import ScoredListing
from src.naver_client import NaverBlocked, NaverClient


def _scored(case_no: str, arb: float = 80.0) -> ScoredListing:
    return ScoredListing(
        case_no=case_no, apt_name="상계주공", address="서울 노원구 상계동",
        property_type="아파트", area_m2=84.9, appraisal_price=620_000_000,
        min_bid_price=397_000_000, fail_count=2, sale_date="2026-07-01",
        est_market_price=818_000_000, matched_trades=3, confidence=1.0,
        real_acquisition_cost=420_000_000, expected_profit=398_000_000,
        gap_rate=0.5, gap_score=50.0, rights_score=30.0, liquidity_score=20.0,
        arb_score=arb, grade="차익 유력",
    )


# ── ① 수집 0건이 서빙 DB를 지우지 않음 ──
def test_replace_all_empty_preserves_existing():
    """크롤 0건(전 샤드 차단/전량 파싱 실패)에 전량 교체를 돌려도 기존 매물이 보존된다."""
    conn = store.connect(":memory:")
    store.upsert(conn, [_scored("KEEP")])
    n = store.replace_all(conn, [])                       # 수집 0건 시나리오
    assert n == 0
    assert {s.case_no for s in store.load_scored(conn)} == {"KEEP"}  # 안 지워짐


def test_replace_all_nonempty_still_replaces():
    """정상 스냅샷은 여전히 전량 교체된다(만료 매물 제거 기능 보존)."""
    conn = store.connect(":memory:")
    store.upsert(conn, [_scored("OLD")])
    store.replace_all(conn, [_scored("NEW")])
    assert {s.case_no for s in store.load_scored(conn)} == {"NEW"}


# ── ② 네이버 안티봇 차단 vs 데이터 없음 구분 ──
class _FakePage:
    def __init__(self, resp):
        self._resp = resp

    def evaluate(self, js, arg):
        return self._resp


def _naver(resp) -> NaverClient:
    c = object.__new__(NaverClient)          # __init__(플레이라이트) 우회
    c._page = _FakePage(resp)
    c.token = "t"
    c.n = 0
    c.refresh_every = 9999
    c.min_delay = 0.0
    c.max_delay = 0.0
    c.calls = 0
    return c


def test_naver_200_json_returns_data():
    assert _naver({"status": 200, "body": json.dumps({"ok": 1})}).fetch("/x") == {"ok": 1}


def test_naver_200_nonjson_is_block():
    """200인데 JSON이 아니면(안티봇 챌린지 HTML) None이 아니라 차단으로 중단."""
    with pytest.raises(NaverBlocked):
        _naver({"status": 200, "body": "<html>bot check</html>"}).fetch("/x")


def test_naver_403_is_block():
    """403은 조용한 None이 아니라 차단 신호로 중단."""
    with pytest.raises(NaverBlocked):
        _naver({"status": 403, "body": ""}).fetch("/x")


def test_naver_404_is_empty_not_block():
    """404는 리소스 미존재 — 정상적 '데이터 없음'(None), 차단 아님."""
    assert _naver({"status": 404, "body": ""}).fetch("/x") is None


# ── ③ MOLIT 페이지네이션: 필터된 행에도 조기 종료하지 않음 ──
def _item(name: str, amount: str) -> str:
    return (f"<item><거래금액>{amount}</거래금액><전용면적>84.0</전용면적>"
            f"<년>2026</년><월>6</월><법정동>상계동</법정동><층>5</층>"
            f"<아파트>{name}</아파트></item>")


def _page(items: list[str]) -> str:
    return ("<response><header><resultCode>00</resultCode></header><body><items>"
            + "".join(items) + "</items></body></response>")


def test_pagination_not_early_exit_on_filtered_rows(monkeypatch):
    """page1이 raw 2건(1건은 거래금액 0으로 필터돼 parsed 1건)이어도 다음 페이지를 받는다.

    구버전은 len(page_trades)=1 < num_rows=2 로 조기 종료해 page2를 놓쳤다(comps 누락).
    """
    pages = {
        "1": _page([_item("상계주공", "45,000"), _item("걸러질행", "0")]),  # raw 2, parsed 1
        "2": _page([_item("둘째장단지", "50,000")]),                        # raw 1 < 2 → 종료
    }
    calls = []

    def fake_get(session, url, params, timeout, retries):
        calls.append(params["pageNo"])
        return pages[params["pageNo"]]

    monkeypatch.setattr(molit_client, "_get_with_retry", fake_get)
    trades = molit_client.fetch_trades("apt", "11350", "202606", "KEY",
                                       num_rows=2, max_pages=5)
    assert calls == ["1", "2"]                                   # 두 페이지 모두 요청
    assert {t.apt_name for t in trades} == {"상계주공", "둘째장단지"}
