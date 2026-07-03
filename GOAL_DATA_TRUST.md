# GOAL_DATA_TRUST — 데이터 신뢰도 개편 밤샘 루프 (2026-07-03)

> 근거 문서: `데이터_신뢰도_문제의식_및_개선방향.md` (18장 체크리스트를 7단계로 구현).
> 운영 규칙: CLAUDE.md(versions.md 절대 규칙) + harness/LOOP.md(절대 금지·품질 게이트) 그대로 적용.
> **운영자 지시(2026-07-03)**: 사이클 간격 1분(ScheduleWakeup 60s), T1~T7 완주 후 다관점 감사까지.
> 핵심 원칙: "높은 수익률을 보여주는 것"이 아니라 "이 데이터를 어디까지 믿어도 되는지 알려주는 것".

## 단계 (위에서부터 순서대로, 사이클당 1단계)

### [done] T1. 식별자 구조 수정 — 물건 누락 방지 (최우선) ✅ 사이클#1 평가자 PASS
- 왜: 한 사건번호에 물건 여러 개 가능한데 `case_no TEXT PRIMARY KEY`(store.py)면 조용히 덮어씀.
- 완료 정의:
  - [x] `auction.db` 백업 후 작업 → data/backup/auction.db.bak-20260703-1315
  - [x] scored_listings PK → PRIMARY KEY (court, case_no, item_no), 실DB 2521건 무손실 이관(v0→v2)
  - [x] `item_no`(maemulSer)·`doc_id`·`court` 수집→모델→채점→저장 관통 + pipeline 전국병합·cache record_key도 복합키화
  - [x] raw_listings 테이블(uid PK, raw_json, fetched_at) + run.py 배선
  - [x] 마이그레이션 자동(v1 감지→트랜잭션 이관) + 멱등성 테스트
  - [x] 같은 case_no + 다른 item_no 공존 회귀 테스트(tests/test_store_identity.py 8개)
  - [x] pytest 253 passed(245→253) + ruff 클린 + evidence/t1_pk_migration.txt Read 확인
- 평가자 MEDIUM 2건(상세 라우트·watchlist 등 소비단계 case_no 잔존) → BACKLOG B14 이월

### [done] T2. 추천 대상 아파트 제한 + 유형 불명 시 시세추정불가 ✅ 사이클#2 평가자 PASS
- 왜: 빌라/상가/토지는 현 비교군 방식으로는 위험. 유형 매핑 실패(want None) 시 모든 kind 통과는 치명적.
- 완료 정의:
  - [x] `_kind_ok` 재정의: `want is not None and trade.kind == want` — 유형불명·미태깅 통과 전부 제거
  - [x] SUPPORTED_ESTIMATION_KINDS={apt,officetel} + estimate_market_price 조기 차단(비교군 자체를 안 만듦)
  - [x] '미지원유형' 등급 신설(시세추정불가=데이터부족과 원인 구분), 뱃지 5템플릿+방법론 문서화, digest/랭킹 구조적 배제
  - [x] 테스트 13개(test_estimation_policy.py) + 구정책 테스트 4건 정책반전 갱신
  - [x] pytest 267 passed + ruff 클린 + evidence/t2_apt_only.txt Read 확인
- 평가자 MEDIUM(죽은 rh/sh/nrg/land 수집 API 쿼터 낭비)→B15 이월, LOW 2건(_WARN_GRADES·주석) 즉시 수정.
  오피스텔 '조건부'는 T5 표본게이트에서 강화(주석·GOAL 명시).

### [done] T3. 비교군 scope 저장 ✅ 사이클#3 평가자 PASS
- 왜: 시세 추정치가 "어떤 집합에서 나온 값인지"를 저장해야 신뢰 등급을 말할 수 있음.
- 완료 정의:
  - [x] match_trades_scoped 계층 매칭(같은평형±3% → 인접평형±band → 법정동 폴백) + MarketEstimate(est,matched,scope)
  - [x] scored_listings v3(market_scope) + v1/v2 자동 마이그레이션(실DB 2521건 보존) + 상세페이지 '시세 비교군' 행
  - [x] 추천 게이트: score 등급 캡(폴백/인접평형 → '관심') + digest TOP은 same_complex_same_area만
  - [x] 테스트 15개(test_market_scope.py) + confidence_samples 2건 T3 의미론 갱신
  - [x] pytest 282 passed + ruff 클린 + evidence/t3_scope.txt Read 확인
- 평가자 LOW 2건(레거시 "" 이중통과=의도적 하위호환·등급명 리터럴=기존 관행), INFO(목록에서 캡'관심' 구분 불가→T7에서 처리)

### [done] T4. 시세 단일값 → 2선 가격 밴드 ✅ 사이클#4 평가자 PASS
- 왜: 중앙값 하나는 "정답 가격"처럼 보여 과신 유발. 검증 하한가/검증 기준가 밴드로.
- 완료 정의:
  - [x] MarketEstimate에 band_low(트림 후 최저 평단가)·band_high(트림 후 중앙값=est) — 표본수는 matched_trades가 담당
  - [x] profit_low/profit_high 계산·저장. 추천 판단 profit_low 기준: digest 정렬·min_profit·비양수 제외 + score '차익없음' 게이트
  - [x] 이상치 방어 문서화(trim_outliers 4건↑ 상·하단 1건 제거 — 가족거래 저가/신고가성 고가 방어)
  - [x] est 호환(=기준가)·점수 무회귀(test_band_does_not_change_score), 기존 테스트 무수정 전체 통과
  - [x] DB v4(ALTER 4컬럼, 실DB 2521건 보존) + 상세페이지 밴드·보수차익 행
  - [x] pytest 296 passed(+14) + ruff 클린 + evidence/t4_band.txt Read 확인(광교 보수차익 -0.01억 → 차익없음·추천 제외 실증)
- 평가자 노트: 실DB 레거시 행 profit_low=NULL → **다음 전량 새로고침 전까지 라이브 digest는 기준차익 폴백**(운영자 고지)

### [done] T5. 표본 부족 시 추천 금지 게이트 ✅ 사이클#5 평가자 PASS
- 왜: 같은 단지/평형 실거래 2~4건으로 만든 분위수는 통계 흉내. 과감히 "시세근거 부족"이라 말해야 함.
- 완료 정의:
  - [x] 3단계 게이트(밴드 실기반 basis 기준 — T4 권고 반영): <3 밴드 금지·시세근거 부족 / 3~4 등급 캡+digest 제외+웹 경고 / ≥5 정상
  - [x] 매칭 4건→트림 후 basis 2→금지 (부풀린 표본 차단 실증)
  - [x] config 외부화: band_min_basis/band_confident_basis + env + 단조 방어
  - [x] v5 스키마(market_sample_basis, 실DB 2521건 보존) + 상세 '밴드 근거 표본' 행 + 낮은신뢰 경고 박스 + 방법론 문단
  - [x] 테스트 16개(경계 2·3·4·5·7) + 샘플 fixture 단지당 7건 현실화(게이트는 합성 소표본 테스트로 검증 — 평가자 정당성 인정)
  - [x] pytest 312 passed(+16) + ruff 클린 + evidence/t5_sample_gate.txt Read 확인
- 평가자 노트: ⚠실DB 전행 basis=None → **전량 새로고침 전까지 실데이터 게이트 미발효**(T4와 동일 패턴, 운영자 고지)

### [done] T6. 호가 스텁 — 점 표시 구조만 (실데이터는 운영자 대기) ✅ 사이클#6 평가자 PASS
- 왜: 호가는 밴드 재료가 아니라 검증 보조 점. 단 네이버 등 수급은 약관 문제 → 문서 12장 순서 준수.
- 완료 정의:
  - [x] src/asking.py — AskingPrice 모델·asking_points(밴드 대비 아래/안/위)·수동 입력 파일(data/asking_prices.json) 로드, 없으면 완전 무표시(계약)
  - [x] band_overstated — 최저 호가 < 검증 하한가 → "실거래 밴드 과대 가능성" 경고 + 상세페이지 렌더
  - [x] QUESTIONS.md Q2 등록(수동입력/제휴API/보류 — 논블로킹). asking.py 네트워크 코드 0(크롤 금지 준수, 평가자 grep 확인)
  - [x] 표시 전용 보장 — matcher/score/digest 무접촉(시세·점수·추천에 영향 없음, 평가자 확인)
  - [x] pytest 325 passed(+13) + ruff 클린 + evidence/t6_asking_stub.txt Read 확인
- 평가자 LOW 3건(무효 행 침묵 skip·float 거부·테스트 환경 의존) 전부 즉시 수정

### [ready] T7. UI 메시지·포지셔닝 전환
- 왜: "예상 차익 2.3억"(단정) → "보수 가격 기준 차익 1.4억 · 실거래 6건 기준"(신뢰 고지).
- 완료 정의 (전부 false):
  - [ ] 추천 표면(digest TOP)에서 '위험'·'권리미확인' 등급 제외 또는 명시 분리(평가자 3회 반복 지적 — T2/T4/T5)
  - [ ] 목록/상세/비교/알림의 차익 표기를 profit_low 중심 "보수 가격 기준" 문구로 전환
  - [ ] 표본 수·scope 근거 문구 병기("같은 단지/같은 평형 실거래 N건 기준")
  - [ ] "권리 확인 완료 전까지 최종 판단 금지" 고지 상세 페이지 포함
  - [ ] README 등에서 "차익 확정"류 단정 표현 제거 → "1차 필터/검토 보조 도구" 포지셔닝
  - [ ] 템플릿 스모크 + pytest 전체 + ruff 클린 + evidence/t7_ui_copy.txt Read 확인

### [ready] T8. 최종 감사 (T1~T7 완료 후 자동 진행)
- 완료 정의 (전부 false):
  - [ ] 다관점 감사 병렬 — ① 기능/코드품질(code-reviewer) ② 침묵실패(silent-failure-hunter) ③ 도메인 안전(근거 문서 원칙 위반: fallback 추천 부활·단정 표현 잔존 등) ④ 디자인/UX(templates 신뢰 고지 가독성·정보 위계)
  - [ ] CRITICAL/HIGH 즉시 수정, MEDIUM 이하 BACKLOG 이월
  - [ ] 종합 리포트 versions.md 기록 + 커밋 → 루프 종료·운영자 보고

## 루프 운영 (이번 밤샘 전용 오버라이드)

- **사이클 간격**: 1분 (ScheduleWakeup delaySeconds=60, 운영자 지시).
- **사이클 상한**: 14 (T1~T8 + NEEDS_WORK 재시도 여유). 도달 시 AGENT_STOP 생성·요약 보고.
- **무변화 가드**: 연속 3사이클 커밋 0건 → AGENT_STOP 생성 + QUESTIONS.md 기록 + 푸시 알림.
- **평가**: 단계마다 신선한 컨텍스트 평가자(Write/Edit 금지) PASS 필요. NEEDS_WORK 최대 3회 → [blocked].
- **오프라인 원칙**: 전부 캐시/fixture 검증. courtauction·국토부 라이브 무호출. push 금지(아침 리뷰).
- **진행 기록**: 매 사이클 versions.md 맨 위 KST 항목 + 이 파일의 상태([ready]→[done]·체크박스) 갱신 + git 로컬 커밋.
