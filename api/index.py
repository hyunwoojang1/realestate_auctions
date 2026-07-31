"""Vercel 서버리스 진입점 — Flask WSGI 앱(create_app)을 노출.

Vercel @vercel/python 런타임이 이 모듈의 `app`(WSGI 콜러블)을 감지해 서빙한다.
vercel.json 의 rewrite 가 모든 경로를 이 함수로 보낸다.

데이터: 환경변수 SUPABASE_URL/SUPABASE_SECRET_KEY 가 설정되면 web 서빙이 Supabase REST 에서
읽는다(AUCTION_DB 미설정 시). 로컬 상태파일(관심목록/스냅샷)은 Vercel 읽기전용 FS 를 피해 /tmp 로.
"""
import os
import sys
from pathlib import Path

# api/ 하위에서 루트의 src 패키지를 import 할 수 있도록 경로 추가.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Vercel 함수 파일시스템은 /tmp 만 쓰기 가능 → 관심목록/스냅샷 경로를 /tmp 로 우회(500 방지).
# (클라우드에선 인스턴스마다 휘발 — 영구 관심목록은 추후 Supabase 백업으로 승격.)
os.environ.setdefault("AUCTION_WATCHLIST", "/tmp/auction_watchlist.json")
os.environ.setdefault("AUCTION_SNAPSHOT", "/tmp/auction_snapshot.json")

from src.web import create_app  # noqa: E402


class _DecodePathInfo:
    """Vercel 서버리스가 넘기는 퍼센트-인코딩된 PATH_INFO 를 WSGI 규격대로 디코딩.

    진단으로 확인: Vercel 은 PATH_INFO 를 URL-디코딩하지 않고 원본 그대로 넣는다
    (예: /property/2025%ED%83%80%EA%B2%BD669). WSGI 규격상 PATH_INFO 는 디코딩된 경로여야
    하므로 Werkzeug 가 리터럴 '%ED%83%80' 를 매칭하려다 실패해 한글 사건번호 상세가 404 났다
    (퍼센트-인코딩이 없는 목록/통계는 정상이었던 이유).

    unquote_to_bytes 로 %XX 를 바이트로 되돌린 뒤 latin-1 로 디코딩해, Werkzeug 의
    wsgi_decoding_dance(latin-1→utf-8)가 정상적으로 한글을 복원하도록 만든다.
    '%' 가 없는 ASCII 경로는 건드리지 않는다 — 회귀 없음.
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        from urllib.parse import unquote_to_bytes  # noqa: PLC0415
        path = environ.get("PATH_INFO", "")
        if "%" in path:
            environ["PATH_INFO"] = unquote_to_bytes(path).decode("latin-1")
        return self.wsgi_app(environ, start_response)


_REWRITE_PREFIX = "/api/index"


class _StripRewritePrefix:
    """`vercel.json` rewrite 가 실어 보낸 `/api/index` 접두를 떼어 원래 경로를 복원한다.

    (2026-07-31 실사고) Vercel 빌드 CLI 58.4.0 부터 "Internal rewrites in backend framework
    projects now route requests using the rewritten destination path" 로 동작이 바뀌었다.
    종전 `{"source":"/(.*)","destination":"/api/index"}` 는 목적지가 **고정 문자열**이라
    Flask 가 원래 경로 대신 늘 `/api/index` 를 받아 **모든 요청이 404** 가 됐다(실측).

    그렇다고 rewrite 를 지우면 `/health`·`/` 는 살아나지만 **퍼센트 인코딩 경로가 404** 난다
    (실측: 프로덕션 코드 그대로 rewrite 만 제거한 통제 배포에서 한글 사건번호 상세만 404).
    즉 새 CLI 에서는 '있어도 안 되고 없어도 안 되는' 상태라, 목적지에 원래 경로를 실어
    (`/api/index/$1`) 보내고 여기서 되돌리는 것이 유일하게 양쪽을 만족한다.

    원래 경로를 담은 헤더는 없다 — 프리뷰 배포에서 WSGI environ 을 통째로 덤프해 확인했다
    (x-vercel-original-path 류 없음). 그래서 복원은 반드시 경로 자체로 해야 한다.

    ⚠️ 조사 중 함정: 프리뷰 배포에는 **Production 환경변수가 붙지 않아** Supabase 를 못 읽고
    상세가 404 난다. 이걸 경로 문제로 오해하면(실제로 그랬다) 없는 버그를 쫓게 된다 —
    프리뷰에서 라우팅을 판정할 땐 `/health`·`/stats` 같은 **데이터 없이도 200 인 경로**로
    보고, 상세 404 는 환경변수 부재로 먼저 의심할 것.
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path == _REWRITE_PREFIX:
            environ["PATH_INFO"] = "/"
        elif path.startswith(_REWRITE_PREFIX + "/"):
            environ["PATH_INFO"] = path[len(_REWRITE_PREFIX):]
        return self.wsgi_app(environ, start_response)


app = create_app()
# 바깥부터: rewrite 접두 제거 → 퍼센트 디코딩 → flask.
# 순서가 중요하다 — 접두를 먼저 떼야 디코딩이 원래 경로에만 적용된다.
# 실측 검증(프리뷰): /__echo/2025%ED%83%80%EA%B2%BD463 → 라우트 인자 '2025타경463'.
app.wsgi_app = _DecodePathInfo(app.wsgi_app)
app.wsgi_app = _StripRewritePrefix(app.wsgi_app)
