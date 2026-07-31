"""Vercel 서버리스 진입점(api/index.py)의 WSGI 미들웨어 — 2026-07-31 프로덕션 장애 회귀 가드.

## 무엇이 터졌나
Vercel 빌드 CLI 58.4.0 부터 "Internal rewrites in backend framework projects now route
requests using the rewritten destination path" 로 동작이 바뀌었다. 종전 rewrite 는
`{"source":"/(.*)","destination":"/api/index"}` 로 **목적지가 고정 문자열**이라, Flask 가
원래 경로 대신 늘 `/api/index` 를 받게 됐다 → **배포 직후 전 경로 404**(실측: /health·/ 둘 다).
직전 배포는 200 이었으므로 코드가 아니라 플랫폼 동작 변경이었다.

`vercel.json` 을 `/api/index/$1` 로 바꿔 원래 경로를 목적지에 실어 보내고, 여기서 접두를
떼어 복원한다. 원래 경로를 담은 헤더는 없다(프리뷰에서 WSGI environ 전체를 덤프해 확인).

## 이 테스트가 지키는 것
설정과 코드가 **짝**이라는 사실이다. vercel.json 의 destination 을 되돌리거나 미들웨어를
빼면 프로덕션이 전면 404 가 되는데, 그건 배포 전엔 드러나지 않는다.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _strip():
    """미들웨어만 떼어 쓴다 — create_app() 없이 순수 경로 변환만 검증."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_api_index", ROOT / "api" / "index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._StripRewritePrefix, mod._DecodePathInfo


def _run(middleware_cls, path):
    """미들웨어를 통과한 뒤의 PATH_INFO 를 돌려준다."""
    seen = {}

    def inner(environ, _start_response):
        seen["path"] = environ["PATH_INFO"]
        return []

    middleware_cls(inner)({"PATH_INFO": path}, lambda *a, **k: None)
    return seen["path"]


def test_vercel_json_destination_carries_original_path():
    """rewrite 목적지가 `$1` 을 실어야 한다 — 고정 문자열로 되돌리면 전 경로 404."""
    cfg = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    dests = [r["destination"] for r in cfg["rewrites"]]
    assert dests == ["/api/index/$1"], (
        "목적지가 고정 문자열이면 Vercel 이 Flask 에 '/api/index' 를 넘겨 모든 요청이 404 난다"
    )


@pytest.mark.parametrize(("given", "want"), [
    ("/api/index/health", "/health"),
    ("/api/index/", "/"),
    ("/api/index", "/"),                                  # 루트 요청은 접두만 남는다
    ("/api/index/sold", "/sold"),
    ("/api/index/api/listings.geojson", "/api/listings.geojson"),   # /api/ 로 시작하는 진짜 경로
    ("/api/index/property/2025%ED%83%80%EA%B2%BD463",
     "/property/2025%ED%83%80%EA%B2%BD463"),              # 퍼센트 인코딩은 여기서 건드리지 않는다
])
def test_strip_rewrite_prefix(given, want):
    strip, _ = _strip()
    assert _run(strip, given) == want


def test_strip_leaves_unprefixed_paths_alone():
    """접두가 없으면(로컬 서버·직접 호출) 그대로 둔다 — 로컬 실행을 깨지 않는다."""
    strip, _ = _strip()
    assert _run(strip, "/health") == "/health"
    assert _run(strip, "/api/index-something") == "/api/index-something"   # 접두 유사 문자열


def test_decode_runs_after_strip_and_yields_korean_case_no():
    """접두 제거 → 퍼센트 디코딩 순서로 통과하면 Flask 가 한글 사건번호를 매칭할 수 있다.

    실측(프리뷰 /__echo): `/api/index/property/2025%ED%83%80%EA%B2%BD463` →
    라우트 인자 '2025타경463'. 여기서는 그 앞단(디코딩 결과 바이트)까지만 확인한다.
    """
    strip, decode = _strip()
    stripped = _run(strip, "/api/index/property/2025%ED%83%80%EA%B2%BD463")
    decoded = _run(decode, stripped)
    # Werkzeug 가 latin-1 → utf-8 로 되돌릴 수 있는 형태여야 한다.
    assert decoded.encode("latin-1").decode("utf-8") == "/property/2025타경463"
