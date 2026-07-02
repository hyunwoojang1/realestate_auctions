# CLAUDE.md — 장시간 자율 루프 규칙 (auction-arbitrage)

## 0. 절대 규칙 — versions.md 기입 (최우선 · 예외 없음)
**이 프로젝트에서 어떤 파일이든 작성/편집(Write/Edit/MultiEdit)하면, 그 턴을 끝내기 전에
반드시 `versions.md` 맨 위에 KST 타임스탬프 항목을 1건 추가한다. 예외 없음.**
- 코드·테스트·문서·설정 등 무엇을 바꿔도 기입 대상. (단 `versions.md` 자신 수정은 제외)
- 형식은 아래 "## 항목 작성 양식"과 동일(무엇/증거/평가자/커밋/다음).
- 시각은 KST(UTC+9), `YYYY-MM-DD HH:MM KST`까지.
- 사용자 지시로 명시 도입(2026-06-30). 전역 `PostToolUse` 훅
  (`~/.claude/hooks/auction-versions-reminder.js`)이 리마인더로 이 규칙을 강제한다.

1. **항상 `PROGRESS.md` 먼저 읽기.** 현재 상태/다음 할 일 파악 후 시작.
2. **한 번에 기능 하나.** GOAL.md의 F1~F10 중 하나씩. 완주 못 하면 PROGRESS에 남기고 중단.
3. **증거 후 합격.** 테스트/출력 결과 파일(evidence/, test 통과 로그)을 Read로 확인하기 전에는
   완료로 표시하지 않는다. "돌려보지 않고 됐다고 하지 않는다."
4. **완료 시 기록.** PROGRESS.md 갱신 + `versions.md` 맨 위에 KST 타임스탬프 항목 추가 + git 커밋.
5. **종료조건 준수.** F1~F9 전부 PASS·커밋되면 루프 종료(F10은 운영자 키 대기). 연속 3회 무변화 →
   `AGENT_STOP` 생성하고 멈춤. 토큰 폭주/무한루프 금지.
6. **블로커는 분리.** 국토부 라이브 API(키 필요)·실제 크롤러 등 환경 의존 단계는 루프에서 막지 말고
   "운영자 대기"로 표시. 샘플 fixture로 검증 가능한 것까지만 무인 진행.
7. **시각**: KST(UTC+9). `powershell (Get-Date).ToUniversalTime().AddHours(9)`.
8. **자율 성장 사이클.** "루프 돌려/계속 발전시켜" 류 요청 시 `harness/LOOP.md`의 사이클
   (레퍼런스 탐색→모방 구현→감사→하네스 조이기)을 따른다. 백로그는 `harness/BACKLOG.md`,
   사람 결정 대기는 `harness/QUESTIONS.md`(+푸시 알림), 방향 수정은 `harness/STEER.md`.
   LOOP.md의 절대 금지(범위 잠금) 항목은 이 파일 규칙과 동급으로 준수.
