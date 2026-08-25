"""테스트 위생 — 스위트를 로컬 .env/셸 환경과 격리(라이브 0 원칙).

배경: run.py CLI 는 시작 시 .env 를 os.environ 에 로드한다(정당한 동작).
run.py 를 호출하는 테스트가 실행되면 그 프로세스 전역에 SUPABASE_* 가 남아,
이후 모든 테스트의 _scored() 가 샘플 대신 클라우드 실데이터를 읽는 오염이 생겼다
(클라우드 테이블이 비어 있던 동안은 폴백으로 숨어 있다가, 데이터 이관 후 21건 연쇄 실패로 발현).

매 테스트 전에 데이터 백엔드 환경변수를 지워 '명시적으로 setenv 한 테스트만' 백엔드를 쓴다.
monkeypatch 라 테스트별 자동 복원 — 로컬 fixture 의 setenv(test_store_rest 등)와도 안전하게 조합.
"""
from __future__ import annotations

import pytest

# AUCTION_ADMIN_KEY: run.py 경유 .env 로드가 운영자 키를 프로세스에 남기면 이후 워치리스트
# 테스트가 전부 403 으로 오염된다(2026-08-24 보안 가드 도입 때 실측). 명시 setenv 한 테스트만 사용.
_BACKEND_ENVS = ("AUCTION_DB", "SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_TABLE",
                 "AUCTION_ADMIN_KEY")


@pytest.fixture(autouse=True)
def _no_live_backends(monkeypatch):
    for k in _BACKEND_ENVS:
        monkeypatch.delenv(k, raising=False)
    # (2026-08-25) fetch_sold TTL 캐시는 env 확인 **전에** 히트한다 — 한 테스트가 채운
    # 캐시가 다음 테스트로 새지 않게 매 테스트 초기화(모듈 전역이라 monkeypatch 밖).
    from src import store_rest  # noqa: PLC0415
    store_rest._sold_cache.update(rows=None, at=0.0)
