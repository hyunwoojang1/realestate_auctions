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
    assert seen["params"]["order"] == "arb_score.desc.nullslast"
    assert seen["url"].endswith("/rest/v1/auction_scored_listings")


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
        # 페이로드 = 스칼라 컬럼(_COLS) + market_comps(차트 실거래 점 jsonb). 비컬럼(rights_verified 등) 금지.
        assert set(json[0].keys()) == set(_COLS) | {"market_comps"}
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
        order.append(("delete", params.get("refreshed_at")))
        return FakeResp()

    monkeypatch.setattr(store_rest.requests, "post", fake_post)
    monkeypatch.setattr(store_rest.requests, "delete", fake_delete)
    store_rest.replace_all([_sl(case_no="a"), _sl(case_no="b")])
    # 순서 불변식: 먼저 업서트(post), 그 다음 만료 삭제(delete lt.<stamp>).
    assert order[0][0] == "post"
    assert order[-1][0] == "delete"
    assert order[-1][1].startswith("lt.")


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
