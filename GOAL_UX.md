# GOAL_UX.md — 사용자 관점 UX 개선 + 재감사 밤샘 루프 (2026-07-16)

> 이 파일은 이번 밤샘 루프의 미션·완료정의·가드다. 매 사이클 harness/LOOP.md 절차를 따르되
> 완료정의(Default-FAIL)는 여기서 읽는다. CLAUDE.md 절대규칙(versions.md 등) 항상 우선.

## 미션
배포된 auction-arbitrage(Vercel `auction-arbitrage-nine.vercel.app` / 폰 Tailscale)를 **사용자 관점**에서
쓸 만하게 만든다. 사용자 보고 4버그 + 기능UX 결함 + 코드리뷰/Ponytail 재감사로 나온 **진짜 결함만**
증거 기반으로 수정한다.

## ⚖️ 오버플래그 방지 (사용자 명시 요청 — 최우선 원칙)
모든 감사·진단 발견은 **기본 = 허위(not-a-real-problem)** 로 시작한다. 코드/실행으로 재현되기
전엔 수정하지 않는다. 각 발견을 적대검증(별도 관점, "이게 진짜 문제인가 반증해봐")으로 재확인:
- `confirmed`(코드/실행으로 재현됨) → 수정 대상
- `unconfirmed`(추측·취향·재현 불가) → **수정 금지**, `harness/REJECTED.md`에 "오탐이었음+사유" 기록
- 모호하면 `harness/QUESTIONS.md`에 올리고 넘어간다
아침 보고에 **"무엇을 기각했고 왜"**(오탐 리포트)를 반드시 포함한다. 없는 문제를 만들지 않는다.

## 절대 규칙 (LOOP.md · CLAUDE.md)
- **git push 금지 — 로컬 커밋만.** 아침 운영자 리뷰 후 push (Vercel 배포는 그때 자동).
- Supabase DDL은 CLAUDE.md ##0에 따라 에이전트가 Management API로 직접 실행 가능 — **단 additive·
  `IF NOT EXISTS`만**, 파괴적(DROP/데이터삭제) 금지.
- 라이브 호출 절제(courtauction/국토부 사이클당 스모크 1회 이내). 전국+상세 동시 크롤 금지.
- pytest 실패 상태로 사이클 종료 금지. ruff clean 유지. 삭제성 작업 백업 후.

## 완료 정의 (Default-FAIL — 각 항목 증거 Read 확인 전엔 false)

### 확정 버그 (진단 wf_a6fa366d-db9, confirmed)
- [x] ~~B1 사진 미표시 = photo_url 컬럼 부재→400~~ **기각(오탐)** — 라이브 검증서 컬럼 존재·URL 200·
  배포 사이트 사진 정상 렌더 확인(harness/REJECTED.md). DB ALTER 미실행.
- [ ] **B1' 사진 빈 상태·로딩(재진단, 재현됨)** — 전체 93%가 사진 미수집인데 `{% if photos %}`가 빈 상태
  안내 없이 블록을 숨겨 '깨진 듯' 보임. 완료 = ①`detail.html`에 사진 없는 물건 명시적 빈 상태('사진 미수집')
  ②`<img>`에 `loading`·`width/height`·`fetchpriority` 힌트로 로딩·CLS 개선. `store_rest`의 사진 침묵실패에
  logger.warning 추가(무증상 은폐 방지, 동작 불변). 완료검증 = 사진無/有 물건 각각 로컬 렌더 확인.
- [ ] **B2 사진 확대** — `detail.html`에 경량·의존성0 접근성 라이트박스(클릭→오버레이, Esc/←→/백드롭 닫기,
  포커스 복귀, alt 승계). 완료 = 로컬서버 사진2장+ 사건에서 클릭→확대·키보드 동작 검증.
- [ ] **B3 느린 첫 로딩** — 홈이 3테이블 전량(~19 순차왕복) 페치. 서버측 limit/필터+정렬로 상위 N만,
  세 로드 병렬화, `SUPABASE_CACHE_TTL` 상향, `create_app` 이중실행 제거, `vercel.json` 크롤러JSON(~83MB) 번들 제외,
  폰트 렌더블로킹 완화. 완료 = 왕복수·TTFB 전/후 계측 증거(evidence/).
- [ ] **B4 크롤 특수문자** — ⚠ '사진 캡션'은 **unconfirmed**(파이프라인에 캡션 없음). 상세 자유텍스트
  (`surviving_rights`/`lien_note`/`remark`/`appraisal_notes`)에 `_sanitize`(제어문자 \x00-\x1F·제로폭·NBSP·
  연속공백) 적용. 완료 = 전/후 텍스트 비교 증거 + **사용자에게 '어느 텍스트인지' 확인**(QUESTIONS).

### 기능 UX (진단 confirmed 6건 — 경량)
- [ ] **U1** 지도 사이드목록·지역칩 키보드/스크린리더 접근(button/role+keydown)
- [ ] **U2** 상세 가격차트 터치(폰) 툴팁(touchstart/move → 기존 mousemove 공통화)
- [ ] **U3** 대비 AA 미달 색 상향(.hh, .mcard .ms .k → ≥4.5:1)
- [ ] **U4** `sale_date` 빈값 '미상' 가드(listings/detail 통일)
- [ ] **U5** 재매각(대금미납 재경매) 보증금 10% 하드코딩 → '(통상10%, 재매각 20~30%)' 표기
- [ ] **U6** 홈 이중 `<form>` 입력 누락 → 단일폼 병합/동기화

### 재감사 (적대검증 필수)
- [ ] **A1** code-reviewer 재감사 → 적대검증 후 **CRITICAL/HIGH만** 수정
- [ ] **A2** Ponytail 재감사(과설계·죽은코드) → 적대검증 후 **확정분만**
- [ ] **A3** 오버플래그 리포트(harness/REJECTED.md: 기각 목록+사유)

## 사이클 순서 (가치·확실성순)
B1(사진) → B2(확대) → B4(텍스트정제) → U4·U3·U6·U1·U2·U5(경량 UX) → B3(로딩, 무거움) → A1·A2·A3(재감사)

## 종료·정지 조건 (토큰폭주 방지)
- `AGENT_STOP` 존재 → 즉시 정지. 연속 3사이클 무변화 → 자가 `AGENT_STOP` + QUESTIONS 기록.
- 세션 구현 사이클 상한 **8**. 모든 완료정의 PASS·커밋 시 종료.
- 각 사이클: 구현 → 증거/신선평가 → (필요시)감사 → PROGRESS/versions 기록 → **로컬 커밋** → ScheduleWakeup.

## 실행 명령 (Windows)
- 테스트: `PYTHONUTF8=1 AUCTION_DB=:memory: .venv/Scripts/python.exe -m pytest -q`
- 린트: `.venv/Scripts/python.exe -m ruff check .`
- 로컬서버: `scripts/start.ps1` (waitress 127.0.0.1) — UX 검증용
- Supabase 질의/DDL: `.env` `SUPABASE_ACCESS_TOKEN` → Management API (CLAUDE.md ##0)
