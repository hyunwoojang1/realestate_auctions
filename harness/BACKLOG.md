# BACKLOG — 레퍼런스 갭 분석에서 나온 기능 백로그 (우선순위순)

> 탐색 모드가 항목을 추가하고, 구현 모드가 위에서부터 하나씩 소비한다.
> 상태: `[ready]` 구현 가능 / `[in-progress]` 진행 중 / `[blocked]` 사람 결정 대기(QUESTIONS.md 참조) / `[done]` 완료.
> 모든 항목은 **검증 가능한 완료 정의**(Default-FAIL 체크리스트)를 반드시 포함한다.

## 항목 양식

```
## [ready] B<번호> <제목> (출처: docs/references/<서비스>.md)
- 왜: 레퍼런스의 어떤 기능을 모방하며, 우리 사용자에게 왜 가치 있는지 1~2줄
- 완료 정의 (전부 false에서 시작):
  - [ ] 테스트 <구체 파일/케이스> 통과
  - [ ] evidence/<파일> 생성·Read 확인
  - [ ] pytest 전체 + ruff 클린
- 제약: (라이브 호출 여부, 금지사항 관련 주의)
```

---

## [done] B4 매각·스코어 통계 페이지 `/stats` (사이클 #2, 2026-07-02)
- 완료: src/stats.py(순수 집계 6함수) + 테스트 12개, /stats SSR + /api/stats, 내비 링크.
  - [x] 단위테스트 12개 통과 / [x] test_client 라우트 테스트 / [x] evidence/stats_smoke.txt
    (실 DB data_source=db, 200) / [x] pytest 203 passed + ruff 클린
- 평가자 PASS · code-reviewer APPROVE(LOW 2건 참고: avg_confidence UI 미노출, smoke 상대경로)

## [done] B1 경매 일정 캘린더 `/calendar` (사이클 #3, 2026-07-02)
- 완료: src/sale_calendar.py(순수 그룹핑 5함수, 비ISO 날짜 방어) + 테스트 9개, /calendar 월 뷰
  (예정/전체 토글, 오늘 뱃지, 상세 링크), 내비 '일정' 링크.
  - [x] 단위테스트 / [x] test_client / [x] evidence/calendar_smoke.txt(실 DB 200) / [x] pytest 212 + ruff
- 평가자 PASS · 감사 APPROVE. MEDIUM 2건 즉시 수정(dim CSS .params 적용, 비ISO sale_date → 미상 집계).
  잔여 노트: warnbadge 매크로 3벌 중복(기존 관례 — 통합 리팩터는 별도 항목 후보), '오늘' 뱃지 b-unknown 재사용(LOW).

## [done] B2 관심물건 웹 UI `/watchlist` (사이클 #4, 2026-07-03)
- 완료: src/watchlist.py 웹 승격 + /watchlist 페이지 + 토글(POST /watchlist/toggle/<case_no>) +
  목록·상세 ☆ 버튼. 관심 case_no set 세션 파일 저장.
  - [x] pytest 220 passed(+8 watchlist 웹 테스트) / [x] ruff 클린
  - [x] evidence 스모크(scripts/watchlist_smoke.py): add/list/page/remove 전부 200 확인
- 잔여 노트: 로컬 단일 사용자 전제(인증 없음 — 외부 공개 시 QUESTIONS 재검토). warnbadge 매크로 중복(기존 관례).

## [done] B8 침묵실패 보강 (사이클 #7, 2026-07-03)
- 완료: watchlist.py 손상 파일 폴백(`_safe_load_json`: JSONDecodeError→logger.error+빈값, 500 방지) +
  `load_watchlist_status`/`load_snapshot_status`(손상여부 반환) + 원자적 쓰기(`_atomic_write`: tempfile+os.replace).
  web watchlist_page가 corrupted/snapshot_missing 플래그 전달 → watchlist.html 손상 배너 + '변동없음/스냅샷없음' 원인 구분.
  ScoredListing.est_market_price 계약(None=추정불가, sentinel 금지) 주석.
  - [x] pytest 225(+5: 손상폴백·상태플래그·없음≠손상·원자쓰기 잔여물없음·손상배너) / [x] ruff 클린
- 노트: calendar/stats/watchlist data_source 배너는 base.html 헤더가 이미 표시(세 라우트 모두 data_source 전달) — 별도 배너 불요.

## [done] B6 물건 비교 뷰 `/compare` (사이클 #8, 2026-07-03)
- 완료: src/compare.py `select_for_compare`(순서보존·중복제거·없는건 무시·최대4). `GET /compare?case=A&case=B...`
  SSR 비교표(경고·예상차익·갭·최저가·시세·취득세·취득원가·감정가·유찰·면적·신뢰·기일·소재지·사건번호).
  <2건이면 안내. watchlist 페이지에 '↔ 비교하기(상위N건)' 링크(관심물건 재사용).
  - [x] pytest 232(+7: 순서/중복/상한/빈결과/라우트2건/1건안내/없는건무시) / [x] ruff 클린 / [x] evidence/compare_smoke.txt
- 노트: '비교 담기 cart'는 세션상태 필요 → watchlist를 선택집합으로 재사용(오프라인). 상세페이지는 ★ 토글로 담기.

## [done] B7 CSV 내보내기 `/export.csv` (사이클 #10, 2026-07-03)
- 완료: report.py `csv_text(items)->str` 헬퍼 도입 + `to_csv`가 재사용(중복 구현 제거). `GET /export.csv`가
  `_filtered(request.args)` 재사용 → 목록과 동일 필터·정렬, UTF-8-SIG BOM + Content-Disposition attachment.
  listings.html에 현재 쿼리 보존 "⤓ CSV 내보내기" 링크.
  - [x] pytest 242(+8: csv_text 3·라우트 5[타입/attach·BOM·행수일치·필터통과·링크]) / [x] ruff 클린
  - [x] evidence/export_smoke.txt(200·BOM True·행수=/api/listings·min_profit 필터 일치) Read 확인
- 평가자 PASS. LOW(to_csv 중복 Path 호출) 즉시 정리. MEDIUM(CSV 인젝션)→B11 이월.

## [done] B9 스냅샷 없음 vs 빈 스냅샷 구분 (사이클 #11, 2026-07-03)
- 완료: watchlist_page가 `snapshot_missing = (not snap_path.exists() and not snap_corrupt)`로 판정
  (파일 존재 기준). 빈 스냅샷 {}(매물 0건 새로고침 정상 저장)을 'prev falsy'라는 이유로 '없음' 오판하던 문제 해소.
  손상은 corrupted 배너가 별도 처리(template 3-상태 elif 체인 정합).
  - [x] pytest 244(+2: 빈스냅샷≠없음·파일없음=없음 회귀가드) / [x] ruff 클린
  - [x] evidence/snapshot_missing_smoke.txt(없음O·빈{}오안내사라짐+변동없음O·손상배너O) Read 확인
- 평가자 PASS(C/H/M/L 0).

## [ready] B17 T7 잔여 문구 정리 — rank 열 의미·report.py 리포트 (출처: T7 평가자 LOW 2건, 2026-07-03)
- 왜: ① 히어로 도입 후 테이블 rank가 "차익 순위"가 아니라 "추천 우선 표시 순서"가 됨(위험 물건이
  rank 2인데 금액은 히어로보다 큼 — 뱃지로 오독 위험 낮으나 정리 필요). ② run.py 콘솔 표·report.to_html은
  여전히 expected_profit 열 중심(정렬만 보수 기준) — CLI/HTML 리포트도 보수 차익 열 추가 권장.
- 완료 정의(전부 false):
  - [ ] rank 열 의미 명확화(표시순 라벨 or 위험 행 순위 제외) + report.py 보수 차익 열
  - [ ] 테스트 + pytest 전체 + ruff 클린
- 제약: 순수 로컬.

## [ready] B16 신뢰계수를 basis 기반으로 통일 (출처: T5 평가자 LOW, 2026-07-03)
- 왜: confidence(신뢰계수)는 matched_trades(트림 전) 기반인데 T5 게이트는 basis(트림·최근성 후) 기반 —
  "매칭 7건·신뢰 1.00"인데 "낮은 신뢰" 경고가 병존 가능(라벨 분리로 혼란 제한적이나 장기 통일 필요).
- 완료 정의(전부 false):
  - [ ] confidence_ladder 입력을 basis로 전환(레거시 None은 matched 폴백) + 골든셋 영향 검토
  - [ ] 테스트 + pytest 전체 + ruff 클린
- 제약: 점수 스케일 변동 가능 — 의도된 변경으로 versions.md 기록.

## [ready] B15 죽은 시세 수집 제거 — rh/sh/nrg/land 라이브 호출 스킵 (출처: T2 평가자 MEDIUM, 2026-07-03)
- 왜: T2로 시세 추정이 아파트·오피스텔만 지원되면서 load_live_trades의 rh 기본 수집(pipeline.py:198)과
  sh/nrg/land 확장 수집(186~187)이 어떤 물건과도 매칭 불가 → 국토부 API 쿼터만 소모(죽은 코드).
  단, T5 표본게이트·향후 유형 확대 시 재사용 가능성 있어 삭제 아닌 '수집 스킵 + 재활성 스위치' 권장.
- 완료 정의(전부 false):
  - [ ] load_live_trades가 SUPPORTED_ESTIMATION_KINDS 기준으로만 수집(rh/확장 스킵, config로 재활성 가능)
  - [ ] digest '위험' 등급 TOP 노출 정책 점검(경고 칼럼만으로 충분한지 — T7 문구 개편과 함께)
  - [ ] 테스트 + pytest 전체 + ruff 클린
- 제약: 라이브 경로 변경 — 오프라인 테스트로만 검증, 실호출 금지.

## [ready] B14 multi-item 사건의 소비단계 식별 모호성 해소 (출처: T1 평가자 MEDIUM 2건, 2026-07-03)
- 왜: T1로 저장 누락은 해소됐으나, `/api/listings/<case_no>`·`/property/<case_no>` 상세 라우트와
  watchlist(스냅샷 dict)·backtest(조인)·compare(by_case)가 여전히 case_no 단일 키 → 한 사건에 물건
  여러 개면 임의의 한 물건만 보이거나(상세) 마지막 항목이 이김(dict). 데이터 손실은 아니고 표시/집계 왜곡.
- 완료 정의(전부 false):
  - [ ] 상세 라우트에 uid(doc_id 또는 court|case_no|item_no) 기반 조회 추가(기존 case_no 라우트는 유일할 때만 매칭, 모호하면 선택 안내)
  - [ ] watchlist/backtest/compare 키를 uid로 전환(기존 case_no 데이터 하위호환 이관)
  - [ ] multi-item fixture 회귀 테스트 + pytest 전체 + ruff 클린
- 제약: 순수 로컬, 오프라인.

## [ready] B10 워치리스트 쓰기 실패 로깅·사용자 메시지 (출처: 사이클#9 감사 silent-failure MEDIUM+LOW)
- 왜: `_atomic_write`가 실패 시 로그 없이 raise(로드측 `_safe_load_json`과 비대칭) → OneDrive 락/디스크풀 시
  운영자가 어느 case_no add/remove가 실패했는지 모름. 사용자에겐 500만 노출("별표 눌렀는데 안 됨" 모호).
  (LOW) corrupted 배너 문구가 손상/권한거부를 뭉뚱그림 → "손상되었거나 읽을 수 없습니다"로 일반화.
- 완료 정의(전부 false):
  - [ ] `_atomic_write` except에서 `logger.error("워치리스트 쓰기 실패: %s (%s)", p, e)` 후 re-raise
  - [ ] toggle/api add·remove가 `OSError` catch → 사용자에게 "저장 실패, 다시 시도" 명시 메시지
  - [ ] watchlist.html 손상 배너 문구 일반화(LOW) / 테스트 + pytest 전체 + ruff 클린
- 제약: 순수 로컬, 오프라인.

## [ready] B11 CSV 인젝션(엑셀 수식) 완화 (출처: 사이클#10 평가자 MEDIUM)
- 왜: `csv_text`가 apt_name/address/grade 등 크롤 문자열을 이스케이프 없이 셀에 씀. `=`,`+`,`-`,`@`로
  시작하는 값은 엑셀에서 수식으로 해석될 여지. 출처가 공공데이터라 저위험이나 "엑셀 검토용" 기능이라 표준 완화 가치.
- 완료 정의(전부 false):
  - [ ] `csv_text` 셀 값이 위 선행문자로 시작하면 `'` 프리픽스(또는 동등 완화) — to_csv/export 공통 적용
  - [ ] 테스트: `=cmd` 같은 값이 `'=cmd`로 나오는지 / 정상 한글값은 불변 / pytest 전체 + ruff 클린
- 제약: 순수 로컬, 오프라인. 데이터 의미 변형 최소화(숫자·정상 텍스트는 그대로).

## [ready] B12 워치리스트 '비교 불가' vs '변동 없음' 문구 구분 (출처: 사이클#12 감사 침묵실패, CRITICAL→MEDIUM 강등)
- 왜: 직전 스냅샷이 빈 `{}`(직전 전체 매물 0건 새로고침)일 때 events=[]가 되어 "변동 없음"으로 표시되나,
  실제로는 "비교할 이전 데이터가 없어 비교 불가"가 더 정확. 단 이는 detect_changes의 보편 설계(prev에 없는
  신규 case_no는 이벤트 없음)와 동일하며 빈스냅샷 고유 회귀 아님 → CRITICAL 아닌 MEDIUM(문구 정밀도).
  검증(사이클#12): 빈{}·비어있지않음(신규case) 둘 다 events=[] 동일, prev에 데이터 있으면 정상 감지.
- 완료 정의(전부 false):
  - [ ] events 계산 기준을 snapshot_missing과 통일(파일 존재/비손상), 또는 prev가 {}이고 watched 있으면
        "이전 스냅샷에 관심물건 데이터 없어 비교 불가" 별도 문구
  - [ ] 테스트: 빈 스냅샷 + 워치 항목 존재 시 '비교 불가'류 안내(‘변동 없음’ 오인 아님) / pytest 전체 + ruff
- 제약: 순수 로컬, 오프라인. 극단 엣지(직전 0건)라 우선순위 낮음.

## [ready] B13 빈 CSV(0건)도 헤더행 유지 (출처: 사이클#12 감사 침묵실패 MEDIUM)
- 왜: `csv_text([])==""` (헤더도 없음)라 필터 0건 시 완전 빈 파일 → "필터 실수"와 "정상 0건"을 구분 불가.
- 완료 정의(전부 false):
  - [ ] 0건일 때도 고정 컬럼 스키마 헤더행은 출력(쿼리 정상 수행+결과 0건 신호) — to_row 키 고정 소스에서 파생
  - [ ] 테스트: 필터로 0건인 export가 헤더행 1줄 포함 / 기존 csv_text([])=="" 계약 변경 영향 점검 / pytest+ruff
- 제약: 순수 로컬. csv_text 계약 변경이면 to_csv/CLI 영향도 함께 확인.

## [blocked] B3 지도 뷰 `/map` (출처: 두 레퍼런스 공통 기본기능) — QUESTIONS Q1 대기
- 블로킹 사유(2026-07-03 사이클#5): 좌표계 문제. courtauction `wgs84Xcordi/Ycordi`는 정수부만(127/37, 무용),
  `xCordi/yCordi`는 투영좌표인데 CRS 식별 모호(역산 시 경도 128.2°로 서울과 불일치) + pyproj 미설치.
  무인 손계산 변환은 핀 오배치 위험 → 운영자 결정 필요(QUESTIONS Q1). 결정 나면 [ready] 복귀.
- 왜: 지도검색은 양쪽 모두 핵심 진입점. Leaflet+OSM 타일(키 불요)로 물건 핀 + 스코어 색상.
  좌표는 V-World 지오코더(키 보유)로 배치 1회 변환 후 **로컬 캐시**(재호출 금지).
- 완료 정의:
  - [ ] `src/geocode.py` 주소→좌표 + 캐시(파일/DB) + 단위테스트(fixture 응답)
  - [ ] `GET /map` Leaflet 페이지(핀 클릭→상세 링크) + `GET /api/listings.geojson` + 테스트
  - [ ] evidence/map_smoke.txt (지오코딩 캐시 적중률 포함) Read 확인
  - [ ] pytest 전체 + ruff 클린
- 제약: V-World 라이브 지오코딩은 배치 1회(사이클당 라이브 1회 규칙), 이후 캐시만

## [ready·후순위] B5 낙찰결과 수집 → 실데이터 낙찰가율 (출처: jiji-auction.md ALG의 전제)
- 왜: 예상낙찰가·백테스트 실데이터화의 전제. courtauction **공개 매각결과**(공공 원천, 유료사이트
  아님 — GOAL 데이터 원칙 준수) 수집 → V1 백테스트의 합성 outcomes를 실데이터로 교체.
- 완료 정의:
  - [ ] 매각결과 파서 + fixture 테스트 (라이브 정찰은 스모크 1회)
  - [ ] outcomes 저장 스키마 + V1 백테스트 연동 테스트
  - [ ] evidence/outcomes_live_smoke.txt Read 확인 + pytest 전체 + ruff 클린
- 제약: 전국 리스트 크롤과 동시 실행 금지(WAF 정책). 상세 크롤 폭주 금지 — 점진 수집.
