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

_BACKEND_ENVS = ("AUCTION_DB", "SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_TABLE")


@pytest.fixture(autouse=True)
def _no_live_backends(monkeypatch):
    for k in _BACKEND_ENVS:
        monkeypatch.delenv(k, raising=False)
