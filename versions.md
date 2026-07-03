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

## 2026-07-03 13:51 KST — 🎯 신뢰루프 사이클 #3: T3 비교군 scope 저장 + 계층 매칭
- 무엇: 시세가 "어떤 집합"에서 나왔는지 저장·게이트(문서 13장 — 비교군이 틀리면 통계가 좋아도 틀린다).
  - matcher.py: match_trades_scoped 계층 매칭 — ①같은 단지·같은 평형(±3%) ②같은 단지·인접
    평형(±area_band) ③같은 법정동 폴백. 같은 평형 표본이 있으면 인접 평형 혼입 금지(희석 방지).
    MarketEstimate(est, matched, scope) 도입, 기존 함수는 하위호환 래퍼.
  - store.py v3: market_scope 컬럼(v2→ALTER, v1→재생성 이관 한 번에 v3). 실DB 2521건 보존.
  - score.py: scope 게이트 — same_complex_same_area 외 표본으로는 '차익 유력'/'양호' 불가('관심' 캡).
  - digest.py: 추천 TOP도 same_complex_same_area(+레거시 '')만.
  - detail.html: '시세 비교군 (어떤 거래로 추정했나)' 행 — 5개 scope 한국어 라벨.
  - test_confidence_samples 2건 T3 의미론 갱신(밴드 확대 효과는 같은평형 표본 부재 시로 한정 — 정당성
    평가자 확인).
- 증거: evidence/t3_scope.txt — 실DB v3 이관(2521==2521), 샘플 6건 scope 전부 기록, 추천 TOP 5
  전부 same_complex_same_area, 상세페이지 비교군 행 노출 True.
- 게이트: pytest 282 passed(+15), ruff 클린.
- 평가자: PASS (a~e 비판 포인트 전부 무해 확인: elif 우선순위 안전·래퍼 우회 0곳·마이그레이션 무손실).
  LOW 2건(레거시 '' 이중통과=의도적, 등급명 리터럴=기존 관행), INFO(캡'관심' 목록 구분 → T7).
- 커밋: (이 항목과 함께 커밋)
- 다음: 사이클 #4 = T4 시세 단일값 → 2선 가격 밴드(검증 하한가/기준가, profit_low 기준 추천).

## 2026-07-03 13:38 KST — 🏢 신뢰루프 사이클 #2: T2 시세추정 아파트·오피스텔 한정 + 유형불명 차단
- 무엇: 유형 매핑 실패 시 모든 kind 거래가 비교군에 혼입되던 치명 결함 제거(문서 5장) +
  v1 시세추정을 아파트·오피스텔로 제한(문서 6장 — 빌라/상가/토지는 개별성 때문에 동네 중앙값 위험).
  - matcher.py: `_kind_ok = want is not None and trade.kind == want`(유형불명·미태깅 통과 제거),
    SUPPORTED_ESTIMATION_KINDS={apt,officetel}, estimate_market_price 미지원 유형 조기 차단.
  - score.py: '미지원유형' 등급 신설 — '시세추정불가'(데이터 부족)와 원인 구분(정책상 미추정).
  - 템플릿 5곳 warnbadge + methodology 정책 문단, report/digest 경고등급 목록에 미지원유형 추가.
  - 구정책 테스트 4건 정책반전 갱신(백테스트 빌라 제외·토지 추정금지·미태깅 불매칭), 신규 13개.
- 증거: evidence/t2_apt_only.txt — 샘플 6건 중 다세대만 미지원유형·est None, digest TOP 5 전부
  아파트/오피스텔, GET / 뱃지·/methodology 문구 노출. 평가자가 독립 재실행으로 재현 확인.
- 게이트: pytest 267 passed(+14), ruff 클린.
- 평가자: PASS. MEDIUM(죽은 rh/sh/nrg/land 라이브 수집 쿼터 낭비)→B15 이월, LOW 2건 즉시 수정.
  기존이슈 노트: digest TOP에 '위험' 등급이 profit 정렬로 1위 노출 가능(경고칼럼은 표시) → B15에 포함.
- 커밋: (이 항목과 함께 커밋)
- 다음: 사이클 #3 = T3 비교군 scope 저장(same_complex_same_area 등 4분기 + 추천 게이트).

## 2026-07-03 13:29 KST — 🔑 신뢰루프 사이클 #1: T1 식별자 구조 수정 (복합 PK + raw 보존)
- 무엇: case_no 단일 PK가 같은 사건의 다른 물건번호를 조용히 덮어쓰던 구조 결함 수정(문서 3장, 최우선).
  - store.py: `PRIMARY KEY (court, case_no, item_no)` + user_version=2 + 구스키마 자동 마이그레이션
    (트랜잭션, 실DB 2521건 무손실 이관 확인) + raw_listings 원본보존 테이블 + save_raw_records.
  - models.py: AuctionListing/ScoredListing에 court·item_no·doc_id + uid 프로퍼티.
  - courtauction_fields.py: CourtAuctionRecord.item_no(maemulSer) 명시 + to_auction_listing 관통.
  - score.py 두 분기 관통, run.py raw 저장 배선, pipeline 전국병합 키·cache record_key 복합키화
    (구키는 법원 누락으로 타법원 동번호 충돌 여지 — 캐시 diff 1회 전량신규 churn은 docstring 명시).
  - 백업: data/backup/auction.db.bak-20260703-1315 (스키마 변경 전, LOOP.md 준수).
- 증거: evidence/t1_pk_migration.txt (v0→v2 이관·2521==2521·복합키 공존 데모·멱등성) Read 확인.
- 게이트: pytest 253 passed(기준 245, +8 tests/test_store_identity.py), ruff 클린.
- 평가자: PASS (MEDIUM 2건 — 상세라우트·watchlist/backtest/compare의 case_no 단일키 잔존 → B14 이월).
- 커밋: (이 항목과 함께 커밋)
- 다음: 사이클 #2 = T2 추천 대상 아파트 제한 + 유형 불명 시 시세추정불가(matcher._kind_ok 개편).

## 2026-07-03 13:13 KST — 🚀 데이터 신뢰도 개편 루프 착수 (GOAL_DATA_TRUST.md 생성)
- 무엇: 운영자가 타 세션에서 작성한 `데이터_신뢰도_문제의식_및_개선방향.md`(18장 체크리스트)를
  T1~T7 + T8(최종 감사) 단계로 구조화한 GOAL_DATA_TRUST.md 생성. PROGRESS.md 현재 루프 전환.
  운영자 지시: 사이클 간격 1분, 체크리스트 순서대로, 완료 후 다관점 감사(기능+디자인).
  핵심 문제 코드 대조 확인: store.py:11 `case_no TEXT PRIMARY KEY`(물건 덮어씀 위험),
  matcher.py:72-74 `want is None → 통과`(유형불명 비교군 오염) — 문서 진단과 일치.
- 증거: (셋업 사이클 — 코드 변경 없음)
- 평가자: - (셋업)
- 커밋: (이 항목과 함께 커밋)
- 다음: 사이클 #1 = T1 식별자 구조 수정(auction.db 백업 → 복합 PK → raw 보존 → 마이그레이션).

## 2026-07-03 04:16 KST — 🔎 사이클 #12(감사·최종): #10/#11 다관점 감사 + HIGH 수정 → 루프 종료
- 무엇: #10 B7 CSV·#11 B9 스냅샷 판정 변경분(git diff HEAD~4 HEAD)을 3관점 병렬 감사(보안·코드품질·침묵실패).
  - **종합 판정**: 신규 CRITICAL/HIGH 확정 0(아래 교차검증). 침묵실패 단독 HIGH 1건은 채택·즉시 수정, 단독 CRITICAL 1건은 검증 후 MEDIUM 강등·이월.
  - **HIGH 수정(침묵실패, 채택)**: /export.csv가 샘플 폴백 데이터를 출처 표시 없이 파일로 저장 → 저장된 CSV만
    보면 라이브/샘플 구분 불가(프로젝트 'data_source 오인방지' 원칙과 정합). web.py export_csv에서 data_source!=db면
    파일명 `auction_arbitrage_SAMPLE.csv`로 각인. 테스트 추가.
  - **CRITICAL 강등(침묵실패 단독)**: "빈 스냅샷 시 events가 옛 prev-truthy 기준"이라는 지적 → 실코드 검증 결과
    'prev에 없는 신규 case_no는 이벤트 없음'은 detect_changes 보편 설계(빈{}·비어있지않은 신규case 둘 다 []),
    B9 회귀 아님. 트리거=직전 전체 0건 극단엣지. → MEDIUM 문구정밀도로 강등, BACKLOG B12 이월.
  - 보안·코드품질 관점: CRITICAL/HIGH 0, APPROVE. 공통 MEDIUM=CSV 인젝션(기존 B11로 정확히 이월 확인).
  - 이월(BACKLOG): B12(비교불가 vs 변동없음 문구), B13(빈 CSV 헤더행 유지). B11(CSV 인젝션) 유지.
- 증거: 감사 3에이전트 최종 리포트 + detect_changes 반증 실행(빈{}·신규case 동일 [] 확인).
- 게이트: pytest 245 passed(+1: _SAMPLE 파일명), ruff 클린.
- 평가자: 감사 패널 종합 APPROVE(HIGH 반영, CRITICAL 반증).
- 커밋: b53da1d (로컬, push 안 함)
- 다음: **밤샘 루프 종료**(#12 = 12%3==0 감사 사이클, 최대 사이클 도달). 추가 예약 없음 — 운영자 아침 리뷰 대기.

## 2026-07-03 03:49 KST — 🩹 사이클 #11: B9 스냅샷 '없음' vs '빈 스냅샷' 구분 (감사#9 이월)
- 무엇: 관심물건 페이지가 매물 0건 새로고침으로 정상 저장된 빈 스냅샷 {}을 "이전 스냅샷 없음"으로
  오안내하던 침묵성 UX 버그 수정.
  - web.py watchlist_page: `snapshot_missing = (not snap_path.exists() and not snap_corrupt)` —
    'prev가 falsy'가 아니라 '파일 존재'로 판정. 손상은 corrupted 배너가 별도 처리(3-상태 정합).
- 증거: evidence/snapshot_missing_smoke.txt ([없음]안내O·[빈{}]오안내사라짐+변동없음O·[손상]배너O) Read 확인.
- 게이트: pytest 244 passed(+2), ruff 클린.
- 평가자: PASS (CRITICAL/HIGH/MEDIUM/LOW 0).
- 커밋: a17e9ac (로컬, push 안 함)
- 다음: 사이클 #12 = 감사 사이클(12%3==0) → 다관점 병렬 감사 후 CRITICAL/HIGH만 수정하고 **루프 종료**(운영자 아침 리뷰 대기).

## 2026-07-03 03:25 KST — ⤓ 사이클 #10: B7 CSV 내보내기 (/export.csv)
- 무엇: 목록 필터·정렬 결과를 CSV로 다운로드(엑셀 검토용, overseas-foreclosure 모방, 오프라인 순수조회).
  - report.py `csv_text(items)->str` 헬퍼 도입 → `to_csv`가 이를 재사용(파일 저장/웹 다운로드 단일 소스, 중복 구현 제거).
  - `GET /export.csv`가 `_filtered(request.args)` 재사용 → 목록과 동일 필터·정렬 보장. UTF-8-SIG BOM(엑셀 한글) +
    `Content-Disposition: attachment`.
  - listings.html 필터바에 현재 쿼리스트링 보존 "⤓ CSV 내보내기" 링크.
- 증거: evidence/export_smoke.txt (200·text/csv·attachment·BOM True·데이터 6행=/api/listings 일치·min_profit=1 필터 4행 일치) Read 확인.
- 게이트: pytest 242 passed(+8), ruff 클린.
- 평가자: PASS. LOW(to_csv 중복 Path 호출) 즉시 정리, MEDIUM(CSV 인젝션 완화)→BACKLOG B11 이월.
- 커밋: 7a88150 (로컬, push 안 함)
- 다음: 사이클 #11 = B9 스냅샷없음 vs 빈스냅샷 구분(감사 이월). #12는 감사 사이클 후 자동 종료.

## 2026-07-03 02:58 KST — 🔎 사이클 #9(감사): B6/B8 다관점 감사 + HIGH 즉시 수정
- 무엇: 3관점 병렬 감사(보안·코드품질·침묵실패)로 #7 B8·#8 B6(`git diff HEAD~4 HEAD`) 점검.
  종합 CRITICAL/HIGH=0 (침묵실패 HIGH 1건만) → LOOP #9 절차대로 HIGH만 즉시 수정.
  - **HIGH 수정(침묵실패)**: `/compare`가 요청 case N건 중 조회된 M건만 렌더하고 사라진 건을 침묵 드롭.
    web.py compare_page에 `requested`/`missing_cases` 계산 전달, compare.html에 "요청 N건 중 M건만 조회"
    n-warn 배너 + 빠진 사건번호 노출(watchlist의 missing 처리 원칙을 compare에도 반영).
  - **보안 MEDIUM 동반 처리**(수정 지점 동일): `case` 파라미터 `[:MAX_COMPARE]` 하드캡(방어).
  - 이월(BACKLOG): B9 스냅샷없음vs빈스냅샷 구분(code-review MEDIUM), B10 워치리스트 쓰기실패 로깅·사용자
    메시지+corrupted 문구 일반화(silent-failure MEDIUM/LOW).
- 증거: 감사 3에이전트 최종 리포트(보안 0C/0H·1M, 코드품질 0C/0H·1M APPROVE, 침묵실패 1H·1M·1L).
- 게이트: pytest 234 passed(+2: dropped 배너·case 캡), ruff 클린.
- 평가자: 감사 패널 APPROVE(HIGH 반영 완료).
- 커밋: 3fd93f3 (로컬, push 안 함)
- 다음: 사이클 #10 = B7 CSV 내보내기 `/export.csv`(report.to_csv 재사용).

## 2026-07-03 02:20 KST — ↔️ 사이클 #8: B6 물건 비교 (/compare)
- 무엇: 후보 물건 2~4건 나란히 비교(auction.com/Zillow compare 모방, 오프라인).
  - src/compare.py `select_for_compare`(순서보존·중복제거·없는 case_no 무시·최대4) 순수함수.
  - `GET /compare?case=..&case=..` SSR 비교표(경고·예상차익·갭·최저가·시세·취득세·취득원가·감정가·유찰·면적·신뢰·기일·소재지·사건번호), <2건 안내.
  - watchlist 페이지에 '↔ 비교하기(상위 N건)' 링크 — 관심물건을 선택집합으로 재사용(cart 상태 불요).
- 증거: pytest **232 passed**(+7), ruff 클린, evidence/compare_smoke.txt(/compare 2건 200·양쪽 렌더·1건 안내).
- 평가자: 게이트 직접 실행 + 자체검토(입력면=getlist→dict조회, Jinja 이스케이프 — 저위험). 전체 다관점 감사는 다음 #9(감사 사이클)에서 이 diff 포함 리뷰.
- 커밋: (이 커밋, 로컬)
- 다음: 사이클 #9 = 감사 사이클(3관점 병렬 — #7 B8·#8 B6 포함 최근 변경 리뷰).

## 2026-07-03 01:45 KST — 🛡️ 사이클 #7: B8 침묵실패 보강 (watchlist 손상처리·원자쓰기)
- 무엇: 사이클 #6 감사의 MEDIUM/LOW 침묵실패를 구현.
  - watchlist.py: 손상 JSON 폴백(`_safe_load_json` — JSONDecodeError→logger+빈값, 500 대신 원인로그) +
    `load_watchlist_status`/`load_snapshot_status`(손상여부 반환) + **원자적 쓰기**(`_atomic_write` tempfile+os.replace, 동시 토글 유실 방지).
  - web watchlist_page: corrupted/snapshot_missing 플래그 → watchlist.html **손상 배너** + '변동 없음/스냅샷 없음' 원인 구분(오해 방지).
  - models: est_market_price 계약(None=추정불가, sentinel 금지) 주석. calendar/stats/watchlist data_source 배너는 base 헤더가 이미 처리(불요).
- 증거: pytest **225 passed**(+5: 손상폴백·상태플래그·없음≠손상·원자쓰기 잔여없음·손상배너 200), ruff 클린.
- 평가자: 게이트 직접 실행 + 자체 diff 검토(소규모 내부 하드닝 — 전체 다관점 감사는 #9에서). 
- 커밋: (이 커밋, 로컬)
- 다음: 사이클 #8 = B6 물건비교(/compare) 구현.

## 2026-07-03 01:24 KST — 🔎 사이클 #6(감사): 3관점 리뷰 → HIGH 1건 수정(stats 기타버킷 원인분리)
- 무엇: 병행 루프가 추가한 미감사 기능(웹 라우트·watchlist·calendar·stats)을 3관점 병렬 감사
  (security-reviewer·code-reviewer·silent-failure-hunter).
  - **보안**: CRITICAL/HIGH 0. LOW 3(watchlist 락없음·POST CSRF·_safe_back Host) — 전부 로컬 단일사용자 전제서 무위험(외부공개 재검토).
  - **코드품질**: APPROVE. CRITICAL/HIGH 0. MEDIUM 1(watchlist 비원자 쓰기). 캘린더 경계·stats None처리·게이트 우회없음 검증.
  - **침묵실패**: **HIGH 1** — `stats.by_sido`가 '주소없음(파싱실패)'과 '시도미인식'을 한 '기타'로 뭉갬 → 데이터품질 오해.
    **즉시 수정**: `_sido_key`로 '주소없음' vs '기타(시도미인식)' 분리(test_stats 갱신·신규). + MEDIUM 3·LOW 2는 **B8**로 백로그.
- 증거: pytest **220 passed**, ruff 클린, /health·/stats·/api/stats 200 스모크(by_sido 원인분리 확인).
- 평가자: 3 병렬 리뷰어 교차검증 + 게이트 직접 실행.
- 커밋: (이 커밋, 로컬)
- 다음: 사이클 #7 = B8(침묵실패 보강: watchlist JSON손상 처리·원자쓰기·데이터출처 배너) 구현.

## 2026-07-03 01:02 KST — 🔍 사이클 #5(탐색): 해외 레퍼런스 → B6 비교·B7 CSV 추가, B3 지도 blocked
- 무엇: BACKLOG [ready] 최상위 B3(지도)를 착수하려 했으나 **좌표계 문제로 blocked** 판정.
  courtauction `wgs84Xcordi/Ycordi`=정수부만(127/37, 무용), `xCordi/yCordi`=투영좌표인데 역산 경도 128.2°로
  서울 불일치 → CRS 모호 + pyproj 미설치. 무인 손변환은 핀 오배치 위험 → QUESTIONS Q1(운영자 결정) 등록.
  대신 탐색 모드: 해외 레퍼런스(Zillow foreclosure·auction.com) 분석(docs/references/overseas-foreclosure.md) →
  오프라인 구현가능 갭으로 **B6 물건비교(/compare)·B7 CSV 내보내기** [ready] 추가.
- 증거: docs/references/overseas-foreclosure.md 작성, BACKLOG 갱신(B3 blocked·B6/B7 ready), QUESTIONS Q1.
  코드 변경 없음(md만) → pytest 220 불변.
- 평가자: 탐색 사이클(구현 없음, 평가 생략) — LOOP.md 2' 절차.
- 커밋: (이 커밋, 로컬)
- 다음: 사이클 #6 = B6 물건 비교(/compare) 구현(오프라인, TDD). Q1은 비블로킹이라 루프 계속. 푸시알림은 운영자 취침으로 아침 확인용 QUESTIONS만.

## 2026-07-03 00:44 KST — ✅ 사이클 #4: B2 관심물건 웹 UI (/watchlist) 완결 커밋 + 밤샘루프 재개
- 무엇: 이전 세션이 미커밋으로 남긴 B2(관심물건 웹) 사이클을 게이트 통과 확인 후 완결.
  /watchlist 페이지 + 토글 버튼(목록·상세 ☆), src/watchlist.py 웹 승격, 신규 test_watchlist_web.py.
  이어서 운영자 취침 — harness/LOOP.md 자율 성장 루프를 밤샘 재개(ScheduleWakeup 자가페이싱, 로컬 커밋 only).
- 증거: pytest **220 passed**(+8), ruff 클린, watchlist_smoke add/list/page/remove 200 확인(Read).
- 평가자: 게이트 직접 실행 확인(이전 세션 code-reviewer APPROVE 이력).
- 커밋: (이 커밋, 로컬)
- 다음: 밤샘 루프가 B3 지도(/map)부터 사이클 진행. 3사이클마다 감사(다관점) 삽입. push는 아침 운영자 리뷰.

## 2026-07-02 16:15 KST — 🚀 T5 완료: 전국 재채점·배포 (+PK충돌 규모 확인)
- 무엇: 캐시 4,956건 + 라이브 MOLIT(ym 202605) 재채점 완료(exit 0) → auction.db replace_all 적재 → 서버 재시작·검증. (별도 세션이 /stats·/calendar 사이클 병행 중 — 이 항목은 T5 배포분.)
  - **배포 검증(게이트 통과)**: /health `data_source: db`, 목록 렌더, 현재 마커(예상차익·gapmeter·권리미확인·라이브 DB·1주택) 전부. http://127.0.0.1:8000.
  - **결과 분포**(DB 2,586건): 차익 양수 370(최대 22억)·차익없음 318·시세추정불가 1,898(오늘 MOLIT 502 잦음+비주거 물건+표본<2건). 전부 권리미확인(D 전).
  - **🔴 PK 충돌 실측**: 4,956 수집인데 DB 2,586 — `case_no`만 PK라 다물건 사건이 서로 덮어써 **2,370건(48%) 소실**. 미결 이슈 #5가 전국 규모에서 심각. **다음 최우선 = doc_id(사건번호+물건번호) PK 전환**.
- 증거: 재채점 로그 evidence/refresh-20260702-150709.log exit 0·"저장 4956", 서버 200, DB 등급 분포 쿼리 확인.
- 평가자: 직접 실행(크롤 exit·서버 /health·DB 쿼리).
- 커밋: (이 커밋)
- 다음: ① doc_id PK 전환(다물건 소실 해소, 재채점 필요) ② D 물건상세 정찰(권리+감정평가서 사진) ③ MOLIT 재시도 여유↑.

## 2026-07-02 16:14 KST — 📅 사이클 #3: B1 경매 일정 캘린더 (/calendar)
- 무엇: 지지옥션 '경매 캘린더' 모방(TDD).
  - `src/sale_calendar.py` — 순수 함수 5개(기일별 그룹핑·예정/과거 분리(오늘 포함)·월 묶음·
    기일미상 카운트·요일). **비ISO sale_date 방어**(크롤 원문 잔여물은 미상으로 집계, 오정렬 차단).
  - `GET /calendar` — 월별 뷰, 예정/전체 토글(?all=1), 오늘 뱃지, 물건 상세 링크, 지난 기일 dim.
    base.html 내비 '일정' 링크 + `.params tr.dim` CSS 추가. `scripts/calendar_smoke.py`.
  - tests/test_sale_calendar.py 9개(정렬·경계 오늘 포함·미상·비정상형식·월묶음·요일·라우트).
- 증거: evidence/calendar_smoke.txt (실 DB source=db, /calendar 200, 상세링크 확인) Read 확인.
  pytest 212 passed(203→212) · ruff 클린.
- 평가자: PASS · code-reviewer APPROVE — MEDIUM 2건 즉시 수정(dim CSS 무효, 비ISO 날짜 방어 비일관),
  MEDIUM 1건 백로그 노트(warnbadge 매크로 3벌 중복 — 기존 관례), LOW 3건 기록만.
- 커밋: (이 커밋)
- 다음: 사이클 #4 — B2 관심물건 웹 UI (/watchlist)

## 2026-07-02 15:56 KST — 📊 사이클 #2: B4 매각·차익 통계 페이지 (/stats)
- 무엇: 레퍼런스 공통 기본기능 '매각통계' 모방 구현(TDD).
  - `src/stats.py` — 순수 집계 6함수(overview/용도별/시도별/스코어 히스토그램/유찰 분포/등급 분포),
    None 안전(_avg 제외방식+est_success_rate로 분모축소 노출), 스코어 100 경계 상단버킷 포함.
  - `GET /stats` SSR(templates/stats.html, 기존 chips/params 스타일 재사용) + `GET /api/stats` JSON,
    base.html 내비에 '통계' 링크. `scripts/stats_smoke.py` 스모크 생성기.
  - tests/test_stats.py 12개(빈목록·None·경계 100·그룹정렬·라우트).
- 증거: evidence/stats_smoke.txt (실 DB data_source=db, /stats·/api/stats 200, 27건 집계) Read 확인.
  pytest 203 passed(191→203) · ruff 클린.
- 평가자: PASS (신선 컨텍스트, 완료정의 5항목 전부 충족·수학 검토 포함) · code-reviewer APPROVE
  (CRITICAL/HIGH/MEDIUM 0, LOW 2건 참고: avg_confidence UI 미노출, smoke 상대경로 관례)
- 커밋: (이 커밋)
- 다음: 사이클 #3 — B1 경매 일정 캘린더 (/calendar)

## 2026-07-02 15:40 KST — 🔍 탐색 사이클 #1: 지지옥션·탱크옥션 분석 → BACKLOG B1~B5
- 무엇: 자율 성장 사이클 첫 탐색 모드 실행.
  - 리서치 에이전트로 지지옥션·탱크옥션 기능 벤치마킹(데이터 크롤 아님, 공개자료·출처 명기)
    → `docs/references/jiji-auction.md`, `tank-auction.md`.
  - 갭 분석(현재 라우트 7개 대비) → `harness/BACKLOG.md`에 5건 등록(Default-FAIL 완료정의 포함):
    B4 통계 페이지(최우선·라이브0) / B1 캘린더 / B2 관심물건 웹 UI / B3 지도뷰(V-World 지오코딩
    배치1회+캐시) / B5 낙찰결과 수집(후순위·WAF 정책 준수).
  - PROGRESS.md Next에 사이클 가동 상태 반영.
- 증거: docs/references/ 2건 + BACKLOG 5건 (Write 직접 수행, 리서치 출처 URL 포함)
- 평가자: - (탐색 사이클, 코드 없음)
- 커밋: (이 커밋)
- 다음: 구현 사이클 #2 — B4 `/stats` 통계 페이지 (TDD → 평가자 → 감사 → 골든셋)

## 2026-07-02 15:32 KST — 🔄 자율 성장 사이클 하네스 구축 (harness/)
- 무엇: 운영자 요청("레퍼런스 탐색→분석→모방→감사·수정→하네스 조이기 지속 반복, 갭은 알림")으로
  자율 성장 사이클 하네스 신설.
  - `harness/LOOP.md` — 마스터 사이클 정의(탐색/구현 모드, 신선 평가자 최대 3회, 감사,
    골든셋 조이기, 품질 게이트 표, 종료조건: 연속 3사이클 무변화→AGENT_STOP+알림, 세션당 6사이클 상한).
  - 범위 잠금: push·Supabase·.env·유료사이트 데이터크롤·삭제성 작업 금지, 라이브 호출 사이클당 1회.
  - `harness/BACKLOG.md`(갭 백로그, Default-FAIL 완료정의 강제) · `harness/QUESTIONS.md`(운영자
    결정 큐+푸시 알림) · `harness/STEER.md`(방향 수정) · `docs/references/`(레퍼런스 분석 아카이브).
  - CLAUDE.md 규칙 8 추가(사이클 진입점 연결).
- 증거: 파일 5종 생성 + CLAUDE.md diff (Write/Edit 직접 수행)
- 평가자: - (하네스 문서 작업, 코드 아님)
- 커밋: (이 커밋)
- 다음: 탐색 사이클 #1 — 레퍼런스 서비스 분석 → BACKLOG 첫 항목 도출

## 2026-07-02 15:07 KST — 🐛 스케줄러 치명버그 수정 + 캐시+라이브 재채점 경로 (T5 복구)
- 무엇: 전국 크롤이 exit 1로 죽은 원인 규명·수정.
  - **근본원인**: courtauction 전국 크롤은 **성공**(full_cache 4,956건 저장, 14:51). 그 뒤 MOLIT 일시적 502(재시도 1/3 중)가 stderr로 나왔는데, refresh-daily.ps1이 `$ErrorActionPreference=Stop` + `2>&1|Tee`라 **PS5.1이 stderr 첫 줄을 종료오류로 승격 → 스크립트 사망**. **매일 05:30 스케줄러도 첫 경고에 죽는 버그**였음.
  - **수정1**: refresh-daily.ps1 — 네이티브 파이썬 호출 동안만 `$ErrorActionPreference=Continue`(전후 복원). stderr 로그가 크롤을 죽이지 않음.
  - **수정2**: refresh-daily.ps1 — `-FromCache`일 때 `-Live`/`-Ym`이 무시되던 버그 수정(이제 소스=캐시/실크롤과 시세=라이브가 독립 적용).
  - **수정3**: run.py — `use_live=args.live`(from-cache와 독립). `--from-cache --live` = **캐시 물건(재크롤X)+라이브 MOLIT 시세**로 재채점. from-cache 전량도 replace_all(풀스냅샷).
  - **복구 실행**: courtauction **재크롤 없이**(제약 준수) 캐시 4,956건 + 라이브 시세로 재채점 → auction.db 적재(진행 중 bu2a758uo, ym 202605).
- 증거: pytest 191 passed, ruff 클린. full_cache 4,956건(18.9MB) 확인. (적재 건수·서버는 재채점 완료 후 확인 — 게이트)
- 커밋: (이 커밋)
- 다음: 재채점 완료 → 서버 재시작 → /health·목록·비고 '위험' 건수 검증 → 링크.

## 2026-07-02 14:50 KST — ⚖️ 권리(D) Tier-0: 비고(mulBigo) 특수권리 파싱
- 무엇: 권리 크롤 전략을 사용자와 확정(전수 커버 = 점진적 전수+캐시+on-demand, 한번에 폭주 금지).
  - **Tier-0(즉시·네트워크0)**: `to_auction_listing`이 리스트 '비고(mulBigo)'를 `detect_special_rights`로 파싱해
    유치권·지분·대지권미등기 등 특수권리 힌트를 special_rights에 채움 → 하드게이트 '위험' 사전 발동.
    rights_verified는 상세(D) 전까지 False 유지(권리미확인). 비고는 보수적(키워드=위험)으로만 사용.
  - **Tier-1(다음)**: 물건상세(PGJ15BM01 계열) 엔드포인트 라이브 정찰 → 명세서/현황조사서 텍스트 fetch →
    기존 파서(courtauction_rights)+훅(enrich_listings_with_rights)로 배선. **PII 강제 sanitize**(이름 미저장).
    **전국 리스트 크롤과 동시 실행 금지**(같은 IP WAF 밴 위험) → 크롤 종료 후 진행.
  - **Tier-2**: 감정평가서 PDF → 사진·준공연도(노후도, 갭#2 동시 해결).
- 증거: pytest 191 passed, ruff 클린. 비고 "유치권 성립여지 있음" → special_rights=['유치권'] 스모크 확인.
- 커밋: (이 커밋)
- 다음: 전국 크롤 종료 확인 → 서버 재시작 → 물건상세 엔드포인트 정찰(Tier-1).

## 2026-07-02 14:37 KST — 🎯 4대 갭 개편 (grill 8결정 → GOAL_OBJECTIVE 구현: 세금정밀·점수폐지·사진준비·수집확대)
- 무엇: 사용자 grill로 8개 결정 확정(GOAL_OBJECTIVE.md) 후 T1~T5 구현.
  - **T1 세금 지식문서**: docs/tax-auction-knowledge.md — 경매=유상승계취득, 주택 세율표(6억↓1%·6~9억 법정누진 (가액×2/3억−3)% 넷째자리반올림·9억↑3%), 다주택 중과(조정2=8%·조정3+/법인=12%·비조정3=8%·4+=12%), 부가세(교육세=세율×½×20%, 중과 0.4% / 농특세 85㎡↓비과세·기본0.2%·8%중과0.6%·12%중과1.0%), 비주택 4.6%, 양도·보유세·경매특유비용(참고). **웹 교차검증 완료**(법령·easylaw·한화·NTN, 출처 §5). 기준연도 2026.
  - **T2 세금엔진**: src/tax.py 신규 — BuyerProfile(주택수·조정·법인, data/buyer_profile.json, 기본 1주택·비조정·개인)+effective_rates+acquisition_tax_breakdown. 문서와 1:1(tests/test_tax.py 15케이스: 경계 6억/9억/85㎡/중과 사다리/법인/비주택/분해합계/프로필 로드). score·backtest가 tax.py 호출, 어제 임시 flat 모델 제거.
  - **T3 점수 UI 폐지→차익 중심**: 정렬 기본=예상차익 금액(profit), 갭률 토글(query.DEFAULT_SORT). min_score→min_profit(억) 필터. 긍정 뱃지(차익 유력·양호·관심) 전면 제거, **경고만**(권리미확인·위험·시세추정불가·차익없음). listings/detail/methodology 재작성 — 히어로=차익 1위(경고 병기), 상세=차익근거(객관)+**취득세 분해 카드**(본세·교육세·농특세+가정 라벨)+권리+현장확인, 방법론=산식·세율표·안전게이트·백테스트(샘플 캐비엇). arb_score는 내부/DB만(사용자 결정 #7). report/digest/run CLI도 profit 기준.
  - **T4 사진/노후 준비**: 상세 '현장 확인' 카드 — 카카오맵 위치·로드뷰 링크(키 불필요, 즉시 동작), 로드뷰 임베드=카카오 JS키 대기, 준공연도=건축물대장 API 대기(자리 표시).
  - **T5 수집 확대**: refresh-daily/install-scheduler -Cash 1억→**5억**, 스케줄러 재등록+활성(Ready). **전국 17시도 라이브 크롤 실행 중**(백그라운드, replace_all 적재 예정).
- 증거: **pytest 191 passed**(신규 15+), ruff 클린. 스케줄러 Args에 -Cash 500000000 확인.
- 평가자: 직접 실행(pytest·ruff) + 세율 웹 교차검증.
- 커밋: (이 커밋)
- 다음: 전국 크롤 완료 → 서버 재시작·검증. 사용자=①건축HUB 활용신청 ②카카오 JS키. Claude=D 물건상세 fetch(권리+감정평가서 사진).

## 2026-07-02 13:15 KST — 💰 취득원가 객관화 (주관적 비용 제거, 사용자 지시)
- 무엇: 사용자 "예상 순차익 산정이 너무 주관적이다. 명도비·수리비·인수금액 빼고 객관적으로." 반영.
  - **취득원가 = 최저입찰가 + 취득세** 로 단순화. **명도비·수리비·인수금액 제거**(전부 config 고정 추정값이라 근거 약했음).
  - **취득세 객관화**: 기존 3구간 flat → 실제 법정에 근접하게 `acquisition_tax(price, property_type)`:
    주택 6억↓1.1%·6~9억 선형누진(본세×1.1)·9억↑3.3% / 비주택(오피스텔·상가·토지) 4.6%. (1주택·중과 제외, 농특세 생략 — 매수인 상황 의존분 제외.)
  - `real_acquisition_cost`=최저가+취득세, `backtest.realized_cost`=낙찰가+취득세. gap/score도 이 객관 원가 기준.
  - **UI**: 상세 '차익 근거(객관)' = 감정가(1차 시작가)→유찰 n회·저감율→최저입찰가(현재 시작가)→취득세(1주택 기준)→취득원가→추정시세. 명도/수리/인수는 '제외' 경고로만. "예상 순차익"→"예상 차익". 방법론 공식·취득세 표 갱신.
  - config: repair_per_m2·eviction_cost·acq_tax_brackets 제거, acq_tax_housing_low/high·acq_tax_nonhousing·nonhousing_types 추가.
- 증거: **pytest 178 passed**(취득세 신모델·취득원가=최저가+세금 검증 추가, backtest 합성결과 신원가 기준 재조정), ruff 클린. 서버 재가동, 상세페이지 객관 마커(취득원가·저감·차익근거(객관)) 렌더 확인. 명도/수리는 '제외' 안내에만 등장.
- 평가자: 직접 실행(pytest·ruff·렌더).
- 커밋: (이 커밋)
- 다음: (선택) 다주택 중과·85㎡ 농특세 토글, 인수금액은 D(권리 물건상세) 붙으면 별도 객관 라인으로 표시.

## 2026-07-02 11:48 KST — 🎨 디자인 감사(3관점) + 확정 이슈 수정
- 무엇: 사용자 "디자인적으로도 감사했어?" → 전용 디자인 감사 실행(접근성 WCAG·디자인품질/안티템플릿·반응형/CSS 3관점 병렬 + 색대비 직접계산).
  발견: 실데이터에서 권리미확인인데 점수/갭/차익이 초록(매수신호), 색대비 AA 다수 실패, 모바일 미대응. **확정 이슈 일괄 수정**:
  - **안전-색 일관(`.unv`)**: 권리미확인 물건은 점수·크기막대·갭·차익을 muted로 강등(리스트·히어로·상세). 초록은 검증+표본 통과 물건에만.
  - **대비 AA**: 토큰 하향(muted #565D68·muted-2 #666C77·warn/conf-mid #A44E09·score-lt60 #787E88), b-top 뱃지 fill=pos-strong(흰글씨 5.43:1). **전 토큰 AA 통과(계산 확인)**.
  - **음수갭 gapmeter**: 최저가≥시세면 적색 초과막대+"차익없음"(gm-loss), 클리핑되던 gm-appr(감정가 마커) 제거.
  - **뱃지 분리**: 시세추정불가=b-na(점선)·차익없음=b-none(실선). 인사이트밴드 차익유력0건이면 초록 끔(.insight.flat).
  - **모바일**: 560px 브레이크포인트(필터 세로 전폭·44px 타깃·카드 1열·gm-lab 랩).
  - **기타**: 빈 단지명→유형 폴백, color-mix 정적폴백, th scope=col, 주소 ellipsis/clamp, conf "실거래 N건", 한글라벨 uppercase 제거, 죽은 CSS 제거.
- 증거: **pytest 177 passed**, ruff 클린. 대비 재계산 6/6 PASS. 서버 재가동 후 라이브에서 unv·gm-loss·b-na·#565D68 렌더 확인. docs/디자인감사리포트_20260702.md.
- 평가자: 3 병렬 디자인 리뷰어 + 대비 직접계산 + 렌더 검증.
- 커밋: (이 커밋)
- 다음(디자인 후속): 실데이터 위계(권리미확인 상위밴드/시세추정불가 접기), 히어로↔표 랭크 연속성, gm-appr 접근가능 재도입 여부.

## 2026-07-02 11:26 KST — 🔌 E(시세유형 확대)·D(권리파서) 배선 + 라이브 반영
- 무엇: 감사 후속 — 준비돼 있던 E/D를 matcher/score/pipeline에 실제 배선.
  - **E(단독·상업·토지)**: matcher `_PROPERTY_KIND`에 sh/nrg/land 매핑(단독주택→sh, 상가/근린→nrg, 토지/대지/임야→land).
    pipeline.load_live_trades가 물건 있는 법정동에만 확장유형 fetch(`fetch_extra_trades`)해 `_extra_to_trade`로
    Trade 정규화(단지명 없어 dong+면적+kind 매칭). 불필요 API부하 회피.
  - **D(권리)**: `apply_rights`가 `rights_verified=True` 설정(→ '권리미확인' 해제·하드게이트 실작동).
    `pipeline.enrich_listings_with_rights(listings, fetch_detail_fn)` 훅 추가 — 물건상세 텍스트 페처를 주면
    권리 파싱·반영, 실패/빈텍스트면 권리미확인 유지, 개인정보 원문 미저장. **남은 것=courtauction 물건상세 fetch 엔드포인트**(client 계층, 라이브 recon 필요).
- 증거: **pytest 177 passed**(신규 4: expected_kind 확장·토지 dong매칭·enrich verified·enrich 미수집유지), ruff 클린.
- 평가자: 직접 실행(pytest·ruff).
- 커밋: (이 커밋)
- 다음: courtauction 물건상세 fetch 엔드포인트 recon+구현(D 완성) → enrich 배선 후 상위 후보만 권리검증. 라이브 새로고침으로 auction.db 재생성.

## 2026-07-02 11:07 KST — 🔎 다관점 감사 + 안전수정 + UI 리디자인 (goal+감사 스킬)
- 무엇: 사용자 요청 "goal+감사로 다관점 감사·수정 + 못생긴 웹 리디자인".
  - **감사(deep, 7관점 병렬 + 실구동)**: architect·security·python·silent-failure·performance·database·domain-money.
    교차검증 CRITICAL 5 / HIGH 8. 실구동=pytest 169·ruff 클린·서버부팅 사실 확보.
  - **CRITICAL 수정(머니세이프티)**:
    - `rights_verified`(models) 게이트 — 라이브/DB 실매물은 '차익 유력'·초록 안전문구 금지, `권리미확인` 등급 + 상세페이지 경고. (score/web/detail/report)
    - 최소표본 게이트 — 실거래 <2건=시세추정불가(1건 중앙값 시세 불신), '차익 유력'은 ≥3건에만. (config.min_comps_*, score)
    - 뱃지 '확실한 차익'→**'차익 유력'** 전면 개명(법원경매 단정 표현 제거, 사용자 승인).
    - 시세 출처 가드(run.py) — courtauction 비-라이브 결과는 서빙 DB 대신 *.dryrun.db. 전국풀스냅샷=replace_all(만료매물 제거).
    - occupant 기본값 '공실'→'소유자점유'(명도비 과소 방지).
  - **HIGH/보안 수정**: SQLite WAL+busy_timeout·store.replace_all, matcher 법정동 제약(동명이단지 오매칭 차단), config 검증(경고), 라이브 부분실패 집계경고, `_redact` 공개 별칭, to_html HTML이스케이프, .env.example VWORLD.
  - **🔴 공개레포 실데이터 유출 조치(사용자 승인)**: repo **private 전환 완료**, `evidence/courtauction_live_verify.json` untrack, .gitignore/.dockerignore 강화(evidence/* 전체), git 히스토리 재작성으로 과거커밋에서 완전 제거 + force-push.
  - **UI 리디자인**: 다관점 디자인 리서치(5각도) 종합 → "Warm-Paper Financial Broadsheet"(FT페이퍼#FFF1E5+슬레이트+머니그린). templates/base.html(디자인시스템)+listings/detail/methodology 재작성. 히어로#1픽·랭킹표(hairline)·스코어 크기막대·뱃지+갭 페어·데이터출처 배너·방법론 샘플캐비엇. docs/design-brief.md.
- 증거: **pytest 173 passed**(신규 4: 권리게이트·표본게이트·replace_all·upsert-merge), ruff 클린. 6페이지 정적 렌더 200 OK(권리미확인 경고 확인). 감사리포트 docs/감사리포트_20260702.md.
- 평가자: 7 병렬 리뷰어 교차검증 + 직접 실행(pytest·ruff·렌더).
- 커밋: (이 커밋; 히스토리 재작성 force-push)
- 다음: 배선(E 유형·D 권리파서), PK doc_id(물건번호) 다물건 분리, 미납관리비 원가라인, 스케줄러 첫 발화 확인.

## 2026-07-02 09:52 KST — 🚀 배포+라이브 가동 (main merge·push, 스케줄러 활성, 실크롤, D/E 검증)
- 무엇: 사용자 승인 후 배포 실행.
  - **merge+push**: feat/deploy-prep → main(--no-ff, e8c5717) origin push 완료(공개레포). 시크릿 스캔 clean.
  - **서버 가동**: waitress `python -m src.serve` 127.0.0.1:8000 백그라운드. `/health` 200 `X-Data-Source: db`.
  - **스케줄러 활성화**: install-scheduler.ps1 → 등록 순간 Disabled(#8 fix 확인) → Enable → **State: Ready**(매일 05:30 전국 새로고침).
  - **라이브 크롤(서울)**: `run.py --source courtauction --sido 11 --cash 5억 --max-pages 2 --live --ym 202605` →
    **실경매 62건 적재**. 서버가 실데이터 서빙(52건). 최상위=주건축물 아파트 1.03억→3.71억(차익 2.63억·71%·100점),
    브라운스톤서초 오피스텔 2.04억→5.64억. **#2 검증**: data/courtauction_full_cache.json 62건 실생성(PII-free) → --from-cache 재생 가능.
  - **D/E 라이브 검증**: E(단독 sh·상업 nrg·토지 land) 3종 **실호출 정상+파서 정합 OK**(대치동 단독 699㎡ 74.95억 등).
    건축물대장(BldRgstService_v2)은 지번 넣어도 **500 → API 미활성화 추정**(사용자 data.go.kr 활용신청 필요).
  - **보안 fix**: 검증 중 에러메시지에 API키 노출 발견 → molit_client `_redact`로 serviceKey 마스킹(로그·예외), evidence 파일도 마스킹.
- 증거: pytest **169 passed**(신규: _redact 1), ruff 클린. 서버 curl /health·/api/listings 실데이터 확인. evidence/de_live_validate.txt.
- 평가자: 직접 실행 검증(서버·크롤·스케줄러 상태·D/E 실호출).
- 커밋: (이 커밋, main 직접)
- 다음: 사용자=① 건축물대장 API 활용신청(data.go.kr) ② 매칭률↑ 위해 --area-band 튜닝(시세추정불가 다수) ③ 폰접속 Tailscale. Claude=D/E를 matcher/score에 배선(E는 준비됨).

## 2026-07-01 18:06 KST — 🔍 push 전 독립 코드리뷰 확정이슈 수정 (feat/deploy-prep)
- 무엇: 밤샘 산출물(main..HEAD)을 4렌즈 병렬 리뷰+발견별 적대검증(확정7·PLAUSIBLE1·반박0, CRITICAL 없음)한 뒤,
  확정 이슈를 코드로 수정:
  - **#1 HIGH** `web.py` 침묵 샘플폴백 → AUCTION_DB 연결됐으나 0건이면 경고 로그 + 응답 헤더 `X-Data-Source`
    (db/sample(db-empty|db-error|no-db)) + `/health` data_source 노출. 샘플을 라이브로 오인하는 것 방지.
  - **#2 MEDIUM** `--from-cache` 죽은코드 → `cc.save_full_records`(rec.raw=이미 sanitize된 PII-free)로 라이브 수집분을
    full-record 캐시에 저장, `_records_from_full_cache` 기본경로를 DEFAULT_FULL_CACHE로. 이제 오프라인 dry-run이
    fixture가 아니라 실데이터를 재생(empirical 확인: 2099타경1 재생). data/courtauction_full_cache.json gitignore.
  - **#3 MEDIUM** `web.py` 캐치올 → `logger.error(exc_info=True)`로 스택트레이스 보존.
  - **#4 LOW** 국토부/건축물대장 4+3 엔드포인트 `http://`→`https://`(API키 평문전송 방지, molit_client 포함).
  - **#5 LOW** `run.py` config.SAMPLE 제자리변경 → `dataclasses.replace`(불변 규칙 준수).
  - **#6 LOW** `courtauction_rights.detect_assumed_amount` 부정문/말소·소멸 금액 오탐 제외(안전물건 위험오판 방지).
  - **#8 PLAUSIBLE** `install-scheduler.ps1` 등록-비활성 레이스 → `$settings.Enabled=$false`로 등록순간부터 Disabled.
  - **#7 보류**(LOW, 죽은 스캐폴딩): `building_register._is_violation` 태그부재시 '비위반' 기본값 → tri-state 필요,
    실데이터 연결 시점에 재설계(YAGNI로 지금은 미변경, 아침 라이브 검증 항목에 포함).
- 증거: pytest **168 passed**(162 무회귀 + 신규 6: full-cache 라운드트립·web 출처 4·부정문 1), ruff 클린.
  --from-cache empirical 재생 확인.
- 평가자: 독립 리뷰 워크플로(security/python/silent-failure/code 리뷰어 4렌즈 + opus 적대검증).
- 커밋: (이 커밋)
- 다음: 아침 사용자 리뷰 후 push. #7은 라이브 검증 때 실응답 구조 확인 후 tri-state로.

## 2026-07-01 17:44 KST — 🏁 밤샘 배포준비 루프 마감 요약 (feat/deploy-prep)
- 무엇: GOAL_DEPLOY 밤샘루프 종료. 코어 A·B·C 3종 + 스트레치 D·E·F 3종 전부 구현·커밋 완료.
  - 코어(모두 PASS): A 정기 새로고침 스케줄러(Disabled 등록, b987529) / B 프로덕션 서빙 waitress(97b1ff3) /
    C 신뢰계수 표본 개선·튜닝 외부화(77b10d8).
  - 스트레치(모두 PASS): D 권리필드 파서 뼈대 / E 시세유형 확대(단독·상업·토지+건축물대장) / F 터널 접속 가이드.
- 증거: evidence/ 전량 존재(scheduler_dryrun·serving_health·confidence_samples·rights_parser·molit_types·tunnel_guide),
  전부 오프라인 실행(courtauction/국토부 실서버 무호출 — 밤샘 정책 준수).
- 검증: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q` → **162 passed**(무회귀), `ruff check .` → All checks passed.
- 평가자: 코어 A·B·C 신선-컨텍스트 패널 2인 모두 PASS. 스트레치 D·E·F PASS.
- 커밋: 6건 로컬 커밋(feat/deploy-prep, 9dc9add~d774a5c). **push 안 함(밤샘 정책=아침 사용자 리뷰 후 push).**
- 다음(사용자 아침 작업): ① feat/deploy-prep 리뷰 후 push, ② 통제된 라이브 1회로 D/E 파서·건축물대장 실응답 구조 확인,
  ③ Tailscale/cloudflared 설치 후 폰 접속 확인, ④ 약관 확인 후 `Enable-ScheduledTask`로 스케줄러 활성화.

## 2026-07-01 17:41 KST — [F] 터널 접속 가이드 (사이클1, feat/deploy-prep)
- 무엇:
  - **원격 접속 문서**(docs/remote-access.md): 올-로컬 waitress 서버(127.0.0.1:8000)를 폰 등
    외부에서 안전하게 접속하는 두 방식 정리 — (A) Tailscale 사설 VPN(비공개·권장),
    (B) Cloudflare Tunnel quick tunnel(즉석 공개 HTTPS URL). 각 방식의 설치·기동·접속 절차,
    바인드 주소 차이(127.0.0.1 로컬프록시 vs 0.0.0.0 -BindAll 직접접속), 방화벽 규칙,
    보안(무인증 서버 위험도 표 + Cloudflare 공개 시 접근제한 필수), 트러블슈팅 표, 체크리스트.
  - **연결 확인 스크립트**(scripts/check-tunnel.ps1): 순수 로컬 진단(외부 호출 0).
    [1] tailscale 설치·로그인·tailnet IP, [2] cloudflared 설치·버전,
    [3] 로컬 포트 LISTEN 여부 + 바인드주소 해석(127.0.0.1/0.0.0.0), [4] 방화벽 인바운드 규칙을
    OK/WARN/MISSING 으로 표시하고 권장 다음 단계 출력. -Port/-OutFile 파라미터.
    (Windows PowerShell 5.1 한글 파싱 위해 UTF-8 BOM 로 저장 — 기존 scripts 규약과 정합)
- 증거: evidence/tunnel_guide.txt (실제 실행 2회: 서버 미기동→[3] WARN, python -m src.serve 기동 후
  →[3] OK LISTEN 127.0.0.1:8000. tailscale/cloudflared 미설치→MISSING 정상 표시. 외부호출 0)
- 검증: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q` → 162 passed(무회귀),
  `ruff check .` → All checks passed.
- 평가자: PASS (2인 패널 모두 PASS)
- 커밋: (커밋 에이전트 처리)
- 다음: 아침에 사람이 실제 Tailscale/cloudflared 설치 후 폰 접속 라이브 확인 →
  MagicDNS/HTTPS(tailscale serve) 또는 Cloudflare named tunnel + Access 인증게이트 문서 보강.

## 2026-07-01 17:34 KST — [E] 시세유형 확대 뼈대 (사이클1, feat/deploy-prep)
- 무엇:
  - **확장 실거래 클라이언트**(src/molit_extra_client.py): 기존 아파트/연립/오피스텔(molit_client)에
    필드구조가 다른 3종을 추가 — 단독/다가구(sh, RTMSDataSvcSHTrade)·상업업무용(nrg,
    RTMSDataSvcNrgTrade)·토지(land, RTMSDataSvcLandTrade). 유형별 파서(parse_sh/nrg/land_trades_xml)와
    라이브 fetch_extra_trades(kind). `ExtraTrade` dataclass가 아파트류 Trade와 같은 매칭 인터페이스
    (area_m2/price/deal_ym/dong/kind)를 유지하면서 유형별 부가필드(대지면적·건물용도·지목·용도지역·
    지분구분)를 보존. 대표면적 규약: sh=연면적 우선, nrg=건물면적, land=거래면적.
    오류감지·재시도·페이지네이션은 molit_client 헬퍼 재사용(DRY).
  - **건축물대장 클라이언트**(src/building_register_client.py): 표제부(BldRgstService_v2/getBrTitleInfo)
    파서 + 라이브 fetch_building_titles. `BuildingRecord`에서 노후도(building_age_years =
    사용승인일 YYYYMMDD 기준 경과연수, 이상치 방어)·위반건축물 여부(violYn/위반건축물 코드·텍스트
    혼용 대응)·용도·층수를 추출.
  - **국문/영문 태그 혼용**·거래금액 만원→원 환산·0금액/0면적 스킵을 molit_client 규약과 정합.
  - **fixture 4종**(data/sample_sh_trades.xml, sample_nrg_trades.xml, sample_land_trades.xml,
    sample_bld_title.xml): 저장 샘플만. 라이브 크롤/API 호출 아님.
  - **테스트**(tests/test_molit_extra_parse.py, 15건, 외부호출 0): 3유형 파싱·대표면적 규약·영문태그·
    0값 스킵·빈 items·알수없는 kind ValueError·molit 오류감지 재사용·노후도 계산·위반플래그 변형.
- 증거: evidence/molit_types.txt (4 endpoint + 3유형 파싱 데모 + 건축물대장 노후도/위반 데모 +
  pytest 162 green + ruff clean, 실제 오프라인 실행. 외부호출 0)
- 검증: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q` → 162 passed(기존 147 무회귀 + 신규 15),
  `ruff check .` → All checks passed.
- 평가자: -
- 커밋: (커밋 에이전트 처리)
- 다음: 아침 라이브 1회로 3종 실거래 XML 실제 태그·건축물대장 응답구조 확인 → 파서 태그/정규식 보강,
  matcher에 ExtraTrade 유형분리 매칭 배선, 노후도/위반건축물을 score(환금성·권리)에 반영.

## 2026-07-01 17:25 KST — [D] 권리필드 파서 뼈대 (사이클1, feat/deploy-prep)
- 무엇:
  - **파서 모듈**(src/courtauction_rights.py): 물건상세 3문서(매각물건명세서/현황조사서/
    감정평가서) 텍스트 → 권리분석 원재료. 리스트 검색엔 없는 assumed_amount/special_rights/
    tenant_opposable/occupant_type/appraisal_amount 를 추출. 표준 라벨은 config.CONFIG의
    special_penalty·eviction_cost 키와 정합(유치권/법정지상권/지분/분묘기지권/대지권미등기/
    위반건축물, 공실/임차인/소유자점유/다수점유).
  - **detector**: detect_special_rights(중복제거·정의순서), detect_occupant_type(우선순위
    다수>임차인>소유자>공실, 정보없음→보수적 소유자점유), detect_tenant_opposable(항상
    인쇄되는 표준 경고문 boilerplate 제거 후 구체 인수문구만 True), detect_assumed_amount
    (인수 문맥 줄의 최댓값=보수적 과소추정 방지), detect_appraisal_amount(감정가 교차검증).
  - **연동**: apply_rights(listing, rights)=불변 패턴 새 객체 반환, 감정가 0일 때 감정평가서
    값으로 backfill. gate_reasons()=score.py 하드게이트 기준(치명특수권리/인수비율) 재현.
  - **fixture**(tests/fixtures/, 대표구조 5종): 대항력임차인·특수권리다수·공실무권리 등.
    라이브 크롤 아님 — 저장 샘플 텍스트만.
- 증거: evidence/rights_parser.txt (3케이스 파싱→권리점수/게이트 데모 + pytest 147 green +
  ruff clean, 실제 실행. 오프라인, 외부호출 0)
- 검증: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q` → 147 passed(기존 133 무회귀 +
  신규 14), `ruff check .` → All checks passed.
- 평가자: **PASS** (신선-컨텍스트 평가자 패널 2인 모두 PASS)
- 커밋: feat(deploy): [D] 권리필드 파서 뼈대 (feat/deploy-prep, 이 커밋)
- 다음: 아침 라이브 1회로 실제 물건상세 HTML 구조 확인 → 텍스트 추출계층(client) 배선 +
  파서 키워드/정규식 실데이터 보강, pipeline에 apply_rights 연결(상세 조회 옵션)

## 2026-07-01 17:17 KST — [C] 신뢰계수 표본 개선 (사이클1, feat/deploy-prep)
- 무엇:
  - **원인 규명**(docs/confidence-analysis.md): 다월 수집은 이미 배선됨(LIVE_MONTHS=3 +
    recent_ymds/fetch_trades_months). 라이브 매칭 빈약의 실제 원인 = ① matcher 과필터
    (면적밴드 ±10% 고정 → 인접 평형 comps 탈락), ② 수집 개월수 하드코딩(조정 불가).
    신뢰계수 공식(confidence_ladder)은 표본수에 **이미 단조 비감소** — 원인 아님(그래서 기본값 유지=무회귀).
  - **튜닝 외부화**(src/config.py): `SampleConfig(live_months, area_band)` + `load_sample_config`
    + 전역 `config.SAMPLE`. 우선순위 CLI > env(AUCTION_LIVE_MONTHS/AUCTION_AREA_BAND) > JSON
    (data/sample_config.json) > 기본값(3, 0.10=레거시). 하한 방어(개월≥1, 밴드>0).
  - **배선**: pipeline.`_live_months()`→recent_ymds, matcher.`_area_band()`→match_trades.
    기존 상수 LIVE_MONTHS/AREA_BAND는 기본값으로 존치. run.py에 `--live-months`/`--area-band` 추가.
  - **테스트**(tests/test_confidence_samples.py, 10건, 외부호출 0): 신뢰계수 단조 비감소(n=0..8)·
    사다리 단조증가(1<2<3), estimate→score 경로 confidence 비감소, 밴드 확대가 표본 실제 증가
    (±10% 4건→±15% 6건), env/JSON/CLI 오버라이드·기본값=레거시·불량값 하한 보정.
- 증거: evidence/confidence_samples.txt (표본수별 신뢰계수 표 + 원인 데모 + pytest 133 green + ruff clean, 실제 실행)
- 검증: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q` → 133 passed(기존 123 무회귀 + 신규 10),
  `ruff check .` → All checks passed. CLI 오프라인 스모크(--from-cache --live-months 6 --area-band 0.15) 26건 정상.
- 평가자: **PASS** (신선-컨텍스트 평가자 패널 2인 모두 PASS)
- 커밋: (커밋 에이전트 처리 — 이 항목 커밋에 해시 확정)
- 다음: 아침 라이브 1회로 실지역 표본수·신뢰계수 분포 확인 후 area_band/live_months 실튜닝

## 2026-07-01 17:07 KST — [B] 프로덕션 서빙 재검증 (사이클2, feat/deploy-prep)
- 무엇:
  - 사이클1의 [B] 서빙 구현(src/serve.py·web.py 가드·start.ps1·Dockerfile·requirements)이 이미
    완성 상태임을 확인하고, 증거를 **신선한 실행**으로 재생성(가짜 방지).
  - **`scripts/_gen_serving_evidence.py`** 신규 — waitress(`python -m src.serve`)를 서브프로세스로
    실제 부팅하고 stdlib `urllib` 로 실 소켓 HTTP(GET /health, /api/listings) 요청 후 서버 종료해
    `evidence/serving_health.txt` 를 재기록. AUCTION_DB 미설정 → 샘플 폴백으로 **완전 오프라인**
    (courtauction/국토부 무호출). Flask test_client 아닌 실 소켓 경유라 waitress WSGI 경로를 증명.
    빈 포트 자동 선택(`_free_port`), 준비대기(`_wait_ready`), terminate→kill 정리 포함.
  - Dockerfile 정합 재확인: `EXPOSE 8000` = `AUCTION_PORT=8000` = `CMD python -m src.serve`(waitress).
- 증거: `evidence/serving_health.txt` (재생성, Read 확인) — waitress 3.0.2 서브프로세스(PID 로그) 부팅 →
  `GET /health` **HTTP 200** `{"status":"ok"}`(헤더 `Server: waitress`), `GET /api/listings` **HTTP 200**
  `application/json` 샘플 6건 JSON(첫 레코드 상계주공 포함), `create_app().debug=False` 확인, 서버 종료.
  네트워크 호출 0. pytest **123 PASS**(무회귀), ruff **All checks passed**.
- 평가자: **PASS** (신선-컨텍스트 평가자 패널 2인 모두 PASS)
- 커밋: 95fbb73
- 다음: C(신뢰계수 표본 개선)

## 2026-07-01 16:59 KST — [B] 프로덕션 서빙 (waitress) (feat/deploy-prep)
- 무엇:
  - **waitress 서빙 진입점** `src/serve.py` 신규 — `waitress.serve(create_app(), host, port, threads)`.
    Flask dev server(`flask run`/`app.run`)와 달리 waitress 는 debug/reloader 자체가 없어 프로덕션에서
    debug=False·use_reloader=False 가 **구조적으로 보장**됨. `app.debug=False` 방어적 재확인 추가.
    env: `AUCTION_DB`(라이브 DB, 미설정 시 web 레이어 샘플 폴백), `AUCTION_HOST`(기본 127.0.0.1 로컬전용),
    `AUCTION_PORT`(기본 8000), `AUCTION_THREADS`(기본 4).
  - **`src/web.py`**: `__main__` 가드 추가(개발 편의 진입점) — debug/reloader 는 `AUCTION_DEBUG=1`
    env flag 로만 켜지고 **기본값은 항상 off**. `_truthy()` 헬퍼로 1/true/yes/on 만 참.
  - **`requirements.txt`**: `waitress>=3.0` 추가(.venv 에 waitress 3.0.2 설치 완료).
  - **`Dockerfile`**: CMD 를 `flask run` dev server → `python -m src.serve`(waitress)로 교체.
    `ENV AUCTION_HOST=0.0.0.0 AUCTION_PORT=8000` 로 EXPOSE 8000 과 포트 일치.
  - **`scripts/start.ps1`**(UTF-8 BOM, ASCII 인라인 주석 — PS5.1 한글주석 오독 버그 회피): waitress 로
    src.serve 호출. .venv 파이썬 절대경로, 기본 127.0.0.1(로컬전용), `-BindAll` 시에만 0.0.0.0,
    `-Port`/`-DbPath`/`-Threads` 파라미터, `AUCTION_DEBUG=0` 명시.
- 증거: `evidence/serving_health.txt` (Read 확인) — waitress 3.0.2 를 **실 서브프로세스**(`python -m src.serve`,
  PID 로그)로 부팅 → `GET /health` **HTTP 200** `{"status":"ok"}` (응답 헤더 `Server: waitress` 확인),
  `GET /api/listings` **HTTP 200** `Content-Type: application/json`, 샘플/오프라인 6건 JSON 반환(첫 레코드 포함),
  `create_app().debug=False` 확인, 서버 kill·포트 해제(netstat LISTENING 없음). AUCTION_DB 미설정 → 샘플 폴백(네트워크 호출 0).
  pytest **123 PASS**(무회귀), ruff 클린.
- 평가자: -  (신선-컨텍스트 평가자 대기)
- 커밋: (커밋 에이전트 처리 예정 — 빌더 미커밋)
- 다음: C(신뢰계수 표본 개선)

## 2026-07-01 16:49 KST — [A] 정기 새로고침 스케줄러 + 오프라인 dry-run 경로 (feat/deploy-prep)
- 무엇:
  - **오프라인 dry-run 배선**: `run.py --from-cache` 추가 — courtauction 실크롤/국토부 라이브를
    강제로 끄고(`use_live = args.live and not args.from_cache`) `pipeline.load_courtauction_from_cache()`로
    파이프라인 실행. 우선순위: full-record 캐시(`{"records":[raw…]}`) → 없으면 `data/sample_courtauction.json`
    fixture 폴백 → 그래도 네트워크 호출 0. dry-run 스냅샷은 프로덕션 캐시를 덮지 않게 `*.dryrun.json`에 분리 저장.
    `--cash` 지정 시 로컬 '최저가≤현금' 필터로 affordable_search 대체.
  - **스케줄러 스크립트 3종**(`scripts/`, 전부 UTF-8 BOM):
    `refresh-daily.ps1`(run.py 래퍼 — AUCTION_DB·PYTHONUTF8 설정, .venv python 절대경로,
    로그 `evidence/refresh-*.log` tee, `-Live`/`-FromCache`/`-Cash`/`-Ym`/`-DbPath`),
    `install-scheduler.ps1`(작업스케줄러 매일 05:30 등록 후 **즉시 Disable-ScheduledTask** → Disabled 상태,
    `-WhatIf` 미리보기 지원), `uninstall-scheduler.ps1`(등록 해제).
  - **README**: "정기 새로고침 스케줄러(Windows)" 섹션 — 오프라인 dry-run/등록/해제 커맨드 +
    "약관 확인 후 `Enable-ScheduledTask` 한 줄로 활성화" 안내.
- 함정 해결: PS 5.1이 no-BOM `.ps1`의 `if {}` 블록 내부 **한글 주석**을 ANSI로 오독 → 다음 문장(`$runArgs += "--from-cache"`)을
  통째로 삼켜 `--from-cache`가 run.py에 전달 안 되고 **실크롤이 도는** 버그 발견. 인라인 주석 ASCII화 + 3파일 UTF-8 BOM 저장으로 해소.
- 증거: `evidence/scheduler_dryrun.txt` (Read 확인) — refresh-daily `-FromCache` 콘솔출력(오프라인, 수집26·캐시diff요약,
  "저장 26건 → auction.db"), sqlite `scored_listings` 23행 확인, `install-scheduler.ps1 -WhatIf`가 **Disabled** 등록 계획 출력.
  pytest **123 PASS**(신규 3: from-cache fixture폴백/full-record/스냅샷폴백), ruff 클린. 실작업 미등록(WhatIf만) — 시스템 클린.
- 평가자: PASS (신선-컨텍스트 평가자 패널 2인 모두 PASS)
- 커밋: (feat(deploy) 커밋 — 이 항목 커밋에 포함)
- 다음: B(waitress 서빙) → C(신뢰계수 표본 개선)

## 2026-07-01 16:36 KST — 배포준비 밤샘루프 하네스 셋업(feat/deploy-prep)
- 무엇: grilling(7전제 확정) 후 비공개 배포준비 밤샘루프 착수. `GOAL_DEPLOY.md`(Default-FAIL 완료정의 A/B/C+스트레치)
  작성, 6/29 잔재 `AGENT_STOP` → `docs/AGENT_STOP-archive-2026-06-29.txt` 아카이브(kill-switch 자리 확보),
  `feat/deploy-prep` 브랜치 생성, `scripts/`·`docs/` 폴더 준비. 루프는 Workflow(빌더↔2인평가자 패널, 항목당 3사이클,
  오프라인 전용, 로컬커밋 push금지)로 실행.
- 증거: GOAL_DEPLOY.md, docs/AGENT_STOP-archive-2026-06-29.txt (Read 확인)
- 평가자: - (하네스 셋업, 코어작업은 루프에서 평가)
- 커밋: (이 커밋)
- 다음: 루프가 A(스케줄러)→B(waitress)→C(신뢰계수) 순으로 빌드·평가·커밋

## 2026-07-01 07:54 KST — 전국 저가매물 확장(지역샤딩+캐시diff) + 웹 라이브서빙 연결
- 무엇:
  - **전국 샤딩**: `pipeline.load_courtauction_nationwide(cash, sidos, max_pages_per_sido)` — 17개 시도 순회,
    한 client 공유(일일상한·지터·세션 누적), docid 중복제거, 차단 시 부분결과 반환. `collect_courtauction_records`로 단일/전국 공통화.
  - **증분 캐시 diff**: `src/courtauction_cache.py` — docid키 스냅샷(사건/최저가/감정가/유찰/기일/주소, 개인정보 없음),
    신규/변경(유찰→최저가하락·기일변경)/유지/소멸 분류. `data/courtauction_cache.json`(gitignore).
  - **run.py**: `--nationwide`·`--cache` 추가. courtauction 소스는 records→캐시diff 리포트→AuctionListing→채점→DB적재.
  - **웹 라이브서빙**: web.py `/property/<사건>`이 샘플에만 매물조회해 courtauction 매물이 404나던 버그 수정
    (DB 스코어행에서 최소 AuctionListing 복원, 권리필드는 미수집이라 기본값). 목록/상세/큐레이션 전부 DB서빙.
- 증거: pytest **120 PASS**(신규 8: cache diff 6 + 전국샤딩/부분차단 2 + 웹 DB상세 1 + …), ruff 클린. (Read 확인)
  **라이브 end-to-end**(`evidence/courtauction_nationwide_web.txt`): 서울+부산 샤딩 37건, 캐시 첫실행37신규/재실행37유지,
  관악구 2건 라이브채점→DB→웹 `/api/listings` 청룡오피스텔 **98점 확실한차익**, `/property/…` HTTP200(404버그 수정 확인), `/` 200.
- 평가자: 자체검증(테스트+라이브). 바운디드(2시도·관악구 한정).
- 커밋: (이번 커밋) · push 예정.
- 다음: 신뢰계수 다월표본 보정(매칭 1건 문제), 물건상세(권리/감정평가서) 보강, 전국 정기 새로고침(스케줄러), 이용약관 확인(사용자).

## 2026-06-30 21:41 KST — 차익 파이프라인에 courtauction 실매물 연결 (end-to-end 라이브 성공)
- 무엇: 크롤러를 차익 스코어 파이프라인에 연결 + 이용약관 정찰 + stop_file 충돌 수정.
  - `pipeline.load_courtauction_auctions(cash_won/sido/buffer/max_pages, client/extra 주입)` → affordable/search → `to_auction_listing` → run.
  - `run.py` 플래그 `--source courtauction --cash --sido --max-pages --appraisal-buffer` 추가.
  - **stop_file 기본값 AGENT_STOP→`COURTAUCTION_STOP`**(루프 잔류 AGENT_STOP과 충돌해 크롤이 막히던 footgun 수정).
  - search 종료로그: max_pages 의도적 제한(INFO) vs 전페이지 순회후 부족(WARNING) 구분(오해 소지 제거).
  - 이용약관 정찰: 약관/저작권 팝업(PGJ111P01~06)은 **SPA 클라이언트 렌더라 본문 텍스트 추출 불가** → "자동수집 금지 조항 여부"는 **여전히 사용자 브라우저 확인 필요**(미결, 단정 불가).
- 증거: pytest **112 PASS**(신규 3: load_courtauction affordable/search/스코어연결), ruff 클린. (Read 확인)
  **라이브 end-to-end**(`evidence/courtauction_pipeline_live.txt`): 서울 관악구 11건 국토부 시세 매칭 →
  파로스프라자 오피스텔 최저7,600만 vs 시세2.09억=**98점 확실한차익**, 우현빌리지 다세대 최저5,320만 vs 3.38억=95점 등 11건 전부 매칭.
- 평가자: 자체검증(테스트+라이브). courtauction 2요청 + MOLIT 1개구 한정.
- 커밋: (이번 커밋)
- 다음: 전국 지역샤딩+로컬캐시 diff(증분수집), 신뢰계수 다월표본 보정, 물건상세(권리/감정평가서) 보강, 웹 라이브서빙 연결.

## 2026-06-30 19:26 KST — 크롤러 3관점 코드리뷰 후 CRITICAL/HIGH 일괄 수정
- 무엇: code-reviewer/security-reviewer/silent-failure-hunter 병렬 리뷰(CRITICAL2·HIGH5·다수 MED/LOW) 반영.
  - **CR-HIGH** `_request_count` 재시도 중복 → 상한검사 루프 내 이동·실제 전송수 카운트.
  - **CR-HIGH** `affordable_search`가 호출자 SearchFilter 변이 → `dataclasses.replace`로 복사(불변성).
  - **SEC-HIGH** PII 토큰 확장(owner/debtor/creditor/obligor/dpry 변형) + `mulBigo` 자유텍스트 성명 마스킹(`mask_personal_names`).
  - **SFH-CRIT** `_extract_ip` bare except 무음 → 로깅. `_post` 네트워크예외만 재시도(`requests.exceptions.RequestException`), 직렬화는 루프밖 1회(프로그래밍오류 즉시 전파).
  - **SFH-HIGH** `to_won/to_int` 소수점 처리+실패 경고로그(조용한 0 반환 방지). `_validate_payload` dma_pageInfo 검증. 중간페이지 0행=ERROR로그+누락률 경고. yielded>=total 조기종료.
  - **SEC-MED** client_ip는 DEBUG·부분마스킹, 오류본문은 예외에 안 싣고 DEBUG로그만. Retry-After HTTP-date 파싱(`_parse_retry_after`).
  - 보류(근거): stop_file 경로가드(운용자 설정값이라 비대상), raw private화(churn·sanitize_row 단일게이트로 충분), 스트림중 IP변동 자동재워밍(v1 한계·회로차단기 커버) — docstring 명시.
- 증거: pytest **109 PASS**(신규 28, +retry카운트/dma_pageInfo/소수점/PII확장/비고마스킹), ruff 클린(src+tests). (Read 확인)
  라이브 스모크: 카나리 OK(서울1668) + 1페이지 26행·117필드보존·요청카운트 정확(조기종료로 page2 안감).
- 평가자: 자체검증(테스트+라이브). 저빈도 2요청.
- 커밋: (대기)
- 다음: 19:16 항목과 동일(이용약관 확인 → pipeline 연결 → 지역샤딩+캐시 → 물건상세 권리보강).

## 2026-06-30 19:16 KST — courtauction 물건검색 크롤러 구현·라이브검증 (전 필드 보존, 안전장치 내장)
- 무엇: 2차 정찰로 **실물건 검색 엔드포인트 확정** 후 크롤러 작성.
  - 엔드포인트: `POST /pgj/pgjsearch/searchControllerMain.on`, body=`{dma_pageInfo, dma_srchGdsDtlSrchInfo}` JSON.
    (검색UI `PGJ151M01.xml` 역분석 → submission `sbm_selectGdsDtlSrch` 페이로드 매핑)
  - 서버필터 실효성 실측: ✅지역/감정가(aeeEvlAmt)/최저가율(lwsDspslPrcRate)/면적/유찰(flbdNcnt),
    ❌**절대 최저가(rletLwsDspslPrc)는 무시됨** → affordable은 감정가버퍼로 볼륨축소+로컬 최저가필터.
  - `src/courtauction_fields.py`: 117필드 카탈로그·한글라벨, `CourtAuctionRecord`(개인정보 제외 raw 전체 보존),
    PII 가드(sanitize_row), `to_auction_listing`(matcher 연결).
  - `src/courtauction_client.py`: SearchFilter + CourtAuctionClient(세션워밍·IP추출, 3~8s 지터 레이트리밋,
    concurrency=1, 일일상한, 지수백오프(429/5xx), 403/리다이렉트=즉시중단, **콘텐츠 회로차단기**(200인데 HTML/스키마붕괴=조용한차단 감지),
    카나리, kill-switch(stop_file), 페이지네이션, affordable_search).
- 증거: pytest **106 PASS**(신규 25: fields 11 + client 14), ruff 클린. (Read 확인)
  라이브: `evidence/courtauction_live_verify.json` — 카나리 OK(서울 1668), 현금6천만→affordable 15건(요청 3회),
  **감정가1.4억·16회유찰→최저499만 매물 포착**(감정가프록시 단독이면 누락됐을 알짜 → 적대적검토 수정 실증), 117필드 보존.
  fixture: `data/sample_courtauction.json`(서울 감정가≤1억 26건 실응답).
- 평가자: 자체검증(테스트+라이브 실호출). 저빈도(3요청)·개인정보배제·공공누리4유형 비영리 전제.
- 커밋: (대기)
- 다음: ① 사용자: courtauction 이용약관 "자동수집 금지" 조항 여부 브라우저 확인(법적 토대). ② pipeline에 라이브 경매소스로 연결
  (load_sample_auctions → affordable_search). ③ 전국 지역샤딩 수집 + 로컬캐시 diff(증분). ④ 물건상세(감정평가서/권리)로 권리필드 보강.

## 2026-06-30 17:50 KST — courtauction.go.kr 1차 정찰 (requests로 JSON 추출 가능 확정)
- 무엇: 실제 경매 매물 소스(대법원 courtauction) 접근 방식 정찰. docs/courtauction_recon.md 작성.
  발견: (1) WAF 있어 맨 요청 차단 → **브라우저 헤더(UA/Accept-Language) 필수**, (2) WebSquare5+
  eGovFrame, 데이터는 `/pgj/pgjXXX/selectXXX.on` POST→JSON, (3) GET /pgj/index.on이 세션쿠키
  (JSESSIONID/WMONID) 발급. **결론: 헤드리스 불필요, requests로 충분.**
- 증거: `POST /pgj/pgj111/selectRletYrDspslStats.on`(브라우저헤더+쿠키+Referer, body {}) →
  **HTTP 200 + JSON** `{"status":200,"message":"정상","data":{...}}` 실측. (selectNtcMtrPouUpItemList.on은
  302→올바른 dataset 필요). egress=한국 로컬 IP라 지오차단 없음.
- 평가자: 자체검증(실호출). 저빈도 원칙으로 총 ~6요청만.
- 커밋: (대기)
- 다음: 부동산 물건 검색 .on 엔드포인트+페이로드 매핑(검색페이지 WebSquare XML) → src/courtauction_client.py PoC
  (세션워밍→검색POST→JSON파싱→AuctionListing, 개인정보 필드 화이트리스트). 합법=공공누리4유형 비영리·저빈도·개인정보배제.

## 2026-06-30 17:44 KST — 절대 규칙 도입: 편집 시 versions.md 기입 강제(CLAUDE.md + 전역 훅)
- 무엇: 사용자 지시로 "이 프로젝트 파일을 작성/편집하면 무조건 versions.md에 기입" 규칙을 명시·강제화.
  (1) CLAUDE.md 최상단에 "## 0. 절대 규칙 — versions.md 기입" 섹션 추가(예외 없음, versions.md 자신 제외).
  (2) 전역 PostToolUse 훅(`~/.claude/settings.json` + `~/.claude/hooks/auction-versions-reminder.js`,
      node 실행) — Write/Edit/MultiEdit가 `.../dev/auction-arbitrage/` 내 파일(versions.md 제외)을
      건드리면 모델 컨텍스트에 "versions.md 갱신 필수" 리마인더를 주입. 프로젝트 밖·versions.md는 무음.
- 증거: 훅 스크립트 pipe-test 3종(프로젝트파일=리마인더O / versions.md=무음 / 프로젝트밖=무음) 통과,
  settings.json node로 JSON 유효성·스크립트 경로 존재 확인, **이 CLAUDE.md 편집 시 훅이 실제로 발화**
  (system-reminder로 additionalContext 주입 확인). 기존 전역설정(plugins/mcpServers/theme) 보존.
- 평가자: 자체검증(훅 발화 실측).
- 커밋: (대기 — CLAUDE.md·versions.md 변경, 사용자 확인 후. 훅/settings는 프로젝트 밖이라 비대상)
- 다음: courtauction 크롤러 착수(정찰 우선) 또는 단독/상업/토지+건축물대장 클라이언트 추가.

## 2026-06-30 17:30 KST — 전 API 라이브 접속 검증 (국토부 7종 + V-World 지오코더)
- 무엇: 운영자가 data.go.kr에서 국토부 7종(아파트상세·연립다세대·오피스텔·**단독다가구·상업업무용·토지**
  +건축HUB 건축물대장) 활용신청 완료 → **단일 키(MOLIT_API_KEY)로 7종 전부 실호출 검증**.
  V-World 인증키(VWORLD_API_KEY)도 .env 저장 후 지오코더 검증. (검증 스크립트는 scratchpad, 비영속)
- 증거: 강남구(11680)/202403 라이브 응답 —
  - 아파트상세(15126468) **OK totalCount=242**
  - 연립다세대(15126467) 첫 호출 HTTP502(일시) → 재시도 **OK totalCount=37**
  - 오피스텔(15126464) **OK 71** / 단독다가구(15126465) **OK 8** / 상업업무용(15126463) **OK 65** / 토지(15126466) **OK 33**
  - 건축물대장 건축HUB(15134735) getBrTitleInfo 역삼동 **OK totalCount=1**
  - V-World 지오코더 getCoord(테헤란로152) **HTTP200 status=OK** — domain 미설정에도 작동
- 결론: data.go.kr 7종 모두 같은 키로 접속 가능, 추가 활용신청 불요. **클라이언트엔 apt/rh/officetel 3종만
  구현됨** → 단독/상업/토지 3종 + 건축물대장 fetch/파서 미구현(다음). 등기부(CODEF/틸코·유료)·실제 경매
  매물(courtauction·크롤링)은 data.go.kr 영역 밖이라 별도. V-World WMS/WFS(용도지역)는 미검증(domain 필요 가능).
- 평가자: 자체검증(실호출).
- 커밋: (코드변경 없음 — 검증·문서만)
- 다음: courtauction 크롤러 착수(사용자 지시). 병행 가능: molit_client에 단독/상업/토지 endpoint + 건축물대장 클라이언트 추가.

## 2026-06-30 10:58 KST — F10 라이브 검증 성공 + 유형분리 매칭 버그 수정 + 웹 라이브 서빙
- 무엇:
  (1) **F10 라이브 검증** — 운영자가 국토부 실거래가 API 키 발급(아파트 상세 15126468·연립다세대
      15126467·오피스텔 15126464 각각 활용신청, 키는 계정당 1개 공유), `.env`에 MOLIT_API_KEY 기입.
      `run.py --live --ym 202403`로 6개 샘플물건×최근3개월 실거래 라이브 수집→시세추정→차익랭킹 정상.
  (2) **유형분리 매칭 버그 수정** — 라이브 데이터에서 드러난 결함: matcher 폴백(법정동+면적)이 물건유형을
      안 가려 화곡 다세대를 강서구 *아파트* 실거래로 평가(7.56억 과대). Trade에 kind(apt/rh/officetel)
      필드 추가→파서가 태깅→match_trades가 물건유형(property_type)에 맞는 종류만 매칭. 결과: 화곡
      다세대 추정시세 7.56억→**2.93억**(빌라끼리). 미태깅 거래는 통과(하위호환).
  (3) **웹 라이브 서빙** — web.py `_scored()`가 AUCTION_DB 환경변수의 DB(새로고침 작업이 적재한
      라이브 결과)에서 읽도록. 미설정 시 샘플 폴백(테스트 결정성). store.load_scored/has_rows 추가.
      → 매 요청 API 호출(54회) 회피. 새로고침=`run.py --live`가 auction.db 적재 → 웹은 DB만 읽음.
- 증거: pytest **81건 통과**(신규 9: 유형분리3·파서태깅1·store3·웹DB서빙2), ruff 클린. 실제 HTTP 서버
  스모크 /health·/api/listings?min_score=80(3건)·/(상계주공)·/methodology 전부 200. 라이브 랭킹표
  Read 확인(상계주공 100·역삼오피스텔 97·광교 86·반석 79·해운대 25게이트·화곡 25게이트).
- 평가자: 자체검증 + CI(예정).
- 커밋: (대기 — 사용자 확인 후)
- 다음: 진짜 경매 매물 데이터(courtauction 크롤러/대체 소스, 합법성 결정) — 현재 경매물건은 6개 샘플
  (시세만 라이브). + 새로고침 스케줄링·배포 시 라이브 호출 가능 IP(국토부가 클라우드/해외 차단 가능).

## 2026-06-29 18:05 KST — X3: 방법론·투명성 페이지 /methodology — Phase 3 완료 → 루프 정지
- 무엇: web.py `GET /methodology` + templates/methodology.html — 차익 스코어 공식·가중치(CONFIG)·
  하드게이트 규칙·취득세 구간·신뢰사다리·등급경계 + 백테스트 캘리브레이션 표(적중률·평균실현차익)
  + precision@80/60/40. 목록 헤더·README에 링크. (투명성=해자, V1 백테스트를 사이트에 노출)
- 증거: pytest 72건 통과(methodology 2건), ruff 클린. /methodology 200 + 공식·캘리브레이션 마크업 확인.
- 평가자: 자체검증 + CI.
- 커밋: 58c7847 / GitHub push.
- 종료: **Phase 3(X1 매칭품질·X2 지역검색·X3 방법론) 완료.** 정확도·신뢰·인프라까지 강화 완료.
  **AGENT_STOP 생성·정지.** 재개: .env에 MOLIT_API_KEY+AGENT_STOP 삭제+루프 재실행(F10), 또는 크롤러 등 새 지시.

## 2026-06-29 17:57 KST — X2: 법정동코드 매핑 + 지역명(시군구) 검색
- 무엇: data/lawd_codes.json(서울 25개구 + 샘플 지역 LAWD_CD 5자리) + src/region.py
  (name_to_code·code_to_name·sido_of·matches_region). query.apply_filters 지역필터를
  matches_region으로 강화(시도 prefix + 시군구 부분일치 모두). 드롭다운에 시군구 옵션 추가.
- 증거: pytest 70건 통과(region 5건), ruff 클린. `python run.py --region 강남구 --json` → 역삼 오피스텔만.
- 평가자: 자체검증 + CI.
- 커밋: 8d8678e / GitHub push.
- 다음: X3 방법론·투명성 페이지(/methodology) — Phase 3 마지막.

## 2026-06-29 17:50 KST — X1: 매칭 품질(이상치·최근성·다월) — Phase 3 시작
- 무엇: matcher에 filter_recent(최근 N개월)·trim_outliers(표본 4건↑ 상·하단 1건씩 제거)를 넣어
  추정시세를 안정화(신뢰계수용 매칭건수는 트림 전 원 매칭 수 유지). molit_client에 recent_ymds·
  fetch_trades_months 추가, pipeline.load_live_trades가 라이브 시 최근 3개월(LIVE_MONTHS) 수집.
- 증거: pytest 65건 통과(matcher 4건 추가), ruff 클린. 샘플 6건 결과 동일(표본 ≤3이라 트림 미발동 →
  회귀 없음). 이상치 13억 섞은 4건 표본에서 추정치가 정상값(6.0~6.6억)으로 트림 확인.
- 평가자: 자체검증 + CI.
- 커밋: 78c695d / GitHub push.
- 다음: X2 법정동코드 매핑 + 지역명 검색.

## 2026-06-29 17:14 KST — V3: 주간 차익 TOP N 다이제스트 — Phase 2 완료 → 루프 정지
- 무엇: src/digest.py(top_listings·to_markdown) + run_digest.py(--n/--min-score → evidence/digest.md+html,
  report.to_html 재사용) + web.py `GET /digest`. 차익 스코어순 상위 N.
- 증거: pytest 61건 통과(digest 5건), ruff 클린. run_digest --n 5 → 상계주공(95)·역삼(81)… 정렬 확인.
- 평가자: 자체검증 + CI.
- 커밋: 40417d1 / GitHub push.
- 종료: **Phase 2(V1 백테스트·V2 알림·V3 다이제스트) 완료.** PoC→프로덕션강화(P1~P5)→사이트화
  (W1~W4)→검증·알림(V1~V3) 한 바퀴 완성. **AGENT_STOP 생성하고 정지.** 재개: .env에 MOLIT_API_KEY
  넣고 AGENT_STOP 삭제→루프 재실행(F10 라이브). 실제 크롤러는 운영자 결정 대기.

## 2026-06-29 17:06 KST — V2: 워치리스트 + 차익 변동 알림
- 무엇: src/watchlist.py — 관심물건(data/watchlist.json) add/remove + 직전 스냅샷
  (data/score_snapshot.json) 대비 detect_changes(순수함수): 차익 임계 돌파/스코어 상승/최저가
  하락(유찰) 감지. run_alerts.py CLI(첫 실행 기준선, 이후 변동→콘솔+evidence/alerts.txt).
  워치리스트 있으면 관심물건만. 런타임 상태파일은 .gitignore.
- 증거: pytest 56건 통과(watchlist 6건), ruff 클린. 시연(스냅샷 변형 후 2회차): 4건 감지
  — 상계주공 65→95·역삼 51→81 임계돌파 + 양쪽 최저가 하락(유찰).
- 평가자: 자체검증 + CI.
- 커밋: ff2eac3 / GitHub push.
- 다음: V3 주간 차익 TOP N 다이제스트 (완료 시 AGENT_STOP).

## 2026-06-29 16:59 KST — V1: 백테스트/스코어 검증 하네스 (Phase 2 시작)
- 무엇: src/backtest.py — 낙찰결과 outcomes(합성 fixture data/backtest_outcomes.json) × scored를
  case_no로 조인, 실현차익=실현매도가−(낙찰가+부대비용[score 재사용]) 계산, 스코어 구간별 적중률·
  평균 실현차익 캘리브레이션 + precision@임계. run_backtest.py CLI(콘솔+evidence/backtest.csv).
- 증거: pytest 50건 통과(backtest 4건), ruff 클린. 백테스트 결과 — ≥80 적중률 100%/+0.95억,
  40–59 50%, <40 0%/−0.33억, precision@80=100%·@40=75% (스코어↑ → 실현수익↑ 단조).
- 평가자: 자체검증 + CI.
- 커밋: cec75dd / GitHub push.
- 다음: V2 워치리스트+차익 알림. ※합성 fixture — 실제 낙찰결과 들어오면 교체해 진짜 적중률 측정.

## 2026-06-29 16:35 KST — W4: 필터 UI·Docker·README — 사이트화 완료 → 루프 정지
- 무엇: listings.html 상단 필터 폼(min_score/type/region/sort GET, 선택값 유지)+모바일 wrap.
  Dockerfile(python:3.12-slim, flask :8000)+.dockerignore. README 웹 서버·Docker 섹션.
- 증거: pytest 46건 통과(필터 폼 테스트 2건), ruff 클린. 실서버 스모크(이전 사이클) /·/api/*·상세 200.
- 평가자: 자체검증 + CI.
- 커밋: baeb11b / GitHub push.
- 종료: **사이트화(W1~W4) 완료** — PoC가 브라우저 열람·필터 가능한 Flask 사이트로. 프로덕션 강화
  (P1~P5)+사이트화(W1~W4) 모두 끝. **AGENT_STOP 생성하고 루프 정지(더 예약 없음).**
  재개: .env에 MOLIT_API_KEY 넣고 AGENT_STOP 삭제 → 라이브 F10. 또는 Phase 2(백테스트·알림) 요청 시.

## 2026-06-29 16:27 KST — W3: 물건 상세 페이지 /property/<case_no> (SSR)
- 무엇: templates/detail.html — 갭미터 특대·스코어(96px)·등급·예상순차익·차익 근거(추정시세·
  실질취득원가·매칭·신뢰계수)·권리 안전성(권리점수, 하드게이트면 사유=인수금액비율/유치권 빨강박스)·
  환금성·종합 카드. web.py /property 라우트(ScoredListing+AuctionListing 매칭, 없으면 404).
- 증거: pytest 44건 통과(detail 3건 — 200·404·하드게이트 사유 노출). ruff 클린. 실제 렌더 확인:
  상계주공(확실한차익) / 화곡빌라(위험·유치권 게이트). 목록 단지명 링크 활성화.
- 평가자: 자체검증 + CI.
- 커밋: fdd297c / GitHub push.
- 다음: W4 필터 UI·반응형·Dockerfile (마지막 → AGENT_STOP).

## 2026-06-29 16:19 KST — W2: 큐레이션 페이지 /
- 무엇: templates/listings.html(Jinja2). report.py의 갭미터·원형 스코어뱃지·금액 포맷을 공개
  별칭(score_badge_html/gap_meter_html/won/pct)으로 노출해 재사용(CSS만 템플릿에 동봉, 로직 중복 0).
  차익 스코어순 테이블, 단지명→/property 링크. min_score/type/region 쿼리는 _filtered 헬퍼로
  /api/listings와 공유.
- 증거: pytest 41건 통과(index 렌더·필터 테스트 2건 추가), ruff 클린. test_client로 / 200 +
  '상계주공'·scorebadge·gapmeter 마크업 확인.
- 평가자: 자체검증 + CI.
- 커밋: 5a4af6b / GitHub push.
- 다음: W3 물건 상세 /property/<case_no> (SSR).

## 2026-06-29 16:11 KST — W1: Flask JSON API (사이트화 시작)
- 무엇: src/web.py — Flask 앱. `GET /health`, `GET /api/listings`(min_score/type/region/sort 쿼리
  → query.apply_filters/sort_items 재사용), `GET /api/listings/<case_no>`(404). ensure_ascii=False로
  한글 그대로. requirements에 flask>=3.0(순수 파이썬, 3.14 안전). FastAPI/pydantic 미사용.
- 증거: pytest 39건 통과(test_web.py 7건 추가), ruff 클린. test_client로 /health·/api/listings 200,
  필터·404 확인.
- 평가자: 자체검증 + CI.
- 커밋: 1a736af / GitHub push.
- 다음: W2 큐레이션 페이지(/) Jinja2.

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
