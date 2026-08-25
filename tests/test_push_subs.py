"""PWA Web Push 구독(views/push + push_subs) 계약 테스트 — 네트워크 0.

계약: ①구독 JSON 검증(https endpoint + p256dh/auth 필수) ②저장 실패는 503(침묵 금지)
③표준 필드만 저장(임의 페이로드 저장 방지) ④sw.js 에 push 핸들러·버전 v4(캐시 세대 교체)
⑤base.html 버튼은 hidden 기본(standalone 에서 JS 가 해제).
"""
from src import push_subs
from src.views.push import _valid_sub


def _client(monkeypatch):
    from src.web import create_app
    return create_app().test_client()


GOOD = {"endpoint": "https://web.push.apple.com/abc123",
        "keys": {"p256dh": "pk", "auth": "au"}}


def test_valid_sub_boundaries():
    assert _valid_sub(GOOD) is True
    assert _valid_sub({**GOOD, "endpoint": "http://insecure"}) is False
    assert _valid_sub({"endpoint": GOOD["endpoint"], "keys": {}}) is False
    assert _valid_sub(None) is False
    assert _valid_sub({**GOOD, "endpoint": "https://" + "x" * 2000}) is False


def test_subscribe_stores_clean_fields_only(monkeypatch):
    saved = {}
    monkeypatch.setattr(push_subs, "enabled", lambda: True)
    monkeypatch.setattr(push_subs, "save_sub", lambda s: saved.update(s) or True)
    c = _client(monkeypatch)
    r = c.post("/api/push/subscribe",
               json={**GOOD, "expirationTime": None, "evil": "<script>"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert saved == GOOD          # 표준 필드만 — 'evil'·expirationTime 미저장


def test_subscribe_bad_payload_400(monkeypatch):
    monkeypatch.setattr(push_subs, "enabled", lambda: True)
    c = _client(monkeypatch)
    assert c.post("/api/push/subscribe", json={"endpoint": "nope"}).status_code == 400


def test_subscribe_store_failure_503_not_silent(monkeypatch):
    monkeypatch.setattr(push_subs, "enabled", lambda: True)
    monkeypatch.setattr(push_subs, "save_sub", lambda s: False)
    c = _client(monkeypatch)
    r = c.post("/api/push/subscribe", json=GOOD)
    assert r.status_code == 503 and r.get_json()["ok"] is False


def test_unsubscribe_contract(monkeypatch):
    calls = []
    monkeypatch.setattr(push_subs, "enabled", lambda: True)
    monkeypatch.setattr(push_subs, "delete_sub", lambda ep: calls.append(ep) or True)
    c = _client(monkeypatch)
    r = c.post("/api/push/unsubscribe", json={"endpoint": GOOD["endpoint"]})
    assert r.status_code == 200 and calls == [GOOD["endpoint"]]


def test_sw_has_push_handlers_and_new_version():
    sw = open("static/sw.js", encoding="utf-8").read()
    assert "'v4'" in sw                      # 핸들러 추가 = 캐시 세대 교체(구 SW 대체)
    assert "addEventListener('push'" in sw
    assert "addEventListener('notificationclick'" in sw
    assert "showNotification" in sw          # iOS: push 마다 표시 필수(침묵 3회 = 강제 해지)


def test_base_html_button_and_key_injection():
    html = open("templates/base.html", encoding="utf-8").read()
    assert 'id="pushBtn"' in html and "hidden" in html
    assert "{{ vapid_public_key }}" in html
    assert "requestPermission" in html
