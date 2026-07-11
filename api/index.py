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


app = create_app()
app.wsgi_app = _DecodePathInfo(app.wsgi_app)
