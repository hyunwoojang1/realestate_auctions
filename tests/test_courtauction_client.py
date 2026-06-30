"""courtauction_client — 검색페이로드·페이지네이션·차단감지·안전장치(네트워크 없음, 가짜세션)."""
from __future__ import annotations

import json

import pytest

from src.courtauction_client import (
    CourtAuctionBlocked,
    CourtAuctionClient,
    SearchFilter,
)
from src.courtauction_fields import SRCH_COND_REAL_ESTATE


# ---------------------------------------------------------------------------
# 가짜 응답/세션
# ---------------------------------------------------------------------------
class FakeResp:
    def __init__(self, status=200, body=None, ctype="application/json;charset=UTF-8",
                 headers=None):
        self.status_code = status
        self._body = body
        self.headers = {"Content-Type": ctype}
        if headers:
            self.headers.update(headers)
        self.text = body if isinstance(body, str) else json.dumps(body or {})

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


def _ok_page(total, rows_on_page):
    return {"status": 200, "data": {
        "dma_pageInfo": {"totalCnt": str(total)},
        "ipcheck": True,
        "dlt_srchResult": [{"srnSaNo": f"2025타경{i}", "gamevalAmt": "50000000",
                            "minmaePrice": "40000000", "yuchalCnt": "1",
                            "jiwonNm": "서울중앙지방법원"} for i in range(rows_on_page)],
    }}


class FakeSession:
    """get→워밍, post→큐에서 순서대로 반환."""
    def __init__(self, post_responses, set_cookie="wcCookieV2=1.2.3.4_T_X_WC; path=/"):
        self.headers = {}
        self._posts = list(post_responses)
        self._set_cookie = set_cookie
        self.post_calls = []

    def get(self, url, timeout=None):
        return FakeResp(200, "<html>index</html>", ctype="text/html",
                        headers={"Set-Cookie": self._set_cookie})

    def post(self, url, headers=None, data=None, timeout=None, allow_redirects=None):
        self.post_calls.append(json.loads(data))
        return self._posts.pop(0)


def _client(posts, **kw):
    kw.setdefault("min_interval", 0.0)
    kw.setdefault("max_interval", 0.0)
    kw.setdefault("stop_file", None)
    return CourtAuctionClient(session=FakeSession(posts), **kw)


# ---------------------------------------------------------------------------
# SearchFilter
# ---------------------------------------------------------------------------
def test_filter_payload_has_required_keys_and_values():
    p = SearchFilter(sido_cd="11", appraisal_max=100_000_000, fail_count_min=2).to_payload()
    assert p["cortAuctnSrchCondCd"] == SRCH_COND_REAL_ESTATE
    assert p["pgmId"] == "PGJ151M01"
    assert p["notifyLoc"] == "Y"
    assert p["rprsAdongSdCd"] == "11"
    assert p["aeeEvlAmtMax"] == "100000000"
    assert p["flbdNcntMin"] == "2"
    # 미설정 키는 빈 문자열로 존재(서버가 전 키를 요구)
    assert p["rletLwsDspslPrcMax"] == ""
    assert "objctArDtsMin" in p


def test_empty_filter_is_nationwide():
    p = SearchFilter().to_payload()
    assert p["rprsAdongSdCd"] == ""   # 전국


# ---------------------------------------------------------------------------
# 페이지네이션
# ---------------------------------------------------------------------------
def test_search_paginates_until_total():
    # total=90, 40/page → 3페이지(40,40,10)
    posts = [FakeResp(200, _ok_page(90, 40)),
             FakeResp(200, _ok_page(90, 40)),
             FakeResp(200, _ok_page(90, 10))]
    c = _client(posts)
    recs = list(c.search(SearchFilter(sido_cd="11"), max_pages=25))
    assert len(recs) == 90
    assert c._request_count == 3


def test_search_respects_max_pages():
    posts = [FakeResp(200, _ok_page(1000, 40)) for _ in range(3)]
    c = _client(posts)
    recs = list(c.search(SearchFilter(), max_pages=3))
    assert len(recs) == 120          # 3페이지에서 멈춤
    assert c._request_count == 3


def test_search_zero_results():
    c = _client([FakeResp(200, _ok_page(0, 0))])
    recs = list(c.search(SearchFilter(sido_cd="99")))
    assert recs == []


# ---------------------------------------------------------------------------
# 차단/안전장치
# ---------------------------------------------------------------------------
def test_403_raises_blocked():
    c = _client([FakeResp(403, "blocked")])
    with pytest.raises(CourtAuctionBlocked):
        list(c.search(SearchFilter(), warm=False))


def test_redirect_raises_blocked():
    c = _client([FakeResp(302, "")])
    with pytest.raises(CourtAuctionBlocked):
        list(c.search(SearchFilter(), warm=False))


def test_silent_ban_html_200_raises_blocked():
    # 200인데 HTML(조용한 차단)
    c = _client([FakeResp(200, "<html>maintenance</html>", ctype="text/html")])
    with pytest.raises(CourtAuctionBlocked):
        list(c.search(SearchFilter(), warm=False))


def test_schema_broken_raises_blocked():
    c = _client([FakeResp(200, {"status": 550, "errors": {"errorMessage": "요청된 데이터가 없습니다."}})])
    with pytest.raises(CourtAuctionBlocked):
        list(c.search(SearchFilter(), warm=False))


def test_daily_cap_circuit_breaker():
    posts = [FakeResp(200, _ok_page(1000, 40)) for _ in range(5)]
    c = _client(posts, daily_cap=2)
    with pytest.raises(CourtAuctionBlocked):
        list(c.search(SearchFilter(), max_pages=10))


def test_kill_switch(tmp_path):
    stop = tmp_path / "STOP"
    stop.write_text("x", encoding="utf-8")
    c = CourtAuctionClient(session=FakeSession([FakeResp(200, _ok_page(40, 40))]),
                           min_interval=0.0, max_interval=0.0, stop_file=str(stop))
    with pytest.raises(CourtAuctionBlocked):
        list(c.search(SearchFilter(), warm=False))


def test_5xx_then_blocked_after_retries():
    posts = [FakeResp(503, "")] * 6
    c = _client(posts, max_retries=2, backoff_base=0.0, backoff_cap=0.0)
    with pytest.raises(CourtAuctionBlocked):
        list(c.search(SearchFilter(), warm=False))


def test_5xx_recovers_then_succeeds():
    posts = [FakeResp(503, ""), FakeResp(200, _ok_page(10, 10))]
    c = _client(posts, max_retries=3, backoff_base=0.0, backoff_cap=0.0)
    recs = list(c.search(SearchFilter(), warm=False))
    assert len(recs) == 10
    # 재시도 포함 '실제 전송 수' = 2 (503 + 200). 재시도가 카운트를 부풀리되 정확히 셈.
    assert c._request_count == 2


def test_missing_dma_pageInfo_is_blocked():
    # data는 있는데 dma_pageInfo 누락 → 스키마붕괴/차단으로 간주
    bad = {"status": 200, "data": {"dlt_srchResult": []}}
    c = _client([FakeResp(200, bad)])
    with pytest.raises(CourtAuctionBlocked):
        list(c.search(SearchFilter(), warm=False))


def test_retry_after_seconds_floor():
    # Retry-After 정수 → 최소 60초 하한(여기선 파싱만 검증, 실제 sleep 없음)
    assert CourtAuctionClient._parse_retry_after("5") == 60.0
    assert CourtAuctionClient._parse_retry_after("120") == 120.0
    assert CourtAuctionClient._parse_retry_after(None) is None
    assert CourtAuctionClient._parse_retry_after("garbage") is None


# ---------------------------------------------------------------------------
# affordable_search — 로컬 최저가 정밀필터
# ---------------------------------------------------------------------------
def test_affordable_filters_min_bid_client_side():
    # 4건: 최저가 4천만(2건)·1.2억·8천만. 현금 1억 → 4천만 2건 + 8천만 1건 = 3건
    data = {"status": 200, "data": {"dma_pageInfo": {"totalCnt": "4"}, "dlt_srchResult": [
        {"srnSaNo": "A", "gamevalAmt": "50000000", "minmaePrice": "40000000", "yuchalCnt": "1"},
        {"srnSaNo": "B", "gamevalAmt": "60000000", "minmaePrice": "40000000", "yuchalCnt": "1"},
        {"srnSaNo": "C", "gamevalAmt": "300000000", "minmaePrice": "120000000", "yuchalCnt": "3"},
        {"srnSaNo": "D", "gamevalAmt": "100000000", "minmaePrice": "80000000", "yuchalCnt": "1"},
    ]}}
    c = _client([FakeResp(200, data)])
    out = c.affordable_search(100_000_000, warm=False)
    assert {r.case_no for r in out} == {"A", "B", "D"}
    # 서버엔 감정가버퍼(현금×3=3억)로 요청됐는지 확인
    assert c.session.post_calls[0]["dma_srchGdsDtlSrchInfo"]["aeeEvlAmtMax"] == "300000000"


def test_canary_zero_raises_blocked():
    c = _client([FakeResp(200, _ok_page(0, 0))])
    with pytest.raises(CourtAuctionBlocked):
        c.canary()
