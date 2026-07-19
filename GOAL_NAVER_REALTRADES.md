# GOAL: 네이버 complexNo 실거래 도입 — 시세 정확도 대개편

> 2026-07-19 시작. 진천태왕아너스1단지 실측으로 확인된 문제(폴백 오염 est 4.69억 vs 실제 2.8억,
> 시세추정불가 ~20%, 차트 잡동 comps)를 **네이버 complexNo 기반 국토부 실거래 이력**으로 해결한다.
> 근거: 세션 실측 S0~S3 (12개월창 교정 est 2.80억 / 5년창은 가격 아닌 등급 부풀림 / 직거래 1건이 경계표본).

## 원칙

- **네이버 크롤은 절대 순차**(밴 회피 — 병렬 금지). 코드 편집도 직렬(타 세션이 detail.html·web.py 수정 중 + versions.md 훅 충돌 방지). **병렬 허용 = 검증·리뷰·오프라인 재파싱**.
- `templates/detail.html`·`src/web.py`는 이 작업에서 **수정 금지**(타 세션 UI 개편과 머지 충돌 방지). UI 반영은 데이터 계층(서버가 주는 값)으로만 — 타 세션의 "탭 없이 한 화면" 레이아웃이 새 데이터를 그대로 받게 한다.
- 원본 JSON 전량 저장(C1) — 파싱 버그가 나도 재크롤 없이 회수.
- 모든 편집 시 versions.md 기입(KST).

## 스케줄표

| # | 작업 | 산출물 | 의존 | 상태 |
|---|---|---|---|---|
| P0 | **prices/real 라이브 스모크** (complexNo 24958, 3~5콜): year 최대치·페이지네이션(addedRowCount)·필드(deleteYn/등기일/직거래)·평형목록(105㎡ 존재?) 실측 확정 | 실측 노트(이 파일 하단) | — | ✅ |
| T1 | DB 스키마 확장: `naver_real_trades`·`naver_complexes`·`naver_kb_history`·`naver_articles` 신규 + `naver_prices` 컬럼 추가 → **신규 파일 `src/naver_store.py`** | naver_store.py + 테스트 | — | ✅ |
| T2 | 국토부 파서 확장: `dealingGbn`(직거래)·`rgstDate`(등기일) 파싱 + `Trade.is_direct/rgst_date` | molit_client.py·models.py + 테스트 | — | ✅ |
| T3 | **캐시 재파싱(크롤 0번)**: naver_cache.json 37MB → naver_complexes(세대수·사용승인일·용적률·주차·전세가율·매물수) 적재 | scripts/reparse_naver_cache.py + 실행 | T1 | ✅ |
| T4 | NaverClient 확장: `real_prices()`(addedRowCount 커서 루프+수신율 검증+해제 처리), `overview()`(areaNo 소스), 호가 페이지네이션·항상수집(C2 수정), naver_match 전용·공급 이중 대조 | naver_client.py·naver_match.py | P0 | ✅ |
| T5 | 크롤러 개편: 단지단위 dedup(C5)·우선순위 큐(오염/시세추정불가/no_kb 먼저, C6)·진행상태 저장·재시작·원본 raw 저장(C1)·KB시계열 저장 | deploy/crawl_naver.py | T1·T4 | ✅ |
| T6 | **소규모 라이브 검증**: 진천태왕아너스 포함 ~10건 크롤 → naver_real_trades 적재 확인 | 실측 로그 | T5 | ✅ |
| T7 | 채점 주입: naver_real_trades → Trade 변환, **scope=same_complex_same_area 직접 부여**(이름매칭 우회), 창 계층화(12개월 부족시 확장+신뢰↓), 직거래 표시 | matcher.py·pipeline.py | T1·T2 | ✅ |
| T8 | 가드 2종: KB 밴드 교차검증 플래그, same_dong_fallback 밴드폭 과대 가드 | score.py 또는 matcher.py | T7 | ✅ |
| T9 | 재채점(소규모→검증) + pytest 전체 통과 | 테스트 로그 | T6·T7·T8 | ✅ |
| T10 | 전량 우선순위 크롤 (백그라운드, 수 시간, 순차) | 크롤 로그 | T9 | 🔄 백그라운드 |
| T11 | 전체 재채점 → auction.db → Supabase 적재 | DB | T10 | ⏳ |
| T12 | 차트·UI 데이터 확장(comps 전체기간·KB밴드·직거래 뱃지) — **서버 데이터만**, 템플릿은 타 세션 머지 후 | 데이터 준비 | T11 | ⏳ |
| T13 | 비포/애프터 캡처(경매-비포애프터 규칙) → 사용자 배포 결정 | 합성 이미지 | T12 | ⏳ |

## P0 실측 노트

- (오프라인 캐시 조회, 확정) **complexNo 24958** = 진천태왕아너스1단지, cortarNo 2729011700,
  세대수 182, 사용승인 2008-02-22.
- (오프라인 캐시 조회, 확정) 캐시된 complexPyeongDetailList에 **전용 84.96㎡(34B) 1개 평형만** 존재 —
  경매물건 105.22㎡와 불일치. 이 물건이 naver_prices 0건(무매칭)인 원인 후보.
  캐시가 불완전한지, 실제로 105㎡ 평형이 없는지는 라이브 스모크로 확정해야 함.
### 스모크 실측 결과 (05:30 KST, 6콜, 원본=evidence/smoke_prices_real_24958.json)

- **평형 목록(라이브) 6개**: areaNo 1/2/3(전용 84.96, 34A/B/C)·4(84.06, 37py)·**5(전용 105.22, 공급 136.83, 41py)**·6(115.77, 44py)
  → **경매물건 105.22㎡ = areaNo 5 정확 일치**. 캐시(1개 평형)는 불완전했음 — 무매칭 원인은 캐시 불완전.
  → 사용자가 참조한 네이버 실거래표는 areaNo=2(84.96㎡) 타입이었음(응답과 행 단위 일치).
- **prices/real 응답 구조**: 루트 `{addedRowCount, areaNo, realPriceBasisYearMonth, realPriceOnMonthList, totalRowCount}`,
  행 필드 `{dealPrice(만원), deleteYn(옵션 — 취소행에만), exclusiveArea, floor, formattedPrice,
  formattedTradeYearMonth, leasePrice, rentPrice, representativeArea, tradeDate, tradeMonth, tradeType, tradeYear}`.
  등기일·직거래 필드 **없음** → 직거래는 국토부 dealingGbn으로(T2).
- **페이지네이션 실측**: 1p 6행(addedRowCount=6) → 커서 6으로 2p 7행(addedRowCount=13, 2024년까지 내려감). 동작 확인.
  totalRowCount=201 (요청 areaNo=2인데 201 — 전 평형 or 전 기간 합산 의심) → 크롤러는 **빈 페이지까지 루프**(+상한 캡)
  하고 행별 exclusiveArea로 사후 필터.
- **year=15 무효**: year=5와 동일 응답. year 파라미터로 확장 불가 — 커서 루프가 실제 어디까지 과거로 가는지 T6에서 확정.
- deleteYn: 2026-03-04 2.8억 2행 중 1행에만 존재(취소분) — 행별 옵션 키, 미존재=정상거래로 처리.

## 진행 로그

- 05:14 KST git 정찰: main 브랜치, 타세션 미커밋 = README.md·coords_cache·web.py·detail.html·versions.md
  → detail.html·web.py 수정 금지 원칙 확정. 스케줄표 작성.
- 05:30 KST P0 완료(위 실측). T1(naver_store)·T2(국토부 직거래) 착수.
- 05:55 KST T1·T2·T3 완료 — 스키마 4테이블, 직거래·등기일 파싱, 캐시 재파싱(단지 1,373·KB 2,528·호가 3,049).
- 06:10 KST T6 실측 — **커서가 전 기간(2006~) 수집**(year=5는 차트용), 9쌍 2,069행. 진천 105 평형=희소 확인.
- 06:25 KST **호가 API 사망 발견** — 7/15 감사 픽스(priceMax 999억)가 서버 거부(200+error바디)로 호가
  수집 전멸 상태였음 → 만원 단위(999999)로 교정 + error 감지. isMoreData 종료 신호 채택.
- 06:40 KST T7·T8·T9 완료 — 진천 재채점 실측: est 4.69억→3.30억(scope same_complex_same_area,
  신뢰 0.75, KB·감정가와 삼자 정합). 테스트 555 passed. 스케줄표 P0~T9 상태 갱신.
- 06:45 KST T10 착수 — 전량 백필 크롤(1,089쌍 잔여) 백그라운드 시작.
- 07:20 KST 초기 ETA 45h 실측 → 속도튜닝 재시작(실거래 25p 캡·백필 호가 제외, 쌍당 ~90→~7콜).
  상태판(evidence/backfill_status.html, 5분 갱신) 개통. 상태판 "마지막 런" 파싱 버그 수정.
- 07:50 KST **T11 리허설(DB 사본, 크롤과 병렬)**: 전체 파이프라인 7,800건 완주 — ①진천 교정 재확인
  (4.69억→3.30억, same_complex_same_area, conf 0.75) ②band_too_wide 가드 183건 발동(오염 폴백 정직 무효화)
  ③⚠교훈: --live-months 12로 돌리면 국토부 풀이 좁아져 est 865→601로 감소 —
  **본 T11은 프로덕션과 동일하게 --live-months 24 필수**. 크롤 완료 후 실거래 주입되면 커버리지 반등 예상.
