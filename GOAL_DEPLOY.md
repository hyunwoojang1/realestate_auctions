# GOAL_DEPLOY — auction-arbitrage 비공개 배포 준비 밤샘루프

> 이 문서는 2026-07-01 grilling으로 합의된 배포준비 범위의 **완료정의(Default-FAIL)**다.
> 각 항목은 `false`에서 시작. **증거 파일을 Read로 확인한 신선-컨텍스트 평가자가 PASS 판정**을
> 내려야만 완료로 본다. 빌더는 자기 작업을 스스로 합격 처리할 수 없다.

## 합의된 전제 (grilling 2026-07-01)

- **제품 목표**: 비공개 개인용 먼저 (정확도 검증 후 공개 전환)
- **서빙 위치**: 올-로컬(Windows) + 필요시 Tailscale/Cloudflare 터널. → gunicorn 불가, **waitress** 사용
- **새로고침**: 전국 매일 (단 일일상한·지터·요청분산 준수)
- **밤샘 라이브 정책**: build/test는 **오프라인(캐시/fixture)만**. courtauction/국토부 실서버 호출 금지.
  라이브 검증은 아침에 사용자가 통제된 1회로 별도 실행.
- **git**: `feat/deploy-prep`에서 로컬 커밋만. **밤샘 push 금지.** 아침에 사용자 리뷰 후 push.
- **무회귀**: 기존 pytest(현 120개) 전부 통과 유지 + ruff 클린. 깨지면 그 사이클 NEEDS_WORK.

## 코어 완료정의 (A·B·C — 모두 PASS해야 루프 성공)

### A. 정기 새로고침 스케줄러 (비활성 등록까지)
- [ ] `scripts/refresh-daily.ps1` — `run.py --source courtauction --nationwide --cash <원> --live --ym <YYYYMM>`를
      실행하는 래퍼(AUCTION_DB·PYTHONUTF8 설정, .venv python 사용, 로그를 `evidence/`에 남김)
- [ ] `scripts/install-scheduler.ps1` — Windows 작업스케줄러에 위 작업을 **Disabled 상태로** 등록
      (매일 새벽, 예: 05:30). 활성화는 사용자가 `Enable-ScheduledTask`로 수동.
- [ ] `scripts/uninstall-scheduler.ps1` — 등록 해제
- [ ] **증거**: `evidence/scheduler_dryrun.txt` — refresh-daily.ps1을 **오프라인(캐시 사용, 라이브 미호출)**
      dry-run 모드로 돌려 auction.db가 캐시 데이터로 채워지고 캐시 diff 요약이 찍힌 로그. install 스크립트가
      Disabled로 등록함을 보여주는 `Get-ScheduledTask` 출력(또는 -WhatIf 검증)
- [ ] README/docs에 "약관 확인 후 Enable-ScheduledTask 한 줄로 활성화" 안내

### B. 프로덕션 서빙 (waitress)
- [ ] `requirements.txt`에 `waitress` 추가
- [ ] `scripts/start.ps1` — waitress로 `src.web:create_app()` 서빙(AUCTION_DB 지정, 포트 지정, debug off)
- [ ] `src/web.py` — 프로덕션에서 Flask debug/reloader off 보장(개발 편의는 env flag로만)
- [ ] `Dockerfile` — CMD를 dev server(`flask run`)에서 **waitress**로 교체
- [ ] **증거**: `evidence/serving_health.txt` — start.ps1로 앱 부팅 후 `GET /health` HTTP 200 응답 본문,
      그리고 `GET /api/listings`가 JSON 반환(샘플/캐시 데이터로) 로그. debug=False 확인
- [ ] 기존 web 테스트 무회귀

### C. 신뢰계수 표본 개선 (원인규명 → 개선)
> 발견: 다월 수집(`LIVE_MONTHS=3`, `fetch_trades_months`/`recent_ymds`)은 **이미 배선됨**.
> 따라서 "배선"이 아니라 **"라이브 매칭이 빈약한 실제 원인 규명 + 신뢰계수가 표본수를 올바르게
> 반영하도록 개선"**이 목표.
- [ ] `src/matcher.py`/`src/pipeline.py` 분석: 라이브 매칭 표본이 적은 원인 규명
      (matcher 과필터 vs 월수 부족 vs 신뢰계수 공식) — 규명 결과를 `docs/confidence-analysis.md`에 기록
- [ ] `LIVE_MONTHS`(및 matcher 면적/거리 허용범위)를 config/CLI로 **노출·조정 가능**하게
- [ ] 신뢰계수가 **표본수에 단조 증가**함을 검증하는 다월 fixture 테스트 추가
      (표본 1건 → 낮은 신뢰계수, 표본 다수 → 높은 신뢰계수)
- [ ] **증거**: `evidence/confidence_samples.txt` — 표본수별 신뢰계수 산출 표(테스트 출력),
      기존 120 테스트 + 신규 테스트 전부 green
- [ ] 기존 스코어 동작 무회귀(샘플 파이프라인 결과 안정)

## 스트레치 (A·B·C 전부 PASS 후, 시간/예산 남을 때만)
- [ ] **D. 권리필드 파서 뼈대** — courtauction 상세(매각물건명세서·감정평가서) 파서 스캐폴딩.
      저장된 샘플 HTML 상대 파싱 + 단위테스트만. 라이브 검증은 나중.
- [ ] **E. 시세유형 확대** — 단독/다가구·상업·토지 endpoint + 건축물대장(노후도·위반건축물) 클라이언트 뼈대
- [ ] **F. 터널 접속 가이드** — Tailscale/Cloudflare Tunnel 셋업 문서 + 검증 스크립트

## 정지 조건 (무한루프·토큰폭주 방지 — 필수)
- 각 코어 항목 빌더↔평가자 사이클은 **최대 3회**. 3회째도 NEEDS_WORK면 해당 항목 "보류"로
  기록하고 다음 항목으로 넘어간다(루프 전체를 멈추지 않음).
- **`AGENT_STOP` 파일**이 프로젝트 루트에 있으면 즉시 중단(각 사이클 시작 시 확인). 비상정지 버튼.
- A·B·C·(스트레치)가 모두 PASS·커밋되면 종료.
- 종료 시 `PROGRESS.md` 갱신 + `versions.md`에 사이클별 항목 추가(KST).

## 실행 커맨드 (빌더/평가자 공용)
- 테스트: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q`
- 린트: `.venv/Scripts/python.exe -m ruff check .`
- 절대 규칙: 파일 편집 시 `versions.md` 맨 위에 KST 항목 추가(전역 훅 리마인드). 커밋 전 테스트 green.
- 작업 경로: `C:\Users\notebiz765\장현우\auction-arbitrage` (브랜치 `feat/deploy-prep`).
