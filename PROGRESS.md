# PROGRESS — auction-arbitrage PoC

> 매 세션 시작 시 이 파일을 먼저 읽는다. 한 번에 기능 하나. 완료 시 증거 확인 → 커밋 → versions.md 기록.

## Done
- **F1 scaffold** — venv(Python 3.14) + requests/pytest 설치, `python run.py` 동작
- **F2 score-engine** — 차익 스코어(가격갭50/권리30/환금20 ×신뢰), 하드게이트(인수>30%·유치권→권리0), 단위테스트
- **F3 molit-client** — 국토부 실거래 API 클라이언트 + XML 파서(한글/영문 태그·만원→원), 파서 테스트
- **F4 sample-data** — 경매 5건 fixture(확실한차익/신건/권리폭탄/시세추정불가/보통) + 실거래 11건 XML
- **F5 matcher** — 단지명+면적±10% → 법정동 폴백, 평단가 중앙값 추정시세, 테스트
- **F6 store** — SQLite 저장/조회(차익순 정렬, NULL 후순위)
- **F7 pipeline** — 샘플 end-to-end 완주
- **F8 report** — 콘솔 랭킹표 + CSV + HTML(잉크블루+시그널그린)
- **F9 docs+tests** — README + pytest 15건 통과
- (평가자 피드백 반영) 순차익 음수 → "차익없음" 등급으로 정직 표기
- **[사이클2] 연립다세대(빌라) 실거래 클라이언트** — molit_client 다물건유형 일반화(apt/rh/officetel), 화곡 빌라 시세추정불가 해소(→2.8억). 파서 테스트 추가.
- **[사이클2] 하드게이트 보강(중요)** — 빌라 추가로 드러난 허점 수정: 권리점수만 0이면 가격갭(50%)이 커서 유치권 물건이 '양호'로 상위 노출되던 버그 → 최종 스코어 상한(GATE_CEILING 25)+ '위험' 등급으로 강등. 화곡(유치권,갭40%)·해운대(인수34.7%) 모두 25 위험으로 정상 강등. 16 테스트 통과.

- **[사이클3] 오피스텔 실거래 클라이언트 연동** — pipeline 아파트+빌라+오피스텔 합본, 오피스텔 fixture + 검증용 오피스텔 경매(강남역삼) 추가. 17 테스트 통과. 결과: 역삼 오피스텔 시세 3억 → 81점 확실한차익(2위)로 end-to-end 작동.
- **[사이클4] HTML 리포트 시각화** — 갭미터 3중 막대(최저가→시세 갭을 시그널그린으로) + 원형 차익 스코어 뱃지(등급색) + 범례. 잉크블루+시그널그린, tabular-nums. evidence/result.html 재생성·확인.

## 루프 재개 (2026-06-29 — 프로덕션 강화 단계, 키 불요)
운영자가 F10(API 키)을 나중으로 미룸 → 키 없이 가능한 **프로덕션 강화(P1~P5)**로 루프 재개.

- **[P1] molit 클라이언트 프로덕션 강화** — MolitApiError(인증오류 시 'Decoding 키 확인' 안내) + check_api_error(OpenAPI fault/resultCode 감지) + 페이지네이션(max_pages) + 지수백오프 재시도 + logging. 오류감지 테스트 3건 추가 → 20 테스트 통과. 샘플 회귀 OK.
- **[P2] `--live` 통합테스트** — 로컬 mock HTTP 서버(http.server 스레드)가 fixture 서빙 + ENDPOINTS monkeypatch로 fetch_trades·pipeline.run(use_live=True)를 실제 키 없이 end-to-end 검증. tests/test_live_integration.py 4건 → **24 테스트 통과**. 라이브 경로(F10) 사전검증 완료 — 키 도착 시 그대로 동작.
- **[P3] 스코어 config 외부화** — 모든 튜닝 파라미터(가중치·취득세 구간·명도/수리비·페널티·하드게이트·type_base·gap_points·신뢰사다리·등급경계)를 src/config.py의 ScoreConfig로 분리. data/score_config.json 있으면 덮어씀(없으면 기본=현 동작 동일). score.py가 CONFIG 참조하도록 리팩터. config 로드/오버라이드 테스트 2건 → **26 테스트 통과**, 샘플 결과 동일(회귀 없음). data/score_config.example.json 템플릿 추가.
- **[P4] CI + 린트** — .github/workflows/ci.yml(push/PR(main) 시 ruff check + pytest). pyproject.toml ruff 설정(E/W/F/I/B/UP, E501 무시, tests·run.py E402 면제). ruff --fix로 24건 정리(Optional→`|None`, import 정렬, zip strict 등) → **ruff 클린(exit 0) + 26 테스트 통과**. push 시 GitHub Actions 자동 실행.

## In progress
- **P5** CLI 필터(--min-score/--type/--region)·정렬·JSON 출력

## Next
- (P5 완료 시 안전 프로덕션 작업 소진 → AGENT_STOP. F10·v1은 운영자 대기)
- **F10** 🔒 운영자 국토부 API 키 (나중) — .env에 키 넣으면 라이브 자동검증
- **v1** 실제 법원경매 크롤러 — anti-bot 리스크로 무인 제외, 운영자 결정 대기
- **F10** 🔒 운영자 국토부 API 키 발급 → `.env` MOLIT_API_KEY → `python run.py --live` 검증
- **v1** 실제 법원경매(courtauction.go.kr) 크롤러 — anti-bot/JS 리스크로 무인 루프 제외, 운영자 결정 대기

## Notes
- 환경: Windows 11, Python 3.14.5. venv = `.venv`. 실행 시 `$env:PYTHONUTF8="1"` 권장.
- 검증 결과(증거): 상계주공 95점(확실한차익) / 해운대마린시티 인수2억→하드게이트→38점(주의) / 화곡빌라 매칭0→시세추정불가. **핵심 가설(차익 큰데 권리 폭탄을 걸러냄) 작동 확인.**
- 종료조건: F1~F9 PASS·커밋 완료 → 사이클1 종료. 다음은 F10(키 대기) 또는 v1 크롤러. 연속 3회 무변화/막힘 → AGENT_STOP.
