"""관심물건 웹 UI(B2) 테스트 — API·페이지·토글·변동 이벤트. 라이브 호출 0.

워치리스트/스냅샷 경로는 AUCTION_WATCHLIST/AUCTION_SNAPSHOT env로 tmp에 격리
(운영 data/watchlist.json 오염 금지).
"""
import json

import pytest

from src.web import create_app

SAMPLE_CASE = "2024타경51234"   # 샘플 fixture의 상계주공


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUCTION_WATCHLIST", str(tmp_path / "watchlist.json"))
    monkeypatch.setenv("AUCTION_SNAPSHOT", str(tmp_path / "snapshot.json"))
    return create_app().test_client()


# ---- JSON API ----

def test_api_roundtrip_add_list_remove(client):
    assert client.get("/api/watchlist").get_json() == []
    r = client.post(f"/api/watchlist/{SAMPLE_CASE}")
    assert r.status_code == 200 and r.get_json()["watching"] is True
    assert client.get("/api/watchlist").get_json() == [SAMPLE_CASE]
    r = client.delete(f"/api/watchlist/{SAMPLE_CASE}")
    assert r.status_code == 200 and r.get_json()["watching"] is False
    assert client.get("/api/watchlist").get_json() == []


def test_api_add_unknown_case_404(client):
    assert client.post("/api/watchlist/없는사건번호").status_code == 404


# ---- 페이지 ----

def test_watchlist_page_empty_state(client):
    r = client.get("/watchlist")
    assert r.status_code == 200
    assert "관심물건" in r.get_data(as_text=True)


def test_watchlist_page_shows_watched_item(client):
    client.post(f"/api/watchlist/{SAMPLE_CASE}")
    body = client.get("/watchlist").get_data(as_text=True)
    assert "상계주공" in body
    assert f"/property/{SAMPLE_CASE}" in body


# ---- 토글(HTML 폼) ----

def test_toggle_adds_then_removes(client):
    r = client.post(f"/watchlist/toggle/{SAMPLE_CASE}")
    assert r.status_code == 302
    assert client.get("/api/watchlist").get_json() == [SAMPLE_CASE]
    client.post(f"/watchlist/toggle/{SAMPLE_CASE}")
    assert client.get("/api/watchlist").get_json() == []


def test_toggle_external_referrer_not_followed(client):
    """open redirect 방지 — 외부 referrer면 /watchlist로."""
    r = client.post(f"/watchlist/toggle/{SAMPLE_CASE}",
                    headers={"Referer": "https://evil.example/phish"})
    assert r.status_code == 302
    assert r.headers["Location"] in ("/watchlist", "http://localhost/watchlist")


def test_toggle_unknown_case_404(client):
    assert client.post("/watchlist/toggle/없는사건번호").status_code == 404


# ---- 변동 이벤트 (직전 스냅샷 대비) ----

def test_watchlist_page_shows_change_events(client, tmp_path):
    client.post(f"/api/watchlist/{SAMPLE_CASE}")
    # 직전 스냅샷: 스코어를 인위적으로 낮춰 '스코어 상승' 이벤트 유도
    snap = {SAMPLE_CASE: {"arb_score": 1.0, "min_bid_price": 10**12, "apt_name": "상계주공"}}
    (tmp_path / "snapshot.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    body = client.get("/watchlist").get_data(as_text=True)
    assert "스코어 상승" in body or "차익 임계 돌파" in body
    assert "최저가 하락" in body    # 1조 → 실제 최저가


# ---- B9: 스냅샷 '없음' vs '빈 스냅샷 정상 저장' 구분 ----

def test_empty_snapshot_not_reported_as_missing(client, tmp_path):
    # 매물 0건 새로고침으로 빈 스냅샷 {}이 정상 저장된 경우 → "스냅샷 없음"이 아니라 "변동 없음"
    (tmp_path / "snapshot.json").write_text("{}", encoding="utf-8")
    body = client.get("/watchlist").get_data(as_text=True)
    assert "이전 스냅샷이 없습니다" not in body     # 빈 스냅샷은 '없음'이 아님
    assert "변동 없음" in body


def test_absent_snapshot_reported_as_missing(client):
    # 스냅샷 파일 자체가 없으면 여전히 "스냅샷 없음" 안내(회귀 방지)
    body = client.get("/watchlist").get_data(as_text=True)
    assert "이전 스냅샷이 없습니다" in body


# ---- B8: 파일 손상 침묵실패 방지 + 원자적 쓰기 ----

def test_load_watchlist_corrupt_falls_back_empty(tmp_path):
    from src import watchlist as wl
    p = tmp_path / "wl.json"
    p.write_text("{깨진 json", encoding="utf-8")
    assert wl.load_watchlist(p) == set()           # 500 대신 빈 폴백
    val, corrupt = wl.load_watchlist_status(p)
    assert val == set() and corrupt is True         # 손상 사실을 신호


def test_load_snapshot_corrupt_status(tmp_path):
    from src import watchlist as wl
    p = tmp_path / "snap.json"
    p.write_text("not json at all", encoding="utf-8")
    assert wl.load_snapshot(p) == {}
    val, corrupt = wl.load_snapshot_status(p)
    assert val == {} and corrupt is True


def test_missing_file_is_not_corrupt(tmp_path):
    from src import watchlist as wl
    _, corrupt = wl.load_watchlist_status(tmp_path / "does_not_exist.json")
    assert corrupt is False                          # 없음 != 손상


def test_add_watch_atomic_no_tmp_leftover(tmp_path):
    from src import watchlist as wl
    p = tmp_path / "wl.json"
    wl.add_watch("2024타경1", p)
    assert json.loads(p.read_text(encoding="utf-8")) == ["2024타경1"]  # 유효 JSON
    leftover = [f.name for f in tmp_path.iterdir() if f.name != "wl.json"]
    assert leftover == []                            # tmp 잔여물 없음


def test_watchlist_page_survives_corrupt_file_with_banner(tmp_path, monkeypatch):
    monkeypatch.setenv("AUCTION_WATCHLIST", str(tmp_path / "watchlist.json"))
    monkeypatch.setenv("AUCTION_SNAPSHOT", str(tmp_path / "snapshot.json"))
    (tmp_path / "watchlist.json").write_text("{corrupt", encoding="utf-8")
    c = create_app().test_client()
    r = c.get("/watchlist")
    assert r.status_code == 200                       # 500 아님
    assert "손상" in r.get_data(as_text=True)         # 원인 배너 노출
