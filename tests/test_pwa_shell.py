"""PWA 앱 셸(서비스워커 + 외부 CSS) — 홈 로딩 개선 B·C단계의 계약 고정.

배경(실측 2026-07-23): Vercel 콜드 부팅 58초 + 서비스워커 부재로, 홈 화면 아이콘을 탭할
때마다 완전한 네트워크 로드를 기다렸다. 인라인 61KB CSS 는 매 응답 재전송(캐시 불가)이었다.

이 테스트가 깨지면: 라우트가 사라졌거나(SW 가 죽은 경로를 캐싱), CSS 가 인라인으로
되돌아갔거나(전송량 회귀), SW 등록이 빠진 것이다.
"""
import re
from datetime import UTC
from pathlib import Path

from src.web import create_app

ROOT = Path(__file__).resolve().parent.parent
SW = ROOT / "static" / "sw.js"
CSS = ROOT / "static" / "base.css"


def _client():
    return create_app().test_client()


# ── 서빙 라우트 ──

def test_sw_route_serves_javascript_nocache():
    r = _client().get("/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.mimetype
    # no-cache 여야 VERSION 올림(옛 캐시 청소)이 지체 없이 전파된다.
    assert "no-cache" in r.headers.get("Cache-Control", "")
    body = r.get_data(as_text=True)
    assert "NAV_PATHS" in body and "staleWhileRevalidate" in body


def test_base_css_immutable_only_for_current_hash():
    """(적대감사 F9) 영구 캐시는 현재 해시 v 에만 — 옛/무버전 URL 에 새 내용이 1년 고정되지 않게."""
    import hashlib
    client = _client()
    v = hashlib.md5(CSS.read_bytes()).hexdigest()[:8]
    ok = client.get(f"/base.css?v={v}")
    assert ok.status_code == 200 and ok.mimetype == "text/css"
    assert "immutable" in ok.headers.get("Cache-Control", "")
    body = ok.get_data(as_text=True)
    assert ":root" in body            # 실제 토큰 CSS 가 서빙된다
    assert ".sw-fresh" in body        # 갱신 필 스타일 포함
    for bad in ("/base.css", "/base.css?v=00000000"):
        r = client.get(bad)
        assert r.status_code == 200
        assert "immutable" not in r.headers.get("Cache-Control", ""), bad
        assert "no-cache" in r.headers.get("Cache-Control", ""), bad


def test_base_css_has_no_jinja():
    """정적 파일은 렌더링되지 않는다 — Jinja 구문이 섞이면 화면이 통째로 깨진다."""
    css = CSS.read_text(encoding="utf-8")
    assert "{{" not in css and "{%" not in css


# ── HTML 배선 ──

def test_home_links_external_css_with_hash_not_inline():
    body = _client().get("/").get_data(as_text=True)
    m = re.search(r'href="/base\.css\?v=([0-9a-f]+)"', body)
    assert m, "외부 CSS 링크가 없다 — 인라인으로 회귀?"
    assert len(m.group(1)) == 8       # md5 8자리 콘텐츠 해시
    # base CSS 대표 시그니처가 HTML 에 인라인으로 남아 있으면 이중 전송(회귀).
    assert ".gatebar{display:flex" not in body


def test_home_registers_service_worker_with_fresh_pill():
    body = _client().get("/").get_data(as_text=True)
    assert "serviceWorker" in body and "register('/sw.js')" in body
    assert 'id="swFresh"' in body     # '새 데이터 도착' 필(기본 hidden)
    assert "nav-fresh" in body        # SW 메시지 수신 배선


def test_css_version_changes_with_content(tmp_path, monkeypatch):
    """base.css 내용이 바뀌면 링크의 ?v= 도 바뀐다 — immutable 캐시가 안전한 이유."""
    import hashlib
    v_now = hashlib.md5(CSS.read_bytes()).hexdigest()[:8]
    body = _client().get("/").get_data(as_text=True)
    assert f"/base.css?v={v_now}" in body


# ── SW 스크립트 ↔ 실제 라우트 정합(드리프트 가드) ──

def _sw_array(name: str) -> list[str]:
    src = SW.read_text(encoding="utf-8")
    m = re.search(rf"const {name} = \[([^\]]*)\]", src)
    assert m, f"sw.js 에서 {name} 배열을 찾지 못함"
    return re.findall(r"'([^']+)'", m.group(1))


def test_sw_nav_paths_all_serve_200():
    """SW 가 캐싱하는 탭 경로가 전부 실존한다 — 죽은 경로를 캐싱하면 영구 404 셸이 남는다."""
    client = _client()
    paths = _sw_array("NAV_PATHS")
    assert len(paths) >= 8
    for p in paths:
        assert client.get(p).status_code == 200, f"{p} 가 200이 아님 — sw.js 와 라우트 불일치"


def test_sw_assets_all_serve_200():
    client = _client()
    assets = _sw_array("ASSETS")
    assert assets, "프리캐시 에셋이 비어 있음"
    for p in assets:
        assert client.get(p).status_code == 200, f"{p} 에셋 라우트 소실"


def test_sw_does_not_touch_api_or_detail():
    """SW 개입 범위가 탭 페이지·에셋으로 한정돼 있다 — 상세·API 는 최신성이 생명.

    검사 대상은 **캐싱 배열**이다(주석의 설명 문구는 무관 — 첫 구현 때 주석에 오탐했다).
    """
    cached = _sw_array("NAV_PATHS") + _sw_array("ASSETS")
    for p in cached:
        assert not p.startswith("/api"), f"API 경로가 캐싱 배열에: {p}"
        assert not p.startswith("/property"), f"물건 상세가 캐싱 배열에: {p}"
        assert p != "/export.csv", "CSV 내보내기가 캐싱 배열에"
    # 쿼리 붙은 내비게이션(필터 결과)은 제외하는 가드가 있어야 한다.
    assert "url.search" in SW.read_text(encoding="utf-8")


# ── v2 계약(적대감사 2026-07-23 확정 발견의 회귀 가드) ──
# 이 검사들은 문자열 수준이다 — SW 는 파이썬 테스트가 실행할 수 없어서, 각 수정이
# '코드에 존재하는가'를 고정하고 실동작은 Playwright 검증 스크립트가 맡는다.

def _sw_src() -> str:
    return SW.read_text(encoding="utf-8")


def test_sw_version_past_v1_and_old_cache_purge():
    """v1 의 '영구 동결' 캐시(clone 버그 시절)를 activate 가 반드시 청소해야 한다.

    버전 문자열을 리터럴로 고정하지 않는다 — 배포마다 올리는 값이라 고정하면 정상적인
    버전업이 테스트 실패로 나타난다(2026-07-27 v3 에서 실제로 걸렸다). 지켜야 할 불변식은
    'v1 이 아닐 것 + activate 가 옛 캐시를 지울 것' 두 가지다.
    """
    import re
    src = _sw_src()
    m = re.search(r"const VERSION = 'v(\d+)'", src)
    assert m, "VERSION 선언을 찾을 수 없음"
    assert int(m.group(1)) >= 2, "v1 캐시 폐기를 위해 버전은 2 이상이어야 한다"
    assert "caches.delete" in src            # activate 의 옛 캐시 삭제


def test_sw_clones_cached_before_respond():
    """(F1·CRITICAL) 비교용 clone 은 respondWith 가 body 를 소진하기 **전에** 떠야 한다.

    종전엔 fetch 완료 후 cached.clone() → TypeError → 빈 catch 가 삼켜 재검증 전체가
    죽고 캐시가 영구 동결됐다. cachedText 선확보 패턴이 있는지 + 소진 후 clone 이 없는지.
    """
    src = _sw_src()
    assert "var cachedText = cached ? cached.clone().text()" in src
    # 옛 버그 패턴(Promise.all 안에서 cached.clone())이 되돌아오면 안 된다.
    assert "Promise.all([cached.clone()" not in src


def test_sw_skips_caching_sample_fallback():
    """(F2·HIGH) X-Data-Source: sample* 폴백은 서빙만 하고 캐시하지 않는다."""
    src = _sw_src()
    assert "X-Data-Source" in src
    assert "indexOf('sample')" in src


def test_sw_purges_nav_cache_on_mutation():
    """(F3·CRITICAL) 비GET(관심 토글 POST)이 오면 탭 캐시를 퍼지 — PRG 스테일 방지."""
    src = _sw_src()
    m = re.search(r"req\.method !== 'GET'[\s\S]{0,400}", src)
    assert m, "비GET 분기가 없다"
    assert "caches.open(NAV_CACHE)" in m.group(0) and "c.delete" in m.group(0), \
        "비GET 분기에 NAV_CACHE 퍼지가 없다 — 토글 후 토글 전 화면이 서빙된다"


def test_sw_notifies_revalidation_failure():
    """(F4·HIGH) 재검증 실패를 침묵시키지 않는다 — failed 통지가 존재."""
    assert "{ failed: true }" in _sw_src()


def test_sw_strips_rendered_at_before_compare():
    """(F4) rendered-at 메타는 렌더마다 바뀐다 — 비교 전 제거해야 '변경' 오탐이 없다."""
    src = _sw_src()
    assert "stripVolatile" in src and "rendered-at" in src


def test_sw_notify_excludes_query_clients():
    """(LOW) 쿼리 페이지는 SW 무개입 — 알림 대상에서도 제외(개입 범위와 1:1)."""
    assert "u.search === ''" in _sw_src()


def test_home_has_rendered_at_meta_and_stale_pill():
    """base.html 정직성 배선 — rendered-at 메타(파싱 가능한 ISO) + 스테일/실패 문구."""
    from datetime import datetime
    body = _client().get("/").get_data(as_text=True)
    m = re.search(r'<meta name="rendered-at" content="([^"]+)"', body)
    assert m, "rendered-at 메타가 없다 — 캐시본 나이를 알 방법이 없다"
    dt = datetime.fromisoformat(m.group(1))
    assert abs((datetime.now(UTC) - dt).total_seconds()) < 60
    assert "저장된 화면" in body           # 스테일 표식 문구
    assert "갱신 확인 실패" in body         # 재검증 실패 문구


def test_health_exposes_rights_source():
    """(F7·HIGH) 권리 로드 실패가 헬스체크에 보인다 — 배지 전멸의 침묵 방지."""
    d = _client().get("/health").get_json()
    assert "rights_source" in d


def test_rights_load_failure_sets_flag_and_health(monkeypatch, tmp_path):
    """(F7) 로드 실패 시: 페이지는 살아있되(폴백) g 플래그·/health 로 실패가 드러난다."""
    import os

    from src import store, web

    def _boom(conn):
        raise RuntimeError("column listing_rights.new_col does not exist")

    monkeypatch.setattr(store, "fetch_all_rights", _boom)
    monkeypatch.setenv("AUCTION_DB", str(tmp_path / "empty.db"))
    app = web.create_app()
    try:
        c = app.test_client()
        r = c.get("/")
        assert r.status_code == 200            # 배지 없이도 페이지는 렌더(폴백)
        h = c.get("/health").get_json()
        assert h["rights_source"].startswith("failed"), h
        assert "new_col" in h["rights_source"]  # 원인 문자열이 실려 온다
    finally:
        web._rights_last_error["at"] = 0.0      # 모듈 상태 청소 — 타 테스트 오염 방지
        web._rights_last_error["msg"] = ""
        os.environ.pop("AUCTION_DB", None)
