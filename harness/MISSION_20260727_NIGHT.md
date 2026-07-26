# MISSION 2026-07-27 — 상세 즉시화 + 낙찰 결과 표시 (밤샘루프)

> 운영: 크론 2분 간격 자율 루프. 킬스위치 = `harness/AGENT_STOP_NIGHT` 파일 생성 시 즉시 정지.
> 각 이터레이션: ①이 파일의 미완(☐) 최상단 작업 1개 진행 ②증거 기록 ③체크 ④진행바 보고.
> 원칙: 추측 최적화 금지(계측 먼저), 모름을 확정으로 표기 금지(낙찰가 미공개=미공개),
> 커밋은 파일 선별 + `git commit -F`, versions.md 필수, UI 변경은 비포/애프터.

## 배경 (사전 계측 — 2026-07-27 12:25 실측)
- 프로덕션 상세 HTML: 워밍 2,263/4,059/2,005ms · 105~114KB (콜드 30~60초 별도).
  원인 추정 = property_detail이 Supabase REST를 순차 왕복(rights→photos→tenants(게이트로 스킵)
  →survey→…). A1에서 fetch별 분해 계측으로 확정할 것.
- 사용자 증상: 목록에서 물건 클릭 → 수 초 빈 화면 → "안 넘어간다"고 체감.
- 낙찰가 데이터 제약(7/24 실측): 정상 낙찰은 dspslAmt 항상 null. **실낙찰가는 재매각 물건의
  maeAmt에만 존재**(schedule 'sold' 키 백필, 244+건). 소멸 물건 낙찰가는 원천 부재 →
  "낙찰 종결 · 낙찰가 미공개"로 정직 표기. 과거 = raw_listings(3.9만행) maeAmt 마이닝.

## Phase A — 서버 응답 단축 (목표: 워밍 상세 <800ms)
- [x] A1. 프로파일 실측(12:40): fetch_rights 396 + photos 404 + survey 372 + building 407
      = 순차 1,579ms(콜마다 신규 TLS). 단순 병렬만으로 918ms.
- [x] A2. 전체 리스트·전체 rights는 램다 내 TTL(600s) 캐시 — 워밍 히트 0ms, 콜드에만 섞임.
      상세 워밍 지연의 주범 아님 확인 → 순차 REST가 주범으로 확정.
- [x] A3. 구현: ①store_rest에 커넥션 풀 Session(콜마다 TLS 핸드셰이크 제거, 목킹 호환 유지)
      ②web.py 상세 5종 조회(권리·사진·임차인·점유·건축물) ThreadPoolExecutor 병렬 + 실패 격리
      유지. 테스트 3종 신설(test_detail_parallel_fetch.py) — 관련 97 passed.
- [ ] A4. 재계측: 배포 후 워밍 3회 — 목표 <800ms. pytest 전체 그린(커밋 게이트)

## Phase B — 체감 즉시화 (클릭 → 바로 반응)
- [ ] B1. 목록→상세 프리페치: touchstart/mouseenter 시 상세 HTML 미리 fetch(중복 방지,
      Save-Data 존중, 동시 제한). SW 상세 무개입 계약과 충돌 없는지 확인
- [ ] B2. 클릭 즉시 피드백: 카드 눌림 + 상단 전환 프로그레스 바
- [ ] B3. Playwright 실측: 클릭→상세 h1 가시화 시간 비포/애프터 수치 + 캡처

## Phase C — 낙찰 결과 표시
- [ ] C1. 데이터: `sold_listings` 테이블(로컬 store.py + Supabase DDL). 스키마 = scored 핵심
      스냅샷 + sold_price(실낙찰가|NULL) + sold_date + evidence + snapshot_at. 복합PK
- [ ] C2. 파이프라인: run.py replace_all 직전 diff — 소멸 물건을 sold_listings에 보존(프룬 전!).
      maeAmt/schedule 'sold' 있으면 sold_price, 없으면 NULL. Supabase 미러. 테스트
- [ ] C3. 과거 백필: raw_listings 전체 maeAmt 마이닝 → 소급 적재(멱등)
- [ ] C4. UI: ①'최근 낙찰' 목록(낙찰가 보유 우선) ②상세 낙찰모드 — 배너 + 동일 정보 +
      **시뮬레이터 입찰가=실낙찰가 고정**, 미공개면 고정 없이 안내만
- [ ] C5. 정직성 가드 테스트: 미공개에 0원/추정가 금지, last_sold_floor 혼용 금지
- [ ] C6. Playwright 검증 + 비포/애프터 캡처

## Phase D — 리뷰·감사 (구현 완료 후)
- [ ] D1. code-reviewer 리뷰(이번 변경 diff 범위)
- [ ] D2. 지적사항 실검증 — 재현/근거 있는 것만 수정 대상 확정(기각 사유 기록)
- [ ] D3. 확정 수정 + pytest 전체 + Playwright 재검증
- [ ] D4. versions.md + 커밋(파일 선별) push + 배포 + /health

## Phase E — 마감
- [ ] E1. 프로덕션 최종 실측(상세 ms·클릭 TTI·낙찰 표시·시뮬 고정)
- [ ] E2. 최종 보고 + 루프 종료(CronDelete)

## 진행 로그
- 12:30 미션 작성 · 사전 계측 기록 · 루프 가동
