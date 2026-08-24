"""`delete_photos_beyond_seq` 계약 — 클라우드에서 사진을 **지우는** 유일한 경로라 못 박는다.

세트3 재감사 지적: 이번 라운드 최대 신규 위험(삭제 경로)인데 단위테스트가 0건이었다.
"""
from __future__ import annotations

import pytest

from src import store_rest


class _Resp:
    def __init__(self, code=200):
        self.status_code = code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_x")


def test_empty_input_is_noop(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("빈 입력인데 요청이 나갔다")
    monkeypatch.setattr(store_rest.requests, "delete", _boom)
    assert store_rest.delete_photos_beyond_seq([]) == (0, 0)


def test_filters_pin_the_exact_listing_and_seq(monkeypatch):
    seen = []

    def _delete(url, headers=None, params=None, timeout=None):
        seen.append((url, params))
        return _Resp()

    monkeypatch.setattr(store_rest.requests, "delete", _delete)
    done, tried = store_rest.delete_photos_beyond_seq([("서울중앙", "2024타경1", "", 3)])
    assert (done, tried) == (1, 1)
    url, params = seen[0]
    assert url.endswith("/auction_listing_photos")
    assert params == {"court": "eq.서울중앙", "case_no": "eq.2024타경1",
                      "item_no": "eq.", "seq": "gte.3"}, "필터가 물건·seq 를 정확히 못박지 못한다"


def test_partial_failure_is_reported_not_swallowed(monkeypatch):
    calls = {"n": 0}

    def _delete(url, headers=None, params=None, timeout=None):
        calls["n"] += 1
        return _Resp(500 if calls["n"] == 2 else 200)

    monkeypatch.setattr(store_rest.requests, "delete", _delete)
    done, tried = store_rest.delete_photos_beyond_seq(
        [("c", "A", "1", 2), ("c", "B", "1", 0), ("c", "C", "1", 5)])
    assert tried == 3, "시도 수를 안 돌려주면 부분 실패가 완전 성공과 구별되지 않는다"
    assert done == 2, "실패 한 건이 나머지를 막았거나 성공으로 계상됐다"
