"""store_rest(Supabase REST 어댑터) 유닛 테스트 — requests 를 모킹해 라이브 없이 검증.

읽기(load_scored 페이지네이션/캐시, has_rows content-range 파싱)와
쓰기(upsert 청킹, replace_all 의 업서트+만료삭제 순서)의 불변식을 고정한다.
"""
from __future__ import annotations

import pytest

from src import store_rest
from src.models import ScoredListing
from src.store import _COLS


def _row(**over) -> dict:
    r = {c: None for c in _COLS}
    r.update({
        "case_no": "2024타경1", "apt_name": "샘플아파트", "address": "서울 강남",
        "property_type": "아파트", "area_m2": 84.9, "appraisal_price": 800_000_000,
        "min_bid_price": 640_000_000, "fail_count": 1, "sale_date": "2026-08-01",
        "est_market_price": 900_000_000, "matched_trades": 7, "confidence": 0.9,
        "real_acquisition_cost": 660_000_000, "expected_profit": 240_000_000,
        "gap_rate": 0.27, "gap_score": 50.0, "rights_score": 30.0,
        "liquidity_score": 20.0, "arb_score": 90.0, "grade": "차익 유력",
        "court": "서울중앙", "item_no": "1", "doc_id": "", "market_scope": "same_complex_same_area",
    })
    r.update(over)
    return r


def _sl(**over) -> ScoredListing:
    return ScoredListing(**{c: _row(**over)[c] for c in _COLS})


class FakeResp:
    def __init__(self, json_data=None, headers=None, status=200):
        self._json = [] if json_data is None else json_data
        self.headers = headers or {}
        self.status_code = status

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test")
    monkeypatch.setenv("SUPABASE_TABLE", "auction_scored_listings")
    store_rest.invalidate()
    yield
    store_rest.invalidate()


def test_enabled_requires_both(monkeypatch):
    assert store_rest.enabled()
    monkeypatch.delenv("SUPABASE_SECRET_KEY")
    assert not store_rest.enabled()


def test_load_scored_single_page(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        seen["params"] = params
        seen["url"] = url
        return FakeResp([_row(case_no="2024타경1"), _row(case_no="2024타경2")])

    monkeypatch.setattr(store_rest.requests, "get", fake_get)
    items = store_rest.load_scored(use_cache=False)
    assert [s.case_no for s in items] == ["2024타경1", "2024타경2"]
    assert seen["params"]["order"] == "arb_score.desc.nullslast,court.asc,case_no.asc,item_no.asc"
    assert seen["url"].endswith("/rest/v1/auction_scored_listings")


def test_load_scored_selects_and_restores_market_comps(monkeypatch):
    """회귀(2026-07-20): market_comps 는 _COLS 밖 별도 컬럼 — select 에 빠지면 응답에 없어서
    프로덕션 상세 차트 실거래 점이 전부 사라진다(저장은 되는데 로드만 [] 되던 비대칭)."""
    seen = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        seen["params"] = params
        row = _row(case_no="2024타경1")
        row["market_comps"] = [["202401", 368000000], ["200812", 145000000]]
        return FakeResp([row])

    monkeypatch.setattr(store_rest.requests, "get", fake_get)
    items = store_rest.load_scored(use_cache=False)
    assert "market_comps" in seen["params"]["select"].split(",")
    assert items[0].market_comps == [["202401", 368000000], ["200812", 145000000]]


def test_load_scored_selects_and_restores_sale_time(monkeypatch):
    """(2026-07-24) sale_time 은 _COLS 밖 미러 컬럼 — select 에 빠지면 프로덕션
    bidding_closed 가 전 물건 10:00 폴백 가정으로만 동작한다(±30분 오차).
    저장(_payload)·로드 양쪽 대칭을 고정한다."""
    seen = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        seen["params"] = params
        row = _row(case_no="2024타경1")
        row["sale_time"] = "1030"
        return FakeResp([row])

    monkeypatch.setattr(store_rest.requests, "get", fake_get)
    items = store_rest.load_scored(use_cache=False)
    assert "sale_time" in seen["params"]["select"].split(",")
    assert items[0].sale_time == "1030"
    # 컬럼 NULL(구행)은 "" 로 강등 — bidding_closed 의 10:00 폴백 경로 유지
    monkeypatch.setattr(store_rest.requests, "get",
                        lambda *a, **k: FakeResp([_row(case_no="c2")]))
    items = store_rest.load_scored(use_cache=False)
    assert items[0].sale_time == ""


def test_payload_mirrors_sale_time(monkeypatch):
    """run.py 가 미러 직전 주입한 sale_time 이 upsert 페이로드에 실린다."""
    posted = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        posted["row"] = json[0]
        return FakeResp()

    monkeypatch.setattr(store_rest.requests, "post", fake_post)
    s = _sl(case_no="t1")
    s.sale_time = "0955"
    store_rest.upsert([s])
    assert posted["row"]["sale_time"] == "0955"


def test_load_scored_paginates(monkeypatch):
    pages = iter([
        [_row(case_no=f"c{i}") for i in range(store_rest._PAGE)],  # 꽉 참 → 다음 페이지 요청
        [_row(case_no="last")],                                    # 미만 → 종료
    ])
    offsets = []

    def fake_get(url, headers=None, params=None, timeout=None):
        offsets.append(params["offset"])
        return FakeResp(next(pages))

    monkeypatch.setattr(store_rest.requests, "get", fake_get)
    items = store_rest.load_scored(use_cache=False)
    assert len(items) == store_rest._PAGE + 1
    assert offsets == [0, store_rest._PAGE]


def test_load_scored_uses_cache(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, headers=None, params=None, timeout=None):
        calls["n"] += 1
        return FakeResp([_row()])

    monkeypatch.setattr(store_rest.requests, "get", fake_get)
    store_rest.load_scored()           # 1회 페치 → 캐시
    store_rest.load_scored()           # 캐시 사용 → 페치 안 함
    assert calls["n"] == 1
    store_rest.invalidate()
    store_rest.load_scored()           # 무효화 후 재페치
    assert calls["n"] == 2


def test_has_rows_true_and_false(monkeypatch):
    monkeypatch.setattr(store_rest.requests, "get",
                        lambda *a, **k: FakeResp(headers={"content-range": "0-0/3751"}))
    assert store_rest.has_rows() is True
    monkeypatch.setattr(store_rest.requests, "get",
                        lambda *a, **k: FakeResp(headers={"content-range": "*/0"}))
    assert store_rest.has_rows() is False


def test_upsert_chunks(monkeypatch):
    posted = []

    def fake_post(url, headers=None, json=None, timeout=None):
        posted.append(len(json))
        # 페이로드 = 스칼라 컬럼(_COLS) + market_comps(차트 jsonb) + sale_time(마감컷오프 미러).
        # 그 외 비컬럼(rights_verified 등) 금지.
        assert set(json[0].keys()) == set(_COLS) | {"market_comps", "sale_time"}
        return FakeResp()

    monkeypatch.setattr(store_rest.requests, "post", fake_post)
    items = [_sl(case_no=f"c{i}") for i in range(store_rest._WRITE_CHUNK + 10)]
    n = store_rest.upsert(items)
    assert n == store_rest._WRITE_CHUNK + 10
    assert posted == [store_rest._WRITE_CHUNK, 10]


def test_replace_all_upserts_then_deletes_stale(monkeypatch):
    order = []

    def fake_post(url, headers=None, json=None, timeout=None):
        order.append(("post", len(json)))
        assert "refreshed_at" in json[0]           # replace_all 은 run 시각 스탬프를 넣는다
        return FakeResp()

    def fake_delete(url, headers=None, params=None, timeout=None):
        order.append(("delete", params.get("or")))
        return FakeResp()

    monkeypatch.setattr(store_rest.requests, "post", fake_post)
    monkeypatch.setattr(store_rest.requests, "delete", fake_delete)
    store_rest.replace_all([_sl(case_no="a"), _sl(case_no="b")])
    # 순서 불변식: 먼저 업서트(post), 그 다음 만료 삭제(delete). 만료 조건은 오래된 행 + NULL
    # refreshed_at(증분 upsert 구행)을 함께 지운다(감사 2026-07-15).
    assert order[0][0] == "post"
    assert order[-1][0] == "delete"
    assert "refreshed_at.lt." in order[-1][1] and "refreshed_at.is.null" in order[-1][1]


def test_fetch_rights_exact_match_only(monkeypatch):
    """(재검증 감사 idx16) 정확 매칭만 — 폴백 쿼리 없음(형제 물건 명세서 과신 금지),
    court 조건 필수(타법원 동명 사건 오표시 방지)."""
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(dict(params))
        return FakeResp([])

    monkeypatch.setattr(store_rest.requests, "get", fake_get)
    assert store_rest.fetch_rights("대구지방법원", "2025타경1235", "1") is None
    assert len(calls) == 1                                # 폴백 쿼리 없음
    assert calls[0].get("court") == "eq.대구지방법원"
    assert calls[0].get("item_no") == "eq.1"


# ── 병렬 페이지네이션(_fetch_pages, 2026-07-27) ────────────────────────────────
# 콜드 인스턴스 첫 홈 요청 25초의 원인이 "1000행마다 한 왕복"의 직렬 누적이었다.
# 병렬화하면서 절대 깨지면 안 되는 것: ①전량 수집(누락 0) ②offset 오름차순 순서 보존.


def _paged_get(pages: dict[int, list[dict]], total: str | None, calls: list):
    """offset→행 목록 매핑을 서빙하는 가짜 GET. total 이 None 이면 count 헤더 없음."""
    def fake_get(url, headers=None, params=None, timeout=None):
        off = int(params.get("offset", 0))
        calls.append(off)
        hdrs = {}
        if off == 0 and total is not None:
            hdrs["content-range"] = f"0-999/{total}"
        return FakeResp(pages.get(off, []), headers=hdrs)
    return fake_get


def _pages_of(n_total: int) -> dict[int, list[dict]]:
    """n_total 행을 _PAGE 크기로 자른 offset→행 맵. 행은 case_no 로 순서 식별."""
    page = store_rest._PAGE
    rows = [_row(case_no=f"C{i:06d}") for i in range(n_total)]
    return {off: rows[off:off + page] for off in range(0, max(n_total, 1), page)}


def test_fetch_pages_collects_all_rows_in_order(monkeypatch):
    """3.5페이지 분량을 병렬로 받아도 전량·순서가 순차 수집과 동일해야 한다."""
    page = store_rest._PAGE
    n = page * 3 + 17
    calls: list[int] = []
    monkeypatch.setattr(store_rest.requests, "get",
                        _paged_get(_pages_of(n), str(n), calls))
    rows = store_rest._fetch_pages("https://x.supabase.co", "k", "t", {"select": "*"})
    assert len(rows) == n
    assert [r["case_no"] for r in rows] == [f"C{i:06d}" for i in range(n)]
    assert sorted(calls) == [0, page, page * 2, page * 3]     # 페이지당 정확히 1회


def test_fetch_pages_single_page_skips_count_roundtrips(monkeypatch):
    """1페이지 미만이면 첫 응답으로 끝 — 추가 왕복 금지(상세·소량 테이블 회귀 방지)."""
    calls: list[int] = []
    monkeypatch.setattr(store_rest.requests, "get",
                        _paged_get({0: [_row(case_no="C1")]}, "1", calls))
    rows = store_rest._fetch_pages("https://x.supabase.co", "k", "t", {"select": "*"})
    assert len(rows) == 1
    assert calls == [0]


def test_fetch_pages_falls_back_to_sequential_without_count(monkeypatch):
    """count 헤더가 없으면(총 건수 미상) 순차 루프로 폴백해도 전량을 받아야 한다."""
    page = store_rest._PAGE
    n = page * 2 + 5
    calls: list[int] = []
    monkeypatch.setattr(store_rest.requests, "get",
                        _paged_get(_pages_of(n), None, calls))
    rows = store_rest._fetch_pages("https://x.supabase.co", "k", "t", {"select": "*"})
    assert len(rows) == n
    assert [r["case_no"] for r in rows] == [f"C{i:06d}" for i in range(n)]


def test_fetch_pages_reads_tail_when_count_understates(monkeypatch):
    """조회 중 행이 늘어 count 가 과소했던 경우에도 tail 을 마저 읽어 누락 0."""
    page = store_rest._PAGE
    n = page * 3                      # 실제는 3페이지인데 count 는 2페이지로 과소 보고
    calls: list[int] = []
    monkeypatch.setattr(store_rest.requests, "get",
                        _paged_get(_pages_of(n), str(page * 2), calls))
    rows = store_rest._fetch_pages("https://x.supabase.co", "k", "t", {"select": "*"})
    assert len(rows) == n
    assert [r["case_no"] for r in rows] == [f"C{i:06d}" for i in range(n)]


def test_rights_loader_orders_by_unique_key(monkeypatch):
    """(2026-07-27) 권리 로더에 order 부재 = 페이지 경계 행 누락 위험. 유일키 정렬 고정."""
    seen = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        seen["params"] = params
        return FakeResp([{"court": "서울중앙", "case_no": "2024타경1", "item_no": "1"}])

    monkeypatch.setattr(store_rest.requests, "get", fake_get)
    store_rest.load_all_rights(use_cache=False)
    assert seen["params"]["order"] == "court.asc,case_no.asc,item_no.asc"
