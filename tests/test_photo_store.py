"""photo_store — R2(SigV4) / Supabase 듀얼 백엔드 검증.

핵심은 첫 테스트다: 직접 구현한 SigV4를 **AWS 공식 테스트 스위트의 정답(get-vanilla)** 과
대조한다. 자기 구현을 자기 출력으로 검증하면 순환이라, 외부 정답으로 못을 박는다.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from src import photo_store

R2_ENV = {
    "R2_ACCOUNT_ID": "acct123",
    "R2_ACCESS_KEY_ID": "AKIDTEST",
    "R2_SECRET_ACCESS_KEY": "secret",
    "R2_BUCKET": "auction-photos",
    "R2_PUBLIC_BASE": "https://pub-abc.r2.dev",
}
SB_ENV = {
    "SUPABASE_URL": "https://proj.supabase.co",
    "SUPABASE_SECRET_KEY": "sb_secret_x",
}
ALL_KEYS = [*R2_ENV, *SB_ENV, "SUPABASE_PHOTOS_BUCKET", "AUCTION_PHOTO_BACKEND"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """실제 .env 가 로드된 환경에서도 테스트가 흔들리지 않게 관련 변수를 전부 걷어낸다."""
    for k in ALL_KEYS:
        monkeypatch.delenv(k, raising=False)


def _set(monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)


# ------------------------------------------------------------------ SigV4 정합성

def test_sigv4_matches_aws_official_get_vanilla_vector():
    """aws-sig-v4-test-suite / get-vanilla 의 정답 Authorization 헤더와 바이트 단위로 일치."""
    h = photo_store.sigv4_headers(
        "GET", "example.amazonaws.com", "/", b"",
        akid="AKIDEXAMPLE", secret="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        now=datetime(2015, 8, 30, 12, 36, 0, tzinfo=UTC),
        region="us-east-1", service="service", content_sha_header=False)
    assert h["Authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
        "SignedHeaders=host;x-amz-date, "
        "Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31")


def test_sigv4_put_signs_content_type_and_payload_hash():
    """R2 실사용 경로(PUT): content-type·payload 해시가 SignedHeaders 에 포함돼야 한다."""
    h = photo_store.sigv4_headers("PUT", "acct.r2.cloudflarestorage.com", "/b/k.jpg",
                                  b"jpegbytes", akid="A", secret="S", content_type="image/jpeg")
    assert "SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date" in h["Authorization"]
    # 빈 페이로드 해시가 아니라 실제 본문 해시가 실려야 한다(본문 변조 방지).
    assert h["x-amz-content-sha256"] != photo_store._EMPTY_SHA256
    assert "host" not in h  # host 는 requests 가 붙인다 — 중복 전송 금지


def test_sigv4_signature_changes_with_payload():
    kw = dict(akid="A", secret="S", content_type="image/jpeg",
              now=datetime(2026, 8, 5, 0, 0, 0, tzinfo=UTC))
    a = photo_store.sigv4_headers("PUT", "h", "/b/k.jpg", b"one", **kw)
    b = photo_store.sigv4_headers("PUT", "h", "/b/k.jpg", b"two", **kw)
    assert a["Authorization"] != b["Authorization"]


# ------------------------------------------------------------------ 백엔드 선택

def test_backend_prefers_r2_when_configured(monkeypatch):
    _set(monkeypatch, {**SB_ENV, **R2_ENV})
    assert photo_store.backend() == "r2"
    assert photo_store.enabled()


def test_supabase_is_never_auto_selected(monkeypatch):
    """Supabase 사진 버킷은 **자동 선택 금지**.

    R2 키가 하나라도 빠졌을 때 자동으로 Supabase 로 흘러가면 업로드는 계속 성공하므로
    (자격증명이 .env 에 함께 산다) 크롤이 exit 0 으로 끝나고 아무도 못 알아챈다.
    '미설정'으로 떨어져야 상위의 안전망(사진 생략 + 0장이면 exit 3)이 작동한다.
    """
    _set(monkeypatch, SB_ENV)
    assert photo_store.backend() == ""
    assert not photo_store.enabled()


def test_backend_empty_when_nothing_configured():
    assert photo_store.backend() == ""
    assert not photo_store.enabled()


def test_missing_public_base_does_not_select_r2(monkeypatch):
    """R2_PUBLIC_BASE 누락 = 업로드는 되는데 URL을 못 만드는 침묵실패 → R2도 Supabase도 아니다."""
    _set(monkeypatch, {**SB_ENV, **{k: v for k, v in R2_ENV.items() if k != "R2_PUBLIC_BASE"}})
    assert photo_store.backend() == ""
    assert not photo_store.enabled()


def test_supabase_still_usable_when_explicitly_forced(monkeypatch):
    """레거시 경로를 완전히 없애진 않는다 — 명시 지정하면 쓸 수 있어야 한다(탈출구)."""
    _set(monkeypatch, {**SB_ENV, "AUCTION_PHOTO_BACKEND": "supabase"})
    assert photo_store.backend() == "supabase"
    assert photo_store.enabled()


def test_forced_backend_overrides(monkeypatch):
    _set(monkeypatch, {**SB_ENV, **R2_ENV, "AUCTION_PHOTO_BACKEND": "supabase"})
    assert photo_store.backend() == "supabase"


# ------------------------------------------------------------------ 경로/URL

def test_object_path_is_stable_across_backends(monkeypatch):
    _set(monkeypatch, R2_ENV)
    a = photo_store.object_path("서울중앙", "2024타경1234", "1", 0)
    _set(monkeypatch, SB_ENV)
    monkeypatch.delenv("R2_ACCOUNT_ID")
    b = photo_store.object_path("서울중앙", "2024타경1234", "1", 0)
    assert a == b, "키가 백엔드마다 달라지면 이전이 멱등하지 않다"
    assert a.endswith(".jpg")


def test_public_url_per_backend(monkeypatch):
    _set(monkeypatch, R2_ENV)
    assert photo_store.public_url("abc.jpg") == "https://pub-abc.r2.dev/abc.jpg"
    monkeypatch.delenv("R2_ACCOUNT_ID")
    _set(monkeypatch, SB_ENV)
    assert photo_store.public_url("abc.jpg") == (
        "https://proj.supabase.co/storage/v1/object/public/auction-photos/abc.jpg")


def test_public_base_trailing_slash_does_not_double(monkeypatch):
    _set(monkeypatch, {**R2_ENV, "R2_PUBLIC_BASE": "https://pub-abc.r2.dev/"})
    assert photo_store.public_url("abc.jpg") == "https://pub-abc.r2.dev/abc.jpg"


# ------------------------------------------------------------------ 미러 스테일 가드

SB_PHOTO = "https://proj.supabase.co/storage/v1/object/public/auction-photos/a.jpg"
R2_PHOTO = "https://pub-abc.r2.dev/a.jpg"


def test_split_stale_rows_blocks_supabase_urls():
    """되돌릴 수 없는 사고(버킷 삭제 후 사진 깨짐)를 막는 가드 — 회귀 감지 대상."""
    rows = [{"photo_url": R2_PHOTO, "seq": 0}, {"photo_url": SB_PHOTO, "seq": 1}]
    clean, blocked = photo_store.split_stale_rows(rows)
    assert [r["seq"] for r in blocked] == [1], "스테일 supabase URL 이 미러로 새어나간다"
    assert [r["seq"] for r in clean] == [0]


def test_split_stale_rows_blocks_empty_url():
    """URL 이 빈 행을 올리면 클라우드의 멀쩡한 R2 URL 이 빈 값으로 덮인다(사진 사라짐)."""
    rows = [{"photo_url": R2_PHOTO, "seq": 0}, {"photo_url": "", "seq": 1},
            {"photo_url": None, "seq": 2}]
    clean, blocked = photo_store.split_stale_rows(rows)
    assert [r["seq"] for r in blocked] == [1, 2]
    assert [r["seq"] for r in clean] == [0]


def test_split_stale_rows_strips_base64_from_mirror():
    """클라우드 테이블에 thumb_b64 컬럼이 실재한다 — 그대로 올리면 DB 용량 사고가 재현된다.

    AUCTION_ALLOW_BASE64_PHOTOS=1 로 만들어진 행이 미러를 타고 Supabase Postgres 로
    흘러가는 경로를 막는다(2026-08-05 리뷰에서 발견된 CRITICAL).
    """
    rows = [{"photo_url": R2_PHOTO, "seq": 0, "thumb_b64": "AAAA" * 5000},
            {"photo_url": "", "seq": 1, "thumb_b64": "BBBB" * 5000}]
    clean, blocked = photo_store.split_stale_rows(rows)
    assert [r["seq"] for r in blocked] == [1], "base64 전용 행(URL 없음)은 미러 대상이 아니다"
    assert clean[0]["thumb_b64"] == "", "base64 가 클라우드로 실려 나간다"
    assert clean[0]["photo_url"] == R2_PHOTO
    assert rows[0]["thumb_b64"] != "", "입력 dict 를 파괴적으로 수정하면 호출부가 오염된다"


def test_split_stale_rows_keeps_everything_when_clean():
    rows = [{"photo_url": R2_PHOTO} for _ in range(5)]
    clean, stale = photo_store.split_stale_rows(rows)
    assert len(clean) == 5 and stale == []


def test_split_stale_rows_empty():
    assert photo_store.split_stale_rows([]) == ([], [])


def test_upload_bytes_noop_when_disabled():
    assert photo_store.upload_bytes(b"x", "a.jpg") is None
    assert photo_store.upload_photo(b"x", "c", "2024타경1", "1", 0) is None


def test_r2_request_uses_path_style_uri(monkeypatch):
    """R2는 path-style(/{bucket}/{key}) — 서명 대상 URI와 실제 URL이 어긋나면 403이 난다."""
    _set(monkeypatch, R2_ENV)
    seen = {}

    class _Resp:
        status_code = 200
        text = ""

    class _FakeSession:
        def request(self, method, url, headers=None, data=None, timeout=None):
            seen.update(method=method, url=url, headers=headers)
            return _Resp()

    monkeypatch.setattr(photo_store, "session", _FakeSession)
    photo_store._r2_request("PUT", "deadbeef.jpg", payload=b"x", content_type="image/jpeg")
    assert seen["url"] == "https://acct123.r2.cloudflarestorage.com/auction-photos/deadbeef.jpg"
    assert seen["headers"]["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIDTEST/")


def test_session_is_pooled_and_reused(monkeypatch):
    """장당 TLS 핸드셰이크를 없애는 게 목적 — 같은 세션 객체가 재사용돼야 한다."""
    monkeypatch.setattr(photo_store, "_SESSION", None)
    monkeypatch.setattr(photo_store, "_POOL_SIZE", None)
    monkeypatch.setenv("PHOTO_POOL_SIZE", "12")
    a = photo_store.session()
    b = photo_store.session()
    assert a is b, "호출마다 새 세션이면 커넥션 재사용이 안 된다"
    assert a.get_adapter("https://example.com")._pool_maxsize == 12


def test_session_single_instance_under_concurrent_first_call(monkeypatch):
    """32 워커가 동시에 첫 호출해도 세션은 하나여야 한다.

    이 모듈의 존재 이유가 '커넥션 공유'인데, 락 없는 지연 초기화면 첫 요청 폭주에서 세션이
    여러 개 생겨 풀이 쪼개진다. 문서 주석이 아니라 테스트로 못 박는다.
    """
    monkeypatch.setattr(photo_store, "_SESSION", None)
    monkeypatch.setattr(photo_store, "_POOL_SIZE", None)
    n = 32
    barrier = threading.Barrier(n)

    def _first_call():
        barrier.wait(timeout=10)      # 전원이 동시에 진입하도록 강제
        return photo_store.session()

    with ThreadPoolExecutor(max_workers=n) as ex:
        sessions = list(ex.map(lambda _: _first_call(), range(n)))
    assert len({id(s) for s in sessions}) == 1, "동시 첫 호출에서 세션이 여러 개 생성됐다"


def test_configure_pool_applies_before_session_created(monkeypatch):
    monkeypatch.setattr(photo_store, "_SESSION", None)
    monkeypatch.setattr(photo_store, "_POOL_SIZE", None)
    monkeypatch.delenv("PHOTO_POOL_SIZE", raising=False)
    photo_store.configure_pool(64)
    assert photo_store.session().get_adapter("https://example.com")._pool_maxsize == 64


def test_configure_pool_after_session_is_ignored_not_silently_wrong(monkeypatch, caplog):
    """세션 생성 뒤 호출은 무시하되 **경고를 남긴다** — 조용히 다른 값이 먹은 척하면 안 된다."""
    monkeypatch.setattr(photo_store, "_SESSION", None)
    monkeypatch.setattr(photo_store, "_POOL_SIZE", None)
    monkeypatch.delenv("PHOTO_POOL_SIZE", raising=False)
    photo_store.configure_pool(20)
    s = photo_store.session()
    with caplog.at_level("WARNING"):
        photo_store.configure_pool(99)
    assert s.get_adapter("https://example.com")._pool_maxsize == 20
    assert any("configure_pool" in r.message for r in caplog.records)


# ------------------------------------------------------------------ 썸네일러 계약

def test_thumbnail_jpeg_returns_none_on_bad_input():
    """`exit 3` 게이트가 "실패 시 None" 계약에 통째로 의존하는데 테스트가 없었다(세트3 지적).

    Pillow 는 크롤 전용이라 CI 에 없을 수 있다 — 없으면 skip 한다(그 자체도 계약의 일부:
    Pillow 가 없으면 None 을 돌려주고 조용히 죽지 않는다).
    """
    from src import photo
    assert photo.thumbnail_jpeg("") is None
    assert photo.thumbnail_jpeg("!!!not-base64!!!") is None
    import base64
    assert photo.thumbnail_jpeg(base64.b64encode(b"not an image").decode()) is None


def test_thumbnail_jpeg_roundtrip_when_pillow_available():
    pytest.importorskip("PIL", reason="Pillow 는 크롤 전용(Vercel 225MB 한도로 서빙에서 제외)")
    import base64
    import io

    from PIL import Image

    from src import photo
    buf = io.BytesIO()
    Image.new("RGB", (1600, 1200), (10, 20, 30)).save(buf, format="JPEG")
    out = photo.thumbnail_jpeg(base64.b64encode(buf.getvalue()).decode())
    assert out and out[:2] == b"\xff\xd8", "JPEG 매직바이트가 아니다"
    assert Image.open(io.BytesIO(out)).width == photo.THUMB_MAX_W, "가로 상한이 적용되지 않았다"
