# 밤샘 루프 — 2026-07-19 야간 (사용자 취침 중 자율 진행)

> 요청: 작업 1·2·3 완수 → 감사 → 진짜 오류만 선별 수정 → 요약.
> 제약: 타 세션 UI 개편 **이미 머지됨**(commit d3dd747: 탭제거 2열 대시보드). 그 레이아웃 위에 얹는다.
> 안전: 품질게이트 통과분만 프로덕션 미러. UI는 before/after 캡처 후 배포. 각 편집 versions.md 기입.
> kill: harness 없음 — 각 단계 테스트 게이트로 자기검증. 프로덕션 파괴 위험작업은 백업 선행.

## 현재 사실 (야간 시작 시점)
- 개편 데이터층 프로덕션 반영 완료: scored 7,800 미러, 진천 3.30억. 아파트+오피 시세성공 55%.
- 미커밋: 데이터층 작업(matcher/pipeline/score/data_gates/naver_store/crawl_naver/run/store/molit/models).
  UI(detail.html·listings.html·web.py)는 타 세션이 **커밋**(d3dd747)해둠 → 내 작업과 공존.
- pytest 2건 실패 = 타 세션 UI 카피 회귀(L3). 내 신규 포함 555 passed.
- 미완: auction_naver_prices 미러 400(신규 컬럼), M3 빈쌍 재크롤, 감사 HIGH(증분갱신·하이브리드).

## 작업 순서 (의존성순)

### TASK 1 — 지속 갱신 자동화 (최우선, 안전)
- [ ] 1a. crawl_naver `--incremental`: 최신 실거래가 N일↑ 오래된 (단지,평형) + 신규 매칭 물건만 얇게 재수집.
- [ ] 1b. M3 수정: 실거래 0건 쌍을 '완료'로 기록(naver_real_trades에 sentinel 또는 별도 done 테이블) → 매 실행 재크롤 방지.
- [ ] 1c. refresh-daily.ps1에 네이버 증분 단계 추가(courtauction+molit 뒤). 실패해도 채점 계속.
- [ ] 1d. 스케줄러 등록 스크립트(Disabled 기본). 오프라인 dry-run 테스트.

### TASK 2 — UI (머지된 2열 대시보드에 얹기)
- [ ] 2a. web.py scope_names: 내부 영문 토큰(band_too_wide/appraisal_mismatch/no_comps/share_sale) → 한국어.
- [ ] 2b. KB밴드 병기: 상세에 "KB시세(네이버 기준) X~Y" 명시 — 사용자가 네이버와 대조 가능하게.
- [ ] 2c. 평형 명시: 시세 옆 "전용 105.22㎡(41평형) 기준" 표기.
- [ ] 2d. 무효화 vs 데이터없음 구분 표기(목록·상세).
- [ ] 2e. 실패 테스트 2건: 새 UI에 맞게 갱신 or 필요 카피 복구.
- [ ] 2f. 로컬 서버 스샷 + before/after 합성(경매-비포애프터 규칙).

### TASK 3 — 마무리 정리
- [ ] 3a. Supabase naver 테이블 lease_low/high 컬럼 추가(Management API) → 네이버 미러 재시도.
- [ ] 3b. 임계값 재보정: band_too_wide 1.6·창계층 신뢰계수를 1,267건 분포로 점검.
- [ ] 3c. 매칭 정확도 표본검수(진천 밖 30~50건) — 오매칭율 실측.

### 감사 & 마감
- [ ] A. 다관점 감사(검증+리뷰 병렬) → 발견 목록.
- [ ] B. 트리아지: 진짜 오류 vs 감사 오탐 판별(각 발견 근거 재확인).
- [ ] C. 진짜 중요한 것만 수정. 오탐·저가치는 기록만.
- [ ] D. 아침 요약(한국어): 한 것·발견·수정·남긴 것.

## 진행 로그
- (야간 시작) 계획 수립, UI 머지 확인. TASK 1 착수.
- TASK 1 ✅ pair_status 테이블(M3 해결: 재크롤 115→2)·--incremental·refresh-daily 통합. 556 passed.
- TASK 2 ✅ band_too_wide 한국어·평형 명시·근거 표본(실패2건 해소)·목록 툴팁. 진천 스샷 검증(KB 3.45~3.70 정합).
- TASK 3 ✅ Supabase 컬럼추가+네이버미러 2351·임계값 재보정(변경불요, 밴드폭 p99=1.44)·매칭검수(KB교차 이탈0,
  N단지 가드 추가, 오매칭 3건 삭제). 559 passed.
- 감사 ✅ overnight-final-audit(19에이전트, 리뷰14→진위검증→종합): 🟢 초록, 활성결함 0. 확정13(MED2·LOW11)·오탐1기각.
- 수정 ✅ 진짜중요 4건: data_gates 면제 출처기반 축소·detail.html 보류사유 정정·KB폴백 라벨·run.py NULL정합
  + 하드닝 1(real_trades_for_case 게이트). 테스트 559 passed, 게이트 PASS. 미수정 LOW는 기록만(영향0).
- **미배포**: UI 변경은 사용자 배포결정 대기(before/after=경매-비포애프터\after_*.png). 데이터층은 이미 프로덕션 반영됨.

## 완료 요약 (아침 확인용)
- TASK 1·2·3 전부 완료 + 감사 통과. 코드 미커밋(사용자 리뷰·배포결정 대기). push=배포 규칙이라 임의 배포 안 함.
- 다음 사용자 액션: ①UI 변경 배포 여부 결정(스샷 확인 후) ②일일 스케줄러 활성화 여부(refresh-daily에 네이버 증분 통합됨, Disabled 상태).
