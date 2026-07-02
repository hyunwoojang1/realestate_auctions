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
