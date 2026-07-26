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
- [x] A5. (계측 후 추가) 상세 단건 fast path: 워밍캐시 재현 실측에서 리스트 콜드 1차 요청이
      19초(전량 15k행 로드) — 상세를 load_scored 의존에서 분리. store_rest.fetch_scored_by_case
      (eq.case_no 단건 REST) + web.py _find_by_case 분기(캐시 신선=기존 경로, 콜드=단건).
      계약 테스트: fast path에서 load_scored 호출 시 AssertionError(회귀가드) — 47 passed.
- [x] A6. (계측 후 추가) Vercel 함수 리전 서울 고정: x-vercel-id 실측 icn1::**iad1**(미국 동부
      실행, 서울 Supabase와 콜마다 태평양 왕복) → vercel.json regions=[icn1]. 배포 후
      icn1::icn1 확인(0e99a92).
- [x] A4. 재계측(13:32, 리전+병렬+fast path 합산): 상세 614/1072/1296/2085ms —
      종전 2,005~4,059ms 대비 절반~1/3. 최빈 0.6~1.3s(2s대는 인스턴스 콜드 부팅 편차).
      순수 서버 목표(<800ms)는 워밍 인스턴스에서 달성, 체감 마감은 Phase B 프리페치가 담당.
      pytest 커밋 게이트 전체 그린(5713dbf·0e99a92).

## Phase B — 체감 즉시화 (클릭 → 바로 반응)
- [x] B1. 프리페치 구현(c65e2af): 터치시작/호버 fetch + 상세 `private, max-age=45`(재사용 전제)
      + 관심토글 복귀 캐시버스터 _r(짝 계약). Save-Data·2G 존중, 총 20·동시 3 제한.
      SW는 상세 무개입이라 충돌 없음. 테스트 3종(test_prefetch_wiring.py).
- [x] B2. 클릭 즉시 진행바(.navprog) + bfcache 복귀 정리 — 같은 커밋.
- [x] B3. Playwright 실측(모바일 뷰포트, 터치 1.2s 체류 후 클릭): 클릭→h1 중앙값
      **1,050ms → 444ms** (min 433 — 프리페치 적중 시 즉시 체감. max 1,217 = 미적중 케이스).

## 진행 로그(계속)
- 13:55 Phase A·B 완주. 다음 이터레이션부터 Phase C(낙찰 결과 표시).

## Phase C — 낙찰 결과 표시
- [x] C1. sold_listings 테이블: store.py DDL+upsert_sold/load_sold/load_sold_one,
      Supabase auction_sold_listings 생성(RLS on)+store_rest upsert/fetch/fetch_one.
      테스트 4종(정렬·멱등·정확키·미공개 NULL 유지).
- [x] C2. 파이프라인: run.py _collect_sold_snapshot(diff: 기일 지난 소멸만, 기일 前 소멸=취하
      가능성으로 제외) → replace_all 직전 upsert + 클라우드 미러. prune 5종이 sold 물건의
      자식(사진·권리)을 보존하도록 조정(낙찰 상세 아카이브). 테스트 2종 추가 — 29 passed.
      ⚠ 의미 명확화: sold_price = 재매각 이력의 'sold'(그 회차 실낙찰가, 이후 미납). 정상
      낙찰의 최종가는 법원 비공개 → NULL. UI 라벨에 이 구분을 정직하게 반영할 것(C4).
- [x] C3. 과거 백필(deploy/backfill_sold_listings.py): raw 3.9만행 마이닝 — maeAmt 보유 494키
      중 활성(재매각 진행) 214 제외 → **280행 적재**(로컬+Supabase, 실낙찰가 100%). 가격 없는
      과거 소멸분은 백필하지 않음(채점 스냅샷 부재 = 빈 껍데기 — 앞으로는 C2가 완전 스냅샷).
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
