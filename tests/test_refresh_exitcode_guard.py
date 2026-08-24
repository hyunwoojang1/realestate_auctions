"""`scripts/refresh-daily.ps1` 이 파이썬이 만든 종료코드를 **실제로 소비하는지** 못 박는다.

배경: `crawl_rights` 가 미러 실패 시 exit 4 를 내도록 고쳤는데, ps1 이 그 값을 `$rightsCode` 로
받아만 놓고 `$code` 에 접지 않아 **승격이 통째로 무효**였다(2026-08-05 재감사에서 발각).
파이썬 쪽 `final_exit_code()` 는 테스트로 고정돼 있었지만 **소비처는 무방비**였다 —
"신호를 만들었다"와 "신호가 소비된다"는 다르다는 것을 배운 사고다.

PowerShell 을 실행하지 않고 스크립트 텍스트를 계약으로 검사한다(윈도우/CI 어디서나 돌게).
정규식이 아니라 '있어야 할 구조'를 확인하므로, 리팩터링으로 형태가 바뀌면 테스트가 깨져
사람이 다시 들여다보게 된다 — 그게 목적이다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

PS1 = Path(__file__).resolve().parent.parent / "scripts" / "refresh-daily.ps1"


@pytest.fixture(scope="module")
def script() -> str:
    assert PS1.exists(), f"{PS1} 가 없다 — 일일 오케스트레이션이 사라졌는지 확인하라"
    return PS1.read_text(encoding="utf-8-sig")


def test_rights_exit4_is_folded_into_code(script: str):
    """crawl_rights 의 exit 4(미러 실패)가 스크립트 종료코드로 접혀야 한다."""
    assert "rightsCode" in script and "tenantsCode" in script, "크롤 종료코드를 캡처하지 않는다"
    assert "$code = 4" in script, (
        "exit 4 를 $code 에 접는 코드가 없다 — 작업 스케줄러가 미러 실패를 성공으로 기록한다")


def test_photo_check_failure_is_folded_into_code(script: str):
    """사진 도달성 점검 실패도 종료코드로 드러나야 한다(알림만으로는 놓칠 수 있다)."""
    assert "photoCode" in script, "--check 결과를 캡처하지 않는다"
    assert "$code = 6" in script, (
        "photoCode 를 $code 에 접지 않는다 — 푸시를 놓치면 실패가 어디에도 안 남는다")


def test_photo_check_code_does_not_collide_with_runpy(script: str):
    """5 는 run.py 가 '클라우드 미러 실패'로 쓴다 — 겹치면 워치독이 원인을 오귀인한다."""
    assert "$code = 5" not in script, "사진 도달성 실패가 run.py 의 exit 5 와 충돌한다"


def test_notification_priority_covers_exit4(script: str):
    """알림 우선순위 정규식이 exit=4 를 포함해야 한다(종전 [23] 이라 4를 놓쳤다)."""
    assert "exit=[234]" in script, "알림 우선순위 판정이 exit 4 를 놓친다"


def test_photo_check_skipped_in_offline_mode(script: str):
    """-FromCache 는 '네트워크 호출 0' 계약 — 도달성 점검이 그 안에서 돌면 안 된다."""
    i = script.find("deploy.migrate_photos_to_r2")
    assert i > 0, "일일 사진 도달성 점검이 배선돼 있지 않다"
    assert "if (-not $FromCache)" in script[max(0, i - 600):i], (
        "-FromCache 가드 없이 --check 가 돈다 — 오프라인 검증이 네트워크를 때린다")


def test_final_exit_uses_code(script: str):
    assert script.rstrip().endswith("exit $code"), "스크립트가 $code 로 종료하지 않는다"


WATCHDOG = PS1.parent / "watchdog.ps1"


def test_watchdog_exit_code_legend_matches_contract():
    """워치독 알림 문구가 실제 종료코드 계약과 일치해야 한다.

    이 문구는 운영자가 새벽에 실제로 읽는 urgent 푸시다. 세트2 에서 ps1 을 6 으로 분리하면서
    워치독 문구는 못 고쳐, exit 5(run.py 미러 실패)를 "사진 도달성 실패"로 **오귀인**하고
    있었다(2026-08-05 세트3 발각). 문구 드리프트를 계약으로 막는다.
    """
    s = WATCHDOG.read_text(encoding="utf-8-sig")
    assert "LastTaskResult -ne 0" in s, "워치독이 종료코드를 독립 검사하지 않는다"
    # 4 는 두 의미가 겹친다(run.py 낙찰 보존 실패 / crawl_rights 미러 실패) — 둘 다 적어야
    # on-call 이 오진하지 않는다(2026-08-05 세트3 지적).
    for token in ("4=", "낙찰 보존", "crawl_rights", "5=", "run.py", "6=", "사진 도달성"):
        assert token in s, f"워치독 안내에 '{token}' 가 없다 — 종료코드 해석이 불완전하다"
    assert "5=사진 도달성" not in s, "exit 5 를 사진 도달성으로 오귀인하고 있다"
