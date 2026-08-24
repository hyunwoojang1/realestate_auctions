"""클라우드(Supabase) 미러 업서트 공통 실행기 + 실패 카운터.

(2026-08-05 재감사) `deploy/crawl_rights.py` 의 rights/photos/tenants/survey 4개 미러
블록이 "성공 시 건수 로그, 실패 시 실패 카운터 증가 + stderr" try/except 골격을 각자
복붙해 왔다 — 그중 한 곳(tenants)은 실제로 이 증가를 빠뜨려 실패가 exit code 승격으로
이어지지 않은 사고가 있었다. 증가를 이 클래스 안에 한 번만 두면 호출부가 그걸 잊을 수
있는 지점 자체가 없어진다.

(2026-08-05 후속) `run.py` 의 미러 실패 계수는 **원래 없던 것을 같은 날 새로 넣은 것**이다
(HEAD 에는 `_mirror_fail` 자체가 없었다 — 미러가 통째로 실패해도 exit 0 이었다). 즉 이건
순수 리팩터가 아니라 **동작 변경**을 포함한다: exit 5(클라우드 미러 실패) 신설 + 실패 로그를
`--json` 여부와 무관하게 항상 stderr 로. 처음엔 인라인 try/except 5곳으로 넣었다가, 같은
복붙이 `crawl_rights` 에서 사고를 낸 전력이 있어 이 공용 모듈로 합쳤다.
⚠ 이 이력을 "원래 있던 걸 옮겼다"로 적어뒀다가 세트3 리뷰에서 오기로 지적받아 정정한다.

`upsert()` 는 `deploy/crawl_rights.py` 처럼 고정 포맷(`[+] {label} {n}{unit}` /
`[!] {label} 실패{suffix}: {e}`)이 맞는 호출부용이고, `run()` 은 `run.py` 처럼 성공 로그
문구(이모지·조건부 `--json` 가드 등)가 호출부마다 달라야 하는 경우를 위한 저수준 버전이다.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TypeVar

_T = TypeVar("_T")


class MirrorReporter:
    def __init__(self) -> None:
        self.fail_count = 0

    def run(
        self,
        upload: Callable[[], _T],
        *,
        on_success: Callable[[_T], None],
        on_fail: Callable[[Exception], None],
        count_failure: bool = True,
    ) -> None:
        """`upload()` 실행 후 성공/실패를 호출부가 원하는 형식으로 로그한다.

        `count_failure=False` 면 실패해도 `fail_count` 를 올리지 않는다 — `run.py` 의
        고아 권리 정리(prune_rights)처럼 RPC 미배포가 흔한 정상 상태인 블록 전용 예외다.
        """
        try:
            result = upload()
        except Exception as e:  # noqa: BLE001 — 클라우드 실패는 로컬 결과를 깨지 않음
            if count_failure:
                self.fail_count += 1
            on_fail(e)
            return
        on_success(result)

    def upsert(self, label: str, unit: str, upload: Callable[[], int], *,
               fail_suffix: str = "") -> None:
        """단순 케이스 전용: `[+] {label} {n}{unit}` / `[!] {label} 실패{suffix}: {e}` 고정 포맷."""
        self.run(
            upload,
            on_success=lambda n: print(f"[+] {label} {n}{unit}"),
            on_fail=lambda e: print(f"[!] {label} 실패{fail_suffix}: {e}", file=sys.stderr),
        )
