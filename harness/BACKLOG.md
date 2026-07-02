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

## [ready] B1 경매 일정 캘린더 `/calendar` (출처: jiji-auction.md)
- 왜: 지지옥션 경매캘린더 모방. 매각기일(sale_date)이 이미 DB에 있어 그룹핑만 하면 됨.
  "이번 주 입찰 가능한 고스코어 물건"이 큐레이션과 시너지.
- 완료 정의:
  - [ ] 기일별 그룹핑 쿼리 + 단위테스트
  - [ ] `GET /calendar` 월/주 뷰(물건 상세 링크, 스코어 뱃지) + test_client 테스트
  - [ ] evidence/calendar_smoke.txt Read 확인
  - [ ] pytest 전체 + ruff 클린
- 제약: 라이브 호출 0

## [ready] B2 관심물건 웹 UI `/watchlist` (출처: jiji-auction.md, tank-auction.md)
- 왜: 두 레퍼런스 공통 기본기능 "관심물건+변동 알림". 우리 V2 watchlist(CLI/파일)를 웹으로 승격 —
  추가/제거 버튼, 스냅샷 대비 변동(스코어 상승·유찰·취하) 표시.
- 완료 정의:
  - [ ] `GET /watchlist` + `POST/DELETE /api/watchlist/<case_no>` + test_client 테스트
  - [ ] 기존 V2 diff 로직 재사용(중복 구현 금지) 확인
  - [ ] 목록/상세 페이지에 관심 토글 버튼
  - [ ] evidence/watchlist_web_smoke.txt Read 확인 + pytest 전체 + ruff 클린
- 제약: 로컬 단일 사용자 전제(인증 없음 — 외부 공개 시 재검토 항목으로 QUESTIONS에 남길 것)

## [ready] B3 지도 뷰 `/map` (출처: 두 레퍼런스 공통 기본기능)
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
