# LOOP.md — 자율 성장 사이클 (레퍼런스 탐색 → 모방 구현 → 감사 → 하네스 조이기)

> 이 파일은 무인/반무인 루프의 **마스터 사이클 정의**다. 루프 세션은 매 반복마다
> 이 절차를 그대로 따른다. CLAUDE.md의 절대 규칙(versions.md 기입 등)은 항상 우선 적용.

## 목적

운영자(장현우)가 원하는 서비스 방향을 말하면, 루프가 스스로
**레퍼런스 서비스 탐색 → 기능 분석 → 갭 도출 → 모방 구현 → 감사·수정 → 하네스 조이기**를
반복하며 사이트를 지속적으로 발전시킨다. 사람 결정이 필요한 갭은 QUESTIONS.md에 쌓고
푸시 알림으로 운영자를 호출한다. 운영자 없이도 루프는 멈추지 않는다(블로킹 항목만 건너뜀).

## 절대 금지 (범위 잠금 — 운영자 전용 작업)

- **git push 금지.** 로컬 커밋만. push는 운영자가 아침 리뷰 후 직접.
- **Supabase·외부 배포·도메인·DNS 작업 금지.** 운영자 몫.
- **.env 수정·API 키 재발급·유료 결제 금지.**
- **유료 경매정보 사이트의 데이터 크롤 금지.** 레퍼런스 분석은 기능·UX·화면구조·요금제
  **벤치마킹만** 허용(공개 페이지 열람·웹서치). 데이터를 긁어오는 순간 GOAL.md 데이터 원칙 위반.
- **삭제성 작업 금지.** DB 스키마 변경은 백업 후. `auction.db` 원본 덮어쓰기 금지.
- **라이브 호출 절제.** courtauction·국토부 라이브는 사이클당 스모크 1회 이내.
  전국 크롤과 물건상세 크롤 동시 실행 금지(WAF 밴 위험 — versions.md 2026-07-02 확정 정책).

## 사이클 절차 (매 반복 이 순서대로)

0. **정지·개입 확인**: 루트에 `AGENT_STOP` 있으면 즉시 중단.
   `harness/STEER.md`에 내용 있으면 반영하고 파일을 비운다.
1. **상태 파악**: `versions.md` 최신 3~5개 항목(진짜 최신 상태) → `PROGRESS.md` →
   `harness/BACKLOG.md` → `harness/QUESTIONS.md` 순으로 읽는다.
   미해결 질문에 답이 달렸으면(운영자가 QUESTIONS.md에 답 기입) 해당 항목 잠금 해제.
2. **모드 선택**:
   - BACKLOG에 `[ready]` 항목 있음 → **구현 모드** (3으로)
   - 없음 → **탐색 모드** (2'로)

2'. **탐색 모드** (사이클당 레퍼런스 1~2곳):
   - 웹서치로 레퍼런스 서비스를 찾는다 — 국내 경매정보(지지옥션·탱크옥션·두인경매·스피드옥션 등),
     프롭테크 UX(호갱노노·아실·네이버부동산), 해외(Zillow foreclosure, Auction.com, PropertyRadar).
   - 1곳당 `docs/references/<서비스명>.md` 작성: ① 핵심 기능 분해 ② 화면·UX 패턴
     ③ 데이터 표현 방식(지도/표/카드/게이지) ④ 유료화 포인트 ⑤ 우리가 배울 것 / 버릴 것.
   - **갭 분석**: 우리 사이트 현재 기능(web.py 라우트·templates 기준)과 비교해
     "우리에게 없고, 우리 데이터로 구현 가능한 것"을 골라 BACKLOG.md에 추가.
   - BACKLOG 항목은 반드시 **검증 가능한 완료 정의**(Default-FAIL 체크리스트) 포함.
   - 구현 없이 이번 사이클 종료 → 7로.

3. **구현 모드**: BACKLOG 최상위 `[ready]` 항목 **1개만**. 상태를 `[in-progress]`로.
   - TDD: 테스트 먼저(RED) → 구현(GREEN) → 리팩터.
   - 증거 생성: 실행 로그·응답 캡처를 `evidence/`에 저장하고 **Read로 직접 확인**.
4. **평가 (신선한 컨텍스트)**: 빌드 과정을 본 적 없는 서브에이전트(general-purpose,
   Write/Edit 금지 지시)에게 ① BACKLOG 항목의 완료 정의 ② git diff ③ evidence/ 증거만 주고
   PASS / NEEDS_WORK 판정을 받는다.
   - NEEDS_WORK → 지적사항 반영해 재시도. **최대 3회.** 3회 실패 시 항목을 `[blocked]`로
     바꾸고 QUESTIONS.md에 "무엇이 왜 막혔는지" 기록 → 8로.
5. **감사**: code-reviewer 에이전트로 이번 diff 리뷰. 입력 처리·크롤러·DB 관련이면
   security-reviewer 추가. **CRITICAL/HIGH만 즉시 수정**(MEDIUM 이하는 BACKLOG 후순위로).
6. **하네스 조이기**: 이번 기능이 다시 깨지지 않도록 조인다 —
   - 골든셋/회귀 테스트를 tests/에 추가 (예: 대표 매물의 기대 점수 고정)
   - 품질 게이트 전체 통과 확인: `pytest -q` 전부 + `ruff check .` 클린
   - 웹 기능이면 스모크: `/health`·`/api/listings` 200
7. **기록**: BACKLOG 상태 갱신(`[done]`) → PROGRESS.md 갱신 → versions.md 맨 위 KST 항목
   (양식 준수) → **git 로컬 커밋**.
8. **갭 처리·알림**: 이번 사이클에서 사람 결정이 필요한 갭(법적 판단·유료 API·디자인 방향·
   운영자 계정 필요)이 나왔으면 QUESTIONS.md에 기록하고 **푸시 알림 1건** 발송
   (PushNotification 도구; 불가 환경이면 PowerShell 토스트 알림). 같은 질문으로 중복 알림 금지.
9. **다음 사이클**: 종료 조건 미해당이면 ScheduleWakeup(자가 페이싱)으로 다음 반복 예약.
   탐색·경량 사이클 후는 짧게, 무거운 구현 후는 길게.

## 종료·정지 조건 (토큰 폭주 방지 — 필수)

- `AGENT_STOP` 파일 존재 → 즉시 정지 (`New-Item AGENT_STOP`으로 운영자가 생성)
- **연속 3사이클 무변화**(커밋 0건) → 스스로 `AGENT_STOP` 생성 + 사유를 QUESTIONS.md 기록 + 푸시 알림
- pytest 실패 상태로 사이클을 끝내는 것 금지 — 고치거나 revert 후 종료
- 세션당 구현 사이클 상한: 운영자가 별도 지정 없으면 **6사이클** 후 일시정지하고 요약 보고

## 품질 게이트 (모든 구현 사이클 공통, Default-FAIL)

| 게이트 | 통과 조건 |
|---|---|
| 테스트 | `pytest -q` 전체 통과 (2026-07-02 사이클#3 기준 212개, 줄어들면 FAIL) |
| 린트 | `ruff check .` 클린 |
| 골든셋 | 기존 점수 케이스(tests/test_score.py) 불변 |
| 웹 스모크 | `/health`·`/api/listings` 200 (웹 변경 시) |
| 증거 | evidence/ 파일을 Read로 확인한 것만 완료 주장 가능 |

## 실행 명령 (Windows, 이 레포)

- 테스트: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q`
- 린트: `.venv/Scripts/python.exe -m ruff check .`
- 서버(로컬): `scripts/start.ps1` (waitress, 127.0.0.1)
