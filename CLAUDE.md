# CLAUDE.md — 장시간 자율 루프 규칙 (auction-arbitrage)

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
