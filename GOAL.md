# GOAL — 부동산 경매 차익 큐레이션 PoC

## 한 줄 목표
경매 물건의 **최저입찰가**와 **국토부 실거래 추정시세**를 매칭해, **차익 스코어(0~100)**를
계산하고 차익률 높은 순으로 큐레이션해 출력한다. — "차익이 실제로 맞게 계산되는가"를 검증한다.

## 완료 정의 (Default-FAIL — 증거로만 PASS)
각 항목은 `false`에서 시작. 증거 파일(evidence/)을 Read로 확인한 뒤에만 PASS로 바꾼다.

- [ ] **F1 scaffold** — venv + 의존성 설치, `python run.py --help` 동작
- [ ] **F2 score-engine** — 차익 스코어(가격갭50/권리30/환금20 ×신뢰계수, 하드게이트) + 단위테스트 통과
- [ ] **F3 molit-client** — 국토부 실거래 API 클라이언트 코드 + XML 파서 단위테스트 통과 (라이브 호출은 F10)
- [ ] **F4 sample-data** — 현실적 경매 물건 fixture(아파트 다건) + 실거래 응답 fixture
- [ ] **F5 matcher** — 경매물건 ↔ 인근 실거래 매칭 → 추정시세 + 신뢰계수 산출 + 테스트
- [ ] **F6 store** — SQLite 저장/조회 레이어
- [ ] **F7 pipeline** — 샘플 데이터로 end-to-end(수집→매칭→스코어→저장) 1회 완주
- [ ] **F8 report** — 콘솔 랭킹표 + CSV + 간단 HTML 출력 (evidence/ 생성)
- [ ] **F9 docs+tests** — README 실행법 + `pytest` 전체 통과
- [ ] **F10 live-molit** — 🔒 BLOCKED: 국토부 API 키(운영자 발급) 받으면 라이브 연동·검증

## 프로덕션 강화 (키 불요 — 무인 진행 가능)
키가 도착했을 때 라이브가 견고하게 돌도록, 그리고 유지보수·신뢰성을 높이는 작업.
- [ ] **P1 molit-hardening** — 국토부 클라이언트 프로덕션화: API 오류(resultCode/인증오류) 명확한 예외, 페이지네이션, 재시도/백오프, 로깅. 오류감지 단위테스트.
- [ ] **P2 live-integration-test** — 로컬 mock HTTP 서버로 `--live` 경로 end-to-end 통합테스트(실제 키 없이 F10 사전검증).
- [ ] **P3 config-externalize** — 스코어 파라미터(취득세율·가중치·페널티·부대비용)를 config로 분리해 코드수정 없이 튜닝 가능.
- [ ] **P4 CI** — GitHub Actions(push 시 pytest) + ruff 린트 설정.
- [ ] **P5 cli-filters** — CLI 필터(최소 스코어·지역·물건종류)·정렬·JSON 출력 옵션.

## 웹 레이어 — 사이트화 (키 불요, Flask+Jinja2 기반)
운영자 결정: PoC를 "브라우저에서 열람 가능한 사이트"로. Python 3.14 빌드 리스크 회피 위해 순수
파이썬 Flask 사용(FastAPI/pydantic-core 금지). 샘플/mock 데이터로 진행, 키 도착 시 라이브 전환.
- [ ] **W1 web-api** — Flask 앱 + JSON API: `GET /api/listings`(min_score·type·region 쿼리 필터), `GET /api/listings/<case_no>`, `GET /health`. report.to_json 재사용. Flask test_client 테스트.
- [ ] **W2 web-ui** — `GET /` 큐레이션 페이지(report.py의 잉크블루+시그널그린·갭미터·스코어뱃지 디자인을 Jinja2 템플릿으로 재사용).
- [ ] **W3 detail-page** — `GET /property/<case_no>` 상세 페이지(갭미터 특대·차익 스코어 게이지·권리 안전성·시세 근거). SSR(SEO).
- [ ] **W4 web-polish** — 필터 UI(스코어 슬라이더·종류/지역 드롭다운) + 반응형 + Dockerfile + CI에 web smoke test 추가.

## Phase 2 — 검증·알림 (운영자 요청, 키 불요)
- [ ] **V1 backtest-harness** ⭐ — 차익 스코어가 실제 수익으로 이어지는지 검증. 낙찰결과 outcomes(현재 합성 fixture data/backtest_outcomes.json, 추후 실데이터)와 scored를 조인 → 스코어 구간별 적중률(실현차익>0 비율)·평균 실현차익 캘리브레이션 리포트(콘솔/CSV). 제품 신뢰의 근거.
- [ ] **V2 watchlist-alerts** — 관심물건(watchlist, sqlite/파일) 저장 + 직전 스냅샷 대비 차익 임계치 돌파/스코어 상승/유찰 감지 → 알림(파일·콘솔). 순수함수로 diff 로직 분리 + 테스트.
- [ ] **V3 weekly-digest** — "이번 주 차익 TOP N" 다이제스트(markdown/HTML) 생성 + CLI(또는 /api/digest). report 재사용.

## 차익 스코어 공식 (artifact 기준)
```
score = ( 가격갭×0.50 + 권리×0.30 + 환금성×0.20 ) × 신뢰계수(0.6~1.0)
하드게이트: 인수금액비율 > 30% 또는 치명 유치권 → 권리=0
가격갭 = (추정시세 − 실질취득원가) / 추정시세,  실질취득원가 = 최저가 + 취득세 + 명도/수리 추정 + 인수금액
```

## 데이터 소스 원칙 (리스크 방어)
- 시세: **국토부 실거래가 API**(rt.molit.go.kr / data.go.kr) — 무료, 운영자 키 발급
- 경매: 대법원 법원경매정보 (PoC는 샘플 fixture → v1에서 실제 크롤러)
- ⚠️ 유료사이트 가공데이터 크롤 금지. 차익은 투자판단 보조(면책).

## 운영자(장현우)만 할 일
1. 공공데이터포털 국토부 실거래가 API 키 발급 → `.env`의 `MOLIT_API_KEY`에 입력 (F10 잠금해제)
2. (선택) 테스트용 실제 경매물건 1건 제공

## 비상정지
프로젝트 루트에 `AGENT_STOP` 파일 생성 시 루프 중단 의도로 간주. (`New-Item AGENT_STOP`)
