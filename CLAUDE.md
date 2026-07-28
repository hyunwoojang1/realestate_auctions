# CLAUDE.md — 장시간 자율 루프 규칙 (auction-arbitrage)

## 0. 절대 규칙 — versions.md 기입 (최우선 · 예외 없음)
**이 프로젝트에서 어떤 파일이든 작성/편집(Write/Edit/MultiEdit)하면, 그 턴을 끝내기 전에
반드시 `versions.md` 맨 위에 KST 타임스탬프 항목을 1건 추가한다. 예외 없음.**
- 코드·테스트·문서·설정 등 무엇을 바꿔도 기입 대상. (단 `versions.md` 자신 수정은 제외)
- 형식은 아래 "## 항목 작성 양식"과 동일(무엇/증거/평가자/커밋/다음).
- 시각은 KST(UTC+9), `YYYY-MM-DD HH:MM KST`까지.
- 사용자 지시로 명시 도입(2026-06-30). 전역 `PostToolUse` 훅
  (`~/.claude/hooks/auction-versions-reminder.js`)이 리마인더로 이 규칙을 강제한다.

## 0.5 절대 규칙 — push = 배포까지 (사용자 지시 2026-07-19)
**main에 push했으면 그 턴에서 `bash scripts/deploy_prod.sh`로 Vercel 프로덕션 배포까지 끝낸다.**
"push 완료"는 배포가 아니다 — 이 프로젝트는 GitHub 자동배포 연동이 **없다**(Vercel GitHub App이
프라이빗 레포 접근권한 없음). 스크립트가 깨끗한 worktree에서 배포하므로 워킹트리에 타 세션
변경이 섞여 있어도 안전. 프로덕션 URL은
`auction-arbitrage-hyunwoo-jang-s-projects.vercel.app`
(⚠️ 무접미사 `auction-arbitrage.vercel.app`은 **남의 앱** — 절대 혼동 금지).

## 0.6 절대 규칙 — 배포 출력을 파이프로 자르지 말 것 (2026-07-28 사고)

**`scripts/deploy.ps1` 을 `| Select-Object -First N` 으로 잘라 실행하지 말 것.**

`Select-Object -First N` 은 N개를 받는 순간 **파이프라인 상류를 종료**시킨다. 그러면
deploy.ps1 의 `finally` 블록(대형 캐시 원위치 복원)이 중간에 끊겨, `%TEMP%uction_deploy_stash`
에 파일이 갇힌 채 배포가 "성공"으로 보인다. 그 상태로 다시 배포하면 `Move-Item -Force` 가
stash 의 **원본을 현재 파일로 덮어써 영구 소실**된다 — `molit_trades.db`(214MB)가 이렇게 날아갔고,
`naver_cache.json`(215MB)도 두 번 같은 위기를 겪었다.

- ❌ `deploy.ps1 | Select-String ... | Select-Object -First 4`
- ✅ `deploy.ps1` 그대로 실행하고, 필요하면 **끝난 뒤** 출력을 살펴본다
  (`$out = & deploy.ps1 2>&1; $out | Select-String 'ready'`)
- 배포 후에는 **stash 가 비었는지 반드시 확인**한다:
  `ls "$env:TEMPuction_deploy_stash"` → 비어 있어야 정상.
- deploy.ps1 자체에도 자가복구 가드가 있다(잔여물 먼저 복원, 충돌 시 중단) — 하지만 그건
  **다음 배포 때** 도는 것이라, 그 사이 크롤이 캐시 없이 돌면 국토부를 전량 재조회한다.

## 0.7 절대 규칙 — 표본으로 전체를 단언하지 않는다 (사용자 지시 2026-07-28)

사용자 지적: "왜 항상 거짓 보고를 하나." 되짚어 보면 원인이 하나다 —
**가설을 지지하는 표본 1건을 확인하고 거기서 멈췄다.** 반증을 시도하지 않았다.

실제로 있었던 일(전부 같은 패턴):
- "시뮬레이터 정상화됐습니다" → 시세가 붙은 **1건**만 열어보고 단언. 실제로는 281건 중
  **246건(87.5%)**이 여전히 '거액 손해 확정'으로 보이고 있었다(다음 감사에서 CRITICAL).
- "지분 물건은 차익이 부풀려집니다" → 각주로만 적고 실제 순위를 안 봤다. 차익 상위 8건 중
  **4건**이 그 물건이었다.
- "네이버 링크 65건" → **로컬** 수치를 프로덕션인 양 보고. 프로덕션은 57건이었다.
- "폰 화면에 탭이 보입니다" → 뷰포트 844px로 판정. 주소창 실효 높이를 안 따져 결론이 뒤집혔다.

**지켜야 할 것:**
1. "고쳤다 / 된다 / 정상이다"를 쓰기 전에 **모집단 전체를 센다**.
   `PYTHONUTF8=1 .venv/Scripts/python.exe scripts/verify_claims.py [--prod]`
   보고문에는 그 표의 숫자를 **분모까지** 인용한다("35/281" 이지 "된다" 가 아니다).
2. **로컬 확인은 로컬이라고 쓴다.** 프로덕션을 주장하려면 프로덕션 URL을 실제로 때려 본다
   (`--prod`). 배포 직후엔 콜드·캐시 TTL(600초) 때문에 값이 다를 수 있으니 재확인한다.
3. **반증을 먼저 시도한다.** "되는 케이스"가 아니라 "안 되는 케이스가 몇 건인가"를 센다.
   0건임을 확인했을 때만 "없다"고 쓴다.
4. **부분 성공은 부분이라고 쓴다.** "일부 고쳤다"가 "고쳤다"보다 항상 낫다.
   범위를 좁혀 정확히 말하는 것은 겸손이 아니라 정확성이다.
5. 이전 보고가 틀렸다는 걸 알게 되면 **먼저 정정한다**. 새 성과로 덮지 않는다.

## 1. 시작 절차

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

## 자동화 하네스 3종 (2026-07-23 도입 — 수동 QA 전쟁 종식용)

**① 커밋 게이트(pre-commit)**: 모든 `git commit`에서 `scripts/precommit_gate.ps1`이 자동 실행 —
scratch/DB 파일 차단(PII) → 문서 드리프트 감지 → ruff(스테이징 파일만) → PII 잔여 스캔 → pytest 전체.
실패 시 커밋 차단. 비상 우회는 `AUCTION_SKIP_GATE=1`(scratch/DB 차단은 우회 불가).
훅 재설치: `scripts/install-hooks.ps1`. **테스트를 게이트에 맞추지 말고 코드를 고칠 것.**

**② 문서 동기화 큐**: 크롤러/판정 코드가 README/docs 없이 커밋되면 `harness/DOC_SYNC_QUEUE.md`에
자동 적재된다. **모든 세션은 시작 시(그리고 루프는 매 사이클 1단계에서) 이 큐를 확인**하고,
미처리 항목이 있으면 diff를 읽어 README 해당 섹션·docs/crawler_qa_*를 실코드 기준으로 갱신 후
`- [x]` 체크한다. "코드 고치면 문서가 따라온다"의 실행 주체는 큐를 소비하는 세션이다.

**③ 알림·워치독**: `scripts/notify.ps1`(ntfy 푸시+`harness/ALERTS.log`)이 refresh-daily·
rights-crawl-and-ship 종료 시 자동 발송. `AuctionArbitrage-Watchdog` 작업(30분마다,
`scripts/watchdog.ps1`)이 DailyRefresh 비활성/정체·크롤 ABORT·서빙 다운·push 밀림을 감시해 푸시.
ntfy 토픽은 `harness/notify.json`(gitignore, 예시는 notify.json.example). rights-crawl-and-ship
exit code: 0=OK/10=사전검수FAIL/20=사후검수FAIL/30=푸시·배포실패/40=킬스위치.

## Supabase 스키마/DDL — 에이전트가 직접 실행 (클립보드 금지)

이 프로젝트가 쓰는 Supabase(프로젝트 ref **`trajmfklbyarbkiljogj`**, "Finance AI") 스키마 변경(DDL)이나 임의 SQL은 **에이전트가 Management API로 직접 실행한다.** DDL을 사용자에게 "클립보드에 넣고 대시보드에서 실행" 넘기지 말 것. (사용자 지시 2026-07-13)

- 토큰: `.env`의 `SUPABASE_ACCESS_TOKEN` (`sbp_...`)
- 엔드포인트: `POST https://api.supabase.com/v1/projects/trajmfklbyarbkiljogj/database/query`
- 예: `curl -sS -X POST -H "Authorization: Bearer $SUPABASE_ACCESS_TOKEN" -H "Content-Type: application/json" -d '{"query":"<SQL>"}' https://api.supabase.com/v1/projects/trajmfklbyarbkiljogj/database/query`
- 결과 JSON 배열 반환. DDL 동일 엔드포인트. ⚠️ 파괴적 변경은 사용자 확인 후.
