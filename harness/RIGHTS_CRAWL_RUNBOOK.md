# 권리 크롤 자동 실행 런북 (RIGHTS_CRAWL_RUNBOOK)

> 작성 2026-07-20 23:08 KST. 예약 실행 **2026-07-21 03:08 KST**(지금+4h, 리스트 크롤 종료 추정 시점).
> 실행 주체: Windows 예약작업 `AuctionArbitrage-RightsCrawl-Once`(1회성) → `scripts/rights-crawl-and-ship.ps1`.

## 목적
현재 진행 중인 **물건 리스트 크롤**이 끝난 뒤, 그 물건들을 기준으로 **권리(물건상세) 크롤**을 돌린다.
크롤 **전후로 감사·검수 게이트**를 두어, 문제가 있으면 배포하지 않는다(Default-FAIL). 통과 시에만
푸시·배포한다.

## 원칙 (Default-FAIL 증거 게이트)
- 각 단계는 **증거(테스트·게이트·카운트)로 통과를 증명**해야 다음으로 넘어간다.
- 사전 검수 실패 → **크롤 안 함**. 사후 검수 실패 → **배포 안 함**(로컬·클라우드 데이터는 크롤분 유지).
- 모든 단계는 `evidence/rights-crawl-<타임스탬프>.log` + `harness/RIGHTS_CRAWL_REPORT.md`에 기록.
- 종료 시 푸시 알림으로 결과 통지(성공/게이트차단/실패).

## 절차 (스크립트가 그대로 실행)

### Phase 0 — 리스트 크롤 종료 확인
- `COURTAUCTION_STOP` 킬스위치 파일이 없고, courtauction 크롤 파이썬 프로세스가 안 돌면 진행.
- 다른 크롤이 아직 돌면 최대 N분 대기 후 진행(겹침 최소화).

### Phase 1 — 사전 감사·검수 (게이트, 실패 시 중단)
1. `pytest -q` 전체 통과 (실패 시 **크롤 중단**).
2. `ruff check` (경고만, 비차단).
3. `data_gates.run_gates` 전 게이트 PASS (실패 시 중단 — 오염 데이터 위에 크롤 금지).
4. `listing_rights` / `scored_listings` 현재 카운트 스냅샷(사후 델타 비교용).

### Phase 2 — 권리 크롤
- `python -m deploy.crawl_rights --db auction.db --limit <RightsLimit>`.
- 대상 우선순위 = 보수차익 양수 → 유찰 많은 순(신규 리스트 반영). 일일캡 500·3~8s 스로틀·
  `COURTAUCTION_STOP` 킬스위치는 `CourtAuctionClient`가 관리.
- **exit code 의미(우리 C5 수정)**: 0=정상 · 2=차단/상한 중단 · 3=실패율 과다.
  2/3이면 사후 검수에서 배포 차단.

### Phase 3 — 사후 감사·검수 (게이트, 실패 시 배포 중단)
1. `listing_rights` 카운트 **증가** 확인(델타 > 0).
2. `data_gates.run_gates` 전 게이트 PASS(권리 요지 무결성 포함).
3. `pytest -q` 회귀 없음.
4. 신규 권리 요지 표본 sanity(빈/오파싱 아님).
5. 크롤 exit code 0.

### Phase 4 — 푸시 & 배포 (Phase 1·3 모두 PASS일 때만)
1. `git push origin main` (미푸시 커밋 있으면).
2. `bash scripts/deploy_prod.sh` (Vercel 프로덕션 — 커밋된 main 배포, `/health` 자동검증).
   - bash(Git Bash) 경로를 자동 탐색. 없으면 배포 스킵하고 "수동 배포 필요" 알림.
- 권리 데이터 자체는 `crawl_rights`가 `upsert_rights`로 Supabase에 직접 미러하므로, 코드 재배포와
  무관하게 서빙 상세페이지 권리는 즉시 갱신됨.

## 결과 확인 (사용자)
- `harness/RIGHTS_CRAWL_REPORT.md` — 단계별 PASS/FAIL·카운트 델타·exit code·배포 여부.
- `evidence/rights-crawl-<타임스탬프>.log` — 전체 로그.
- 푸시 알림 — 한 줄 요약.

## 수동 실행 / 취소
- 즉시 수동 실행: `pwsh scripts/rights-crawl-and-ship.ps1 -Limit 500`
- 배포 없이: `-SkipDeploy`
- 예약 취소: `Unregister-ScheduledTask -TaskName AuctionArbitrage-RightsCrawl-Once -Confirm:$false`
- 크롤 강제 중단: 리포지토리 루트에 `COURTAUCTION_STOP` 파일 생성.

## 한계 (정직)
- 이 자동 실행의 "감사"는 **결정론적 검수**(테스트·게이트·카운트·exit code)다. 세션에서 하던
  **다관점 LLM 리뷰어 감사**는 무인 실행 불가 — 필요하면 로그/리포트를 사용자가 세션에서 재검토.
- 현황조사서(점유·전입일) 배선(P4)은 별건. 이번 크롤은 매각물건명세서 요지까지만 채운다.
