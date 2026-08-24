"""접근 제어·요청 제한 테스트 — 2026-08-24 보안감사 대응 검증.

계약:
  - AUCTION_ADMIN_KEY 설정 시: 워치리스트 읽기/쓰기·/find 라이브는 키(쿠키/헤더) 필수.
  - 키 미설정 시: 로컬(샘플/SQLite)은 종전대로 열림(기존 테스트 계약 유지),
    클라우드 서빙(store_rest.enabled)은 fail-closed.
  - /api·/export·/find 는 비운영자에 IP당 슬라이딩 윈도 rate limit(429).
"""
import pytest

from src.web import create_app

KEY = "test-admin-key-123"
SAMPLE_CASE = "2024타경51234"


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    monkeypatch.setenv("AUCTION_WATCHLIST", str(tmp_path / "wl.json"))
    monkeypatch.setenv("AUCTION_SNAPSHOT", str(tmp_path / "snap.json"))
    monkeypatch.setenv("AUCTION_ADMIN_KEY", KEY)
    return create_app().test_client()


# ---- 키 설정 시: 무인증 차단 ----

def test_watchlist_endpoints_require_admin(guarded):
    assert guarded.get("/api/watchlist").status_code == 403
    assert guarded.get("/watchlist").status_code == 403
    assert guarded.post(f"/api/watchlist/{SAMPLE_CASE}").status_code == 403
    assert guarded.delete(f"/api/watchlist/{SAMPLE_CASE}").status_code == 403
    assert guarded.post(f"/watchlist/toggle/{SAMPLE_CASE}").status_code == 403


def test_public_pages_stay_open(guarded):
    assert guarded.get("/health").status_code == 200
    assert guarded.get("/api/listings?limit=1").status_code == 200


def test_admin_login_wrong_key_403(guarded):
    assert guarded.get("/admin/login?key=틀린키").status_code == 403


def test_admin_login_sets_cookie_and_unlocks(guarded):
    r = guarded.get(f"/admin/login?key={KEY}")
    assert r.status_code == 302
    # test_client 는 set-cookie 를 세션에 유지 — 이후 요청은 운영자로 통과해야 한다
    assert guarded.get("/api/watchlist").status_code == 200
    r = guarded.post(f"/api/watchlist/{SAMPLE_CASE}")
    assert r.status_code == 200 and r.get_json()["watching"] is True


def test_header_key_also_accepted(guarded):
    r = guarded.get("/api/watchlist", headers={"X-Admin-Key": KEY})
    assert r.status_code == 200


def test_find_live_blocked_for_anonymous(guarded):
    # 큐레이션에 없는 표준형 사건번호 + 법원 지정 → 라이브 분기 직전에 차단돼야 한다
    r = guarded.get("/find?q=2020타경99999&court=서울중앙지방법원")
    assert r.status_code == 200
    assert "운영자 전용" in r.get_data(as_text=True)


# ---- 키 미설정(로컬) 시: 종전 계약 유지 ----

def test_no_key_local_mode_stays_open(tmp_path, monkeypatch):
    monkeypatch.setenv("AUCTION_WATCHLIST", str(tmp_path / "wl.json"))
    monkeypatch.setenv("AUCTION_SNAPSHOT", str(tmp_path / "snap.json"))
    monkeypatch.delenv("AUCTION_ADMIN_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    c = create_app().test_client()
    assert c.get("/api/watchlist").status_code == 200


# ---- rate limit ----

def test_rate_limit_429_after_burst(tmp_path, monkeypatch):
    monkeypatch.setenv("AUCTION_WATCHLIST", str(tmp_path / "wl.json"))
    monkeypatch.setenv("AUCTION_SNAPSHOT", str(tmp_path / "snap.json"))
    monkeypatch.setenv("AUCTION_ADMIN_KEY", KEY)   # 비운영자 요청이 제한 대상
    monkeypatch.setenv("AUCTION_RL_MAX", "5")
    c = create_app().test_client()
    codes = [c.get("/api/listings?limit=1").status_code for _ in range(7)]
    assert codes[:5] == [200] * 5
    assert codes[5] == 429 and codes[6] == 429


def test_rate_limit_exempts_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("AUCTION_WATCHLIST", str(tmp_path / "wl.json"))
    monkeypatch.setenv("AUCTION_SNAPSHOT", str(tmp_path / "snap.json"))
    monkeypatch.setenv("AUCTION_ADMIN_KEY", KEY)
    monkeypatch.setenv("AUCTION_RL_MAX", "3")
    c = create_app().test_client()
    codes = [c.get("/api/listings?limit=1", headers={"X-Admin-Key": KEY}).status_code
             for _ in range(6)]
    assert codes == [200] * 6
