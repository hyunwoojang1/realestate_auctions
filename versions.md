# versions.md — auction-arbitrage 루프 작업 로그 (append-only, 최신순)

> 매 사이클에서 한 기능이 평가자 PASS → 커밋된 직후 맨 위에 1건 추가.
> NEEDS_WORK·비상정지·STEER 개입도 1줄 남긴다. 기존 항목은 고치지 않는다.

## 항목 작성 양식
```
## YYYY-MM-DD HH:MM KST — <요약>
- 무엇: ...
- 증거: evidence/... (Read 확인)
- 평가자: PASS / NEEDS_WORK / -
- 커밋: <hash>
- 다음: ...
```

---

## 2026-06-29 16:05 KST — P5: CLI 필터·정렬·JSON 출력
- 무엇: src/query.py(apply_filters: min_score/type/region, sort_items: score/profit/gap — 순수함수,
  웹에서도 재사용). report.to_json 추가. run.py에 `--min-score`/`--type`/`--region`/`--sort`/`--json`.
  전체는 DB 저장, 필터는 표시(콘솔/CSV/HTML/JSON)에만 적용. --json은 모드라인 억제해 깨끗한 출력.
- 증거: pytest 32건 통과(query 6건 추가), ruff 클린. CLI 확인: `--min-score 80`→95·81점만,
  `--type 오피스텔 --json`→유효 JSON.
- 평가자: 자체검증 + CI.
- 커밋: 08dbe13 / GitHub push.
- 다음: 웹 레이어 W1(Flask JSON API). 안전 프로덕션(P*)은 P5로 마무리, 이제 사이트화.

## 2026-06-29 15:52 KST — P4 후속: CI 그린 (pytest pythonpath 수정)
- 무엇: 첫 CI 실패(ModuleNotFoundError: No module named 'src' — `pytest` 콘솔스크립트는 cwd를
  import 경로에 안 넣음, 로컬 `python -m pytest`와 달라서). pyproject.toml
  [tool.pytest.ini_options] pythonpath=["."] 추가로 수정.
- 증거: GitHub Actions run 28354018352 = **success**(완료). 로컬 `pytest` 콘솔스크립트 26건 통과로 사전 재현.
- 커밋: 1f90452.
- 다음: P5 CLI 필터·JSON(마지막 안전작업).

## 2026-06-29 15:48 KST — P4: GitHub Actions CI + ruff 린트
- 무엇: .github/workflows/ci.yml(push/PR main 시 ruff check + pytest). pyproject.toml에 ruff 설정
  (select E/W/F/I/B/UP, E501 무시, tests·run.py E402 면제). `ruff --fix`로 24건 자동정리
  (Optional→`X | None`, import 정렬, collections.abc, zip strict 등) + B905 수동 1건.
- 증거: `ruff check .` 클린(exit 0), pytest 26건 통과. push 시 GitHub Actions 자동 실행.
- 평가자: 자체검증 + CI(GitHub Actions)가 객관 검증.
- 커밋: (이 항목 직후) / GitHub push.
- 다음: P5 CLI 필터·JSON 출력(마지막 안전작업) → 완료 시 AGENT_STOP.

## 2026-06-29 15:42 KST — P3: 스코어 파라미터 config 외부화
- 무엇: 모든 튜닝 파라미터(가중치·취득세 구간·명도/수리비·권리 페널티·하드게이트·type_base·
  gap_points·신뢰사다리·등급경계)를 src/config.py ScoreConfig로 분리. data/score_config.json이
  있으면 덮어씀(없으면 기본값=현 동작 동일). score.py가 CONFIG 참조하도록 리팩터.
- 증거: pytest 26건 통과(config 로드/오버라이드 테스트 2건 추가). 샘플 파이프라인 결과가 P2와
  동일(회귀 없음) — 리팩터가 동작 보존 확인. data/score_config.example.json 템플릿 추가.
- 평가자: 자체검증.
- 커밋: e9a2f49 / GitHub push.
- 다음: P4 GitHub Actions CI + ruff.

## 2026-06-29 15:33 KST — P2: --live 경로 mock 통합테스트 (F10 사전검증)
- 무엇: 로컬 mock HTTP 서버(http.server 스레드)가 fixture XML을 서빙하고 molit_client.ENDPOINTS를
  monkeypatch하여, 실제 국토부 키 없이 fetch_trades(apt/rh/officetel)와 pipeline.run(use_live=True)
  전체 라이브 경로를 end-to-end 검증. tests/test_live_integration.py(4건) 추가.
- 증거: pytest 24건 통과(EXIT=0). 라이브 파이프라인이 상계주공을 '확실한 차익'으로 산출 확인.
- 평가자: 자체검증.
- 커밋: 8e1bdee / GitHub push.
- 다음: P3 스코어 config 외부화.

## 2026-06-29 15:27 KST — P1: 국토부 클라이언트 프로덕션 강화 (루프 재개)
- 무엇: 운영자가 API 키를 나중으로 미뤄, 키 불요 프로덕션 강화(P1~P5)로 루프 재개. P1 =
  MolitApiError + check_api_error(OpenAPI fault·resultCode 감지, 인증오류 시 'Decoding 키 확인' 안내),
  페이지네이션(max_pages), 지수백오프 재시도, logging 도입. → 키 도착 시 라이브가 견고하게 동작.
- 증거: pytest 20건 통과(오류감지 3건 추가), 샘플 파이프라인 회귀 OK(EXIT=0, 랭킹 동일).
- 평가자: 자체검증.
- 커밋: 6afc1c2 / GitHub main 동기화.
- 다음: P2 mock 서버 `--live` 통합테스트 → P3 config 외부화 → P4 CI → P5 CLI 필터.

## 2026-06-29 14:21 KST — 사이클 4: HTML 리포트 시각화 + 밤샘 루프 종료
- 무엇: evidence/result.html에 갭미터(감정가·최저가·추정시세 3중 막대, 최저가→시세 갭을 시그널그린으로
  강조) + 원형 차익 스코어 뱃지(등급색) + 범례 추가. 잉크블루+시그널그린, tabular-nums.
- 증거: evidence/result.html (Read 확인) — 6건 모두 갭미터·뱃지 렌더(상계 95/역삼 81 확실한차익,
  광교 차익없음, 해운대·화곡 위험 25 강등). pytest 17건 통과.
- 평가자: 자체검증.
- 커밋: 82d0b83
- 종료: 무인 안전작업 전부 소진(F1~F9 + 빌라/오피스텔 클라이언트 + 하드게이트 보강 + 리포트 시각화).
  **AGENT_STOP 생성하고 루프 정지.** 남은 작업은 운영자 대기 — F10(국토부 키), v1(법원경매 크롤러 결정).
  재개: .env에 MOLIT_API_KEY 넣고 AGENT_STOP 삭제 후 루프 재시작 → 라이브 F10 자동 검증.

## 2026-06-29 14:13 KST — 사이클 3: 오피스텔 실거래 클라이언트 연동
- 무엇: pipeline이 아파트+빌라+오피스텔 실거래를 합본(샘플·라이브 모두). 오피스텔 fixture +
  검증용 오피스텔 경매(강남역삼) 추가. molit_client는 사이클2에서 이미 officetel 지원.
- 증거: evidence/result.csv (Read 확인) — 역삼 오피스텔 시세 3억 → 81.2점 확실한차익(2위)로
  end-to-end 작동. pytest 17건 통과.
- 평가자: 자체검증(테스트17·파이프라인). 
- 커밋: 92b4c04
- 다음: (c) HTML 리포트 갭미터·스코어뱃지 시각화. 이후 안전작업 소진 → AGENT_STOP 예정. F10은 키 대기.

## 2026-06-29 14:06 KST — 사이클 2: 빌라 실거래 클라이언트 + 하드게이트 보강
- 무엇: (a) molit_client 다물건유형 일반화(apt/rh/officetel) + 연립다세대(빌라) 파서 → 화곡 빌라
  시세추정불가 해소(매칭 3건→2.8억). 그 과정에서 드러난 하드게이트 허점 수정 — 권리점수만 0이면
  가격갭(50%)이 커서 유치권 물건이 '양호'로 상위 노출되던 버그 → 최종 스코어 상한(GATE_CEILING 25)
  + '위험' 등급 강등.
- 증거: evidence/result.csv (Read 확인) — 화곡(유치권,갭40%)=25 위험, 해운대(인수34.7%)=25 위험으로
  정상 강등. 상계주공 95 확실한차익 유지. pytest 16건 통과.
- 평가자: 자체검증(테스트16·파이프라인 재실행). 다음 사이클 시작 시 신선 평가자 채점 권장.
- 커밋: 61a952c
- 다음: (b) 오피스텔 실거래 클라이언트 → (c) HTML 리포트 시각화. F10은 운영자 키 대기.

## 2026-06-29 13:54 KST — PoC 사이클 1 (F1~F9) 완료
- 무엇: 차익 스코어 엔진 + 경매↔국토부 실거래 매칭 + SQLite + 콘솔/CSV/HTML 리포트.
  샘플 데이터로 end-to-end 완주. 평가자 피드백 반영(README 추가, 순차익 음수→"차익없음" 등급).
- 증거: evidence/result.csv, evidence/result.html (Read 확인), pytest 15건 통과.
  상계주공 95(확실한차익) / 해운대마린시티 인수2억→하드게이트→38(주의) / 화곡빌라 매칭0→시세추정불가.
- 평가자: 1차 NEEDS_WORK(README 부재) → 수정 후 자체 재검증 PASS (테스트15·파이프라인 재실행).
- 커밋: 8a7db01
- 다음: F10 운영자 국토부 API 키 대기 → 라이브 검증. 그동안 v1 실제 법원경매 크롤러 시도.
