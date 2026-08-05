"""migrate_photos_to_r2 — 원본 URL 검증(SSRF 방어) 회귀 테스트.

`_move_one` 은 DB에서 읽은 photo_url 을 그대로 내려받는다. 종전 필터는 `like '*supabase.co*'`
(부분 문자열)라 호스트 검증이 아니었다 — 오염된 값이 있으면 내부망으로 GET 을 날리고 그 응답을
**인증 없는 공개 R2 버킷에 업로드**하는 체인이 된다. 접두사 정확일치로 막았고, 그 거부 로직이
리팩터링 중 조용히 사라지지 않도록 여기서 못 박는다(2026-08-05 보안 리뷰 권고).
"""
from __future__ import annotations

import pytest

from deploy import migrate_photos_to_r2 as m

SB = "https://proj.supabase.co"
HEX40 = "a" * 40


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", SB)
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_x")
    monkeypatch.setenv("SUPABASE_PHOTOS_BUCKET", "auction-photos")


def _row(url):
    return {"court": "c", "case_no": "2025타경1", "item_no": "1", "seq": 0, "photo_url": url}


def _no_network(monkeypatch):
    """네트워크가 열리면 테스트가 조용히 통과할 수 있다 — 호출 자체를 실패로 만든다."""
    def _boom(*a, **k):
        raise AssertionError("거부돼야 할 URL인데 네트워크 요청이 나갔다")
    monkeypatch.setattr(m.photo_store, "session", lambda: type("S", (), {"get": _boom})())


def test_source_prefix_is_full_authority():
    assert m._source_prefix() == f"{SB}/storage/v1/object/public/auction-photos/"


@pytest.mark.parametrize("bad", [
    f"http://169.254.169.254/latest/meta-data/supabase.co/{HEX40}.jpg",  # 메타데이터 SSRF
    f"http://127.0.0.1:8000/supabase.co/{HEX40}.jpg",                    # 루프백
    f"https://evil.example.com/supabase.co/{HEX40}.jpg",                 # 부분문자열 통과 시도
    f"https://proj.supabase.co.evil.com/storage/v1/object/public/auction-photos/{HEX40}.jpg",
    f"{SB}/storage/v1/object/authenticated/auction-photos/{HEX40}.jpg",  # 경로 다름
])
def test_rejects_urls_outside_source_prefix(monkeypatch, bad):
    _no_network(monkeypatch)
    row, new_url = m._move_one(_row(bad))
    assert new_url is None
    assert row["photo_url"] == bad


def test_rejects_non_key_shaped_filename(monkeypatch):
    """접두사는 맞아도 파일명이 sha1.jpg 형태가 아니면 업로드하지 않는다."""
    _no_network(monkeypatch)
    _, new_url = m._move_one(_row(f"{m._source_prefix()}../../etc/passwd"))
    assert new_url is None


def test_accepts_legitimate_source_url(monkeypatch):
    """정상 URL은 통과해야 한다 — 거부 로직이 과하게 잡으면 이전이 통째로 멈춘다."""
    good = f"{m._source_prefix()}{HEX40}.jpg"
    calls = {}

    class _Resp:
        status_code = 200
        content = b"jpegbytes"

    class _S:
        def get(self, url, timeout=None, allow_redirects=None):
            calls.update(url=url, allow_redirects=allow_redirects)
            return _Resp()

    monkeypatch.setattr(m.photo_store, "session", _S)
    monkeypatch.setattr(m.photo_store, "upload_bytes", lambda b, k: f"https://pub-x.r2.dev/{k}")
    _, new_url = m._move_one(_row(good))
    assert new_url == f"https://pub-x.r2.dev/{HEX40}.jpg"
    assert calls["url"] == good
    assert calls["allow_redirects"] is False, "리다이렉트를 따라가면 접두사 검증이 무의미해진다"
