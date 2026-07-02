"""프로덕션 서빙 진입점 — waitress WSGI 서버로 Flask 앱을 서빙 (B).

waitress 는 순수 파이썬 WSGI 서버라 Windows 올-로컬 환경에서 gunicorn 없이 동작한다.
Flask 내장 dev server(`flask run` / `app.run`)와 달리 debug/reloader 가 애초에 없으므로
프로덕션에서 debug=False·use_reloader=False 가 구조적으로 보장된다.

사용:
    python -m src.serve                      # 127.0.0.1:8000
    AUCTION_HOST=0.0.0.0 AUCTION_PORT=8080 python -m src.serve
    (scripts/start.ps1 이 AUCTION_DB·포트를 지정해 이 모듈을 호출)

env:
    AUCTION_DB       서빙할 라이브 적재 DB 경로(미설정 시 샘플 폴백; web._scored 참고)
    AUCTION_HOST     바인드 호스트 (기본 127.0.0.1 — 로컬 전용. 터널 노출 시에만 0.0.0.0)
    AUCTION_PORT     바인드 포트 (기본 8000)
    AUCTION_THREADS  waitress 워커 스레드 수 (기본 4)
"""
from __future__ import annotations

import logging
import os

from waitress import serve

from .web import create_app

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_THREADS = 4


def main() -> None:
    host = os.environ.get("AUCTION_HOST", DEFAULT_HOST)
    port = int(os.environ.get("AUCTION_PORT", str(DEFAULT_PORT)))
    threads = int(os.environ.get("AUCTION_THREADS", str(DEFAULT_THREADS)))

    app = create_app()
    # 방어적 확인: 프로덕션 서빙 경로에서 Flask debug 는 절대 켜지지 않아야 한다.
    app.debug = False

    db = os.environ.get("AUCTION_DB", "(미설정 → 샘플 폴백)")
    logger.info("waitress 서빙 시작: http://%s:%s (threads=%s, AUCTION_DB=%s)", host, port, threads, db)
    print(f"[serve] waitress on http://{host}:{port}  threads={threads}  AUCTION_DB={db}", flush=True)

    serve(app, host=host, port=port, threads=threads)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    main()
