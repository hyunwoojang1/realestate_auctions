# versions.md — auction-arbitrage 루프 작업 로그 (append-only, 최신순)

## 2026-07-21 16:20 KST — 🎯 함정 방어(권리미확인=점수없음) + 차익 낮은순 정렬 추가(사용자 요청)
- 배경: 라이브 검수에서 보수차익 1위가 함정(유찰 10회·최저가 감정가 3%·대항력 임차인·인수금
  미상→0가정으로 4.95억 차익)이 점수순 최상위로 뜸. 사용자: "인수금 0(권리 미확인)이면 점수 자체가
  없게. 그리고 차익 낮은순 정렬 넣어 손해 물건도 그 정렬일 때만 노출".
- **함정 방어(B)**: `score.py` score_listing·_apply_market_price — 권리 미확인(rights_verified=False,
  하드게이트 아님)이면 **arb_score=None**(점수 없음). grade는 '권리미확인' 유지, 차익(expected_profit·
  profit_low)은 적재(정렬용). `query.py` 점수순 정렬도 미확인을 저장 플래그로 즉시 강등(재채점 전에도 효과),
  차익순 정렬도 미확인을 불확실 티어로 강등.
- **정렬 옵션(A)**: `query.py` SORT_KEYS에 **profit_asc(차익 낮은순)** 추가 — 손해(마이너스 차익)까지
  오름차순 노출. `listings.html` 드롭다운에 '차익 높은순'·'차익 낮은순(손해 포함)' 추가.
  `web.py` 기본/추천 뷰는 손해(효과차익≤0) 숨김 — profit_asc 정렬·전체탐색·이름검색일 때만 노출
  (데이터 적재는 유지, 뷰 필터만). ⚠️web.py는 타 세션 미커밋(사건번호검색)과 공존이라 **인덱스에
  제 hunk만 plumbing으로 스테이징**(워킹트리 타 세션분 불변).
- 테스트: profit_asc 오름차순·점수순 함정강등·미확인 arb_score None 등 추가. **전체 622 passed**.
- 효과: 데이터는 다음 재채점부터 arb_score=None 반영, 정렬 강등은 배포 즉시 효과(저장 플래그 기반).

## 2026-07-21 11:20 KST — 🚀 전량 권리크롤 착수 + crawl_rights 상한/스로틀 옵션화
- **미크롤 ~1.1만 건**(scored 14,564 − 크롤 3,663) 전량 크롤 착수. 500은 매물수 아니라 안티밴 상한이었음.
- **crawl_rights 옵션 추가**: `--cap`(일일요청상한 오버라이드, 기본500), `--min-interval`/`--max-interval`
  (요청 지연). 대량 백필용. 403/위장차단 감지는 이 값과 무관하게 항상 즉시중단(우회 아님).
- **사용자 결정**: 단일IP 유지하되 스로틀 3~8초→**2~4초**로 상향(시간 ~절반). 실행=`--all --cap 20000
  --min-interval 2 --max-interval 4`(백그라운드). 초기 실측 크래시0·403차단0.
- **IP 로테이션/프록시 병렬은 거부**(사용자 문의): 기술은 실재하나 이 프로젝트 합법성 근거(공공누리
  저빈도·프록시/VPN 금지·잡코리아 판례=우회는 가중요소)와 정면 충돌 + 정부사이트라 밴 시 전체 접근
  차단. 단일IP 정중크롤이 안전 최대치.
- 남은 미크롤은 이 전량크롤 + 밤 daily(F1)가 이어감. syntax·ruff 통과.

## 2026-07-21 10:30 KST — 🐛 권리크롤 크래시 2종 수정 + 수동 권리크롤 262건(감사 전후 통과)
- **수동 권리크롤 실행**(사용자 요청, 네트워크 불안정 아침): 크롤 전 감사(pytest 620·data_gates 9 PASS)
  → crawl_rights --limit 500 → 크롤 후 감사(data_gates PASS·pytest 620·표본 실데이터 확인).
  **listing_rights 3380→3642(+262 실크롤)**, Supabase 미러 3642건 완료(프로덕션 반영).
- **크래시 버그 2종 수정**(`deploy/crawl_rights.py`) — 이 환경 네트워크가 연결을 계속 강제리셋
  (ConnectionReset 10054)하며 드러남:
  - ① **사진 업로드 실패가 크롤 전체 크래시**: Supabase Storage 업로드 ConnectionReset이 안 잡혀
    죽던 것 → 사진 블록 try/except(사진은 부수기능, 실패해도 권리크롤 계속).
  - ② **개별 물건 네트워크 리셋이 크롤 크래시**: `_warm_session` 등 retry-미포함 경로의 ConnectionError가
    per-item except(CourtAuctionError만 잡음)를 통과해 죽던 것 → `except Exception` 추가(한 물건 실패는
    스킵하고 계속, 차단신호는 위에서 즉시중단 유지). 크래시<미탐<완주 원칙.
- **⚠️ 03:08 예약 오케스트레이터**: 앞서 RunPy exit-code 오판 버그로 크롤 안 하고 중단했던 것 수정 완료
  (별도 커밋). 이후 daily(05:30)는 실패(HRESULT), 그래서 수동 실행.
- 검증: syntax·ruff·pytest 620 통과. 남은 미크롤은 다음 daily(F1)가 이어감.

## 2026-07-21 03:30 KST — 🔍 밤샘 크롤후검수: 확정단지 감정가 하한 게이트 추가(오매칭 방어)
- 배경: 10억 크롤 완료(백필 1,270쌍·실거래 73,061행, 재채점 14,564건 미러) 후 스냅샷
  (data/backup/auction_postcrawl_20260721.db) 대상 검수·성과·데이터감사 3-에이전트 워크플로.
- 검수 결과: 5~10억 est 149건 중 KB밴드 내 76%(이탈 29건 중 27건이 '아래'=안전방향),
  scope 92.6% same_complex, 과대추정·밴드폭과대·게이트우회 전부 0 — **데이터 양호**.
- **실질 결함 1건 수정**: 확정단지 경로(estimate_from_complex_trades)의 감정가 교차검증이
  상한(2.5배)만 있고 **하한이 없어**, 네이버가 상가/지하유닛을 이름 다른 소형 주거유닛에
  오매칭하면 est가 감정가의 0.106배로 붕괴해도 통과(용인역북더럭스나인 2025타경56016 실측).
  → `SAME_COMPLEX_EST_MIN=0.35` 하한 추가(폴백 0.6보다 관대 — 확정단지는 신뢰↑, 극단만 차단).
  blast radius 실측: est/감정가<0.4=7건(0.33%)만 영향. 다행히 전부 이미 차익없음으로 걸러져
  노출은 안 됐던 건(est 무결성 개선).
- 테스트: 하한 무효화 + 정상저가 통과 2건 추가. **전체 620 passed, 1 skipped**.
- 데이터감사 확정결함(CRITICAL/HIGH/MEDIUM) 0 — 취소거래·중복·조인·게이트 전부 정상.
  LOW 2건(빈매칭쌍 331 크롤공백·comps 2.1% 재구축 타이밍 stale, 영향 미미)은 기록만.
- **push·deploy 안 함**(다른 세션 권리작업+배포 조율 대기). matcher.py·test만 로컬 변경.

## 2026-07-21 05:25 KST — 🐛 권리크롤 오케스트레이터 PS1 버그수정(03:08 예약 오판중단)
- **03:08 예약작업 실측**: 정상 실행됐으나 **사전검수 FAIL로 크롤 안 함**(Default-FAIL 게이트는
  의도대로 작동, 데이터 무손상·배포 안 나감). **원인=진짜 실패 아님**: pytest는 618 passed였는데
  `RunPy`가 exit code 대신 **Tee 통과출력 전체를 배열로 반환** → `-ne 0`이 항상 참 → pytest FAIL 오판.
- **수정**(`scripts/rights-crawl-and-ship.ps1`): `RunPy`에 `| Out-Null` 추가해 통과출력 버리고 exit code만
  반환. 실증: exit0→반환0(-ne0=False), exit1→반환1(-ne0=True). BOM 재적용·파싱 OK.
- **상태**: 현재 auction 크롤 미실행(flask 웹서버만). listing_rights 4081→3288(어제 run.py prune).
  05:30 일일새로고침이 F1로 crawl_rights(limit 300) 포함 — 곧 실행되므로 수동 중복크롤은 밴위험, 미실행.

## 2026-07-20 23:08 KST — ⏰ 권리 크롤 4시간 후 자동실행 예약(사전검수→크롤→사후검수→푸시·배포)
- **요청**: 리스트 크롤 종료 후 그 물건 기준 권리 크롤. 크롤 **전후 감사·검수 게이트**. 통과 시에만
  푸시·배포. 4시간 후 자동 진행. → Windows 예약작업(프로젝트 기존 패턴).
- **신규 `harness/RIGHTS_CRAWL_RUNBOOK.md`**: 절차·게이트·롤백·한계 문서화(Default-FAIL 원칙).
- **신규 `scripts/rights-crawl-and-ship.ps1`**: Phase0 리스트크롤종료확인 → Phase1 사전검수(pytest·ruff·
  data_gates·카운트, FAIL시 크롤 중단) → Phase2 crawl_rights --limit 500 → Phase3 사후검수(rights델타>0·
  data_gates·pytest·exit0, FAIL시 배포 차단) → Phase4 git push + deploy_prod.sh(Git Bash 자동탐색).
  결과=`harness/RIGHTS_CRAWL_REPORT.md` + `evidence/rights-crawl-<ts>.log`. UTF-8 BOM(한글 보존), 파싱 OK.
- **예약작업 `AuctionArbitrage-RightsCrawl-Once`**: 1회성 트리거 **2026-07-21 03:08 KST**(등록 Ready,
  다음실행 03:08:08 확인). AllowStartIfOnBatteries·StartWhenAvailable·3h 상한.
- **한계(정직)**: 무인 실행의 '감사'는 결정론적 검수(테스트·게이트·카운트·exit)뿐 — 다관점 LLM 리뷰어
  감사는 세션 필요(로그/리포트로 사후 재검토). 현황조사서(P4)는 별건, 이번 크롤은 명세서 요지까지.
- ⚠️ 03:08 크롤과 기존 05:30 일일새로고침(F1로 crawl_rights 포함됨)이 같은 밤에 겹침 — 하룻밤 court
  부하 증가(문제 시 -SkipRights 또는 예약 취소). 배포는 커밋된 main만 나가므로 안전.

## 2026-07-20 19:45 KST — ☁️ 적재경로(크롤→로컬→Supabase→Vercel) 감사 + H1/F1/스키마 수정
- **H1 완전 해결·라이브 검증**(`run.py`): naver KB시세/호가가 클라우드로 안 올라가던 것(upsert_naver
  호출자 0개) → run.py scored 미러 옆에 `store_rest.upsert_naver` 배선. **진짜 원인 추가발견**: 클라우드
  auction_naver_prices 가격컬럼이 **integer**라 서울 고가물건(27.5억 등, int32 max=21.4억 초과)이
  22003 out-of-range로 미러 전량 실패 → **Management API로 bigint ALTER**(403은 토큰문제 아니라
  Cloudflare가 기본 UA 차단 — 브라우저 UA로 201 성공). **전량 미러 실측: 클라우드 naver 2351→4754
  (로컬 일치), KB시세 801→1552, 고가물건 정상 저장.** 서빙: 이제 로컬↔Vercel 시세 일치.
- **F1 수정**(`scripts/refresh-daily.ps1`): 일일 새로고침에 **crawl_rights가 아예 없어** 신규 courtauction
  물건 권리가 수동실행 전엔 영영 미채움(전국 24개 법원 통째 미크롤의 근본원인) → run.py **앞**에
  `crawl_rights --limit $RightsLimit(기본300)` 추가(같은 사이클 채점 반영). `-SkipRights` 스위치·일일캡·
  킬스위치 안전장치. ⚠️나이틀리 courtauction 부하 증가(사용자 인지 필요).
- **스키마 DR 풋건 수정**(`deploy/supabase_rights.sql`): 라이브엔 있으나 SQL엔 없던 것 3종 — naver
  가격 bigint화 + lease_low/lease_high 추가, photos photo_url 추가 + thumb_b64 nullable화(Storage모드).
  이 SQL로 재구축 시 값 드롭·사진서빙 사망하던 것 방지(라이브와 일치).
- **2차 적재감사(2에이전트) 재검증 결과**: 미러 완전성=구멍 없음(4테이블 per-컬럼 파리티 통과), real_trades
  비미러=정상, 배지 목록vs상세 일치. **보고(미수정)**: F2 품질게이트가 한 행 위반에 전체 미러 차단
  (정체 취약, 격리방식 검토), fetch_naver_price 죽은코드(무해), naver/photos prune 경로 부재(고아누적·
  서빙무해).
- **검증**: 전체 618 passed·ruff·ps1 파싱 OK. Supabase 라이브 미러 실측 통과(dangerouslyDisableSandbox).

## 2026-07-20 19:15 KST — 🔁 2차 감사 종합: 재검증 후 진짜 문제 3건 수정 + 4건 보고
- **3영역 감사(채점·게이트 / 시세추정 / 서빙정합) 각 auction.db 실행검증. 재현된 진짜 문제만 수정:**
  - **M1 수정**(`courtauction_detail.py` summarize): `has_risk_text`(원문 실질텍스트)를 그대로 burden
    근거로 써서 '임차권등기(다만 말소동의 확약서 제출됨)' 같은 **소멸 예정** 권리도 burden 오판 →
    clean 물건이 차익추천서 제외되던 것. 부정절 제거 후에도 텍스트 남을 때만 위험으로 판정. **실 DB
    재현 86건 → 수정 후 0건**(전체 burden 1666→1580, 86건 clean 전환). opposable/assumed/special은
    이미 부정절 인식하므로 정합. 영향은 서빙 필터/정렬 한정(채점·게이트엔 status 미전달, 감사가 정정).
  - **L2 수정**(`config.py`): summarize가 붙이는 '선순위전세권' 라벨이 special_penalty에 키 없어 조용히
    default(10) 타던 것 → 명시값 20 부여(배당요구 안 하면 실인수).
  - **L1 수정**(`score.py` rights_score): 특수권리 페널티 합산을 `set()`로 — 상류 dedup 실패 시 동일
    라벨 이중감점(잠재) 방어. 실 DB 현재 0건이나 정확성 보장.
- **반증(수정 안 함)**: is_substantive '없음'류 오판 실 DB 0건. 시세추정 감사=CRITICAL/HIGH 0(취소거래
  'O' 수정 DB반영·옆단지혼입0·표본게이트 실효 전부 확인, LOW 3만). 배지 목록vs상세 4081건 divergence 0.
- **검증**: 신규 테스트 2, 전체 **618 passed, 1 skipped**, ruff 통과.
- **보고(수정 보류 — 사용자 결정/별도 처리 필요)**: ①**H1 naver 클라우드 미러 경로 부재**(HIGH·확증) —
  `store_rest.upsert_naver` 호출자 0개 → naver 크롤이 로컬만 갱신, Vercel 프로덕션은 KB시세 절반(801 vs
  1552)만 있어 **로컬↔프로덕션 시세·차익 상이**. 프로덕션 미러 변경이라 검증 미러런과 함께 처리 권장.
  ②H2 data_gates가 rights 내용·naver 신선도·미러성공 미검사(게이트 사각). ③M3 gate_scls_consistency·
  gate_share_sale이 복합키 GROUP BY 없이 조인순회 → 일괄매각 다목적물서 false-FAIL 미러차단 가능(보수방향).
  ④M2 occupant_type 미배선=P4(현황조사서 필요).

## 2026-07-20 18:52 KST — 🔁 2차 감사 착수 + C6 오거부 방지 견고화 + is_substantive 오판 반증
- **C6 견고화**(`deploy/crawl_rights.py`): 응답 사건번호 대조를 raw 문자열 비교→`casesearch.parse_case_query`
  canonical(연도,일련) 환원 비교로 변경. 표기차("2025-63992" vs "2025타경63992")로 전량 오거부되던
  내가 넣은 리스크 제거 — **둘 다 파싱돼 확실히 다를 때만** 스킵(파싱실패/빈응답은 저장 강행, 미탐<오거부).
  실검증: 동일사건 표기차 skip=False·진짜 다른 사건 skip=True·빈응답 skip=False. 616 passed.
- **is_substantive 오판 반증**(수정 안 함): "인수되는 권리 없음"류를 burden 오판할 수 있다는 의심을 실 DB
  4081행으로 검증 → **0건**(추출 인수권리 필드는 비어있거나 실내용, '없음' 문구 안 들어옴). 진짜 문제
  아니므로 미수정(검증-후-수정 원칙).
- **2차 감사 3영역 병렬 착수**: ①채점·게이트 배선 ②시세추정 정확성 ③서빙일관성·품질게이트·저장정합.
  각 에이전트가 auction.db 실행검증 후 진짜 문제만 보고 → 재검증 후 수정 예정.

## 2026-07-20 18:40 KST — 🔬 현황조사서/감정평가서 취득 정찰 스크립트(P4 A/B 확정용)
- **배경**: 권리 크롤이 매각물건명세서만 읽고 **현황조사서(점유·전입일자·보증금)**를 안 가져옴.
  감정평가서는 감정평가액(목록 gamevalAmt)+요항점(pgj15B aeeWevlMnpntLst)은 이미 옴 → 진짜 공백은
  현황조사서. 픽스처 실측: 현황조사서에 점유관계+임대차(전입/확정/보증금)가 있어 **대항력 판정의
  authoritative 전입일 소스이기도 함**.
- **추측 0 원칙 준수**: 현황조사서가 (A)pgj15B 응답의 미판독 키에 있는지 (B)별도 엔드포인트인지
  확인 전엔 fetch 코드를 못 씀(확인 안 된 키로 짜면 침묵실패 신설). → 신규 `deploy/probe_detail.py`가
  **라이브 1콜**로 판정: dma_result 원본 덤프(evidence/, gitignore) + 점유/임대차/전입/보증 신호를
  값·키이름 **재귀 탐색** → (A)후보 경로 보고 or (B)신호 전무=별도 엔드포인트 필요. 대상은 미크롤
  법원 물건 우선(고양지원 등 --court/--case로 지정 가능).
- **실행 시점**: 목록 크롤과 요청 겹치면 밴 위험 ↑ → **목록 크롤 종료 후 단독 실행**. 결과에 따라
  normalize()에 키 추가(A) 또는 현황조사서 열람 요청 추가(B)로 배선 예정.
- 검증: syntax·import·ruff 통과(네트워크 0, 실행은 사용자 시점 결정).

## 2026-07-20 18:22 KST — 🛡️ 권리 크롤러 다관점 감사 + 오판 6종 수정(크롤 전 안전화 P1·P2)
- **배경**: 크롤 확대 중 '권리 미확인' 다수 발견. 진단 결과 ①24개 법원(고양·평택 등) 상세 크롤
  미커버(순번 문제) ②크롤된 것도 판정 오류 존재. 크롤 재개 **전에** 판정 로직부터 안전화.
- **감사**: 전문 리뷰어 3종 병렬(silent-failure / python-reviewer / 취득가능성 규명) + 자체 실코드
  재현. CRITICAL 6·HIGH 5·MEDIUM 4 도출, 상위는 전부 실데이터/실코드로 확증.
- **P1 파서·판정 오판 수정(오프라인·TDD, `courtauction_rights.py`·`courtauction_detail.py`·`config.py`)**:
  - **C1** 가등기·가처분·토지별도등기 특수권리 사전+페널티(25/25/15, 비-fatal) 추가 — 실 DB 가등기24·
    가처분10·토지별도39건이 배지·게이트에 안 잡히던 것. **표준 명세서 항목 안내문(“2. 등기된 부동산에
    관한 권리 또는 가처분으로서…”) boilerplate 제거**로 전물건 가처분 오탐 방지(`_strip_special_boilerplate`).
  - **C2** 특수권리 부존재 문맥 절 제외(`_appears_unnegated`) — “유치권 신고 없음”을 유치권(fatal)→
    하드게이트 ‘위험’ 직행시켜 정상물건 영구배제하던 것 수정. 다른 절의 진짜 신고는 보존.
  - **C3** `_MOVEIN_RE`에 “신고/세대” 허용 — 법원 표준 라벨 “전입신고일자” 미매치로 대항력 근거분석이
    실데이터서 무력화되던 것 수정.
  - **C4** `_strip_negated_clauses` 절 구분자에 콤마 추가 — “A는 인수 아니, B는 매수인 인수” 콤마혼재
    다중임차인 문장에서 인수신호를 통째 삼키던 미탐 수정.
  - **H5** 대항력 강한신호에 “매수인이/에게/ 부담” 추가 — ‘인수’ 대신 ‘부담’ 표기 시 -30점 누락 방지.
- **P2 배치 안전장치(`deploy/crawl_rights.py`)**:
  - **C5** 차단(blocked)→exit 2, 실패율>50%→exit 3, Supabase 미러 비활성 시 명시 경고 — ‘부분 중단’이
    exit 0(완전성공)으로 위장되던 침묵실패 차단.
  - **C6** 응답 `userCsNo` vs 요청 사건번호 대조, 불일치 시 스킵 — 서버 경합으로 ‘엉뚱한 사건 권리’가
    저장되고 재크롤서도 빠져 영구유실되던 위험 차단.
- **검증**: 신규 회귀 6테스트 추가, 전체 **616 passed, 1 skipped**(종전 610), ruff 통과. 네트워크 0.
- **미완/후속(설계 트레이드오프·구조적이라 별도 처리)**: G2 점유(현황조사서)—크롤·저장·서빙 어디에도
  없고 `rights_verified=True`로 거짓표시. pgj15B raw 1건 덤프로 A(응답에 있음·배선만)/B(별도 엔드포인트)
  확정 후 배선 예정(라이브 1콜 필요). H1(동일금액 두임차인 과소산정, set-dedup 재설계), H2(동일날짜
  대항력), H3(임차인-근거절 연결), H4(필드 카나리) 백로그.
- **커밋/배포**: 사용자 확인 후 일괄. **크롤 재개는 P4(점유 배선) 결정 후 권장**.

## 2026-07-20 17:18 KST — 🔎 사건번호 검색 기능(친구가 보내는 "2025-101763" 조회)
- 배경: 친구가 "2025-101763" 식으로 사건번호를 보내는데 조회 경로가 없었음. 저장 포맷은
  "YYYY타경NNNNN"(구분자 오직 "타경", 실측)이라 완전일치가 실패하던 문제.
- **명확한 논리(무한수정 방지)**: 신규 `src/casesearch.py`가 3층 계약으로 고정 —
  ①정규화 `parse_case_query`(어떤 표기든 (year,serial)로 환원, canonical=`{year}타경{serial}`)
  ②로컬매칭 `match_local`(표준형 완전일치·연도미상은 전연도 후보) ③라이브 `live_lookup`
  (법원명→boCd + case_detail). `test_casesearch.py` **32케이스로 규칙 고정**(표기변형은 케이스
  추가로만 확장, 규칙 임의변경 금지).
- **법원코드는 추측 0**: `data/court_codes.json`(57개 법원, court→boCd=cortOfcCd)을 **우리 raw_listings
  실크롤 데이터에서 자동추출**(충돌 0). 재생성 `deploy/build_court_codes.py`(충돌 시 에러로 정지).
- 웹 배선: `/find` 라우트(로컬 우선→없으면 라이브, 법원 드롭다운) + `templates/find.html`(7단계:
  prompt/unparsed/need_year/need_court/error/local_multi/live) + nav "사건검색" + 홈 검색창에
  사건번호 넣으면 `/find`로 자동 라우팅(`looks_like_case_no` 엄격판별, 단지명 검색 오탈취 방지).
  `_find_by_case` 정규화(완전일치 우선→실패시 폴백)로 `/property/2025-101763` 직접링크 동작.
- **경계 명시(과장 금지)**: 라이브 단건은 상세응답에 주소·면적 없어 차익채점 불가 — 최저입찰가·
  감정가·유찰수·청구금액·권리요지·기일만 공식제공. 차익은 야간수집 걸린 물건(로컬 히트)만.
- 검증: 전체 **610 passed, 1 skipped**(casesearch 32 신규). Flask 테스트클라이언트 스모크 —
  홈리다이렉트·친구포맷 로컬히트→상세리다이렉트·prompt/unparsed/need_court·`/property/2025-101763`
  직접 전부 확인. 라이브 실서버 호출은 밴회피상 미실측(목 테스트로 계약만 고정).
- 커밋/배포: 사용자 확인 후 일괄(push=deploy_prod.sh 규칙).

## 2026-07-20 16:55 KST — ⚡ 크롤 속도 상향(밴 여유 실측 근거): 네이버 지터↓ + 국토부 워커↑
- 근거: R2 Phase A 실측 콜 4,899회 후 **진짜 429 0건·차단 0건·fetch죽음 0건** — 네이버가 전혀
  밀어내지 않아 지터 여유 확실. 사용자 지시 "봇 안 걸리는 선에서 최대한 빠르게".
- 네이버 지터 기본값 하향(deploy/crawl_naver.py 2곳): AUCTION_NAVER_MIN 1.5→**1.0**,
  MAX 3.0→**2.2**(평균 2.25→1.6s, ~29% 빠름). 병렬은 여전히 금지(순차 유지), 429 지수백오프·
  세션갱신·연속차단 중단 안전망 그대로라 최악에도 부분저장 후 안전 정지.
- 국토부 병렬 워커(pipeline.py) 8→**12**(코드 주석대로 anti-bot 없어 병렬 안전) — R2-4 재채점
  국토부 수집 가속. 대부분 닫힌달 캐시라 신규분만 실호출.
- 적용범위: **실행 중 Phase A는 미영향**(이미 임포트). 다음 프로세스인 **Phase B(백필)·R2-4
  재채점부터 자동 적용**(fresh import). 일일 스케줄러도 이 기본값 사용.
- py_compile 통과. 커밋은 R2 완료·표본검수 후 일괄.

## 2026-07-20 11:35 KST — 🛡️ 오늘 작업분 27에이전트 적대검증 → 확정 결함 일괄 수정
- 방식: 4렌즈(데이터정확·침묵실패·보안·계약정합) 발견 → 발견별 반박 검증(총 27에이전트).
- **CRITICAL(수정)**: `-MaxPages 80`이 요청예산 불변식(17×25=425≤500) 파괴 — daily_cap 500을
  중간에 치면 부분수집이 **전량교체로 들어가 미수집 시도 물건·권리를 로컬·클라우드에서 삭제**.
  수정 3중: ①`pipeline.load_courtauction_nationwide`가 예산을 `시도수×(페이지+2)+100`으로 산출해
  client(daily_cap)에 주입 ②차단 시 `NATIONWIDE_PARTIAL` 플래그 → run.py가 전량교체를 **병합
  (upsert)으로 강등**(고아정리·클라우드 전량교체 스킵) ③부분수집 시 캐시 스냅샷 덮어쓰기 스킵.
- **HIGH(수정)**: 브리지 그룹키에 동(洞) 누락 — 같은 시군구 동명이단지 병합 주입(재현 est −46%).
  그룹키 (kind,이름,**dong**,면적)으로 분리 — 타단지는 경쟁자로 남아 WINNER_MARGIN 게이트 실효.
- **MEDIUM(수정)**: ①브리지 취소 재주입(닫힌달 영구캐시) — 보충을 **열린 2개월로 한정**(cutoff
  pool_max-1) ②렌더 최악 14s(스칼라 타임아웃=connect+read 각각) — (connect,read) 튜플로 ~8.5s
  ③VWorld 200+ERROR 침묵 — 경고 로그+실패 시 캐시 안 함(일시 장애 영구화 방지) ④bldg 무조건
  호출 — 건물형 유형만 조회 게이트.
- **LOW(수정)**: 전세가율 0→'' 덮어쓰기(0=무데이터 정의 존중, ''→None 보존) · 층0 비대칭 중복
  주입 방어 · 예외 로그 키 레닥션 · .gitignore에 *.log(키 노출 로그 우발 스테이징 방지).
- 기각(반박 성공): 브리지 provenance 계약(12개월창 산술적 불가), 툴팁 XSS(sub는 dead property),
  parse_jibun 오파싱(도달 불가 — PNU가 항상 우선), COMPS_CAP·refresh-daily는 안전 확인.
- 잔여(기록만): naver_complexes INSERT시 결측→DEFAULT 0(UPDATE부터 구분됨, 스키마 변경 필요라 보류),
  VWorld 키 HTML 노출(타일 키의 구조적 특성 — **VWorld 콘솔에서 도메인 제한 설정 권장**, 사용자 몫),
  타일 1~2장 산발 실패 시 회색 타일(3연속 임계 미달, 영향 미미).
- 테스트: 동명이단지·층0 중복 2건 추가 — **전체 578 passed, 1 skipped**.

## 2026-07-20 11:45 KST — 🗺️ 상세 지도 VWorld 한글타일 + 건축물대장 패널 (배포 전 사용자 확인 대기)
- 지도: OSM → **VWorld Base WMTS**(국토부 한글지도) 스왑 — `detail.html` 지도 JS.
  키는 서버(`web.py` vworld_key)→`data-vw-key`, 타일 3연속 오류 시 **OSM 자동 폴백**.
  로컬 실측: 한글 지명 + 학교·POI 라벨 렌더 확인(별도 POI 마커 없이 타일이 입지정보 제공).
  덤: 감사 미수정 #8(지도팝업 XSS) 해소 — bindPopup을 DOM 노드(textContent)로.
- 건축물대장: `src/building_info.py` 신설 — 주소→VWorld 지오코더(**level4LC가 19자리 PNU**로
  옴을 실측, 법정동10+산1+본번4+부번4 파싱)→건축HUB 표제부→요약(주동 기준, 위반은 필지 내 any).
  ⚠ 구 엔드포인트 `BldRgstService_v2`는 500 — **`BldRgstHubService`로 교체**(키 활성 실측).
  `web.py` 상세 라우트 배선 + `detail.html` #col-nums에 패널(준공연월·연식·주용도·규모·위반).
  실패·키부재·산지·도로명-only 주소는 카드 미표시(페이지 정상). 프로세스 캐시(실패 포함).
  실측: 실주소 6건 중 4건 성공(도로명-only 1·data.go.kr 타임아웃 1), 보성청록타운 1998.12·28년차.
- 타임아웃 보수화: VWorld 2.5s + 표제부 4.5s(Vercel 함수 10s 제한 안).
- 테스트: tests/test_building_info.py 7건(지번 파싱·요약 규칙). 비포/애프터:
  `경매-비포애프터\상세_VWorld지도_건축물대장_보성청록타운.png` — **배포는 사용자 확인 후**.
- 미완(배포 시 필수): Vercel env에 VWORLD_API_KEY·VWORLD_DOMAIN·MOLIT_API_KEY 추가해야 프로덕션 작동.
- (거래량 히스토그램은 사용자 결정으로 **취소** — 점 밀도=거래량, 호버 카드가 월 건수 표시 중.)

## 2026-07-20 11:10 KST — 🌉 감사 H5: 국토부 병렬 하이브리드 1단계(지문 브리지) 구현
- 문제(H5): 네이버 실거래 크롤은 안티밴으로 절대 순차 → 쌍 수에 시간 선형 비례(확장 상한).
- 설계: **지문(fingerprint) 브리지** `src/molit_bridge.py` — 네이버 확정쌍의 과거 이력
  (월·가격·층 정확일치)을 지문으로 국토부 aptNm 그룹과의 대응을 확정(이름 퍼지매칭 완전 우회),
  병렬로 이미 수집하는 국토부 풀에서 "네이버가 아직 못 본 최근 거래"만 메모리 보충(채점 1회용,
  naver_store 무기록). 신선도 의존을 국토부(병렬)로 옮기는 H5 취지의 안전한 1단계.
- 보수 게이트: 정확일치 ≥3건 + 창내 커버리지 ≥60% + 압도적 유일승자(2위×2 이상) + 해제 제외 +
  보충은 네이버 최신월 이후·최근 4개월·물건당 20건 상한. 미달=보충 0건(종전 동작 동일).
  `AUCTION_MOLIT_BRIDGE=0` 완전 비활성.
- 배선: `pipeline.run` — real_trades_lookup 결과에 topup 합류 후 estimate_from_complex_trades.
  scope/est_source 불변(게이트·미러 정합 유지). 로그: "국토부 브리지 보충: N물건 +M건".
- 테스트: tests/test_molit_bridge.py 8건(주입·중복방지·지문미달·커버리지·모호성·해제/타지역 제외·
  env·파이프라인 통합). **전체 스위트 569 passed, 1 skipped**(종전 559+신규 10).
- 다음: 오늘 10억 2회전 크롤에 투입 → 표본검수에서 브리지 보충 로그 확인.

## 2026-07-20 10:55 KST — 🔧 감사 잔여 2건 수정: MEDIUM2 0값 high-water-mark + comps 캡 60→240
- 배경: 내일(7/21) 05:30 노트북 OFF 예정 → 오늘 수동 10억 크롤 전에 데이터 품질 수정 선행.
- (MEDIUM2) `src/naver_store.py upsert_complex`: 보존 판정을 값 기반(`v not in (0,0.0,"")`)에서
  **소스 키 존재 기반**으로 분리 — 동적 컬럼(_DYNAMIC_COLS: deal/lease/rent_count·min/max_price·
  전세가율)은 페치가 값을 전달했으면 0/''도 갱신(_i_opt 신설, 전량매도=매물0 반영), 결측(None)만
  보존. 정적 메타(세대수·준공일)는 종전 H1 값-기반 보존 유지. fetched_at도 전값 0일 때 갱신됨.
- (comps 캡) `src/matcher.py COMPS_CAP` 60→**240** — 장기 실거래(19년치) 잘림 58쌍 해소.
  페이로드: 행당 최대 +3.8KB(비압축)·gzip 후 상세 +1KB 미만, /api/listings 최대 ~4배(허용 판단).
  캡 소비처 2곳(_pack_comps·estimate_from_complex_trades) 모두 상수 공유라 상수만 변경.
- 테스트: test_upsert_complex_zero_counts_update + test_upsert_complex_missing_keys_preserve_counts
  신설, 기존 H1 보존 테스트 그린 유지. pytest naver_realtrades+matcher **37 PASS**.
- 다음: 국토부 병렬 하이브리드(H5) → 오늘 2회전 크롤(신규 10억 물건 당일 매칭).

## 2026-07-20 01:34 KST — 🕰️ 장기 실거래 본 재채점 실행·프로덕션 반영(코드 변경 없음)
- 사용자: "타 세션 장기 수집 끝났으니 진행" → 실측: 백필 상태판 **완료**(184쌍·실거래 9,576행·
  실패 0, 01:23)·크롤 프로세스 종료. 타 세션 코드는 아직 미커밋(워킹트리)이므로 코드는 안 건드리고
  **실행만**: `refresh-daily.ps1 -FromCache -Live -SkipNaver`(물건=어제 캐시 재크롤X, 국토부
  라이브 24개월, 네이버 실거래는 파이프라인이 naver_store에서 주입, dangerouslyDisableSandbox로
  국토부 도달).
- 결과(01:26~01:33, exit 0): 7,800건 채점 → **☁ Supabase 미러링 7,800건(전량교체)**.
- 검증: ①진천태왕 est **4.69→3.30억**(scope same_dong 오염→same_complex_same_area, conf 0.75 —
  타 세션 T11 리허설 기대값과 일치, "이상한 시세밴드" 교정 확인) ②프로덕션 band_now
  2.68~4.69→**3.20~3.30억**, trades 2+hist 58 서빙 ③장기 분포: pre-2020 comps 보유 508물건·
  pre-2023 806물건 ④에덴타운(2025타경32264) 프로덕션 '전체' 모드 = **2008~2026 19년** 산점+
  스텝밴드 실화면 확인(`경매-비포애프터\차트_장기실거래_에덴타운_전체.png`).
- 코드 변경 0 → 배포 불필요(데이터만 갱신). 타 세션 코드 커밋·푸시는 해당 세션 몫(T12~ 잔여 미상).

## 2026-07-20 01:06 KST — 🐛 프로덕션 차트 실거래 점 0개 버그(store_rest select 누락) 수정
- 사용자: "역사적 거래 점이 왜 없어(네이버 캡처처럼 있어야지)" → 조사 결과 **렌더링·데이터 둘 다
  아닌 서빙 버그**: 로컬(auction.db)은 trades 2+hist 58(2008~2024, 60건) 정상인데
  **프로덕션(Supabase REST)만 trades 0·hist 0**.
- 원인: Supabase `auction_scored_listings.market_comps`엔 60건이 **있음**(SQL 실측: with_comps
  1,270행·max 60) — 그런데 `store_rest.load_scored`의 REST `select=",".join(_COLS)`에
  **market_comps(_COLS 밖 별도 jsonb)가 빠져** 응답에 없고 `row.get(...) or []`가 항상 [].
  저장(_payload)은 넣는데 로드만 빼는 비대칭.
- 수정: `select=[*_COLS,"market_comps"]` + 회귀 테스트
  `test_load_scored_selects_and_restores_market_comps`. pytest store_rest+web 39 PASS.
- 효과: 배포 시 프로덕션 차트에 실거래 점 복원 — 이 물건은 **2008~2024 역사 점 60개**가 '전체'
  모드에 바로 나타남(장기 데이터가 이미 있던 물건). 네이버식 장기 점은 타 세션 수집 완료 시 전 물건 확대.

## 2026-07-19 16:24 KST — 🚀 정돈 3종 push+배포 완료(d3dd747 라이브)
- 사용자 승인("나쁘지 않네 이걸로 푸쉬 및 배포") → 콤팩트+계산서 통합+좌숫자/우그래프 재배치
  일괄 커밋(`ff150b5..d3dd747`, 이 세션 3파일만: detail.html·test_web.py·versions.md) →
  deploy_prod.sh로 Vercel 프로덕션 배포(##0.5 준수). 라이브 검증: col-nums/col-vis/dlow/
  dhead-acts/계산서/1440px 전부 확인, /health=db. 타 세션 파일(matcher 등 11종) 워킹트리 보존.

## 2026-07-19 16:21 KST — 🗂️ 좌=숫자·우=그래프 재배치 + 스티키바 제거 — 배포결정 대기
- 사용자 피드백: ①스티키바(관심등록/법원경매)가 본문 가림 ②숫자표(명세서·계산서·KB·기일)가
  좌우중하에 산개해 눈에 안 들어옴 — 숫자는 왼쪽 한 번에, 오른쪽은 그래프, 아래 적정성+감정요항,
  섹션 높이 맞출 것.
- **재배치(파이썬 블록 재조립)**: 3컬럼 → **2컬럼+하단 존**.
  좌 `#col-nums` "① 숫자·가격" = 계산서→KB→명세서 요지→기일 내역(숫자 전부 한 줄기) /
  우 `#col-vis` "② 그래프·현장" = 사진(280px)→실거래 차트→지도(300px) /
  하 `.dlow` = 매수 적정성 | 감정 요항 2단(2fr:3fr). 컬럼 종점 실측 ~1390 vs ~1350 균형.
- **스티키바 데스크톱 제거** → 관심등록·법원경매 원문 버튼을 **헤더 우측**(.dhead-acts)으로
  이동(모바일은 기존 하단바 유지). 구 dmedia/pane-auction/value/profit/dnotes-zone 래퍼 소멸.
- pytest 47 PASS. 합성 `경매-비포애프터\좌숫자_우그래프_비포애프터_20260719.png`.
- 배포 대기 누적: 콤팩트(15:09)+계산서 통합(15:20)+본 재배치 — 사용자 확인 후 push+deploy_prod.sh.

## 2026-07-19 15:20 KST — 🧾 가격·수익 계산서 통합(가격표 한 장으로) — 배포결정 대기
- 사용자 피드백: 가격표가 여기저기(KPI·차익산출표·세금표·차트·KB) 흩어져 정신없음 —
  가격 측면은 한쪽으로 몰아 한 표에.
- **③컬럼 개편**: 「예상 차익 산출」+「세금·비용」두 카드 → **「가격·수익 계산서」한 장**
  (영수증 워터폴): §시세(검증 하한가·기준 시세·호가) → §취득 비용(최저입찰가 + 취득세 =
  **취득원가** 합계행) → §차익(**보수 차익** grand행 17px 강조 + 인수 시 최악~최선 범위) →
  §산출 근거(매칭·신뢰·표본 1줄 통합, 비교군, 경고배너, 표면차익·양도세 노트 통합).
- **중복 제거**: 감정가·유찰·저감은 최저입찰가 행의 힌트로 흡수(별도 행 삭제), 세금 3분해는
  취득세 행 힌트로(별도 카드 삭제), 표본은 매칭 행에 병합. 신설 CSS `.dsec-h`(섹션 소제목)·
  `.drow.total`(회색 합계)·`.drow.grand`(차익 강조).
- 테스트 갱신: test_property_detail_found "차익 근거"→"가격·수익 계산서"(의도된 개명). 30 PASS.
- 합성: `경매-비포애프터\가격표통합_비포애프터_20260719.png`. 콤팩트 패스(15:09)와 함께
  배포 대기 — 사용자 확인 후 push+deploy_prod.sh.

## 2026-07-19 15:09 KST — 📐 대시보드 콤팩트 패스(밀도 상향) — 배포결정 대기
- 사용자 피드백(라이브 확인 후): 너무 넓어 한눈에 안 들어옴·정보 밀도 낮아 정신없음.
  폭을 좁히거나 사진/지도/표 크기를 줄여 콤팩트하게.
- **조임**: ①main.wrap 1800→**1440px**, 컬럼 gap 36→22px ②미디어 행(사진|지도) 430→**290px**,
  gap 16px ③표 압축 — drow 패딩 12→7px·k 14→12.5·v 16→13.5px, dnote/dbanner/gatebar 축소
  ④KPI 타일 패딩·값 21→18px ⑤섹션헤더 h2 16.5→14.5px·넘버칩 30→24px ⑥제목 h1 축소
  ⑦ptc-svg min-width 560→0(좁은 컬럼 가로스크롤 방지) ⑧지도 버튼바·KB밴드 축소.
- 결과: 풀페이지 높이 2533→**2060px**(-19%), 3컬럼 종점 균형 개선. pytest test_web 30 PASS.
- 합성: `경매-비포애프터\대시보드_콤팩트_비포애프터_20260719.png`. 다음: 사용자 확인 후
  push+deploy_prod.sh(##0.5).

## 2026-07-19 15:05 KST — 🔁 push=배포 원칙 확립(deploy_prod.sh + CLAUDE.md ##0.5)
- 사용자 질책: "너가 배포하는 방식으로 바꾼 지가 언젠데 뭔 수동배포" — push만 하고 배포 빠뜨리는
  일 재발 방지.
- `vercel git connect` 시도 → **실패**(Vercel GitHub App이 프라이빗 레포
  hyunwoojang1/realestate_auctions 접근권한 없음 — 자동연동은 사용자가 GitHub App에 레포 허용해야
  가능, 허용 시 push=자동배포로 업그레이드됨).
- 대신 **`scripts/deploy_prod.sh`**(원커맨드: HEAD를 깨끗한 worktree로 체크아웃→.vercel 복사→
  `vercel deploy --prod`→/health 자동검증→worktree 정리) + **CLAUDE.md ##0.5 절대규칙**
  "main push했으면 그 턴에 deploy_prod.sh로 배포까지" — 이 레포의 모든 세션에 강제.
- 프로덕션 URL 고정 표기: auction-arbitrage-hyunwoo-jang-s-projects.vercel.app
  (무접미사 auction-arbitrage.vercel.app은 남의 앱 — CLAUDE.md에도 경고 명기).

## 2026-07-19 14:58 KST — 🌐 Vercel 프로덕션 배포 완료(48c03df 라이브)
- 사용자 지적: git push만으론 실서비스 미반영 — **GitHub 자동배포 없음, Vercel CLI 수동 배포 방식**
  (마지막 배포 2일 전 확인). 오늘 개편 3종을 프로덕션 배포.
- **오염 방지**: 워킹트리에 타 세션 미완성 변경 10파일(matcher·pipeline 등)이 있어 폴더째 배포
  금지 → `git worktree add`로 커밋 48c03df 깨끗한 체크아웃 + `.vercel` 링크 복사 →
  `vercel deploy --prod` (배포 후 worktree 제거). **앞으로 배포도 이 절차 권장.**
- 라이브 검증(auction-arbitrage-hyunwoo-jang-s-projects.vercel.app): /health=db,
  상세페이지 dkpi 6타일·dmap 1·네이버 차트색·5y버튼 확인, 구 탭바 0·엔티티 깨짐 0.
- ⚠️ **함정 발견**: `auction-arbitrage.vercel.app`(무접미사)은 **남의 앱**(영문 Kijiji 플리핑
  트래커) — 이름만 같음. 우리 프로덕션 도메인은 반드시
  **`auction-arbitrage-hyunwoo-jang-s-projects.vercel.app`** 사용.

## 2026-07-19 14:50 KST — 🚀 상세페이지 개편 3종 배포(push 48c03df)
- 사용자 승인("좋아 보이네, 이렇게 쭉 바꿔줘") → 누적 3건 일괄 커밋·푸시:
  **①지도 인라인**(카카오맵 외부이동 제거) **②대시보드 v2**(탭 제거·존 기반: KPI밴드→게이트→
  사진|지도→3컬럼→감정요항 2단→푸터) **③차트 v3**(네이버 시세 문법: 상한/하한 스텝라인·호버
  흰 카드·기간 1/3/5/전체) + 비포/애프터 캡처 도구(scripts/shot_pane·compose_ba).
- 스테이징은 이 세션 5개 파일만(detail.html·web.py·scripts 2종·versions.md) — 타 세션의
  네이버 대개편 작업 파일(matcher·models·pipeline 등 10종)은 워킹트리에 그대로 보존.
- 커밋 전 origin 동기 확인(ahead 0), pytest 78 PASS. `1041c63..48c03df`.
- 후속: 시세 밴드 데이터 교정(타 세션 대개편)·장기(3/5년+) 실거래 데이터 수급 시 차트 자동 수혜.

## 2026-07-19 14:44 KST — 📈 차트 v3: 네이버 시세 그래프 문법 이식 — 피드백 대기
- 사용자 피드백(v2 필 버전에): 기간은 1/3/5/전체로, 월평균 추세선 보류(개별점 잇는 게 이상),
  필 2개도 안 이쁨. **레퍼런스 스크린샷 제공**: `경매-비포애프터\네이버 부동산 밴드.png`.
- 레퍼런스 분석: 밴드=면 아님·**최고(빨강)/최저(파랑) 계단 스텝라인 2개**, 우측 상시 라벨 0,
  값은 **호버 흰 카드**(시세 최고/최저/실거래가), 실거래=보라 점, 라인끝 마커, 연도 줄무늬.
- **v3 이식**: ①기간 1년/3년/5년/전체(기본 3년, YEARS 맵 — 장기 데이터 세션과 호환)
  ②월평균 추세선 삭제 ③필 라벨 삭제 → **상한 UP=#e8574f/하한 DN=#3f74e0 스텝라인**+라인끝
  마커(빨강 고리·파랑 점) ④실거래 점 파랑→**보라 #8b46c9**, 유찰 궤적 빨강→**검정**(빨강을
  상한선에 양보) ⑤**호버 흰 카드 툴팁**: 'YYYY년 M월/시세 최고/최저/실거래가(월평균·n건)/유찰
  최저가' — envAt·stepAt·monthTrades 헬퍼 ⑥짝수 연도 세로 줄무늬 ⑦낙폭 % 라벨 삭제(KPI −30%
  뱃지가 대신) ⑧R 672→712(라벨영역 회수). 구 RLABS/pill 코드 전부 제거.
- 참고: 파랑 하한선 후반 flat 구간은 밴드 **데이터** 이슈(사용자 인지 "밴드는 고칠 것" —
  네이버 실거래 대개편 세션 몫). 차트는 데이터 오면 그대로 반영.
- **증거**: 3년 기본·호버 카드·전체 모드 실서버 캡처, 합성
  `시세차트_네이버문법_비포애프터_20260719.png`(+`_chart_naver_hover.png`). pytest test_web 30 PASS.
- 다음: 사용자 피드백 대기. 미배포 누적: 지도 인라인·대시보드 v2·차트 v3.

## 2026-07-19 14:35 KST — 📈 시세 차트 정돈(네이버 실거래 그래프 문법) — 피드백 대기
- 사용자: 차트 우측 정보 과다·전체적으로 정신없음. 네이버 부동산 실거래 그래프 UIUX 적극 반영
  (밴드 상/하한 디자인, 기간 선택 버튼 — 타 세션이 2년+ 장기 데이터 수집 중이라 선행 대비).
- **정돈**: ①우측 라벨 6종 스택(시세상한/검증하한/감정가/차익괄호/취득원가/현재유찰가 — 서로
  뭉개짐) → **필 2개만**(하한=다크·유찰=레드, 충돌회피 유지) ②감정가·취득원가 라벨은 기준선 위
  **좌측 인라인**으로 이동 ③**차익 괄호 제거**(KPI 밴드가 이미 크게 표시 — 차트는 시세 흐름 전담)
  ④**월평균 추세선 신설**(체결+과거 월평균 연결 — 네이버 시그니처 문법) ⑤기간 버튼 2개→4개
  (**1년/2년/3년/전체**, YEARS 맵 — 장기 데이터 오면 그대로 수용) ⑥과거 실거래(hist)를 전체
  모드 전용→기간 창 안이면 항상 표시 ⑦유찰 저감: 굵은 틱+라벨 무더기→저채도 계단 스텝라인+
  낙폭%(세로선 좌측)+현재가만 강조 ⑧차트 폭 R 628→672(라벨영역 축소분 회수)
  ⑨범례 정돈+스와치 버그픽스(trade/hist 스와치 CSS 미정의·minbid 색 불일치는 기존 버그).
- **증거**: 2년/전체 모드 실서버 캡처 정상(필 2개·추세선·계단), 비교합성
  `경매-비포애프터\시세차트_정돈_비포애프터_20260719.png`. pytest test_web 30 PASS.
- 다음: 사용자 피드백 대기(보고 나서 배포 여부 결정). 미배포 누적: 지도 인라인·대시보드 v2·차트 정돈.

## 2026-07-19 14:20 KST — 🎨 대시보드 v2: '존' 기반 정돈(디자인 패스) — 배포결정 대기
- 사용자 피드백: v1은 "세로를 가로로 눕힌 것 같고 정리가 안 돼 있다" — 미적·디자인적으로
  정돈돼 눈이 편해야 '한눈에'가 된다.
- **v2 재설계(존 기반)**: ①헤더(제목·메타) → ②**KPI 스탯 밴드**(감정가/최저가−30%뱃지/보증금/
  시세하한/**예상차익**(초록 강조·음수 시 빨강)/매각기일 **D-n** — 핵심 숫자 6타일 한 줄) →
  ③게이트 배너 → ④**미디어 행: 사진|지도 등높이 나란히**(실물+위치 한 쌍, 430px) →
  ⑤목적별 3컬럼 재편성: **①권리 분석**(명세서→적정성→기일) / **②시세 검증**(차트→KB) /
  **③수익 계산**(차익 산출→세금) — 상단 라벨(번호칩+진행 힌트)로 구획 명시 →
  ⑥**감정 요항 전폭 2단 존**(참고자료 강등 — v1에서 ①컬럼을 지배하던 텍스트벽 해소) →
  ⑦공시자료+근사위치+면책 통합 푸터.
- 구현: 구 `.dsum` 2카드 → `.dkpi` 6타일(web.py에 days_until 전달 추가), 구 pane-site 제거
  (지도는 dmedia로·공시자료는 푸터로), pane-value를 시세(b)/수익(c=pane-profit)으로 분리,
  `.dfootzone` 래퍼(grid area 중복 방지). 모바일: KPI 2열·세로 스택 폴백 유지.
- **증거**: 1600px 실서버 캡처 — dmap 1개(중복 없음)·pane-site 0·KPI 6타일·D-2 표기·컬럼라벨
  3개 visible 확인. v1↔v2 합성 `경매-비포애프터\상세페이지_대시보드정돈_v1v2_20260719.png`.
  pytest test_web(+watchlist) 47 PASS. 다음: 사용자 배포 결정 대기.

## 2026-07-20 — 🚀 프로덕션 배포 + 스케줄러 활성화 + 현금상한 10억
- **배포**: 커밋 158cdc0 → deploy_prod.sh → Vercel 프로덕션 라이브(health ok). 진천 평형·KB밴드·근거표본·
  밴드과대 보류사유 프로덕션 실측 확인. 야간 개편 전체가 사용자 서빙 반영됨.
- **스케줄러 활성화**: AuctionArbitrage-DailyRefresh = Ready(활성), 매일 05:30, 네이버 증분 통합됨.
  다음 실행 2026-07-21 05:30.
- **현금상한 5억→10억**(사용자 결정 2026-07-20): 스케줄task -Cash 1000000000 + refresh-daily·
  install-scheduler 기본값 갱신. **다음 새로고침(7/21 05:30)부터 최저가 ≤10억 물건 수집** →
  수도권 물건 대폭 확대 예상. (기존 5억 데이터는 유지, 증분으로 확장분 추가)

## 2026-07-20 (야간 루프) 최종감사(19에이전트) + 진짜오류 선별수정
- 감사 판정 **🟢 초록** — 리뷰 14건을 라이브DB 재현 검증: **활성 결함(잘못된 시세 서빙) 0건**.
  13 확정(MEDIUM 2·LOW 11, 전부 잠재/표기), 1 오탐 기각(미러 차단 시나리오=NULL+유효complex_no 행 0건 반증).
- **수정(진짜 중요 4건)**: ①[MEDIUM1 data_gates] scls 게이트 면제를 '네이버 매칭 존재'→'실제 주입(scope=
  same_complex_same_area)'로 축소 — 국토부 폴백 est가 유형코드 검증 우회하던 틈 차단. ②[detail.html #9]
  band_too_wide/appraisal_mismatch/share_sale에 정확한 보류 사유 표시(종전 "표본 부족" 오표기 교정).
  ③[detail.html #10] KB/호가/전세 폴백 물건에 "같은단지 추천 인정" 대신 "KB 시세 기반(검증 아님)" 라벨.
  ④[run.py] 주입쿼리 match_conf NULL 포함 제거 → 주석·data_gates와 정합(코드-주석 모순 해소).
- **하드닝**: real_trades_for_case(dead)에도 신뢰게이트 추가(향후 배선 대비).
- **미수정(기록만, 현재 영향 0)**: MEDIUM2(naver_complexes 0값 high-water-mark, 채점무관 메타)·#8(지도팝업
  XSS, 물건명=법원공고 출처라 저위험)·matcher basis/area(의도된 설계). 상세=tasks/wz85459qu.output.
- 테스트 559 passed, 게이트 9종 PASS 유지. **UI 변경은 배포 안 함(사용자 배포결정 대기, before/after 캡처됨)**.

## 2026-07-20 (야간 루프) TASK 2·3 — ✅ UI 개선 + 마무리 정리
- **TASK 2 UI**(머지된 2열 대시보드 위): ①scope_names에 band_too_wide 한국어 추가(영어토큰 노출 제거)
  ②차트헤더에 평형 명시("전용 105.2㎡·31.8평") ③"표본"→"근거 표본" 라벨(실패 테스트 2건도 해소)
  ④목록뷰 시세추정불가 배지에 사유 툴팁(데이터부족/밴드과대/감정가괴리 구분). 진천 상세 스샷 검증:
  KB밴드 3.45~3.70(네이버 일치)·검증하한 3.20·기준 3.30 삼자정합. 스샷=장현우\경매-비포애프터\after_*.png.
- **TASK 3a**: Supabase auction_naver_prices에 lease_low/high 컬럼 추가(Management API, 201) → 미러 400 해소,
  네이버 2,351건 미러 성공.
- **TASK 3b**: 임계값 재보정 점검 — 같은단지 밴드폭 p99=1.44(폴백가드 1.6은 충분한 여유, 진천 과적합 아님)·
  신뢰계수 분포 정상 → **변경 불필요**(감사 M4 기각).
- **TASK 3c**: 매칭정확도 자동검수 — KB밴드 교차검증 556건 **이탈 0건**(주입 시세=독립 KB평가 일치). 단
  중신뢰에서 오매칭 발견(노빌리안1↔2·영등3차↔4차) → **naver_match에 단지식별자(N단지/N차) 가드 추가**
  (미래 오매칭 원천차단). 기존 위반 3건(실 est는 영등3차 1건뿐) 삭제→내일 재매칭. 테스트 559 passed.
- **naver_pair_status 테이블 신설**: 처리한 (단지,평형) 쌍을 last_checked·trade_count로 기록. 실거래
  0건 쌍도 '확인함'으로 남겨 **매 실행 재크롤 제거(M3)** — 잔여 115→2쌍 실측. 증분 기준도 됨.
- **crawl_naver `--incremental --stale-days N`**: naver_pair_status 기준 N일↑ 오래된 쌍+신규만 재수집.
  라이브 검증: 증분 대상 2쌍만 선택·184행 갱신(콜 36).
- **refresh-daily.ps1 통합**: 메인 채점 전 네이버 Phase A(신규 매칭)+Phase B(증분 실거래) 추가.
  -SkipNaver·-NaverStaleDays 파라미터. 네이버 실패는 채점 안 막음(보조 시세). 신규 물건 1일 지연 허용.
  → 기존 스케줄러(Disabled 등록됨)가 이 스크립트를 부르므로 재등록 불요.
- reprocess_real_trades에 pair_status 시딩 추가. 테스트 556 passed(신규 pair_status 경로 무회귀).
- **크롤 완료**: 983쌍·실거래 74,404행(2006~2026). 잔여 115쌍=네이버 실거래 없는 빈 평형(M3, 무해).
  중간 절전으로 2회 중단됐으나 이어받기로 무손실 재개.
- **C1 재처리**: reprocess_real_trades → 취소거래 **2,378건 올바르게 제외**(수정 전 0=전부 오염이었음).
- **재채점**(--live-months 24): 네이버 확정 실거래 **1,360물건 주입**, 권리배선 4,526/7,800.
- **품질 게이트 오탐 1건 수정**: scls 게이트가 덕원아파트(코드 10108, 접두'101') 오탐 → 전체 적재 차단.
  근본수정: 네이버 complexNo 고/중신뢰 매칭 물건은 scls 게이트 제외(건물 ID로 검증된 comps라 유형코드
  무관). 게이트 9종 전건 PASS 후 미러.
- **Supabase 미러**: auction_scored_listings **7,800건 적재 완료** → 프로덕션 반영. 진천 실측 3.30억·
  same_complex_same_area 프로덕션 확인. 아파트+오피 시세성공 **55%(1,270/2,307)**, same_complex_same_area
  **1,267건**(개편 전 748 → +519), band_too_wide 가드 118건(오염 정직 무효화).
- ⚠️미완: auction_naver_prices 미러 400(신규 lease_low/high 컬럼이 Supabase 테이블에 없음, 비치명—
  KB폴백은 기존 컬럼으로 동작). DB백업=auction.db.bak-20260719-1534.

## 2026-07-19 09:10 KST — 🔍 중간감사(6에이전트) + CRITICAL 즉시수정
- 감사 판정 🟡노랑(조건부정상). 검증: pytest 데이터관련 전건 통과, 진천 재실행 정상. 발견 21건.
- **C1 CRITICAL 확정·수정**: 취소거래 판정값이 실제 `'O'`인데 코드가 `'Y'`를 찾아 **캐시 650건
  취소거래 전부 deleted=0으로 시세 혼입**(그중 345건은 정상행 쌍둥이까지 공존). 이 프로젝트의
  존재이유(취소거래 배제)를 정면 위반. 수정: ①`_is_cancelled` 값 'O' ②PK에서 deleted 제거
  ③**거래 단위 취소 집계**(쌍둥이 억제) ④scripts/reprocess_real_trades.py(캐시 재처리 — 재크롤 0).
- **C2 수정**: 매칭 신뢰도 게이트 — 저신뢰(match_conf='저신뢰') 매칭은 실거래 주입 제외
  (naver_match 휴리스틱 오답이 '확정 같은단지'로 둔갑 방지). run.py _load_naver_real_map.
- **H1 수정**: upsert_complex 빈값 덮어쓰기 금지(재파싱이 기존 min/max·전세가율 파괴 방지) +
  전세가율 0.0 falsy 유실 버그(_lease_rate_str). 단 소스 전건 0.0이라 현재 실유실은 없음(감사 확인).
- 신규 테스트 6(취소 쌍둥이 억제·전세가율 정규화·보존 upsert). 전체 555 passed.
- 미수정(의도): pytest 2건 실패 = 타 세션 detail.html 카피 회귀(L3, 데이터무관·수정금지영역).
- 재처리·재보정은 크롤 완료 후: reprocess_real_trades → 재채점(--live-months 24) → Supabase.
- 상세 스코어카드: tasks/whcsruomb.output. HIGH 4·MEDIUM 7·LOW 4는 크롤 후 순차.

## 2026-07-19 08:05 KST — 🛡 크롤 크래시 2중 방어 + 재시작 (246쌍 지점)
- 실측 크래시: Playwright `Page.evaluate: Failed to fetch`(브라우저 컨텍스트 네트워크 순단)가
  방어망 없이 전파 → 크롤 전체 사망(exit 1, 246쌍 저장은 무손실).
- 방어 2층: ①NaverClient.fetch — evaluate 예외를 일시 오류로 보고 세션 재생성 후 재시도(3회 소진 시
  NaverBlocked 정상 중단) ②backfill_real — 쌍 단위 try/except(NaverBlocked만 전체 중단, 그 외 skip+
  카운트·로그, 스킵분은 다음 이어받기가 재시도). [완료] 라인에 실패skip 수 표기.
- 이어받기 재시작(246쌍 자동 skip). ETA 실측 ~100쌍/h → 완료 예상 7/20 새벽 01:30~03:00.

## 2026-07-19 07:20 KST — ⚡ T10 속도튜닝 재시작 + 상태판 개통
- **초기 ETA 실측 45시간**(15분에 6쌍 — 대단지 실거래 80p+호가 5p로 쌍당 ~90콜) → NAVER_STOP으로
  graceful 중단(8쌍 저장) 후 튜닝 재시작: ①실거래 페이지 캡 80→25(env AUCTION_NAVER_REAL_PAGES,
  최근 5~10년 확보 — 창 계층화 최대 60개월·차트에 충분) ②백필에서 호가 수집 제거(실거래 확보 시
  호가 폴백 중요도 급락, 신선도는 Phase A·일일 증분 몫). 쌍당 ~7콜 → 예상 ~6시간.
- **상태판**: evidence/backfill_status.html — 5분 자동갱신(생성 루프+meta refresh), 진행바·ETA·
  스탯 6종·스케줄표·최근 로그. 사용자 전달 완료.
- **물건 수 팩트체크**(사용자 질문): 수집 상한은 10억 아닌 **현금 5억**(refresh-daily.ps1 기본,
  결정 #8). 실측 깔때기: 아파트+오피 2,307건(3억↓ 2,131) → est 865 → 권리확인 1,393 → 둘다 606건.
  10억 확장은 -Cash 파라미터 하나(수집 재크롤 필요).

## 2026-07-19 06:40 KST — ✅ T4~T9 완료 (네이버 실거래 개편 2/3) — 진천 실측 교정 성공
- **T4**: NaverClient.real_prices(addedRowCount 커서, 빈페이지 종료, 80p캡+잘림경고)·overview()·
  articles 페이지네이션(articles_all, isMoreData 종료). **호가 API 사망 버그 발견·수정**: 감사 7/15의
  priceMax=999억이 서버 검증 거부("유효하지 않은 priceMax", 200+error바디) → 만원 단위 999999로 교정 +
  error바디 감지 로그(침묵 0건 방지).
- **T5**: crawl_naver 개편 — Phase B(--backfill-real: 매칭된 (단지,평형)쌍 단위, 물건 매칭 불필요),
  우선순위 큐(est NULL 604→fallback 오염 169→기타 325 = 1,098쌍), naver_real_trades 존재로 이어받기,
  캐시에 overview/real 원본 저장(C1). Phase A(_process)도 개편: 캐시 불완전 시 상세 1회 재수집,
  overview·KB시계열·호가 항상 수집+전페이지, lease_low/high 저장(store.py 컬럼 추가).
- **T6 실측**: 9쌍 라이브 — **커서가 year=5 무시하고 전 기간(2006~2026) 수집 확인**(2,069행 적재).
  80p캡 잘림은 최신순이라 옛 꼬리만 누락(무해·로그 표시). 진천 105.22 평형=거래 희소(최근 12개월 0건) 확인.
- **T7**: matcher.estimate_from_complex_trades — scope 직부여(이름매칭 우회)·같은평형이라 가격 중앙값
  (ppm2 환산 불필요=면적 불일치 부풀림 원천 차단)·**창 계층화 12→24→60개월(신뢰 ×1.0/0.9/0.75)**.
  pipeline.run(real_trades_lookup)·run.py(_load_naver_real_map) 배선. 감정가 괴리 시 이름매칭 폴백 금지.
- **T8**: ①폴백 밴드폭 가드(same_dong_fallback에서 est/band_low>1.6 → SCOPE_BAND_TOO_WIDE 무효화 —
  진천 실사고 1.748배가 1.75 임계는 통과하므로 1.6로 설정) ②KB 괴리 플래그(market_view에서 est가
  KB밴드 ±15% 이탈 시 naver.kb_divergence="over"/"under" — 데이터만, 강등은 정책 미정).
- **T9 실측(진천 재채점)**: est 4.69억→**3.30억**, band 3.20~3.30, scope same_complex_same_area,
  기대차익 2.19억→0.80억, 신뢰 0.75(60개월 확장 하향), 차트 comps 60점(2013~2024). KB 3.45~3.7억·
  감정가 3.53억과 삼자 정합. 테스트 전체 **555 passed**(신규 24) 무회귀.

## 2026-07-19 05:55 KST — ✅ P0+T1+T2+T3 완료 (네이버 실거래 개편 1/3)
- **P0 스모크(라이브 6콜)**: prices/real 실측 확정 — 페이지네이션(addedRowCount 커서) 동작, 행필드
  dealPrice(만원)/floor/deleteYn(취소행에만)/exclusiveArea, year>5 무효(5년 고정 관측), totalRowCount=201
  의미 불명(루프에서 확정). **진천태왕아너스1단지 평형 6개 라이브 확인 — areaNo 5 = 전용 105.22㎡(경매물건과
  일치!)**, 7/15 캐시는 1평형만 저장돼 무매칭 원인이었음. 원본=evidence/smoke_prices_real_24958.json.
- **T1**: src/naver_store.py 신규 — naver_real_trades·naver_complexes·naver_kb_history·naver_articles
  4테이블 + naver_prices ALTER(lease_low/high). 만원→원 통일, 해제행 deleted=1 보존, upsert 멱등.
- **T2**: Trade.dealing_gbn/rgst_date + is_direct 추가, molit_client 파싱(거래유형/등기일자).
- **T3**: scripts/reparse_naver_cache.py — 크롤 0번으로 7/15 캐시 37MB 재파싱 → 단지 1,373(세대수 전부)·
  KB시계열 2,528행(**전세가율 전부 보유** '82~85%'형)·호가 3,049행 적재.
- 테스트: 신규 10 + 전체 541 passed(1 skipped) 무회귀.

## 2026-07-19 05:20 KST — 📋 네이버 complexNo 실거래 대개편 착수 (GOAL_NAVER_REALTRADES.md)
- 배경: 진천태왕아너스1단지 실측(S0~S3)으로 same_dong_fallback 오염(est 4.69억 vs 실제 2.8억) 확인.
- 스케줄표 P0~T13 작성: prices/real 스모크 → naver_store 스키마 → 국토부 직거래·등기일 파싱 →
  캐시 재파싱 → 크롤러 개편(커서·dedup·우선순위큐·재시작) → 채점 주입(scope 직부여·창 계층화) →
  가드 2종 → 전량 크롤 → 재채점. 상세는 GOAL_NAVER_REALTRADES.md.
- ⚠️ 타 세션이 detail.html·web.py 수정 중(미커밋) → 이 작업에서 두 파일 수정 금지(머지 충돌 방지).
- 오프라인 확정: 진천태왕아너스1단지 complexNo=24958, 캐시 평형목록은 84.96㎡ 1개뿐(105.22 물건과 불일치).

## 2026-07-19 14:12 KST — 🖥️ 상세페이지 맥시멀 대시보드 개편(탭 제거·전폭 3컬럼) — 배포결정 대기
- 사용자 지시: "맥시멀리스트" — 양옆 버리는 여백이 너무 많고, 경매정보/시세수익/현장기타를
  **탭으로 각각 눌러 봐야 하는 게 제일 싫다**. 지도도 탭 눌러야 보이는 것 불만. 한 번에 다 보이게.
- **개편**: ①`.ddetail` 680px 중앙컬럼 → 데스크톱(≥1100px) CSS Grid 3컬럼 대시보드
  (`grid-template-areas`: hero|head 상단행 + a|b|c 본문행, main.wrap 1800px).
  ②탭바(`#dtabs`)·`hidden` 완전 제거 — 경매정보·시세수익·현장이 **동시 노출**.
  ③사진 히어로=좌측 고정컬럼(`position:sticky` — 스크롤해도 시야 유지), 제목+요약카드+게이트=우측 2컬럼 스팬.
  ④컬럼 상단 라벨(① 경매정보·권리 / ② 시세·수익 / ③ 현장·위치)로 구획 명시.
  ⑤지도 즉시 초기화(탭 대기 제거)+리사이즈 시 invalidateSize, 전용 컬럼이라 360px로 확대.
  ⑥모바일(<1100px)은 그리드 미적용 → 자연 세로 스택(탭 없이 쭉 스크롤).
- **증거**: 1600px 실서버 풀페이지 캡처 — BEFORE(중앙 460px·탭 뒤 2/3 숨김) vs AFTER(전폭 3컬럼
  차트·KB시세·차익산출·세금·지도 전부 동시 노출). 합성본
  `경매-비포애프터\상세페이지_맥시멀대시보드_비포애프터_20260719.png`. pytest test_web(+watchlist) 47 PASS.
- 다음: **사용자 배포 결정 대기**(비포/애프터 확인 후). 참고: 캡처의 하단바 중간 겹침은
  풀페이지 스크린샷의 sticky 아티팩트 — 실사용 시 뷰포트 하단 고정.

## 2026-07-19 14:00 KST — 📸 비포/애프터 리뷰 캡처 워크플로 도입(사용자 지시)
- 사용자 지시: 앞으로 UI 변경은 **푸시 전 비포/애프터를 한 파일로** 합성해 저장하고 경로를 알려줄 것.
  사용자가 그 이미지 하나로 배포 여부 판단.
- 추가: `scripts/shot_pane.py`(Playwright 단일요소 캡처, 480×900), `scripts/compose_ba.py`(Pillow로
  좌 BEFORE·우 AFTER 라벨 합성). BEFORE는 대상 파일만 `git stash`로 되돌려 렌더.
- 저장 폴더(repo 밖): `C:\Users\notebiz765\장현우\경매-비포애프터\`. 이번 지도 인라인화 건 합성본
  `현장탭_지도인라인_비포애프터_20260719.png` 생성. ⚠️함정 기록: 옛 서버가 포트 점유 시 새 서버
  바인딩 실패 → 비포/애프터가 동일하게 찍힘. 매 렌더 전 포트 리스너 0 확인·별도 포트 사용으로 해결.
- 기억: `feedback_auction_before_after_capture`. 다음: 사용자 배포 결정 대기.

## 2026-07-19 13:51 KST — 🗺️ 상세 현장탭: 카카오맵 외부이동 → 인라인 인터랙티브 지도
- 사용자 불만: 현장 위치를 보려면 카카오맵 링크를 눌러 **페이지를 떠나야** 하는 나쁜 UX
  (레퍼런스 경매사이트는 지도를 인라인으로 보여줌). 편안한 경험이 되게 개편.
- **방식**: 전역 지도(map.html)가 이미 쓰는 Leaflet+OSM 인프라 + 케이스별 좌표 캐시(coords.py,
  KATEC→WGS84)를 재사용해 상세 "현장·기타" 탭에 **인라인 지도**를 바로 렌더. 페이지 이탈 없이
  핀·팝업(단지명)·확대/이동 가능. 로드뷰·카카오맵·길찾기는 **보조 버튼**으로만 외부연결
  (로드뷰는 카카오 JS키 없이 임베드 불가 → 링크 유지가 최선).
- **구현**: ①`web.py` 상세 라우트에서 `coords.lookup(s.uid, s.case_no, court)`로 좌표 조인해
  `coord`(=[lat,lon]) 전달. ②`detail.html` head 블록에 Leaflet CSS/JS(SRI, **coord 있을 때만
  로드** → 좌표 없으면 40KB 미로드 성능절약) + 지도/버튼 스타일. ③현장 탭을 `.dmap` 인라인지도 +
  주소·로드뷰/카카오맵/길찾기 버튼바 + 근사위치 안내로 교체. 좌표 없으면 `.dmap-empty` 폴백 +
  주소검색 링크. ④지도는 숨김 탭이라 탭 클릭 시점 lazy init(`invalidateSize`), 페이지 스크롤
  방해 방지 위해 **클릭 후에만 휠 확대**(그 전엔 안내 토스트), mouseout 시 휠 해제.
- **증거**: 신고맥락 케이스 `2025타경32139`(대구 달서구 진천동, 좌표 35.810105,128.517936) 실서버
  Playwright 스크린샷 → 인라인 지도·핀·한글라벨·팝업·버튼바 정상 렌더 확인(scratchpad/map_pane.png).
  좌표 없는 케이스(`2025타경20481`)는 폴백 블록+주소검색 버튼·Leaflet 미로드 확인. pytest
  test_web(+watchlist_web) 47 PASS. 다음: 프로덕션 배포 후 실기기 확인(사용자). 후속안=VWorld
  한글 타일(키 보유)·주변 지하철/학교 POI·건축물대장 준공연도.

## 2026-07-19 13:00 KST — 🐛 상세페이지 잔고장 2건: 감정요항 HTML엔티티 깨짐 + 사진 넘김 화살표
- 사용자 신고: 물건 상세 "감정 요항" 등 자유텍스트가 `&amp;quot;대구진천초등학교&amp;quot;`,
  `&lt;가축분뇨…법률&gt;` 처럼 깨져 보임 + 사진이 스와이프로만 넘어가 불편.
- **원인**: 법원 API 자유텍스트에 HTML 엔티티가 섞여 오는데(간혹 이중 인코딩), `_sanitize`가
  이를 해제하지 않아 Jinja 자동이스케이프에서 `&amp;`가 다시 이스케이프돼 화면 노출.
- **수정 ①(소스)**: `src/courtauction_detail.py::_sanitize`에 `html.unescape` 반복(최대3회) 추가 →
  향후 크롤은 실제 문자로 저장. **②(렌더 즉시교정)**: `src/web.py`에 Jinja 필터 `deent` 등록,
  `detail.html`의 자유텍스트(감정요항 text·surviving_rights·senior_lien·lien_note·remark)에 `| deent`
  적용 → 재크롤 전 DB/Supabase 옛 데이터(&amp;quot;)도 렌더 시점에 복원(평문 반환이라 자동이스케이프가
  다시 안전 처리). **③(화살표)**: `.dphoto`에 `.dphoto-nav.prev/.next` 버튼 추가 — 평소 opacity:0,
  `@media (hover:hover)`에서 `.dphoto:hover` 시 opacity .5로 연하게 페이드인·버튼 hover 시 1.0,
  focus-visible 노출(키보드 접근성), reduced-motion 존중. JS는 `track.scrollBy(±clientWidth, smooth)` +
  첫/끝 장에서 해당 화살표 hidden 토글. 터치는 기존 스와이프 유지(hover 없어 화살표 미노출).
- **증거**: 실제 신고 케이스 `2025타경32139` 테스트클라이언트 렌더 → `&amp;quot;` 잔존 0,
  `&#34;대구진천초등학교&#34;`·`&lt;가축분뇨…&gt;`(브라우저가 `"`/`<`로 표시), nav 버튼 삽입 확인,
  X-Data-Source=db. 회귀 테스트 `test_sanitize_unescapes_html_entities` 추가. pytest
  test_courtauction_detail(31)·test_web 전부 PASS. 다음: 프로덕션 배포 후 실기기 확인(사용자).
- 사용자 요청: "제일 중요한 엔진=크롤링 코드"를 기준으로 어떤 사이트인지 + 어떤 로직으로 크롤링하는지 한국어 설명.
- 기존 README는 PoC 시절("v1에서 크롤러") 구식이라, 실제 코드를 정독해 정확도 반영 후 재작성.
- 추가/개편 섹션: 전체 파이프라인 다이어그램, **크롤링 엔진 상세**(공통 밴회피 철학 표 + 4개 원천별
  ①courtauction ②molit ③naver ④건축물대장/좌표), 캐시·증분 diff, 매칭/시세추정, 스코어, Supabase 서빙,
  실행/배포/키발급, 크롤링 중심 프로젝트 구조.
- 근거: courtauction_client·molit_client·naver_client·matcher·pipeline·coords·region·store_rest·
  courtauction_rights·photo 실코드 Read 후 서술(엔드포인트·안전장치·버그픽스 이력 반영).
- 코드 변경 없음(문서만). 다음: 세션 노트의 두 갈래(감정가/유찰가/시세 3분할 UI vs 시세추정불가 원인 규명) 대기.

## 2026-07-17 12:35 KST — 🔍 리팩터 애드버서리얼 리뷰 반영 + live_months 정정
- 3에이전트 병렬 리뷰(B1/B2/A) 결과: **critical/high/medium 0건**, low 5건. 확정 결함 없음(무회귀).
- 반영한 low 2건: ①derive_grade의 scope게이트 top/second 비교를 grade_labels→grade_thresholds 단일출처로
  되돌림(두 출처 드리프트 시 강등 무력화 방지) ②폴백 derive_grade에 gap_rate=None 명시(옛 p_low-only와
  정확히 일치, 수학적 동치지만 명료화). pytest 530 유지.
- **정정(중요)**: 앞 항목의 "+269 국토부 회복"은 과장. 프로덕션 새로고침(refresh-daily.ps1)은 이미
  `--live-months 24`를 넘겨 **넓은 창을 사용 중**(evidence/refresh-20260717-053002.log 확인). config 기본값
  3은 실제로 안 쓰이던 값 → B1은 "정합성 수정"이지 회복 드라이버가 아님. 24개월을 쓰는데도 아파트/오피
  1,435건이 시세추정불가 = **창 문제 아님**(지역 미크롤 + 거래 드문 소형 물건). 오프라인 테스트가 426건
  살린 건 완전 캐시 사용 때문 — 프로덕션 라이브 크롤 지역 완전성은 별도 조사거리(후속).

## 2026-07-17 12:10 KST — 🧹 시세 로직 리팩터(중구난방 정리) B+A
- 배경: 사용자가 "코드 로직이 너무 중구난방"이라 지적. 4개 층 병렬 정독(워크플로)으로 지도화 후 정리.
- **B1 (live_months 3→12)**: `config.SAMPLE.live_months` 3→12로 matcher.RECENCY_WINDOW_MONTHS(12)와 정렬.
  3개월만 수집해 12개월 창을 굶기던 문제(실측 재매칭 시 국토부 +269건 회복 가능). 옛 `pipeline.LIVE_MONTHS`
  모듈상수 제거 → config 단일 출처화. molit_cache 22개월치라 재크롤 불요.
- **B2 (상수 외부화)**: liquidity_score의 region 승수(1.10/1.05/1.00/0.85)·turnover(cap10·×2)를 ScoreConfig로
  (유일하게 남았던 하드코딩 예외 해소). 9개 등급을 `CONFIG.grade_labels` 단일 registry로 — 흩어진 문자열
  리터럴 제거, _validate에 점수테이블 일치 검사 추가.
- **A (등급 파생 단일화)**: score_listing과 _apply_market_price가 **중복 구현**하던 등급 파생을 `derive_grade()`
  하나로. **실제 불일치 교정**: 채점=차익없음→권리미확인, 폴백=권리미확인→차익없음 순이 갈렸던 것을
  '차익없음 우선'으로 통일(하한밴드 차익 없으면 권리 무관하게 정직히 '차익없음'). scope·표본 게이트는
  국토부 채점만(apply_scope_sample_gates=True), 폴백은 신뢰계수로 이미 하향돼 게이트 없이(False).
- 검증: pytest **530 통과**(신규 6: live_months·derive_grade 우선순위·게이트·폴백순서). 국토부 채점 경로는
  순서 동일 재현(무회귀), 유일 의도 변경=폴백 등급 순서(둘 다 '추천 안 함'이라 영향 라벨뿐).
- ⚠️ auction.db는 오늘 05:58 스케줄 새로고침이 이미 재채점(stored est 645→902, live_months=3 기준). +269
  국토부 회복은 live_months=12로 재채점해야 반영 — 다음 단계.
- 미배포: 커밋 후 Vercel 배포(서빙 derive_grade)+재채점 필요.

## 2026-07-16 11:20 KST — 🆕 폴백 사다리 확장: 네이버 호가·전세 → +587건 회복
- 배경: 아파트/오피 1,876건이 국토부·KB 둘 다 없어 '시세추정불가'였는데, 그중 **587건은 네이버 호가를
  크롤해두고도 안 쓰고 있었음**(market_view가 matched_kb만 인정). 레퍼런스 조사(은행 담보 캐스케이드·요이땅·
  AVM 특허)로 "실거래 없으면 호가/전세로 신뢰 낮춰 메운다"가 표준임을 확인 → 폴백 사다리 확장.
- 구현(src/score.py): market_view를 사다리로 리팩터 — 국토부(1.0) → KB(0.75) → **호가(0.55/1건 0.45)**
  → **전세역산(0.50)**. 공통 로직 `_apply_market_price` 추출. 헬퍼 `ask_view_of`(유형별 haircut 아파트0.93/
  오피0.85, 매칭 area_no 기준이라 ㎡정규화 불요)·`lease_view_of`(전세가율 아파트0.65/오피0.80).
- 순환차익 함정 없음: 호가·전세는 감정가와 독립인 시장 앵커 → 안전(감정가/공시 프록시는 이번에 미사용).
- 권리 하드게이트(위험)·권리미확인은 폴백으로도 안 풀림(보존). 차익은 하한밴드(보수)로 판정.
- UI(templates): srcpill 'ask'/'lease' pill(caution 색)·pricecell/detail 라벨 "호가 기반 추정"/"전세 역산"
  (실거래로 오표기 방지) + base.html CSS.
- config: ask_confidence 0.55·ask_confidence_single 0.45·lease_confidence 0.50·haircut·전세가율 추가.
- 검증: 홈 노출 1,127→**1,714건**(13→20%). 새 587건 등급=관심119·주의145·차익없음174·권리미확인149
  (양호/차익유력 0 — 신뢰캡으로 과대추천 방지). pytest 522+5신규 통과, 회귀 0.
- ⚠️ 데이터: Supabase 미러에 ask_min(700)·lease_avg(801) 이미 존재·populated → **재미러 불필요, 코드배포만**.
- 다음: ②국토부 실거래 확장조회(807건 구제)·공시가격 역산(교차검증) — 사용자 결정 대기.

## 2026-07-16 10:02 KST — ✅ 프로덕션 배포 성공 — 진짜 원인 확정 + 안전배포 스크립트
- 진짜 원인(앞 두 항목 오진 정정): 번들 315MB는 **의존성도(리눅스 wheel 실측 16MB) playwright도 데이터 크롤캐시도
  아니라**, `includeFiles:"{templates,data,static}/**"` 가 .gitignore/.vercelignore 를 **무시하고** 로컬
  `data/backup/*.pre-*`(84M×3=252M) + `molit_trades.db`(130M) 등을 함수 번들에 강제 포함한 것.
  (070ee8a 성공배포 땐 이 파일들이 없었음. GitHub 자동배포는 git에 없는 이 파일들을 안 올려 무관 — 단 그건 멈춤.)
- 해결: 배포 동안만 대형 로컬 파일을 stash 로 옮기고 원본 includeFiles 로 배포 → **성공**
  (auction-arbitrage-r0tr7zwra…, target=production). 대형 파일 원위치 완료.
- 재발방지: `scripts/deploy.ps1` — 대형 파일 이동→배포→복원 자동화. 이후 CLI 배포는 이걸로.
- 라이브 확인: auction-arbitrage-nine.vercel.app 홈 = "시세 검증 1127건" · 정렬 4종 · 페이지네이션 1/19 ·
  60카드/페이지 · KB폴백/사진/모든 UX 반영 확정.
- (부수) requirements.txt playwright 제외는 유지(크롤 lazy import라 서빙 무영향, 클린).

## 2026-07-16 09:50 KST — 🚀 배포 수정(정정): 진짜 원인=playwright 107MB, requirements에서 제외
- 정정: 앞 항목(includeFiles 대형파일 제외)은 **오진** — 재배포 후에도 번들 315.46MB **동일**해서 데이터가 원인이
  아님이 드러남(.vercelignore가 이미 대형 data/db를 제외하고 있었음). vercel.json includeFiles는 검증된 원래
  값으로 **원복**(내 새 glob은 템플릿 미포함 위험).
- 진짜 원인: **requirements.txt의 playwright(107MB)** — naver_client 크롤 시에만 lazy import되는데 Vercel이
  함수에 통째로 설치. 서빙·테스트 어디도 top-level import 안 함(전수확인) → requirements.txt에서 제외.
  315−107 ≈ 208MB < 225MB 한도. 로컬 크롤은 .venv에 이미 설치돼 무영향, 신규 로컬만 수동 설치.
- 배포: CLI 재배포. CI(requirements.txt+pytest)는 playwright 미사용이라 무영향.

## 2026-07-16 09:45 KST — 🚀 배포 수정: vercel.json includeFiles 대형 크롤러파일 제외(번들 315M→한도내)
- 문제: `vercel deploy --prod` 실패 — 함수 번들 315MB > 225MB 한도. 원인: `includeFiles:"{templates,data,static}/**"`가
  **.vercelignore를 무시하고** data/ 전체(molit_trades.db 130M + naver_cache.json 36M 등)를 함수에 강제 포함.
  이전 배포는 molit_trades.db가 더 작았을 때 통과했던 것.
- 수정: includeFiles를 **서빙 필요 소형 파일만 명시**(templates/**·static/**·coords_cache·lawd_codes·backtest_outcomes·
  sample_* 등). 대형 크롤러 캐시·DB 전부 제외. (실측: 서빙이 읽는 data는 coords/lawd/sample뿐, score_config·buyer_profile·
  asking_prices는 파일 없이 기본값 사용).
- 배포: CLI 재배포. (GitHub 자동배포는 세션2 지적대로 멈춤 상태 — 마지막 자동배포 Phase4 d703571, 이후 CLI만.)

## 2026-07-16 09:38 KST — 📄 홈 페이지네이션(60/페이지) + push·배포
- 무엇: 홈 전체노출(1,127건)이 무거워지는 것 방지 — 페이지당 60개 슬라이스 + 이전/다음 페이저.
  정렬·필터·카운트는 전체 기준(정렬이 전체에 적용된 뒤 페이지만 나눔), 페이저 링크가 sort·필터 전부 보존.
- 변경: web.py index — PAGE_SIZE=60, page 파싱·클램프·슬라이스, _page_url 헬퍼(page 제외 후 재부여), count=전체.
  listings.html — 페이저 블록(total_pages>1일 때, 다른 라우트 재사용 대비 is defined 가드). base.html — .pager CSS.
- 증거: 페이지1 전체count 1,127·렌더 60·이전비활성, 페이지2 이전활성, /?sort=recent&page=2 링크에 sort 보존·
  recent 선택 유지. **pytest 522 pass/1 skip, ruff clean.**
- 배포: 사용자 지시로 origin/main push + Vercel 배포.

## 2026-07-16 (주간) — 🏠 홈 개편: top-9 큐레이션 폐지 → 평가가능 전체(1,127건) 점수순 + 정렬 4종
- 무엇(사용자 요청): 홈이 top-9만 보여주던 것 폐지 → **시세 평가가능 물건 전체를 노출**하고 정렬 선택 제공.
  위험·차익없음·권리미확인도 숨기지 않고 등급 칩과 함께 표시(사용자 명시). 미지원유형·시세추정불가만 all=1.
- 변경: web.py index — 무필터 홈 기본 정렬을 'score'(점수 높은순)로, picks[:9] 큐레이션 제거(items=정렬된 evaluable
  전체). query.py — 정렬키 3종 신설(score_asc 점수낮은순·recent 매각기일 최신순·old 오래된순). listings.html —
  정렬 드롭다운 4종(현재 필터 보존) + 헤더 문구. base.html — .sortbar CSS.
- 정책변경: 옛 '추천서 위험/폴백 제외' 테스트 2종을 새 정책(전체 노출·칩 표시)으로 갱신(test_trust_copy·test_audit_fixes).
  위험은 하드게이트로 점수 낮아 상위엔 안 옴(정렬로 자연 강등). 폴백은 노출하되 라벨 표시.
- 증거: 홈 렌더 count 9→**1,127**, 위험/차익없음 칩 노출, 정렬 recent(2026-07-29↑)·old(2026-07-15↑)·score(95.5↑)
  실측. **pytest 522 pass/1 skip, ruff clean.**
- ⚠️ 후속: 1,127개 카드라 페이지 무거움 → 페이지네이션(N개씩 더보기) 검토 필요. push 시 홈 UX 크게 바뀜.

## 2026-07-16 (주간) — 🎯 KB시세 폴백 교정: 국토부 우선(오버라이드 319건→0) + KB 신뢰 하향
- 발견(정찰 wf_5326503d + 라이브 검증): 사용자가 원한 "KB로 시세추정불가 구제"는 **이미 서빙에 존재**
  (score.market_view가 web._enrich_naver로 적용, 시세추정불가 236건이 이미 KB로 추천 편입 중). 또 오버플래그 예방.
  **그러나 진짜 버그 2개**: ①market_view가 KB 있으면 **국토부 실거래까지 덮음(319건, '실거래 기반' 위배)**
  ②KB 신뢰계수 **1.0**(실거래와 동일 과신, 호가 기반인데).
- 수정: market_view를 **진짜 폴백으로** — 국토부 est가 있으면 실거래 우선(KB로 안 덮음), est None일 때만 KB.
  config.kb_confidence=0.75 신설, KB arb·표시 신뢰를 이 값으로 하향. UI 라벨(KB부동산 pill)은 기존에 이미 있음.
- 증거(실DB 재계산): **국토부 오버라이드 319 → 0** · 시세추정불가→추천 편입 **227건 유지**(사용자 win, 신뢰 0.75) ·
  국토부 추천 208건 실거래 기반 복원. 추천가능 462→435(과신 KB 정직화). **pytest 522 pass/1 skip**(신규 KB 회귀 2종),
  ruff clean.
- 다음(사용자 결정): push/배포 시 라이브 추천이 바뀜(319건 실거래 기반 전환·KB 신뢰 하향) → 확인 후 push.
  (선택) 저장 영속화는 서빙이 이미 KB 적용하므로 불요. 사진 백필 잔여 84건은 별건.

## 2026-07-16 (주간) — 🧹 B4 상세 자유텍스트 정제(사용자 확인 후 착수) + 전체 push
- 무엇: 사용자 확인(Q3)대로 '사진 특수문자' = 상세페이지 크롤 자유텍스트 아티팩트. courtauction_detail에
  `_sanitize` 추가 — 제어문자(\x00-\x1f)·제로폭(U+200B~200D)·방향마크·BOM 제거, NBSP/전각공백→일반,
  연속 수평공백 축약(개행 보존). normalize()의 자유텍스트 5곳(surviving_rights·senior_lien·lien_note·
  remark·appraisal_notes)에 **마스킹 전** 적용 → 마스커 정규식이 깨끗한 텍스트를 보게 됨(제로폭이 이름 앞에
  끼어 마스킹 미스나던 것도 함께 해소).
- 증거: _sanitize 단위검증(제로폭/제어/BOM/NBSP/연속공백 제거·한글/개행 보존 전수) + 신규 회귀테스트
  test_normalize_sanitizes_free_text(정제+마스킹 유지). **pytest 520 pass/1 skip, ruff clean.**
- push: 밤샘 UX 6커밋 + 이번 B4를 origin/main 반영(사용자 지시). Vercel 자동배포.
- 남음(사용자 요청): 홈쿼리 서버측 limit 재구조화(Q4) — 사용자 참석 하에 진행 예정.

## 2026-07-16 01:50 KST — 🔍 밤샘UX 사이클5: 코드리뷰+Ponytail 재감사(적대검증) → 자기결함 3건 수정
- 무엇: 사용자 핵심 요청. 밤샘 diff(9ff0593..HEAD)를 code-reviewer 2 + Ponytail 1 관점으로 감사 후
  **각 발견 적대검증**(워크플로 wf_d8561f00). 발견 3건 전부 CONFIRMED이나 검증관이 **code-reviewer의
  medium 2건을 low로 정확히 하향**(과대평가 교정 — 오버플래그 방지 작동). CRITICAL/HIGH·오탐 0건.
- 수정(전부 이번 밤샘에 내가 넣은 코드의 마감결함, low라 선택적이나 값싸고 사용자 우선순위 부합):
  · **라이트박스 포커스 트랩**(detail.html): aria-modal="true" 선언했으나 Tab 트랩 부재로 배경 컨트롤에
    포커스 새던 것 → keydown에 Tab/Shift+Tab 순환 브랜치 추가(a11y 선언과 동작 일치).
  · **차트 터치 축 감지**(detail.html): touchmove 무조건 preventDefault로 수직 스크롤까지 막던 것 →
    축 감지(수평=스크럽·수직=스크롤 통과). 폰 스크롤 방해 제거(사용자 폰 우선).
  · **중복 CSS 제거**(base.html): `.dphoto-empty span`의 color:--ink-500이 부모 상속과 동일한 no-op → 삭제.
- 증거: 렌더에 Tab트랩·_lock 축감지·CSS정리 반영 확인. **pytest 519 pass/1 skip, ruff clean.**
- 평가자: 적대검증 5에이전트(감사3+검증). **오탐 0·과대평가 2건 교정** — 이번 밤샘 코드가 견고함을 확인.
- 상태: GOAL_UX의 실행가능 확정항목 **전부 완료**. 남은 건 사용자/운영자 게이트(Q3 사진텍스트·Q4 홈쿼리)뿐 → 루프 종료.

## 2026-07-16 01:33 KST — ⚡ 밤샘UX 사이클4: 홈 로딩 저위험 최적화(B3 일부) + B4·근본해결 보류
- 무엇: 콜드 로딩 저위험분만(회귀위험 큰 홈쿼리 재구조화는 운영자 보류).
  · store_rest `SUPABASE_CACHE_TTL` 기본 120→**600**(야간 nightly 새로고침이라 stale 허용, 워엄 콜드 왕복 절감).
  · **.vercelignore**: 크롤러 전용 캐시(courtauction_full_cache 41M·naver_cache 36M·courtauction_cache 3M·*.dryrun)
    함수 번들 제외 — **서빙 미참조 전수확인 후**(서빙이 읽는 data/는 coords_cache·lawd_codes·score_config·
    sample_*·buyer_profile뿐). 콜드부팅·배포크기 절감.
  · web.py **create_app 이중생성 제거**: 모듈전역 `app=create_app()`을 `__main__`으로 이동. 서빙(api/index.py·
    src.serve)은 각자 create_app 호출하고 `web.app` 외부참조 0이라, 임포트 시 불필요한 두 번째 앱 생성이 없어짐.
- **오버플래그 가드 준수**: .vercelignore·create_app 변경 전에 (a)서빙의 data/ 읽기 전수 (b)naver_cache/courtauction_cache
  서빙 미참조 (c)tests·src의 web.app 참조 0 을 grep으로 실증 → 누락 파손 위험 배제 후 적용.
- 증거: 서빙 스모크 /health·/·/api/listings 200(create_app 변경 후 정상), 모듈전역 app 제거 확인, TTL=600.
  **pytest 519 pass/1 skip, ruff clean.**
- 보류(QUESTIONS): Q4 홈쿼리 서버측 limit 재구조화(회귀위험, 운영자) · Q3 "사진 특수문자" 실제 텍스트 확인(B4, 추측수정 금지).
- 다음: **A1 code-reviewer 재감사 → A2 Ponytail 재감사**(적대검증, 오탐 REJECTED.md). push 금지.

## 2026-07-16 01:14 KST — ♿ 밤샘UX 사이클3: 지도 키보드접근(U1)·차트 터치 툴팁(U2)
- 무엇: 확정 접근성/모바일 결함 2건.
  · **U1 지도 a11y**(map.html): 사이드 목록 item과 지역칩(`.rchip`)이 클릭 전용(키보드/스크린리더 불가)이던 것
    → `role="button"`+`tabindex="0"`+Enter/Space keydown 핸들러, 칩엔 `aria-pressed`, item엔 `aria-label`.
    `:focus-visible` 아웃라인 CSS 추가(키보드 포커스 가시화). 모드버튼은 이미 실제 `<button>`이라 무수정.
  · **U2 차트 터치**(detail.html): 가격-시간 SVG 툴팁이 mousemove 전용이라 폰에서 수치 판독 불가 →
    핸들러를 `moveAt(clientX,clientY)`로 공통화하고 touchstart/touchmove(preventDefault)/touchend 배선.
    mousemove 경로 불변(회귀 없음).
- 증거: evidence/ux_map_a11y.html + 렌더 검증(사이드 role/keydown·칩 aria-pressed·focus CSS / moveAt·touch 3종·
  mousemove 유지). **pytest 519 pass/1 skip, ruff clean.**
- 다음: B4 자유텍스트 정제(사용자 확인 대기 → QUESTIONS만) → B3 홈 로딩(저위험분: TTL·vercel번들·create_app 중복만,
  쿼리 재구조화는 운영자) → **A1 code-reviewer · A2 Ponytail 재감사(적대검증 필수)**. push 금지·로컬만.

## 2026-07-16 01:01 KST — ♿ 밤샘UX 사이클2: 대비(U3)·재매각 보증금(U5) + 오탐 1건 기각(U6)
- 무엇: 확정 UX 결함 2건 수정 + 오버플래그 1건 적발.
  · **U3 대비**: 의미있는 라벨 3곳(`.hh`×2·`.mcard .ms .k`)이 `--ink-400`(#98a2b3, ≈2.6:1, WCAG AA 미달)
    → `--ink-500`(#6b7688, 계산상 4.71:1 AA통과). 장식용 --ink-400은 유지(오버리치 방지).
  · **U5 재매각 보증금**: `입찰보증금 (10%)` 하드코딩이 재매각(대금미납 재경매, 통상 20~30%)을 과소안내.
    프로젝트 기존 재매각 신호(`fail_count==0 & cut_rate>0.01`, line274 '재매각 저감')를 재사용해
    해당 물건엔 '(재매각 · 통상 20~30%)' 라벨 + '×2~3' 경고 표기(새 휴리스틱 아님, 정확한 요율 prchDposRate는 후속).
    detail.html 인라인 --ink-400도 --ink-500로 상향.
- **⚠️ U6 오탐 기각(harness/REJECTED.md)**: "홈 이중폼 입력 누락"은 대부분 거짓 — listings.html이 이미
  이름폼↔필터폼 상태를 hidden으로 양방향 보존(66-70·78행). 진단이 놓침. 잔여(미제출 텍스트 손실)는 경미해 미수정.
- 증거: evidence/ux_deposit_{일반,fail0}.html + 재매각 실물건(2025타경22211, 30%저감)에서 '재매각 20~30% ×2~3'
  렌더 확인. **pytest 519 pass/1 skip, ruff clean.**
- 다음: U1 지도a11y · U2 차트터치 → B4 자유텍스트 정제(사용자 확인) → B3 홈 로딩 → A1/A2 재감사(적대검증). push 금지.

## 2026-07-16 00:40 KST — 🖼 밤샘UX 사이클1: 사진 확대·빈상태·CLS + sale_date 가드 (+ 오버플래그 1건 기각)
- 무엇: 사용자 UX 보고 대응. **먼저 적대검증으로 오탐 적발** — 진단(wf_a6fa366d)이 "confirmed high"로
  올린 '사진 미표시=클라우드 photo_url 컬럼 부재→400'을 **라이브 반증**(컬럼 존재·URL 200·배포 사이트
  사진 정상 렌더). DB ALTER 미실행. harness/REJECTED.md 기록. 재진단: 전체 93%가 사진 미수집인데 빈 상태
  안내 없이 블록만 사라져 '깨진 듯' 보였던 것이 진짜 원인.
- 변경(detail.html/base.html): ①사진 없는 물건 명시적 빈 상태('사진 미제공/미수집') ②`<img>` width/height/
  decoding 힌트로 CLS·로딩 개선 ③**의존성0 접근성 라이트박스**(클릭·Enter/Space 확대, Esc/←→/백드롭 닫기,
  포커스 복귀, aria-modal, 카운터) ④매각기일 `sale_date or '미상'` 빈값 가드(U4).
- 증거: evidence/ux_detail_photo.html·ux_detail_nophoto.html(Flask 실렌더 — 사진有=블록+치수, 사진無=빈상태,
  둘 다 라이트박스 JS·status 200). **pytest 519 pass/1 skip, ruff clean.**
- 평가자: 라이브 스키마+이미지 200+배포 fetch로 오탐 반증(오버플래그 가드 실증). 
- 다음(밤샘 대기): U3 대비·U6 이중폼·U1 지도a11y·U2 차트터치·U5 재매각보증금 → B4 텍스트정제(자유텍스트)
  → B3 홈 로딩(3테이블 전량→서버측 limit, 무거움) → A1/A2 코드리뷰·Ponytail 재감사(적대검증). push 금지·로컬만.

## 2026-07-15 23:52 KST — 🎯 지분매각 하드게이트 (통물건 과대평가 424건 차익추천 제외) + 라이브 캡처 정찰
- 무엇: 커버리지 착수 위해 실제 courtauction 응답을 **라이브 1건 캡처**(kill-switch 임시해제→작업후 원위치).
  **핵심 발견**: pgj15B(selectAuctnCsSrchRslt) 응답에 **점유자/이해관계인 표가 아예 없음**(임차/전입/보증금/
  점유/대항 키워드 전체 0회) — scout가 "있다"던 이해관계인 퀵윈은 **존재하지 않는 데이터**였고, 추측 파서였으면
  '점유자 0명' 침묵실패가 됐을 것. 캡처가 이를 막음. selectAroundDspslGds(인근매각사례)는 URL 2종 다 302
  (밴 아님·요청형식 미상, 프론트 JS 분석 필요) → 보류.
  **대신 진짜 win 확보 = 지분매각**: 응답의 gdsDspslObjctLst.dspslStkCtt('…지분 1345.8분의 281.23')·
  gdsSpcfcRmk('-지분매각')로 실증. 이미 저장된 remark에 신호가 있어 **추가 크롤·마이그레이션 0, 소급 적용**.
- 변경: config.fatal_special에 '지분매각' 추가(+special_penalty 30) / courtauction_detail.summarize가
  법원 분류어 정규식 `지분\s*(?:매각|경매)`로 '지분매각' 라벨 부여(remark+surviving+lien). detect_special_rights의
  광범위 '지분'(bare substring, 440건)은 그대로 소프트페널티 유지 — 정밀 '지분매각'만 게이트해 통물건 오배제 방지.
- 증거: **pytest 519 pass / 1 skip**(+2 신규: 게이트 확정·단순언급 미게이트), ruff clean. 전체 6,156 권리 재평가:
  **지분매각 라벨 424건 전부 하드게이트 확정**(remark문자열 376 + surviving/lien '지분경매' 등 48, 유치권 겹침 0).
  이전엔 소프트페널티(20)만 받아 통물건 시세로 과대평가돼 허위 차익 상위 노출되던 424건이 차익추천에서 제외됨.
- 평가자: 라이브 캡처(dma_capture.json 구조 실측) + 실데이터 424건 재평가. 'N분의M' 단순언급 미게이트 확인.
- 다음: (보류) selectAroundDspslGds 요청형식 = 프론트 JS 리버스 필요 / 이해관계인 = 미발견 엔드포인트 /
  지분 분수(dspslStkCtt) 파싱은 표시·정밀도 향상용 후속. kill-switch(COURTAUCTION_STOP) 원위치함.

## 2026-07-15 23:25 KST — 🧹 CI ruff green (24건 → 0, push 차단 해소)
- 무엇: push 시 CI를 red로 만들던 ruff 24건 전부 해소(기능 무관, 이전 세션 이전부터 누적).
  자동수정 17건(I001 import정렬·UP037 주석따옴표·F401 미사용import·UP035 Callable출처·W291 공백) +
  수동 7건: E702 세미콜론분리(migrate_photos), UP031 %→.format 2건(data_gates, SQL ? 바인드 보존),
  B904 raise from None(naver_client 차단감지), E741 `l`→`lawd`(pipeline _fetch_one),
  F841 죽은 placeholder 제거(test_matcher), **B023 lambda 늦은바인딩 `lambda ymd=ymd:` (test_molit_cache,
  실제 정확성 버그 — 루프변수 캡처)**.
- 증거: **ruff `All checks passed!`** + **pytest 517 pass / 1 skip**(회귀0). 14파일 net -2줄.
- 평가자: 정찰 워크플로 wf_4cf4e64b-ebe(ruff 전수 관측·자동/수동 분류). data/coords_cache.json은 내 것 아니라 제외.
- 다음: (운영) 해제거래 소급정정용 molit_trades.db 재수집 + 커버리지(인근매각사례 selectAroundDspslGds ·
  pgj15B 이해관계인 구조화 추출 = 추가HTTP 0 퀵윈).

## 2026-07-15 23:21 KST — 🎯 해제거래(cdealType) 파싱·시세 제외 (데이터 정확성, +11.18% 오염 차단)
- 무엇: 국토부 실거래 중 신고 후 취소된 **해제거래**가 comps에 섞여 시세를 부풀리던 것 차단
  (감사 실측: 강남·분당 2개월 182/1,628건 해제 → 평균 +11.18% 과대). 4파일 최소 변경:
  · models.Trade에 `cdeal_type`·`cdeal_day` 필드 + `is_cancelled` property(cdealType 'O' 또는 해제일 존재).
  · molit_client `_COMMON_TAGS`에 해제여부/cdealType·해제사유발생일/cdealDay 이중태그 추가 + `_parse_root`에서 보존.
  · matcher `match_trades_scoped` pool에 `and not t.is_cancelled` 1조건 — same_area/near_area/by_dong·
    중앙값·밴드·차트 comps를 모두 파생하는 단일 초크포인트라 여기서 한 번만 걸러 전 경로 커버.
  · molit_cache 무변경(asdict/Trade(**d) 자동 직렬화, 신규필드 기본값으로 구캐시 하위호환).
- 증거: **pytest 517 passed / 1 skipped**(512+신규5, 회귀0). 신규테스트: 파싱 해제감지 국·영문 2 +
  정상거래 미해제 1(test_molit_parse) + matcher 제외 1(고가 990M outlier 유입 차단) + 캐시 라운드트립·
  구캐시 하위호환 1. 전체 repo ruff 24건 불변(신규 오류 0).
- 평가자: 정찰 워크플로 wf_4cf4e64b-ebe(cdealType 루트 4곳 라인단위 지목). 이중키 _find라 API가 국문/영문
  어느 태그를 반환해도 견고.
- 다음: (운영) **재수집 필요** — data/molit_trades.db(gitignore 파생캐시)는 파싱본만 저장해 구건은 해제여부
  소실 → 퍼지 후 재워밍해야 소급 정정(무료·법원무관·멱등·재개가능, 쿼터 신선시각 권장). 열린 달은 매 실행
  재fetch라 즉시 반영. 코드수정만으로는 신규 fetch만 정정. + CI ruff green(17자동+7수동) + 커버리지(인근매각사례·pgj15B 이해관계인).

## 2026-07-15 23:06 KST — 🧹 포니테일 죽은코드 정리 (검증 후 확정분만 삭제, -115줄)
- 무엇: Ponytail 감사에서 나온 죽은코드 후보 11종을 병렬 워크플로(11 에이전트)로 읽기전용
  재검증 → SAFE_DELETE 8종만 삭제. 정의·참조·동적사용(getattr/문자열키/템플릿/DB컬럼) 전수 추적.
  삭제: `web.py`의 죽은 `_truthy` 첫 정의(import 시 868행 정의에 덮여 도달불가) /
  `molit_client.fetch_apt_trades`·`fetch_trades_months`(미사용, load_live_trades가 인라인 대체) /
  `models.AuctionListing.discount_vs_appraisal` + `courtauction_fields.CourtAuctionRecord.discount_vs_appraisal`(호출 0) /
  `photo.thumbnail_b64`(Phase5 Storage 이후 고아 — DB컬럼 thumb_b64는 별개·유지) /
  `courtauction_client._PII_NOTE`·`courtauction_fields.USAGE_LCLS`(죽은 상수) /
  `FIELD_LABELS`→`label_row`→`CourtAuctionRecord.labeled()` 죽은 체인 전체(테스트에서만 삶).
  + 전용 테스트 2건(test_discount_vs_appraisal·test_labeled_dump) 제거,
  + courtauction_detail.py:297 stale 주석(thumbnail_b64→thumbnail_jpeg) 교정.
- 증거: py_compile OK, 잔존 코드참조 0건(grep), **pytest 512 passed / 1 skipped**(514−삭제테스트2=512, 회귀0).
  변경파일 ruff 신규오류 0(기존 UP037 5건은 line378 등 미변경부·Phase2 대상).
- 평가자: 병렬 검증 워크플로 wf_65a3501c-969 (적대검증). **FETCH_EXTRA는 KEEP 판정**(AUCTION_FETCH_EXTRA=1
  config 게이트 실기능 — 죽은코드 아님, 삭제 회피). 3중복이라던 페이지네이션은 실측 2중복(naver 무관).
- 다음: (선택) 리팩터 dedup 2종 — pagination(molit 2중복→_paginate 헬퍼)·env파서(5곳→config 헬퍼).
  그 다음 Phase 2 = 데이터 정확성·크롤 커버리지(CI ruff green → 해제거래 cdealType 파싱+국토부 재수집).

## 2026-07-15 22:10 KST — 🛠 2차 감사 수정 A~H(위험추천·알림·캐시크래시·만료·정렬·매처·지역·리포트)
- A(score): 하드게이트(유치권 등)가 시세추정불가(est None) 경로에서 미적용 → 서빙 KB 재계산 시
  market_view가 위험신호 못받아 추천되던 것 수정(est None 경로에서도 is_hard_gated→grade '위험').
- B(run_alerts·run_digest): pipeline.run() 무인자=샘플 6건 → 실제 서빙 DB(store.load_scored) 읽게.
  알림이 영구 '변동 0건', 다이제스트가 항상 샘플이던 것 수정.
- C(molit_cache): _load의 Trade(**d)를 try 안으로 → 캐시 스키마 드리프트/손상 시 크래시 방지(#15).
  ⚠#28(빈결과 미캐시)은 '빈 달 의도적 캐시' 설계와 충돌·요청증폭이라 되돌림(트레이드오프 주석).
- D(store_rest): 전량교체 만료삭제가 NULL refreshed_at(증분 upsert 구행)을 안 지워 팔림/취하
  매물이 영구 잔존 → DELETE에 is.null OR 추가.
- E(store_rest): load_scored·load_all_naver 페이지네이션에 유일키 타이브레이커 order 추가
  (1000건 초과 시 페이지 경계 누락/중복 방지).
- F(matcher): _multi_complex가 한 이름의 두 토큰('2차 3단지')을 혼입으로 오판 → 단지별 식별자
  집합 비교로 교정. G(region): name_to_code 부분일치가 '중구'류를 임의 구로 반환 → 유일할 때만 채택.
  H(report): to_html의 area_m2 None → 크래시 가드. +naver articles 호가 상한 9억 제거(고가 누락).
- 회귀 테스트 tests/test_audit_round2_fixes.py 신설(10건) + store_rest 테스트 2건 갱신. 514 통과.
  (인수금액 파싱 #9는 다른 세션이 소유 중인 courtauction_rights라 충돌회피로 제외.)

## 2026-07-15 21:40 KST — 🔍 크롤 버그 3종 실피해 검증(결론: 손실 0) + 음차맵·실패분 재시도
- 사용자 질문: 방금 고친 크롤 버그 3개(①국토부 페이지 조기중단 ②네이버 차단을 0건으로 삼킴
  ③0건 지역 영구 스킵) 때문에 5시간 크롤링이 덜 긁혔는가? → **데이터 손실 0. 재수집 불필요.**

- **① 페이지 조기중단 — 발동 이력 없음**. `num_rows=1000` 이라 버그가 물리려면 1페이지가 정확히
  1000행 꽉 차고 그중 일부가 필터돼야 하는데, **990~999 구간이 전 유형(apt/rh/officetel/sh/nrg/
  land) 통틀어 0건**. >=1000 은 단 1건(36110 202504, n=1529)이고 2페이지를 정상 수신했다.
  거래 상위 8개 (지역,월)을 고친 코드로 **재수집 → 캐시값과 전부 정확히 일치(차이 0)**.
- **② 네이버 차단 삼킴 — 차단 아니었음**. 과거 no_match 아파트 10건을 **캐시 비우고 실네트워크
  재시도(25콜)** → 단지 목록 정상 수신(동네당 4·26·28·16개), NaverBlocked 미발생, 10건 전부
  여전히 실패. 시간대별 실패율(11시 60%→14시 29%)은 차단이 아니라 그날 오피스텔 버그(13:10)·
  이름매칭(14:05) 수정 효과. 시도별 실패율도 전남 40.8%~전북 3.2% **매끄러운 그라데이션**(차단이면
  지역이 아니라 시간대로 몰린다) = 네이버 등재율 차이.
  ⚠ 1차 검증은 무효였다(자기 기록): `Cache()` 가 디스크 캐시를 로드해 **총 콜 0**으로 옛 답을
  재생 — 콜 수 0을 보고 발각, 캐시 비우고 재실행해 확정.
- **③ 0건 영구 스킵 — 메커니즘 실재, 단 가짜 0은 없었음**. molit_cache `_load` 가 `[]` 를 반환하면
  (닫힌 달) 영영 재수집 안 한다. 그러나 n=0 캐시 14건 표본 재수집 → **14/14 진짜 0건**(시골 지역·
  소수 유형의 실제 무거래). **다만 네이버 경로는 미수정이었다** — `naver_done_keys` 가 status
  무관하게 전부 '처리됨'을 반환해 **실패 861건이 영구 스킵**(미처리 0건). crawl_naver.py 는 이번
  수정 대상 4파일에 없었음.

- **발견: 음차맵 누락(별개 버그, 크롤 오류 아님)**. no_match 원인을 파보니 같은 단지인데 임계
  0.70 미달로 탈락: 에스씨그린아파트↔SC그린(0.44)·창원무동에스티엑스칸1차↔…STX칸1차(0.64).
  - ⚠ **일반 규칙(한글 알파벳 연속열→라틴) 시도했다가 폐기** — '푸르지오'→'푸르go'(지+오=G+O),
    '디오션시티'→'do션시티'. 단음절 letter name 이 한국어 음절과 겹쳐 브랜드를 파괴한다.
    → 다음절 명시 등재만: 에스티엑스/에스씨/에스아이/에스제이/에스엠/엠제이 (실측 관측분).
  - 규모 정정(자기 기록): 처음 "100~125건"이라 보고했으나 표본 4중 2가 우연히 음차였던 과대추정.
    **전수로 세니 11건**.

- **재수집 실측 결과 — 회수 11건 / 손실 0건**:
  `[완료] KB 7·호가 4·KB無 534·매칭실패 315·좌표無 1` (861건 재시도, 차단·429 0건).
  성공 **1,490 → 1,501 / 2,351**. 회수분은 전부 음차 케이스(에스씨그린·에스티엑스칸 1·2차·
  에스제이타워·에스엠스카이빌·에스케이뷰 등).
  - **두 번째 예상 실패(자기 기록)**: "옛 코드 시절 수집분 150건이 살아날 것"이라 예측했으나
    **실제 회수 0건**. 그 실패분은 진짜 미등재였다(오피스텔은 13:10 수정 후 이미 삭제·재수집돼
    있었음 — versions.md 13:10 항목). 음차맵이 예측한 11건만 정확히 나왔다.
  - 남은 실패 850건의 성격: **KB無 534건**(단지는 찾았으나 KB시세·호가 둘 다 없음 — 소형·구축
    단지의 정상 결과) + **매칭실패 315건**(네이버 미등재 소형/시골 아파트). 재시도로 더 얻을 게 없다.
  - 클라우드 미러: `store_rest.upsert_naver` 는 **호출자가 0개**였다(Phase4 때 수동 1회 미러).
    회수분이 서빙에 반영되도록 2,351행 수동 미러 실행. → 자동화는 후속 과제.

- 수정: `naver_match._TRANS` 6종 추가. `store.naver_done_keys(include_failed=)` + 상수
  `NAVER_FAILED_STATUS`. `crawl_naver --retry-failed`(성공분 유지, 실패분만 재시도 — 기본값은
  보수적 유지: 매 실행 재시도는 진짜 미등재 물건에 불필요한 요청 반복).
- 신설 `tests/test_naver_match.py` — **이 모듈엔 테스트가 0개였다**. 음차 통과 8종 + 브랜드
  무손상 10종(푸르지오 등, 일반규칙 폐기 근거 고정) + 오매칭 방지 2종.
- 테스트 **505 passed**. ruff 클린. 백업 `data/backup/auction.db.pre-naver-retry-20260715`.
- **결론(사용자 질문 답)**: 5시간 크롤링 데이터는 멀쩡하다. 세 버그는 **터질 조건이 안 맞아 안 터진
  지뢰**였고(고친 건 옳은 판단 — 거래 많은 달이 오면 ①이 터진다), 지금까지 적재분은 재수집 불필요.
  실제 누락은 크롤 버그가 아니라 **음차맵 부족 11건**이었고 이번에 회수했다.

## 2026-07-15 18:40 KST — 🛠 크롤링 안전 3종 수정(Ponytail+정밀 리뷰 감사 후속)
- 감사(58에이전트, 69 findings)에서 크롤 경로 치명/높음 3건을 수정. 전부 회귀 테스트 추가(486 통과).
- ①수집 0건 data-wipe(치명): run.py 전량교체 분기에 'scored 비면 교체·미러 스킵(기존 보존)' 명시,
  store.replace_all·store_rest.replace_all 에도 빈 items 방어선(no-op) 이중 추가.
- ②네이버 차단 오인: naver_client.fetch 가 200-non-JSON(안티봇 챌린지)·403 등을 조용한 None 대신
  NaverBlocked 로 중단, 404만 정상적 '데이터 없음'(None)으로 구분.
- ③MOLIT 페이지네이션 조기종료: molit_client·molit_extra_client 가 필터 후 len(page_trades)가 아니라
  raw_count(root.iter('item'), 필터 전)로 마지막 페이지 판정 → amount<=0 등 걸러진 행 때문에 다음
  페이지를 놓쳐 comps 누락·시세 편향되던 버그 제거. totalCount 비교도 page*num_rows 기준으로 교정.
- tests/test_crawl_safety_fixes.py 신설(7건). 다른 세션이 크롤 작업 중이라 이 파일들만 선택 스테이징.


## 2026-07-15 20:57 KST — 🚨 감사: 권리 파서가 프로덕션 미배선 + maejibun PII 누출 → ①②③ 수정
- 배경: 사용자 요청 "무료로 가져올 수 있는데 크롤 코드에 없어 못 가져오는 것" 다각도 정밀감사
  (에이전트 4축: 법원 라이브 필드 / 검색리스트 117필드 / 공공API 갭 / 권리분석 능력격차).
  결론 = **크롤 갭보다 "이미 받아놓은 걸 안 읽는 것"이 훨씬 크다.**

- **① PII(CRITICAL)** — `_FREE_TEXT_FIELDS`에 `maejibun`(매각지분) 누락 → **채무자·공유자 실명
  1,870행 무방비 저장**. 마스커도 역할라벨-선행 어순만 처리해 "한웅희 소유"·"최선웅 지분" 류
  미탐(현행 마스커 적용해도 1,746행 잔존 실측). 수정: maejibun 추가 + 이름-선행 패턴
  (`_PII_NAME_FIRST_RE`) + 법률용어 제외어 접두매칭(`_NOT_A_NAME` — '소유권'·'전원의' 오탐 차단)
  + 조사 분리(`_split_name_josa` — "[성명]부터" 훼손 방지) + 역할어 6종 확장(유치권신고인·
  임차권자·연고자 등, 실측 96건). `scripts/remask_existing_pii.py`(신설, 멱등)로 소급.
  - 증거: 2,638행 갱신 → maejibun 1,982행 **실명 잔존 0행 / 오탐 0**(지분비율 보존 확인).
  - raw_listings 자체는 Supabase 미러 대상 아님 + web.py 미서빙 → 이 경로는 로컬 한정.

- **①-b PII 2차(CRITICAL — 클라우드 유출, 뒤늦게 발견)**: `listing_rights`도 오염돼 있었고
  **이쪽은 Supabase 로 미러돼 상세 페이지로 서빙**된다. normalize()가 명세서 자유텍스트
  (remark/lien_note/surviving_rights)를 마스킹 없이 저장 → 유치권신고인·임차인 **실명 652행**.
  클라우드 실측 오염: remark 297행·surviving_rights 29행("유치권신고인 윤용섭로부터…" 원문 확인).
  - 발견 경위: spJogCd 교차검증 중 lien_note 원문을 눈으로 보다가 실명 노출을 목격. ①에서
    raw_listings만 보고 "미유출"이라 단정한 것이 **범위 오판**이었다(자기 기록).
  - 수정: normalize()가 세 자유텍스트 필드를 저장 전 mask_personal_names 적용(senior_lien·
    금액·날짜 등 공시정보 무손상). remask 스크립트를 `--cloud` 로 확장(로컬 SQLite + Supabase
    upsert_rights 동시 반영).
  - **마스커 2차 버그 2건(실측 누출로 발각)**:
    ① 제외어 **접두일치**가 실명을 삼킴 — "전원"이 제외어라 실명 **전원철**이, "소유"가 **소유진**이
       마스킹을 빠져나갔다(천안 2025타경11313 "임차인 전원철, 박화란" 무마스킹 클라우드 유출).
       → 정확일치 + 조사분리(`_strip_josa`)로 교체. "전원의"=제외, "전원철"=실명 판정.
    ② **쉼표 나열** 둘째 이후 성명 미탐("임차인 전원철, **박화란**") → `_PII_CONTEXT_RE` 에
       나열 꼬리 그룹 추가 + `_PII_NAME_LIST_RE` 로 항목별 마스킹(조사 보존).
  - **최종 증거(필드별 검사)**: 로컬 raw_listings **0건** · 로컬 listing_rights **0건** ·
    **클라우드 auction_listing_rights 6,120행 중 0건**. 이전 누출 샘플 재조회로 "[성명]" 치환 확인.
  - 검증 스크립트 자기 버그 1건: 필드를 공백 연결해 스캔하니 경계를 넘는 가짜 매칭(앞 필드 끝
    '임차인' + 뒤 필드 시작 '홍길동')이 39건 나왔다 — 필드별 검사로 교정 후 0건.

- **② 비고 권리검출 미호출(HIGH)** — `to_auction_listing`이 `detect_special_rights`만 부르고
  `detect_tenant_opposable`·`detect_assumed_amount`는 안 불러, batch 전 물건이 assumed_amount=0·
  tenant_opposable=False로 적재 → **인수비율 하드게이트·대항력 30점이 batch에서 영구 사망**.
  - 증거: 재채점 결과 **127건 점수 하락**. 성지새말 92.8→25.0[위험]·프라지움7차 92.8→25.0·
    주공아파트 92.8→25.0(최저가 593만원 vs 인수 1.6억)·율상마을푸르지오 92.0→25.0.

- **③ 권리 배선(CRITICAL)** — `enrich_listings_with_rights` 프로덕션 호출자 **0개**(테스트뿐).
  run.py→pipeline.run()은 estimate_market→score_listing뿐이라 **batch가 권리를 한 번도 안 봄**.
  실측: 적재 8,245건 중 **79.7%(6,570건)가 rights_score=85.0 상수**(=100−15 소유자점유 기본값)
  → 권리 가중치 30%가 전 물건에 25.5점 균일가산 = **변별력 0**. 랭킹과 상세가 다른 사실 위에 섰음.
  - 수정: `pipeline.apply_rights_from_rows()`(신설) — 이미 크롤된 listing_rights 요지를 채점 전
    반영(네트워크 0). 서빙(web.py)과 **동일 어댑터**(CaseRights→summarize→badge)라 목록·상세 정합.
    `store.load_all_rights()`(신설), run.py 배선. 미크롤·빈요지는 무접촉(=권리미확인 유지).
  - `rights_verified` DDL 컬럼 신설(SCHEMA_VERSION 6→7, ALTER 마이그레이션) — 기존엔 컬럼이
    없어 DB 왕복 시 항상 False 복원(audit-t8-20260703 기존지적 #232 해소). load_scored에서 bool 복원.
  - **증거(전체 재채점 실측, scratch DB)**: `권리 배선: 6156/8245건 반영(하드게이트 425건·
    빈요지 0건·미크롤 2089건=권리미확인)`. 85.0 상수 **79.7%→67.7%**. rights_verified 6,156 True /
    2,089 False 영속화 확인. **점수 하락(5점+) 198건**(②단독 127건 → ③포함 198건) — 95.5[최상위]
    →25.0[위험] 10건 포함(구월힐스테이트·반월당효성해링턴·엠타워-1 등).
  - 검증 실패 1회(자기 기록): 첫 시도는 **빈 scratch DB를 args.db로 줘서 listing_rights가 없어
    배선이 조용히 건너뛰어짐**("권리 배선" 로그 부재로 발각). 권리 시드 후 재실행해 확정.

- **⚠️ 내 보고 오류(자기 기록)**: "월성푸르지오(2025타경30912) 상세에 인수 보증금 1.1억이 있다"는
  **틀렸다**. `court` 없이 case_no 만으로 조회한 결과 **강릉지원의 동명 사건**을 끌어다 본 것 —
  사건번호는 법원별 독립 채번이며 이는 audit 2026-07-10이 CRITICAL로 지적한 바로 그 실수다.
  실제: 대구서부지원 월성푸르지오 = 인수권리란 **빈값**(비고에만 대항력 문구) → ②로 권리점수
  85→55, 차익 0은 **시세추정불가(감정가 괴리 가드)** 때문이지 권리와 무관. 인수 1.1억은 강릉지원
  사건이며 그건 ③으로 85→**0** 정상 게이트. **배선 코드 자체는 court 포함 정확 조인**이라
  오염 없음(test_apply_rights_from_rows_requires_exact_item_no 가 회귀 고정). 테스트 docstring의
  월성푸르지오 표기도 강릉지원 실측으로 정정함.

- **정정(중요)**: 감사 에이전트의 최고심각 발견 C-1("빈 인수권리란 74.6%를 인수없음으로 오취급
  = 크롤실패가 ✓안전으로 표시")은 **데이터로 반증**. 공백 4,594행 전수조사 → spec_write_ymd·
  court_dept·claim_amt·schedule **전부 100% 채움**, 판정근거 전무 행 **0건**. 즉 공백은 크롤실패가
  아니라 법원의 "인수권리 없음" 판단이 맞고 기존 `is_empty` 가드로 충분. **④(3값 구분) 취소.**
  - 그 외 정정: 명세서 PDF(ecdocId) 가설은 **검증 실패**(orvParam 교환 필요, HTTP 500, 뷰어 미도달).
    `violYn`(위반건축물)은 수기 fixture에만 존재하는 **허구 필드**(건축HUB 8오퍼레이션 전수 확인).

- **spJogCd(특별매각조건 코드) — 착수했다가 보류(증거 부족)**: 리프트 분석상 유망해 보였으나
  (0004303 유치권 정밀도 86%·리프트 69x), **독립 원천으로 교차검증하니 코드 단독 신호의 적중률이
  47%(7/15)**였다 — 비고에 유치권 문구가 없는 0004303 행의 상세(lien_note/remark)를 대조한 결과.
  "정밀도 86%"는 비고 텍스트와의 **일치율**이지 정확도가 아니다. 유치권은 fatal_special→하드게이트
  직행이라 절반이 오탐이면 멀쩡한 물건을 죽인다. **자동 라벨 적용 보류.** 표본 15건이라 확대검증
  필요. 신뢰 가능 후보(정밀도≥80%+리프트≥3): 0004307 농지취득 98%/4.0x·0004311 맹지 92%/8.2x·
  0004304 분묘 87%/15.3x·0004310 공유자우선매수 83%/12.0x·0004305 재매각 96%/64.5x.
  ⚠ '보증금'은 대부분 코드에 섞이는 교란변수(특약이 으레 보증금 20% 언급) — 0004306·0004309 해독 실패.

- 테스트: **479 passed**(신규 11 — PII 6·비고검출 2·권리배선 3). ruff 신규오류 0.
- 백업: `data/backup/auction.db.pre-pii-mask-20260715` · `.pre-rights-wiring-20260715` (각 84M).
- **프로덕션 반영 완료(사용자 승인)**: auction.db 재채점 + Supabase 미러 8,245건 전량교체 성공.
  선행으로 `auction_scored_listings.rights_verified` 컬럼 DDL 직접 실행(Management API) —
  **store_rest 가 store._COLS 를 그대로 select 하므로 컬럼 없이 배포했으면 클라우드 서빙이 400으로
  죽었다**(deploy/supabase_rights.sql 에도 기록).
- 다음(사용자 지정): 해제거래(`cdealType`) 11.18% 시세오염 — 국토부 캐시가 파싱본만 저장해
  재수집 필요(무료·법원 무관). spJogCd 확대검증. 미착수: maejibun 지분라벨(169건 미탐),
  인근매각사례 엔드포인트(selectAroundDspslGds — 낙찰가율 실측 확인됨).

## 2026-07-15 18:10 KST — ✅ Phase5 마이그레이션 완료 + VACUUM
- 기존 사진 8,076장 전량 base64→Storage 이전 완료(실패 1건은 멱등 재실행으로 재이전, 최종 실패 0).
  실패 원인=전송 순간 네트워크 hiccup(데이터는 정상 114KB), 듀얼모드라 이전 중에도 표시 무중단.
- 로컬 auction.db VACUUM: 575MB→84MB(약 491MB·81% 회수). listing_photos.thumb_b64 잔량 0.
- 검증: 로컬 load_photos·클라우드 fetch_photos 모두 URL 반환, 샘플 URL 200 image/jpeg. Supabase 미러도
  배치 반영(photo_url 채움+thumb_b64 비움). 진행 대시보드(자동새로고침 HTML)로 전 구간 모니터링.

## 2026-07-15 16:35 KST — 📦 Phase5: 사진 base64→Supabase Storage 이전(듀얼모드)
- 목적: listing_photos.thumb_b64(전 사진 base64)가 Supabase 공유티어 DB(500MB)를 압박 → 이미지를
  Storage 버킷(auction-photos, public)으로 옮기고 DB엔 photo_url만 보관(경량화).
- store: listing_photos에 photo_url 컬럼(ALTER 마이그레이션) + save_photo_urls(). load_photos 듀얼모드
  = photo_url 있으면 URL, 없으면 data:image/jpeg;base64,{thumb} 로 감싸 렌더용 반환(전환기 무중단).
- photo: thumbnail_jpeg()(리사이즈→JPEG bytes) 신설, thumbnail_b64는 이를 base64 래핑. photo_store(신설):
  Supabase Storage REST 클라이언트(enabled/ensure_bucket/upload_photo, sha1 ASCII 키). 업로드 실측 200 OK.
- crawl_rights: Storage 가능 시 업로드→save_photo_urls, 아니면 base64 폴백(로컬개발). store_rest.fetch_photos
  듀얼모드. detail.html img src(data-uri→url). migrate_photos_to_storage.py(신설): 기존 base64 일괄 이전
  (멱등·이어받기·PHOTO_MIGRATE_STOP·진행바·local·cloud 동시반영). 468 테스트 통과.

## 2026-07-15 16:20 KST — 🏦 Phase4: KB시세·호가·전세 사이트 통합
- naver_prices(2351건, KB794·호가) 를 서빙에 통합. models.ScoredListing에 naver/market_source(비영속) 추가.
- score.market_view(): KB있으면 시세·밴드·차익·등급을 KB기준 재계산(불변, 권리상태는 보존), 없으면 표시용 첨부.
  kb_price_of() 이중게이트(matched_kb+kb_avg>0). web._enrich_naver()가 _scored() 단일지점서 전 물건 enrich
  → 목록·상세·지도·차익·API 자동 KB반영. web._naver_map() 요청캐시(로컬 store.load_all_naver / 클라우드 store_rest).
- detail.html: KB시세 밴드(하한/일반/상한)+호가+전세 패널 + 출처 pill(KB부동산/국토부추정). base.html CSS(기존토큰).
- store_rest: load_all_naver·upsert_naver·fetch_naver_price. Supabase auction_naver_prices 테이블 생성+2351행 미러.
- 병렬구현: 설계4축→표시·미러2축(담당파일 분리, 충돌0). 468 테스트 통과, 로컬 스모크(삼정그린코아 KB2.95억 렌더).

## 2026-07-15 14:05 KST — 🔍 네이버 매칭 오분류 점검·수정(중간 품질점검)
- 사용자 중간점검 요청. KB시세 vs 감정가 대조: 배율 중앙 0.95·이상치 0(오매칭 거의 없음), 429·오류 0.
- 발견: 짧은 이름 오분류 1건(청라로데오시티포레안→청라봄, partial이 접두 "청라"만 겹쳐도 0.80). name_score
  개선 — partial은 완전포함(≥90)일 때만 신뢰 + 정렬문자비교(어순차이 강건: 시지2차사월↔사월시지2차) +
  음차맵 확장(에쓰제이→sj·엘지→lg 등). 검증 11/11(청라봄 거부, 어순·음차 정상매칭 유지).
- 기존 매칭분 재검사 후 오분류·저점수 재처리(삭제→재수집). 잔여: 차수 모호(노빌리안1↔2)는 인접차수라 영향 작음.

## 2026-07-15 13:35 KST — ⚡ 네이버 수집 속도개선(안전선 내: 중복요청 제거)
- 사용자 "더 빠르게, 근데 크롤링 금지 안 걸리게". 요청 간격 줄이기=429/IP차단 위험이라, 요청 "개수"를
  줄이는 방향으로. 다세대 경매(건물당 2.8세대)·같은 동네 물건이 cortar·KB시세·호가를 재요청하던 것 캐싱.
- crawl_naver Cache에 cortar_pt(좌표 3자리 반올림)·kb(complexNo:areaNo)·arts(complexNo:kind) 추가.
  딜레이 env화(AUCTION_NAVER_MIN/MAX, 기본 2~4s→1.5~3s).
- 검증: 30물건 처리에 9콜(이전 ~60-90콜, 3.3배 감소), 429 0. 캐싱이 KB fetch 안 깨뜨림 확인.

## 2026-07-15 13:10 KST — 🐛 네이버 오피스텔 realEstateType 버그 수정(매칭실패 75% 원인)
- 진단: 수집 중 매칭실패 448건 분해 → 오피스텔 338건(75%)이 원인. 네이버는 아파트(APT)·오피스텔(OPST)이
  별도 realEstateType인데 단지목록을 APT로만 조회 → 오피스텔은 시작부터 못 찾음.
- 수정: crawl_naver._process가 property_type로 kind(OPST/APT) 결정→complexes_in·articles에 전달.
  naver_client.articles에 kind 파라미터. cortar 캐시 키를 f"{cortar}:{kind}"로 분리.
- 검증: 이전 매칭실패 오피스텔 5건 재시도 → 매칭 5/5(한마음·몽삐에뜨골드·스마트시티리버뷰·이지크라운·
  한마음, 전부 1.00), 데이터(KB1+호가4) 5/5. 오피스텔은 KB보단 호가 위주(0이던 게 채워짐).
- 후속: 오피스텔 naver_prices 삭제 후 재수집. 아파트 A유형(맞는단지 면적탈락)은 별도 튜닝 여지.

## 2026-07-15 11:30 KST — 🏢 네이버 KB시세·호가 수집 파이프라인(Phase 1~3 착수)
- 배경: 국토부 통계추정 커버리지 41%·정확도 한계. KB시세(은행기준)를 붙여 밴드·호가·전월세 보강.
- 실증(Phase1): 네이버는 토큰/쿠키 없는 순수 requests를 429 거부 → Playwright 헤드리스로 토큰·쿠키
  확보 후 in-page fetch(page.evaluate)로 200 정상. provider=kbstar로 KB 하한/일반/상한+전세+호가 확보.
- 매핑(Phase2): 좌표→법정동(cortars)→단지목록(regions/complexes)→이름(RapidFuzz token_set/partial
  +음차·접미사 정규화)+전용면적 확증. 2배치 24건 검증: 매칭 24/24, KB 23/24, 데이터(KB+호가) 24/24, 429 0.
- 신규: src/naver_client.py(safety=지터·세션갱신·429백오프·순차·kill-switch NAVER_STOP),
  src/naver_match.py, store.naver_prices 테이블+save/load/이어받기, deploy/crawl_naver.py(진행%·cortar캐시).
- Safety 정책: 순차 전용(병렬 절대 금지), 요청간 2~4s 지터, 80건마다 세션갱신, 429연속=중단.
- 소규모(15건) 테스트: 진행바·저장·이어받기 정상. 실패는 소형 주상복합/사택/미등재(다세대 경매라 세대수
  기준 부풀려짐), 제대로된 아파트는 매칭·KB 정상. requirements에 rapidfuzz·playwright 추가.
- 다음: 전체 2,521건 수집(밤샘 진행% 보고) → Phase4 시세로직 통합(KB 1차>국토부 폴백).

## 2026-07-15 08:20 KST — ⚡ load_live_trades 병렬화(재채점 속도개선) + 스케줄러 충돌 정리
- 문제: 재채점의 열린달 fetch가 순차(약 780콜)라 ~10분+. 또, 05:30 Windows 스케줄러가 수정 커밋
  전 코드로 --nationwide --live 실행 중이라 토지·상가 전국 긁으며 429 폭탄·쿼터경합 유발(kill).
- 수정: load_live_trades를 ThreadPoolExecutor(워커=AUCTION_MOLIT_WORKERS 기본8)로 병렬화 —
  캐시 우선 읽기(메인) → 나머지 병렬 받기 → 닫힌달만 메인스레드 캐시쓰기(WAL+단일writer). apt 쿼터
  정상 확인(land/nrg만 스케줄러가 소진, 수정코드는 미수집). 테스트 468 통과.
- 운영주의: 밤샘 루프는 단일 인스턴스 + 05:30 스케줄러와 시간 겹치면 경합 — 루프 재기동 시 잔여
  run.py/bash 전부 종료 확인.

## 2026-07-15 07:40 KST — ⚡ 국토부 캐시 병렬 워밍 + WAL(속도개선)
- 문제: 순차 워밍이 네트워크 왕복 대기로 느림(콜드 1.5h). 국토부는 courtauction과 달리 anti-bot
  IP밴이 없어(정부 공개 API) 병렬 안전.
- molit_cache.connect에 WAL+busy_timeout(30s)+synchronous=NORMAL → 동시접근 안전.
- 병렬 워머(scratchpad/warm_parallel.py): ThreadPoolExecutor(워커 8) 받기=병렬, 쓰기=메인스레드
  (SQLite 락 회피). 앵커=_prev_month 통일. 검증: 12mo 잔여450건 429=0·DB락=0·실패0.
- night_loop.sh WARM 단계를 병렬 워머로 교체(WARM_WORKERS=8). 운영주의: 밤샘 루프는 반드시
  단일 인스턴스(중복 실행 시 캐시 경합) — bash 오케스트레이터까지 종료 후 재기동.

## 2026-07-15 07:00 KST — 🐛 429 폭탄 원인 2건 수정(앵커 불일치 + 무용 유형 fetch)
- 증상: 밤샘 SCORE 단계가 RTMSDataSvcLandTrade/NrgTrade에서 429 지속(4.5h 정체, est 666 정체).
- 원인①: 워밍 스크립트가 앵커월 202506(하드코딩, 측정스크립트 복붙 잔재) 사용 → 재채점(run.py
  _prev_month=202606)이 쓰는 최근창과 1년 어긋나 캐시 미적중, 최근창 전량 라이브.
- 원인②: load_live_trades가 시세추정 미지원 유형(sh/nrg/land) 실거래까지 라이브 호출 — est에
  안 쓰이면서 쿼터만 소진하고 429 유발(429 전부 land/nrg 엔드포인트).
- 수정: (1) load_live_trades가 아파트·오피스텔 있는 법정동만 호출(supported_lawd 게이트),
  (2) 확장 유형(sh/nrg/land)은 기본 비수집 — AUCTION_FETCH_EXTRA=1 로만 활성, (3) 워밍 앵커를
  _prev_month()로 통일(재채점과 동일). 테스트 468 통과.
- 부수발견: 워밍/재채점 프로세스가 2중 실행되는 현상(SQLite 락경합) — kill로 정리, 재실행 시 단일화.

## 2026-07-15 09:10 KST — 🌙 밤샘 오케스트레이터 + refresh-daily 기본 24개월
- 지난밤 캐시워밍이 네트워크 재연결로 38/129 법정동에서 정체(+워밍 프로세스 2중실행 SQLite 락경합 발견,
  정리). 두밤 연속 네트워크 불안정 → 밤샘 자동재개 루프로 전환(사용자 요청, 휴식 3분).
- night_loop.sh(scratchpad): WARM12→SCORE12→WARM24→SCORE24→PHOTOS→MAINT 단계, 각 멱등·자동재개.
  캐시 성장으로 완성판정, run.py --from-cache --live --live-months N 재채점→auction.db+Supabase 반영,
  crawl_rights --estimable 사진 백필. 정지=COURTAUCTION_STOP, 리포트=scratchpad/night_report.log.
- refresh-daily.ps1 기본 LiveMonths 12→24(캐시가 닫힌달 서빙→깊이↑ 비용은 열린 2개월만). 정기 새로고침도 24개월 깊이.
- 문제: 시세추정 성공이 682/8171건뿐인 최대 원인이 "실거래를 3개월치만 수집"(단지당 거래 희소→
  no_comps). 12개월로 늘리면 429(무료키 일일쿼터)라 불안정.
- 해결: src/molit_cache.py 신규 — (kind,lawd_cd,ymd) 단위 SQLite 캐시(data/molit_trades.db, *.db
  gitignore). 닫힌 달은 1회만 호출·영구재사용, 열린 달(이번+직전)만 재수집. 쿼터 소진돼도 받은 만큼
  캐시→다음 실행 이어받기. load_live_trades를 캐시 기반으로 재작성 + 스로틀(AUCTION_MOLIT_THROTTLE).
- 실증(서울 상위6개 법정동 178개 아파트·오피스텔): est성공 33→52(+58%), 진짜 같은단지 고신뢰
  28→40(+43%), no_comps 79→61. pool 9136→29022건. 캐시 11개월·22386건 저장 확인.
- 배선: refresh-daily.ps1에 -LiveMonths(기본12) 추가→ --live-months 12 전달. 코드 기본값(config
  live_months=3)은 무회귀 유지(테스트 test_confidence_samples 보존). 테스트: molit_cache 4종 + 전체 468 통과.
- 후속: 전국 --from-cache --live --live-months 12 프로덕션 백필(캐시가 쿼터 걸쳐 채움) → 재채점·Supabase 미러.

## 2026-07-14 08:20 KST — ♻️ crawl_rights --estimable 이어받기(resume) + 백필 중단 복구
- 지난 밤 --estimable 백필이 682건 중 329건까지 하고 332번째에서 네트워크(DNS) 단절로 중단
  (getaddrinfo failed, 노트북 오프라인 추정). 코드/크롤러 문제 아님. 사진 329물건/3289장 확보.
- 개선: --estimable을 이어받기 기본으로 — 이미 사진 확보한 물건은 skip(정규화 키). _targets에 skip
  파라미터 추가, main에서 listing_photos 키셋으로 skip 구성. --force로 전량 재크롤 옵션 유지.
  드라이 확인: 이어받기 대상 353건(=남은 것), --force 682건, skip 329건. 테스트 25건 통과.
- 후속: 네트워크 복구 확인(courtauction 200) 후 나머지 353건 이어서 백필.

## 2026-07-13 17:50 KST — 🎯 crawl_rights --estimable 플래그(사진 백필 최적화)
- 문제: 사진 백필에 --all 쓰면 scored 8171건 전부 courtauction 순차크롤 → 수시간+IP밴 리스크인데
  사진은 estimable 682건에만 저장(나머지 ~7500건 헛크롤). estimable이 정렬 앞에 다 모이지도 않음
  (양수차익 339만 앞, 나머지 343은 꼬리에 섞임)이라 --limit로도 못 잡음.
- 해결: `--estimable` 플래그 추가 — estimable_keys(682)만 타겟(limit 무시, refresh 함의). _targets에
  estimable_only 파라미터 추가해 키 불일치 대상 제외. 드라이 확인: 정확히 682건.
- 검증: 앞서 --limit 5 --refresh 테스트로 사진 31장+감정요항 5건 로컬·Supabase 미러·프로덕션 렌더
  확인 완료(2024타경1733 사진12·감정요항, 2025타경500362 사진4). 이어서 --estimable 백그라운드 백필.

## 2026-07-13 17:36 KST — 📘 CLAUDE.md에 "Supabase DDL 직접 실행" 규칙 추가 (사용자 지시)
- 앞으로 이 프로젝트 Supabase(ref `trajmfklbyarbkiljogj`, "Finance AI") 스키마 변경·임의 SQL은 **에이전트가 Management API로 직접 실행**(클립보드로 사용자에게 넘기지 않음). 토큰=`.env`의 `SUPABASE_ACCESS_TOKEN`, 엔드포인트=`POST https://api.supabase.com/v1/projects/{ref}/database/query`. CLAUDE.md 하단에 절 추가. (증거: `select 1` → `[{"ok":1}]` 실행 확인)

## 2026-07-13 17:35 KST — 🧹 .gitignore 정리 + ARCHITECTURE.md 추적 (푸시/배포 전 정비)
- 사진·감정요항 프로덕션 백필 재크롤 전에 깃 상태 정비. 작업트리에 뜨던 로컬 아티팩트를 무시:
  RIGHTS_STOP(크롤 제어파일, AGENT_STOP류) · *.db-shm/*.db-wal(SQLite WAL 임시) ·
  images/(4.8M 스크린샷) · design-refs/(1.5M richgo 레퍼런스). info.md(경매 스터디 로그)는 계속 untracked.
- ARCHITECTURE.md 신규 추적(시크릿 스캔 통과, env 변수명만 언급). 미푸시 커밋 9e829d4(PHOTO_CAP) 포함 push 예정.

## 2026-07-13 17:15 KST — 🖼 사진 물건당 저장 수 확대(3→12) + 전체사진 로컬 export
- 사용자 "사진 다 가져와". 기존 cap=3(대표만) → PHOTO_CAP=12(환경변수, 대부분 물건 전부 커버).
  무제한은 Supabase 공유티어(500MB) 초과 위험(전물건 전사진 1GB+)이라 상한 유지 — 진짜 전부는
  Supabase Storage 이전이 정답(후속 옵션).
- 로컬 확인용: 물건사진/ 폴더에 3개 테스트물건 전체 사진 저장(목동15·두산6·롯데4=25장,
  {아파트명}_{사건번호}_{NN}.jpg). base64→JPEG 디코드.

## 2026-07-13 17:00 KST — 🎨 [개편 Phase 1·2] richgo 스타일 상세페이지 전면 재구성
- 배경: richgo.ai 레퍼런스 개편 plan 승인. 모바일 우선 사진히어로+요약카드+3탭 IA.
- Phase 1(base.html CSS): .ddetail(680px 읽기컬럼) · .dphoto(스와이프 사진히어로+도트) ·
  .dsum(핵심 요약카드 감정/최저−N%/보증금·시세/차익범위) · .tabbar.d3(3탭 풀폭) · .dpane(탭패널)
  · .dtable(임대·배당 표) · .dbottom(스티키 하단바). 기존 토큰 100% 재사용.
- Phase 2(detail.html 전면 재작성): 사진히어로→제목→요약카드→게이트→3탭[경매정보(명세서요지·
  대항력·매수적정성·기일·감정요항) / 시세·수익(실거래차트·차익산출·세금) / 현장·기타(지도·공시자료)]
  →스티키바(관심·법원경매원문). **모든 데이터 바인딩·5-way 매수적정성·차트 렌더러 JS 그대로 보존.**
  탭 JS를 3패널 전환으로 교체, 사진 도트 스크롤 동기화 추가.
- 검증: 실물건 모바일/데스크톱 스크린샷(images/redesign_*.png) — 사진히어로·요약카드·차트(탭전환
  후 정상)·감정요항·스티키바 확인. 사진無 물건 graceful(히어로 생략). 464 passed(라벨테스트 갱신).
- 잔여: 사진은 auction_listing_photos 테이블 SQL + rights 재크롤 백필 필요. 임대수익률·배당표는
  Phase 0-B·3(후속). comps 새로고침 백그라운드 진행중(국토부 429 rate limit).

## 2026-07-13 16:20 KST — 🖼 [개편 Phase 0-A] 물건 사진 파이프라인
- 배경: richgo식 상세페이지 대개편(plan 승인). 사진 히어로용 데이터원 구축. 사진은 pgj15B
  csPicLst.picFile 에 base64 JPEG 인라인 → crawl_rights가 이미 받는 응답에서 추출(추가 요청 0).
- 구현: src/photo.py(Pillow 리사이즈 max_w640·q75, 지연임포트) · courtauction_detail.extract_photos
  · store.listing_photos 테이블+save/load_photos+estimable_keys(시세추정 물건만 저장, 용량억제)
  · store_rest.upsert/fetch_photos + PHOTOS_TABLE · crawl_rights 루프에 사진 수집 연결(estimable만)
  · web.property_detail 사진 로드(로컬/클라우드)→ detail.html photos 전달 · supabase_rights.sql
  auction_listing_photos 테이블.
- 검증: 실 crawl_rights --limit3 → 9장 저장·JPEG유효·load 왕복 OK. 사진 유닛테스트 3종. 462→막 통과.
- 주의: 사진 실제 노출은 Phase 2(detail.html 히어로) + rights 재크롤(백필) 필요. Supabase
  auction_listing_photos 테이블 SQL 사용자 실행 대기(용량 ~700건×3장≈150MB, 공유티어 모니터).

## 2026-07-13 15:40 KST — 🧹 스키마 감사 + 고아 권리 auto-prune(최적화)
- 배경(사용자): "auction 테이블 뭐 있는지 알아? 중복 없고 최적화하면서 해."
- 감사(실측): 로컬 3테이블(scored 8,171·rights 8,923·raw 15,407=로컬전용). Supabase는 auction_
  prefix 2개(scored·rights)로 econ 11테이블과 완전분리. **PK 중복 0**(scored·rights 둘 다). 중복 없음.
- 발견: **listing_rights 고아 1,006건**(scored엔 없는데 rights엔 남은 행). 원인=scored는 매 새로고침
  전량교체(만료제거)인데 rights는 INSERT OR REPLACE라 안 지워져 무한누적. 로컬·클라우드 동일.
- 구현(auto-prune): store.prune_orphan_rights(로컬 NOT EXISTS DELETE) + store_rest.prune_rights
  (RPC 호출) + run.py 풀스냅샷 새로고침 후 로컬·클라우드 양쪽 정리. supabase_rights.sql에
  prune_auction_orphan_rights() RPC 함수 추가(security definer). 함수 미배포면 조용히 skip.
- 검증: 복사본 실측 rights 8,923→7,917(삭제 1,006, 남은 고아 0). prune 유닛테스트 추가. 462 passed.
- ⚠️ 사용자 SQL(누적): ①scored.market_comps jsonb ②rights.appraisal_notes text ③prune RPC 함수.
  세 개 다 supabase_rights.sql/supabase_setup.sql에 idempotent로 준비, 클립보드 복사.

## 2026-07-13 15:10 KST — 🏷 감정 요항 표시(A) + ☁ market_comps 클라우드 미러 배선 수정
- 배경: 사용자 "A(사진·감정요항)부터, 병렬로 빠르게, IP밴 안 나게?". 답 = courtauction는
  anti-bot라 병렬 금지·순차 유지, A 데이터는 이미 받는 pgj15B 응답 안에 있어 추가 요청 0.
- 조사 결과(실측 pgj15B dma_result): 사진은 picFile에 **base64 인라인**(URL 404, 20장×8천건=
  수GB라 저장전략 필요 → 사진은 보류). 감정평가 요항점(aeeWevlMnpntLst)은 텍스트라 즉시 채택.
  임차인 현황표·등기 근저당순서는 JSON에 없음 = 매각물건명세서 전자문서(ecdocId) PDF 파싱 필요(B, 후속).
- 구현(A 감정요항): courtauction_detail에 AEE_ITEM_LABELS(코드→라벨, 미상 폴백) + CaseRights.
  appraisal_notes 필드 + normalize 파싱(빈값 제외) + to_row/from_row JSON 왕복. store: listing_rights
  에 appraisal_notes 컬럼 + 마이그레이션. detail.html '評 감정 요항' 섹션(데이터 없으면 자동 숨김).
  supabase_rights.sql 컬럼+ALTER. crawl_rights는 normalize→to_row 흐름이라 자동 반영.
  실측(대구 1604): 5건 파싱(건물구조·이용상태·설비·제시외물건·**위반건축물 경고**), 렌더 확인.
- ☁ market_comps 미러 버그 수정: store_rest._payload/read 가 _COLS만 써 market_comps를
  **읽기·쓰기 모두 누락** → 재크롤해도 프로덕션 차트에 실거래 점 안 찍히던 근본원인. 양쪽에
  market_comps 반영(jsonb). supabase_setup.sql 컬럼+ALTER 추가. test_upsert_chunks 갱신.
- 검증: 461 passed. 부산 라이브 재스코어 실측 = comps 100% 채워짐(동삼그린힐 실거래 3점 확인).
- ⚠️ 사용자 액션 필요(Supabase SQL Editor): ①auction_scored_listings.market_comps jsonb
  ②auction_listing_rights.appraisal_notes text — ALTER 2줄(파일에 idempotent로 준비됨). 실행 후
  재크롤/새로고침해야 프로덕션에 실거래 점·감정요항 반영.
- 다음: (1)Supabase 컬럼 추가 후 comps 새로고침(--from-cache --live) → 프로덕션 점. (2)권리 재크롤로
  감정요항 소급. (3)B단계=명세서 PDF(ecdocId) 파싱=임차인 현황·등기순서(별도).

## 2026-07-13 13:22 KST — 🔍 홈 단지명·주소 이름 검색 추가('내가 본 그 아파트' 찾기)
- 배경(사용자): 홈에 지역·예산·종류·면적·유찰 '범위 필터'만 있고 아파트 이름으로 찾는 검색이
  없었음. "이 아파트를 내가 봤어" 하고 특정 단지를 바로 찾을 수단 부재.
- 수정(3파일):
  - `query.py`: `matches_query(s, q)` 신설(단지명+주소 부분일치, 공백·대소문자 무시 → '롯데 캐슬'
    ↔'롯데캐슬' 흡수) + `apply_filters(..., q=)` 파라미터 추가.
  - `web.py` 홈(`/`): `q` 파싱, `has_filter`·`filters`에 편입. **이름 검색 시 모수를 evaluable→
    전체(all_scored)로 전환** — 사용자가 본 단지가 시세추정 안 된 유형(빌라·상가)이어도 '없음'이
    아니라 '찾음'이 되게. 다른 드롭다운 필터와 결합 가능.
  - `listings.html`/`base.html`: 검색 패널 최상단에 전폭 검색창(🔍) 신설. 활성 드롭다운 필터는
    hidden input으로 상호 보존(이름+지역 등 함께 좁히기 가능). 결과 헤더에 '‘○○’ 검색' 표기.
- 검증: `pytest tests/test_query.py` 20 passed(이름검색 2케이스 추가). test_client 스모크 —
  샘플 단지명 일부 검색 시 1건 매칭·검색창 렌더·헤더 검색어 표시·없는 이름 빈 상태 메시지 확인.
- 커밋/다음: 로컬 편집(미커밋). 향후 자동완성·초성검색은 데이터량 늘면 검토.

## 2026-07-13 12:35 KST — 📊 상세 그래프 가독성 — Y축 가격 눈금 + 유찰가 표기 + 밴드 시인성
- 배경(사용자 4지적): ①y축 가격이 없어 감정가·유찰가가 얼마인지 안 보임 ②시세밴드가 얇은 선
  하나로만 보임 ③호가 점 안 보임 ④실거래 체결·차익 안 보임. "내가 셋팅한 게 아닌데 왜?"
- 진단: pricechart(feat/price-time-chart, 다른 PC 7/12)는 실거래 점·호가·롤링밴드를 시간축에
  찍는 설계인데, ③호가=수동 스텁(data/asking_prices.json, 미입력) ④실거래 개별점(comps)=현 DB
  미적재라 대부분 레이어가 빈다. +①y축 자체가 없었음.
- 수정(렌더링, detail.html): (1) **Y축 가격 눈금** 좌측 신설 — PLO~PHI 5구간 그리드+억 라벨,
  L 여백 16→48. (2) 유찰 계단 각 회차에 '감정가 1.0억' 식 가격 병기(촘촘하면 회차만, −30%와
  겹침 방지). (3) 현재-시세 밴드 razor-thin이어도 최소 4px 띠로 그려 시인. (4) 상·하한 동일
  반올림 시 '현재 시세' 단일 라벨(전 커밋 이어).
- 검증: 로컬 스크린샷(images/dongsam_yaxis3.png) — 1.0/0.8/0.6/0.4/0.3/0.1억 눈금·1~5차·밴드띠·
  차익 +0.5억 확인. ⚠️Flask debug off면 Jinja auto_reload=False라 템플릿 편집은 서버 재시작 필요.
- 남음(데이터, 렌더링 아님): ③호가=수동 입력이라 자동 안 나옴(별도 크롤러 필요) ④실거래 체결 점·
  롤링밴드=comps 채워야(--live 재스코어). 사용자 결정 대기.

## 2026-07-13 12:00 KST — 📈 상세 그래프 정리(전폭 밴드) + 평수 표시 + 취득원가 문서 정정
- 배경(사용자): 동삼그린힐(2025타경197) 상세 그래프가 이상, 취득원가 계산법 질문, 평수 표기 요청.
- 진단: (1) market_comps(개별 실거래 점) 100% 비어있음 — 현 DB가 _pack_comps 도입 前 스코어링돼
  밴드(하한~상한)만 저장·개별 점 미저장 → 차트에 파란 실거래 점 0개. (2) 이 물건은 감정가
  0.96억 > 시세 0.58억이라, band_env 없을 때 밴드를 '최근 1년 구간 막대'로 그려 유찰 계단을
  가로지르며 떠 있는 것처럼 보였다.
- 수정(차트 detail.html): 실거래 점(시계열)이 없으면 밴드를 감정가/취득원가 기준선처럼 **전폭(L→R)
  참고 밴드**로 렌더. 상·하한이 억 단위로 같게 반올림되면 라벨 하나로 합침('시세 상한/검증 하한
  0.6억/0.6억' 중복 → '현재 시세 0.6억'). band_env(롤링, 실거래 있을 때)는 종전대로 시점 음영대 유지.
- 평수: report._pyeong(㎡→'12.5평', 1평=3.3058㎡), web.py에서 Jinja 전역 등록(1곳). detail 메타
  ·listings 카드/표 3곳에 '41.3㎡ (12.5평)' 병기.
- 취득원가 문서 정정: score.py 모듈 docstring이 '최저가+취득세+명도비+수리비+인수금액'이라 실제 코드
  (real_acquisition_cost=최저가+취득세, 변동비 제외)와 불일치 → 코드와 일치하게 수정. footer는 이미 정확.
- 검증: 로컬 렌더 '41.3㎡ (12.5평)'·'현재 시세' 라벨·전폭 밴드 스크린샷 확인(images/dongsam_chart_fixed.png).
  459 passed. 순수 서빙 계층이라 재크롤 불필요.
- 잔여: 실거래 파란 점은 재스코어(--live molit)로 comps 채워야 표시 — 다음 정기 새로고침에 자동 반영
  (코드는 이미 _pack_comps 로 저장). 즉시 원하면 부분 재크롤 별도.

## 2026-07-13 10:57 KST — 📱 PWA — iOS 홈화면 앱(standalone) 지원 + 앱 아이콘
- 배경(사용자): econ은 홈화면 추가 시 앱처럼(도메인 바 없음)인데 아파트 경매는 사이트처럼 도메인
  노출. 원인 = PWA 메타/manifest/apple-touch-icon 부재(Flask라 자동 없음).
- 구현: static/ 에 앱 아이콘 PNG 3종(180 apple-touch·192·512, 파란 그라데이션+흰'A', Playwright
  렌더 — PIL 없음). Vercel rewrite 가 모든 경로를 Flask 로 보내 정적 폴더 무효 → web.py 명시
  라우트로 서빙(/manifest.webmanifest·/apple-touch-icon.png[변형 포함]·/icon-192·512, 1주 캐시).
  base.html head 에 apple-mobile-web-app-capable=yes(핵심)·status-bar·title'아파트 경매'·
  theme-color·manifest·apple-touch-icon 링크. vercel.json includeFiles 에 static 추가.
- 검증: 로컬 엔드포인트 4종 200(manifest display=standalone·icons 3), head 메타 확인. 459 passed.
- 크롤 루프 완전 종료(RIGHTS_STOP, iter24). 다음: 커밋·푸시·배포 → 폰에서 홈화면 재추가 시 앱 모드.


## 2026-07-13 10:42 KST — 🔎 대항력 판정 근거 표시 (전입일 vs 말소기준일) — 권리 신뢰도 강화
- 배경(사용자): "권리를 잘 읽고 확실한 건 확실하게 — 근저당 이후 전입한 대항력 없는 임차인 등".
  실측 확인: 배당요구여부·전 임차인 현황표는 명세서 '요지'라 없음(전문 PDF 필요). 하지만 말소기준일
  (senior_lien 99%)과 임차인 전입일(surviving_rights 텍스트 31%)은 있어 대항력 날짜비교 가능.
  ※반전: 법원 '인수되는 권리' 필드가 이미 대항력 판정 결과 — 대항력 없는 임차인은 애초 미기재.
  그래서 판정을 뒤집는 게 아니라 그 **근거를 투명 표시** + 모순 검출.
- 구현: courtauction_detail.**analyze_priority()** — 말소기준(유형+날짜)·임차인 전입일 파싱 후 비교.
  verdict 4종: confirmed_opposable(전입≤말소, 근거 확인) / contradiction(전입>말소인데 인수,
  임차권등기명령 등 검증대상) / dates_incomplete(말소만·전입 미기재) / no_basis.
  web.property_detail 이 인수 부담 물건에만 계산·전달, detail.html 명세서 요지 안에 대항력 카드
  (말소기준 vs 전입 날짜 나란히 + 근거문). base.html .dpri 스타일.
- 실데이터 분포(인수 1,402건): 대항력 확정 437 / 말소기준만 952 / 모순 4 / 근거없음 10.
  청주개신 51185: 전입 2022-03-28 < 압류 2023-08-18 → '대항력 있음' 근거 카드 정상 렌더.
- 회귀 테스트 4건 — 전체 **459 passed**. 서빙 계층이라 재크롤 불필요, 전 물건 소급.
- 다음: 커밋·푸시·배포. 더 정밀(전 임차인·배당요구·근저당 순서)은 명세서 전문 PDF 파싱 — 별도 단계.


## 2026-07-13 10:36 KST — ✅ 권리 전수 크롤 체크포인트: rights 8,761건 게이트PASS·Supabase 미러
- 밤샘 루프(iter 24/30) — rights 329→**8,761건** 수집. scored 가 정기새로고침 반복으로 계속
  증가(7,967→8,171)해 미크롤이 400~600 오가는 '움직이는 목표'라, 의미있는 물건(상위·차익 우선)
  전량 크롤된 시점에 RIGHTS_STOP 으로 정지. **전 게이트 PASS 확인 후 최종 미러**(클라우드 8,763).
- 커버리지 분석(권리분석 준비): 인수(대항력) 물건 1,402건 중 전입일 파싱가능 31%(434건)·
  말소기준 99%·둘다있어 대항력 날짜비교 가능 31%(429건)·전입>말소기준 모순후보 4건.
  배당요구여부·전 임차인 현황표는 명세서 '요지'라 없음(전문 PDF 필요).
- 다음: 권리분석 근거 표시 작업(전입일 vs 말소기준 대항력 판정·모순 검출) — 서빙 계층,
  저장된 surviving_rights 파싱이라 재크롤 불필요, 소급 적용.


## 2026-07-13 10:16 KST — 📐 인수금액 정의 정직화: '보증금 상한(배당 0 가정)' + 차익 범위(최악~최선)
- 배경(사용자 질문): "인수금액 정의가 뭐냐, 법원에 잘 나오냐". 실데이터 확인 → 진짜 인수액 =
  보증금 − 배당액인데 **배당액은 명세서 요지에 전혀 없음**(낙찰가·순위 확정 후 결정). 보증금액도
  인수문구 물건의 **48%만** 기재(52%는 '임차권등기 있음'만). 즉 현재 badge.assumed 는 '실제
  인수액'이 아니라 **보증금 전액을 떠안는 최악 상한**. 표기가 과대(보수)였음.
- 사용자 결정: **상한으로 정직하게 표기**(배당 추정 안 함 — 등기부 유료·배당순위 필요, 범위 밖).
- 구현(detail.html·listings.html): 히어로를 단일값→**범위**로 — hero_worst(보증금 전액 인수,
  대표 숫자) ~ hero_best(임차인 전액 배당=인수 0). "실제 인수액 = 보증금 − 배당액, 표시는 배당 0
  가정 최악 상한, 실제 차익은 보통 이보다 큼" 설명 추가. 계산식(최악) 라벨, STEP1 표에 '최대 인수
  가능액(보증금 상한)·최악 차익·최선 차익' 3행 + 주석. STEP3 게이트·목록 '인수금액'→'최대 인수
  (보증금 상한)' 전면 relabel. 금액미상(+α)은 '명세서 미기재 — 등기부 확인'.
- 코드 변경 없음(값 계산 동일, 라벨·프레이밍만) — assumed 는 여전히 상한. 전체 **455 passed**.
  시각 확인(청주개신 51185): −0.92억 최악 ~ +2.38억 최선 범위 정상 렌더.
- 남은 한계(명시): 진짜 인수액은 등기부(유료)+배당순위 계산 필요 — v1 범위 밖. 상한 표기로 정직.


## 2026-07-13 09:41 KST — 🔀 다른PC 개편 pull 병합 + 서빙감사 #19 재발 수정 (가격-시간 차트)
- 배경: 다른 PC에서 10커밋 푸시(가격-시간 차트 pricechart, 지도 3단 개편, 홈 검색 재설계,
  면적·유찰 필터). 로컬은 뒤처져 있었음. **fast-forward pull**(로컬 미푸시 커밋 없음 — 그쪽이
  내 서빙감사 수정 58eb3f9 위에 얹어 작업). 병합 후 전체 **454 passed**로 내 로직 수정
  (파서·배지·매처·정렬) 전부 생존 확인.
- 🐛 병합 회귀 발견·수정: 새 가격-시간 차트(pricemap→pricechart 교체)가 서빙감사 **#19를 재발**시킴
  — 청주개신푸르지오 히어로 **−0.92억**(인수금 3.30억 반영)인데 차트는 **차익 +2.4억**(초록,
  인수금 미반영 보수차익)으로 같은 화면 모순. pricechart.build_timechart 에 assumed 파라미터
  추가 → 유효취득원가(원가+인수금) 기준으로 gain 계산, 밴드하한 이하면 gain=None + assumed_neg
  플래그. web.py 에서 badge.assumed 전달, detail.html 범례에 '⚠ 인수금 N억 반영 시 차익 없음'.
  실측·시각 확인: 초록 차익 브래킷 소멸, 히어로와 정합. 회귀 테스트 1건 — 전체 **455 passed**.
- 크롤: import 체인 호환 확인(새 코드로도 crawl_rights OK), 루프 계속 진행 중(rights 8천대).
- 다음: 커밋·푸시·배포. 권리 크롤 완료 시 최종 게이트·미러.


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

## 2026-07-13 06:58 KST — 🔍 홈 검색 변수 추가: 면적(평대) + 유찰 횟수 (feat/home-area-fails)
- 배경: 홈 검색이 지역·예산상한·종류·정렬 + 칩(인수없음/임박/고차익)뿐 → 부동산·경매 사용자가 먼저
  찾는 '평형'과 '유찰(가격 저감)' 축이 없었음. 실측으로 데이터 커버리지 확인(평가가능 682건 기준
  area_m2 682/682·fail_count 449건>0·최대 15회) 후 도입.
- 무엇: query.apply_filters에 min_area/max_area(전용 ㎡)·min_fails 추가. query.area_bounds(평대
  브래킷 '~20/20/30/40/50plus' → ㎡ 경계, 1평=3.3058㎡). web.index가 area·fails 파싱(헛값 무필터
  흘림) → apply_filters 배선 + filters 딕트 노출. listings.html 폼에 '면적(전용)'·'유찰' 셀렉트 2개
  추가(선택값 유지). 칩 토글은 request.args 기반이라 area/fails 자동 보존.
- 증거: 신규 테스트 5건(area_bounds·area 필터·min_fails·홈 area·홈 fails) + 폼 필드 검증, 전체
  **453 passed, 1 failed**(export read_text py3.12 환경 한정·무관). **Playwright 실 프로덕션 스크린샷**
  (evidence/home_area_fails.png): '30평대+1회+' 필터 → 결과 전부 99~132㎡(30평대)로 정확히 걸러짐 확인.
- 평가자: - (자체 TDD + 실데이터 Playwright)
- 커밋: feat/home-area-fails → main
- 다음: (보류한 후보) 차익률(gap_rate) 필터·예산 하한·신뢰도·인수금액 상한(크롤 후). 사용자가 ①②까지만 지시.

## 2026-07-13 00:09 KST — 🗺️ 지도 3단 스코프(차익 양수만 기본) + pmap 청소 (①②, feat/map-profit-tier)
- 배경: 사용자 지시로 ①지도 기본을 '차익 양수만'으로 조이고 ②죽은 pmap 정리. **다른 세션이 권리분석
  대량 크롤 중**이라 인수금액이 채워지는 상황을 감안하라는 요구 → 정적 목록이 아니라 요청마다 배지에서
  효과 차익을 재계산하는 설계로 크롤과 시너지.
- 판단(실측): evaluable(시세 추정)=861, 표면 차익>0=425, **인수 차감 효과차익>0=365, 금액 확정만=267**.
  즉 인수금액 무시하면 425지만 인수 반영 시 60건이 마이너스, 미상(크롤 미완) 98건 제외 시 267. 크롤이
  미상→확정 채우는 대로 이 집합이 자동 재조정 → 지금 만들어두는 게 정확히 맞물림.
- 무엇(①): query.positive_only(effective=보수차익−인수>0, 미상 부담 제외) 신규. geojson 3단 스코프
  — 기본 profit(267)/ scope=evaluable(861)/ all=1(7,967). feature에 uncertain 플래그. map.html
  3버튼 토글(차익 양수만·평가가능·전체 보기) + 미상물건 '−α(인수 미상)' 표시 + 효과차익 근거 footnote.
- 무엇(②): 죽은 pricemap 제거 — detail.html이 이미 시간축 차트로 교체돼 pmap 미참조였음. web.py
  pricemap import·build·render 인자 제거, src/pricemap.py·tests/test_pricemap.py 삭제.
- 증거: 신규 테스트 3건(positive_only·scope 3단 nesting·feature uncertain) + 기존 갱신, 전체 **448
  passed, 1 failed**(export read_text py3.12 환경 한정·무관). map.html JS node --check OK. **Playwright
  실 프로덕션 스크린샷**(evidence/map_profit_only·map_evaluable): 차익양수만 267핀(부산 245→39로 인수
  리스크 노출)·평가가능 861·전체 7,888핀 확인. 크롤러·재채점 무호출(read-only GET만).
- 평가자: - (자체 TDD + node check + 실데이터 Playwright)
- 커밋: feat/map-profit-tier → main
- 다음: 크롤 완료 후 267 재확인(미상 해소분 편입/제외). Vercel 배포 로그 확보(사용자 CLI).

## 2026-07-12 23:33 KST — 🗺️ 지도 개편: 차익후보 기본 + 지역별 카운트 (③, feat/map-candidates-first)
- 배경: 홈은 '검색 우선 + 평가가능 기본'으로 개편됐으나 지도만 옛 방식(파라미터 없는 geojson 통짜 호출로
  7,888핀 전부, 89%가 회색 노이즈). 결정된 방향(세션 22:56 versions·세션파일 L59)의 두 축이 미착수였음.
- 무엇:
  - query.py: `count_by_sido(items)` 순수함수 신규 — 시도별 건수(최다순, 시도미상은 '기타'로 계상해 총합
    보존). region.sido_of 재사용.
  - web.py: `_filtered`에 `evaluable_default` 파라미터 + `all=1`/`evaluable=` 오버라이드(_truthy) 추가 —
    listings/export 등 기존 표면은 default=False라 동작 불변. `/api/listings.geojson`은 evaluable_default=True
    (차익후보만 기본), 응답에 `by_sido` 집계 + 각 feature.properties에 `sido`(클라 지역필터용) 추가.
  - templates/map.html: '차익후보만 ↔ 전체 보기' 토글(전체=all=1, 미지원 회색범례 노출), '지역별' 카운트 칩
    바(클릭 시 해당 시도로 필터+줌, '전체'로 리셋), layerGroup 재조회 구조로 리팩터(모드/지역 전환 시 핀 교체).
- 증거: 신규 테스트 5건(count_by_sido 1 + geojson by_sido/evaluable기본/feature.sido/맵패널 4) 통과, 전체
  **453 passed, 1 failed**(export read_text(newline=) py3.12 환경 한정·무관). map.html JS `node --check` OK.
  **Playwright 실 프로덕션(Supabase) 스크린샷 3장**(evidence/map_candidates·map_all·map_seoul.png): 차익후보
  861핀·전체 7,967(세션 데이터 진단과 정확히 일치)·서울칩 클릭→834핀 서울 줌 확인.
- 평가자: - (자체 TDD + node check + 실데이터 Playwright)
- 커밋: feat/map-candidates-first → main
- 다음: (선택) 차익후보를 '차익양수만'(425)으로 더 좁히는 2단 필터·잔존 pmap.py 정리. Vercel 배포 로그 확보.

## 2026-07-12 22:56 KST — 🔎 홈 검색 우선 재설계 (무지성 나열 → 큐레이션, feat/search-first-home)
- 배경(데이터 진단): 홈이 7,967건을 통짜 나열 → 실은 **89%가 평가 불가**(미지원유형 69.5%·시세추정불가
  19.7%, 전부 '—'). 실제 신호는 시세밴드 861건·차익 양수 425건뿐. 지도 '빈 지역'도 크롤/좌표 문제 아님
  (전국 7,888핀·좌표 99%·전 시도 수백 건) — 차익 후보(425)가 원래 희소한 것. (프로덕션 API 실측)
- 사용자 확정: ①평가 가능한 것만 기본 ②검색 우선 ③지도=차익후보+지역카운트(지도는 별도 예정).
- 무엇:
  - query.py: `is_evaluable`(시세 추정분)·`is_soon`(임박 7일)·`is_high_profit`(2억)·`days_until` 순수함수,
    apply_filters에 예산(max_bid/min_bid)·evaluable_only 추가.
  - web.py index 전면 재작성: 기본=평가가능만 + 필터 없으면 '엄선 추천'(검증 비교군·비위험·차익양수 상위 9),
    검색(지역/예산 8천~8억+/종류)·빠른진입 칩(인수없음·임박·고차익·관심지역, 토글 URL·카운트),
    '전체 탐색'(all=1)=미지원 포함 기존 밀집 테이블. 커버리지 통계 노출.
  - listings.html 재작성(검색 패널·칩·커버리지·추천 카드 그리드 / all_mode는 기존 테이블 보존, /digest
    재사용 위해 coverage·chips 미전달 시 방어), base.html 검색·칩·카드 CSS. CSV 내보내기 유지(쿼리 보존).
- 증거: query 신규 6건 통과. 전체 **448 passed, 1 failed**(export read_text(newline=) py3.12 환경 한정·무관).
  Playwright 실 렌더 스크린샷 확인(검색·칩·커버리지·추천 카드 정상, 위험 물건 추천 제외 검증). 옛 히어로/라벨
  검사 테스트 4건은 새 표면(추천 카드·상세·all_mode)으로 재작성.
- 평가자: - (자체 TDD+스모크+스크린샷)
- 커밋: feat/search-first-home → main 병합 예정
- 다음: 지도(③ 차익후보 중심+지역 카운트) 개편. 배포는 CLI(vercel --prod, 사용자 실행). econ-dashboard 대기.

## 2026-07-12 16:54 KST — 📉 상세 가격 시각화 세로/시간축 전면 개편 (가격-시간 차트, feat/price-time-chart)
- 무엇: 가로 스냅샷 막대(pmap)를 **네이버부동산형 가격-시간 차트**로 교체. 사용자 UX 피드백(가로 초록봉 오독·
  값의 시점 부재)에서 인터랙티브 목업 v1~v5 반복 확정 → 세로/시간축·롤링 밴드·유찰 저감 드롭마커·호버 툴팁·
  2020~ 맥락 토글로 합의 후 이식.
- 파일:
  - `src/pricechart.py`(신규): 순수함수 `build_timechart(s, comps, schedule, asks, recency_months)` — 개별 실거래
    (comps)·기일이력·호가 → JSON dict(trades/hist 최근창 분리·롤링 band_env·band_now·유찰 step·levels·gain·cheap_pct).
    정책: 밴드·차익은 최근 12개월만 산정, 다년치는 맥락(hist)으로만(사이클 오염 방지).
  - matcher `MarketEstimate.comps`(_pack_comps: 날짜 있는 매칭 최신순 60) / models `ScoredListing.market_comps` /
    score `score_listing(comps=)` 캡처 / pipeline 배선.
  - store **스키마 v6**: `market_comps TEXT DEFAULT '[]'` + v5→v6 ALTER 마이그레이션 + JSON 직렬화/역직렬화.
  - web.py: 상세 라우트에서 build_timechart 호출→`chart` 전달. detail.html: pmap 블록→`ptc`(JSON 임베드+SVG 렌더러
    JS: 점·롤링밴드·유찰드롭·기준선·차익괄호·호버·십자선·기간토글, 우측라벨 de-collision). base.html: ptc CSS·밴드 토큰.
- 증거: pricechart 13건 + store comps/마이그레이션 3건 통과. 전체 **432 passed, 1 failed**(export read_text(newline=)
  py3.13 API — 시스템 py3.12 환경 한정·무관). 렌더러 `node --check` OK. **Playwright 실 스크린샷 검증**(상계주공 실
  pricechart 데이터): 초판서 우측 라벨 충돌 발견→de-collision 로직 추가 후 재검증 정상. 샘플 상세 3건 200.
- 평가자: - (자체 TDD+스모크+스크린샷)
- 커밋: feat/price-time-chart → main 병합 후 커밋·푸시·배포
- 다음: **라이브 새로고침 필요** — 기존 DB 행은 market_comps='[]'(마이그레이션 기본)라 개별 실거래 점 비어 있음.
  재채점해야 실 comps·다년치 채워짐. 호가 시점(observed_at)은 수동입력 스텁(Q2). 잔존 pmap.py는 후속 정리 대상.

## 2026-07-12 16:30 KST — 🔧 서빙 감사 확정 24건 전면 수정 (배지·정렬·매처·렌더)
- 사용자: "ㅇㅇ 고쳐". 크롤 루프 도는 중 서빙 결과물 감사 24건 수정(#7·#12는 정상확인).
- **권리 금액 파서 대수술(courtauction_rights.py)**: #0 백/천 혼합 한글단위('1억9천5백만원'=195M)
  통합 _korean_won/_mixed_man + _MONEY_RE 스캐너로 순수숫자·한글 모두 처리. #15 인수 직접부정
  ('인수하지 아니함')을 이중부정보다 우선 제외. #16 한줄 다건 임차권 distinct 합산 +'중 미반환 Y'
  채택(_RESIDUAL_RE). #9 detect_deposit_amount 신설(금액미상 부담물 보증금 보수추정).
- **배지(courtauction_detail.py)**: #14 최선순위 전세권 burden+special '선순위전세권'. #22
  is_substantive startswith→전체일치(‘해당사항없음 다만 인수’ 함정 제거)+surviving_is_real
  프로퍼티로 배너·배지 판정 통일. #13 is_empty 빈명세서 배지 미생성(web 목록·상세 양쪽 가드).
- **정렬(query/web)**: #1·#9 uncertain_of — 인수 부담+금액미상(+α) 물건을 별도 하위 티어 강등.
  실측: 성원742(유찰4회 임차권 미소멸) 1위→**208위**, 상위8 전원 clean+같은단지.
- **매처(matcher/fields, 재채점 시 반영)**: #8·#10 폴백 전용 감정가 가드(1.5배 초과·0.6배 미만
  무효). #2 마을명 다단지(N단지/N차 2종 이상)면 same_complex 강등. #3 신청채권자 매수신청액을
  유효 최저입찰가로(_creditor_bid_floor). #4 단월 집중 comps는 백로그(신뢰 signal 미도입).
- **렌더(detail/listings/pricemap/report)**: #18 STEP3 게이트가 badge burden 반영(초록✓→주의).
  #19 pricemap 유효취득원가(인수금 포함) 기준 차익구간·STEP1 인수차감 행. #20 +α 문구 사유별
  (지분·전세권 등). #21 유찰·저감 라벨 모순(가격재설정/재매각). #23 won() '-0.00억' 정규화.
  #6·#17 명세서 크롤 완료물 '권리미확인'칩→'명세서 확인됨'. #8 목록 폴백 '참고치' 표기.
- 검증: 실데이터 배지 교정 확인(전세권→burden·빈명세서→미확인·이중부정 4.5억·성원 208위),
  전 페이지 렌더 200, 게이트 PASS. 회귀 테스트 10건 추가 — 전체 **427 passed**.
- 반영 시점: 서빙(배지·정렬·템플릿·파서)=배포 즉시. 매처(시세 est/scope)=다음 05:30 재채점.
- 다음: 커밋·푸시·배포. 권리 크롤 루프 계속(rights 592→). 매처 수정 검증은 재채점 후.

## 2026-07-12 16:30 KST — ✍️ UI 문구 톤 정리 31건 (구어체·질문형·게임은어 → 명사구·격식체)
- 무엇: 4개 파일그룹 병렬 감사(다관점)로 childish/구어체 문구 31건 확정 후 전량 수정. 핵심 오프렌더 —
  detail.html 스텝/게이트 제목("얼마가 남는가"→"예상 차익 산출", "세금·비용 빼면 진짜 얼마"→"세금·비용 반영 순차익",
  "사도 되는가"×4→"매수 적정성"), guide.html 세금·권리 섹션(게임은어 끝판왕·세율폭탄·지뢰밭·두 개의 세계·심장부,
  대화체 넘기세요/~하는 게 정답 → 중립용어·격식체), listings "이게 전부(인수 없음)"→"인수 부담 없음",
  watchlist/compare/calendar/map 빈상태·컬럼헤더 정리, report.py 밴드게이지 판정캡션·히어로설명, digest.py 제목("이번 주"→"주간").
- 톤 가이드라인 6종 확정: 제목=물음표 없는 명사구 / 강조필러(진짜·딱·무조건) 금지 / 1인칭·대화체 금지 /
  게임은어·극적비유 금지 / 라벨 중복설명 금지 / 앱 전체 동일표기 통일. 데이터라벨·컬럼헤더·면책은 이미 전문적이라 미변경.
- 대상: templates(detail·listings·guide·watchlist·compare·calendar·map) + src(report·digest). 로직/수치/의미 불변, 문체만.
- 증거: 문구 관련 테스트 갱신 2건(test_digest '주간…', test_web '매수 적정성') + 서빙 확인(guide·detail 200,
  신문구 반영·구문구 제거 True). 전체 411 passed. 잔여 실패 6→환경(pyproj 미설치 5·read_text(newline=) py3.13 API 1)로
  본 변경과 무관(pyproj 설치 후 5건 해소, 1건은 py3.12 로컬 한정).
- 평가자: PASS (문구 관련·서빙 검증 통과)
- 커밋: origin/main(58eb3f9) 재베이스 후 커밋·푸시
- 다음: 권리 미수집(≈96%) 커버리지 확대는 별건(로컬 auction.db 부재로 crawl_rights --all 불가, 경로 A/B 대기).

## 2026-07-12 16:06 KST — 🌙 권리 전수 크롤 밤샘 루프 시작 + 서빙 감사 24건 확정(별건)
- 배경(사용자): "로컬 auction.db에 루프 돌려 crawl_rights --all 로 모든 물건 권리 나오게 + 깃헙 푸시".
  다른 PC에서 클라우드 모드로 이어가려 Supabase env도 요청(전달 완료).
- 밤샘 데이터 상태: 05:30 정기 새로고침이 max-pages 상향(10→25, 샤딩잘림 수정) 반영해 돌면서
  **scored 3,565 → 7,967건**(전국 커버리지 확대). 미크롤 distinct 7,638건 → 예상 ~11.7시간(500캡×~17회).
- 루프: scratchpad/rights_loop.sh 백그라운드 — crawl_rights --all 반복(회당 최대 500, 3~8s 스로틀),
  각 회차 끝 게이트 검사 후 Supabase 미러(증분 클라우드 반영 = 다른 PC 즉시 수신). 미크롤 0/차단/
  RIGHTS_STOP 시 자동 중단. 시작 전 **전 게이트 PASS 확인**(신규 7,967 데이터도 무결).
- ⚠️ 별건: 서빙 결과물 6관점 감사(78 에이전트) **확정 24건**(CRITICAL 4·HIGH 8, docs/audit-serving-
  20260712.json). 핵심 = 배지 판정 버그(배지는 저장이 아니라 summarize() 실시간 계산이라 크롤과
  독립, 파서 수정 시 전 배지 소급 교정): ①백단위 금액('1억9천5백만원')·개행분리 인수금 0원 파싱
  (2·25위 실질음수가 양수로) ②전세권 최선순위 5건 전부 clean 오판 ③빈 명세서가 '인수없음'으로
  (is_empty 커밋본은 신규크롤 방지, 구 329건 잔존) ④갑오마을류 '같은단지' 라벨이 실은 다른 단지
  시세(+38%) ⑤금액미상 인수(+α)가 무차감 1·2위 ⑥한줄 다건 임차권 최대1건만(3.9억 과소).
  → 크롤 완료 후 별도 수정 예정(사용자 확인). 크롤은 원문 정확 저장이라 무영향.
- 다음: 크롤 완료 대기 → 최종 게이트·미러 확인 → (사용자 승인 시) 서빙 감사 24건 수정.

## 2026-07-11 17:4x KST — 🚀 재검증 수정분 커밋·푸시·배포 완료 (c49ea16)
- 전량 재채점 완료(3,565건, 공고가 기준) → **전 게이트 PASS → 클라우드 자동 미러**(3,565 대조 일치).
- 프로덕션 라이브 검증: 범어 최저가 23,444,000(공고가, 구 33,492,000에서 교정) ·
  군산 20237 = burden·인수 4.5억 정확(이중부정 수정 라이브) · API burden_status/assumed_amount
  필드 · 새 상위권 전원 same_complex · 전 라우트 200.
- git push c49ea16(main) · Vercel prod READY. 신규 상위권 권리 크롤(150건) 백그라운드 진행 중
  — 완료 시 게이트 검사 후 자동 미러.

## 2026-07-11 17:17 KST — 🔬 재검증 감사 확정 26건 수정 (62 에이전트 만장일치, docs/audit-reverify-20260711.json)
- 재검증 결과: 미검증 31건 → **확정 26 / 기각·이미수정 5**. 확정 전건 수정(백로그 3건 제외).
- **idx15 CRITICAL — 최저입찰가가 '직전 회차' 가격**: minmaePrice ≠ 공고가. 실측 **73.6%**
  (5,679/7,712)가 다가오는 기일의 실제 공고가(notifyMinmaePrice1)와 불일치 — 차익 과소
  (보수 방향)였지만 가격 자체가 틀림. → parse_row 공고가 우선 + fixture 테스트 3건 갱신
  + **전량 재채점 실행 중**(--from-cache --live, 게이트 PASS 시 자동 미러).
- **권리 문구 파서 5종(idx7·8·9·10·11·12)**: ①이중부정 "말소되지 않고 …인수함"이 negation
  '말소'로 오판돼 4.5억 인수금이 0원(군산 20237, score 92.8 노출) → _DOUBLE_NEGATION 선판정
  ②한글 단위 금액(4억5,000만원/금1억/6,500만원) 전혀 미파싱 → _KOREAN_AMOUNT_RE ③"인수하지
  아니함"(인수 0원 확정)이 opposable 오탐 → 인수-해소 절 제거 ④'임차권등기'가 말소동의·대항력
  포기 문맥에서 15건 오탐 → 강/약 신호 2단계(약한 신호는 해소표현 존재 시 억제) ⑤'부분의'가
  지분 오탐 → 'N분의 M' 정규식 ⑥다건 보증금 max→distinct 합산. **실측 검증: 군산 20237
  이중부정 4.5억 정확 파싱(0→450,000,000), 금액 파싱 33→38건.**
- **idx0 CRITICAL — digest 추천 TOP도 인수 게이트**(TOP10 중 9건 burden, 7건 실질 음수였음):
  top_listings(badges) — burden 제외, hero 는 전 항목에서 처리됨.
- **idx21 CRITICAL — 샤딩 잘림**: 기본 max-pages 10→25(부산 등 대형 시도 매일 수백 건 침묵
  누락), 잘림 로그 INFO→WARNING 승격(누락 건수 명시).
- **idx16 — rights 폴백 전면 제거**(형제 물건 명세서 과신 15건): 정확 매칭만. idx17 — 빈
  pgj15B 응답 저장 스킵(CaseRights.is_empty, clean 오판 방지). idx18 — crawl_rights 잔여
  배치 finally 저장(유실 방지). idx22 — 오염 rights 2건(광주·인천, 발견 수치 일치) 삭제.
- **idx2 — /stats 권리 도넛 정직화**: '확인 완료 96%'(grade 기반 왜곡) → 명세서 실수집 기준
  3분할(확인·인수없음 / 확인·인수부담 / 미수집). idx4 — CSV(인수부담·인수금액·유효차익 3열)·
  API(burden_status·assumed_amount)·geojson(유효차익+burden) 전부 인수 반영. idx5 — clean
  필터에서 무시세 물건 제외. idx26 — 만료(기일 경과) 물건 크롤 단계 제외. idx28 — 단지명
  한↔영 브랜드 정규화(엘에이치↔LH 등 6종). idx29 — lawd_cd 콤마 오염 정제.
- 백로그(수정 보류): idx25 캐시 diff 변경감지(정보성 로그 한정), idx27 비부동산 혼입(기타
  유형이라 시세추정 미지원 — 무해), idx19 rights 정기 refresh 정책(스케줄러에 --refresh).
- 테스트: 파서 회귀 8건 + 정책 갱신 5건(notify1 fixture·stats 도넛·fetch 정확매칭) —
  전체 **417 passed**.

## 2026-07-11 16:57 KST — 🔧 감사 후속: 복합키 전면 전환 + 순위-표시 일치 + hero 인수게이트
- 배경(사용자): 남은 확정·미검증분도 전부 진행. 미검증 31건은 재검증 워크플로(재현+반증
  만장일치, 62 에이전트) 백그라운드 실행 중 — 확정분은 후속 항목에서 수정.
- **순위-표시 일치(감사 HIGH)**: 기본 정렬·최소차익 필터가 저장 profit_low 가 아니라
  **인수금 차감 후 유효 차익** 기준(query.sort_items/apply_filters 에 burden_of 파라미터,
  web._filtered 배선). 인수 5억 물건이 표시 −2.96억인데 상위 랭크에 남던 역전 해소 —
  실측: 이전 1·3위(청주개신/반월당, 인수 존재)가 유효 차익 순위로 재배치.
- **hero 인수 게이트(감사 CRITICAL 후보)**: 히어로 선정에 채점 게이트 + **명세서 clean 확인**
  요구(badge 없거나 burden 이면 제외, 배지 데이터가 전무한 환경은 기존 동작 유지).
- **복합키(court|case_no|item_no) 전면 전환(감사 HIGH — 동명 사건 67건·다물건 사건)**:
  watchlist(wl_key/is_watched, 스냅샷 키, detect_changes 레거시 폴백+표시용 case_no 보존,
  toggle/api 라우트 court/item 파라미터, 템플릿 hidden 필드) · compare(select_for_compare
  복합키+레거시 겸용, /compare 링크 복합키 생성, missing 계산 갱신). 구 파일(bare case_no)
  하위호환 유지.
- **감사 L1 마무리**: '대지권미등기'도 시세 추정 금지(지분과 같은 온전 소유권 아님 계열,
  scope=share_sale 재사용, 라벨 일반화).
- 테스트: 복합키 토글/레거시 매칭/대지권미등기 신규 3건, 스냅샷 하위호환 폴백 —
  전체 **410 passed**.
- 다음: 재검증 워크플로 결과 수신 → 확정건 수정 → 게이트 PASS → 커밋·푸시·배포.

## 2026-07-11 16:20 KST — 🔬 다관점 감사 확정 13건 전면 수정 (7관점×적대검증, 사용자: "모든 배치 PASS면 진행")
- 감사 결과: 원발견 50건 → 재현자+반증자 **만장일치 확정 13건**(CRITICAL 3·HIGH 6·MEDIUM 3·LOW 1),
  기각/미검증 37건(상당수는 API 세션한도로 verify 미완 — 보고서 docs/audit-crawl-20260710.json).
- **C1(CRITICAL) 유형분류 근본 결함**: dspslUsgNm 은 물건 용도가 아니라 법원 검색 **그룹명**
  ("상가,오피스텔,근린시설" 889건 → 전량 '오피스텔' 분류 → 근생·사무소·지식산업센터 호실이
  오피스텔 시세로 차익 산정, 35% 오염). 카테고리 '아파트' 안의 오피스텔 코드 물건 4건이
  아파트 시세로 rank 10·11·26 노출. → **sclsUtilCd(세부용도코드)를 1차 분류 소스로 전환**
  (_SCLS_TYPE/_SCLS_PREFIX_TYPE + classify_by_scls), 코드 미상+콤마 그룹명은 '혼합'(추정 미지원).
  '아파트형공장' 데드룰도 선순위로 수정.
- **C2(CRITICAL) 타지역 동명(洞名) comps 혼입**: trades 전국 풀에서 '동 이름'만 비교 —
  서울 신정동↔대구 신정동 실거래 혼입 구조. → Trade.lawd_cd 신설, molit fetch 시 태깅,
  matcher 풀 필터에 같은 시군구 제약(레거시 빈 값 통과).
- **C3(CRITICAL) crawl_rights 조인 court 누락**: 타법원 boCd 로 엉뚱한 사건 권리 크롤·적재.
  → 조인에 court 추가 + 동명사건(73개 case_no) rights 3건 삭제(재크롤 대상).
- **H1 목적물(mokmulSer) 다중 행**: doc_id dedup 이 건물행·토지행을 모두 살려
  INSERT OR REPLACE 마지막 행 승리(순서 의존) — 아파트 3건이 토지로 강등된 원인.
  → merge_mokmul_rows(건물행 우선 병합) 신설, run.py 채점 직전 적용(raw 보존은 전 행 유지).
- **H2 coords case: 폴백 court 누락**(인천 물건이 대구·전주 핀) → 키를 case:<court>|<case_no> 로.
- **M1 기본정렬 scope 미반영**(상위 50 의 84%가 '추천 금지' fallback) → sort_items 기본 정렬을
  [같은 단지(0) → 레거시(1) → 동 폴백(2) → 시세 없음(3)] 티어 내 차익 내림차순으로.
- 게이트 강화: gate_scls_consistency 신설(시세 물건의 코드-유형 모순 — 첫 실행서 25건 적발),
  gate_type_physical 을 다중행 그룹 판정으로 개선(건물행 있으면 정상).
- 데이터 정정: scls 모순 25건 유형 교정+시세 무효화, 동명사건 rights 3건 로컬·클라우드 삭제,
  무효화 행 전체(2,828) Supabase 재미러. **전 게이트 PASS 확인 후 미러**(사용자 정책 준수).
- 테스트: 신규 4건(scls 분류·혼합·데드룰·mokmul 병합 양방향) + 구버그를 정답으로 고정하던
  test_classify 1건·coords 키 2건 정책 갱신 — 전체 **407 passed**.
- 남은 확정: H3(compare/watchlist 등 case_no 단일 키 표면 — 구조 변경 커서 백로그),
  L1(대지권미등기 게이트 미발동 2건 — share_sale 게이트가 커버 중). 기각 37건 중 세션한도
  미검증분은 차기 감사에서 재검증.

## 2026-07-10 17:13 KST — 🛡️ 품질 게이트 도입 + 크롤 논리 다관점 감사 (사용자: "이런 오류 재발 금지, 전 배치 PASS여야 진행")
- 배경(사용자): 침산동류 오류를 사람이 눈으로 잡는 구조 금지 — 크롤·검증 논리를 여러 구조로
  나눠 다양하게 검사하고, 모든 배치가 맞다고 할 때만 진행하라.
- **src/data_gates.py 신설** — 독립 관점 8종 배치(유형-물리 정합 / 가격 괴리(시세>감정×2.5) /
  조인 무결성(court 복합키) / 지분·건물만·대지권 문구 / 만료 매물 / PK·가격 sanity /
  권리 JSON 무결성 / 밴드 순서 불변식). 게이트 예외도 FAIL 로 표면화(조용한 통과 금지).
  deploy/validate_data.py 실행기(종료코드로 차단 신호). **run.py·crawl_rights.py 미러 직전
  배선 — 전 게이트 PASS 아니면 Supabase(서빙) 반영 차단, 로컬만 유지.**
- 게이트 첫 실행이 즉시 잡아낸 추가 오염 **38건**(어제 5건 수정은 빙산의 일각이었음):
  ①유형-물리 29건(주거 전 유형으로 확대 시 — 어제는 '아파트' 용도명만) ②비고 '지분매각'인데
  온전가 시세 매칭 4건 ③감정가 괴리 5건 — **금오아파트(사용자에게 소개했던 물건!) 감정 1.4억
  →est 6.3억(4.5배), 신천엘에이치 1.2억→13.7억(11배)**: same_dong_fallback 이 같은 동
  '다른 단지(신축)' 실거래로 시세를 만드는 구조적 함정.
- **matcher.py 원천 방어 2종**: (1) special_rights '지분' → 시세 추정 금지(scope=share_sale)
  (2) est > 감정가×EST_VS_APPRAISAL_MAX(2.5) → 시세 무효화(scope=appraisal_mismatch, 경고 로그).
  신규 scope 2종 detail.html 라벨 등록. 38건 일회성 정정(무효화) + Supabase 미러.
- 검증: 정정 후 **전 게이트 PASS**. 테스트 신규 13건(matcher 회귀 4 + 게이트 9: 위반 검출/클린
  통과/게이트 크래시 표면화/scope 라벨) — 전체 **401 passed**. 프로덕션 확인: 금오·신천
  appraisal_mismatch 강등, 새 상위에 same_complex 물건 부상.
- 진행 중: ultracode 다관점 감사 워크플로(7관점 find→만장일치 verify) 백그라운드 — 확정 발견
  나오면 추가 수정 예정(이미 자체 발견·수정한 항목: rights 폴백 court 무시 건은 워크플로 결과
  대조 후 처리).

## 2026-07-10 14:3x KST — 🐛 나대지가 '아파트'로 둔갑한 허상 차익 수정 (사용자 검증 지적)
- 발단(사용자): "1번 침산동 아파트(차익 4.45억) 말이 안 되잖아, 진짜 인수 없는 거 맞아?"
- 진단: 감정가 1.5억 vs 시세 5.2억 괴리 → 상세 API 감정평가 요항 실측 — **"세장형의 토지로서
  주거나지임"** = 침산동화타운 '인근' 나대지 109㎡. 법원 데이터가 용도명(dspslUsgNm)을
  '아파트'로 등록 → classify_property_type 이 용도명만 신뢰 → 침산동 아파트 실거래와
  오매칭(same_dong_fallback·표본3) → 4.45억 허상 차익이 랭킹 2위 노출. **인수권리 없음(clean)
  판정 자체는 정확** — 문제는 물건유형 오분류.
- 영향 범위(정밀 스캔, court 포함 조인 — 초기 스캔은 타법원 동명사건 오탐 31→**실제 5건**):
  용도명 아파트+건물표식 전무+지목 있음 = 5건, 그중 시세 오매칭 허상 차익은 침산동 1건뿐
  (나머지 4건은 면적 커서 매칭 실패로 이미 무해).
- 수정: courtauction_fields.**verify_property_type()** 신설 — 주거유형인데 건물 표식
  (buldNm/buldList/pjbBuldList) 전무 + 지목(jimokList) 존재 → '토지' 교정(미지원유형 정책 적용).
  parse_row 에 배선. 기존 DB 5건(scored 4행) 정정(토지·미지원유형·시세/차익 NULL·scope=
  unsupported) + Supabase 미러. 유닛 회귀 1건 추가 — 전체 **388 passed**.
- 검증: 프로덕션 재조회 — 침산동={type:토지, grade:미지원유형, profit:None}, 랭킹 상위에서 강등.
  새 상위 = 범어(+α경고)·금오·청주개신푸르지오.
- 교훈: 법원 용도명 단독 신뢰 금지 — 물리 신호(건물표식·지목) 교차검증. 유사 패턴(감정가 대비
  시세 3배↑ 괴리 게이트)은 후속 검토.

## 2026-07-10 14:08 KST — 💰 예상 투입 분리: '낙찰가만 내면 끝' vs '인수 부담' 물건 구분 (사용자 요구)
- 배경(사용자): 명세서 인수 정보를 각 물건에 넣어 "실제 유찰가 / 예상 투입 2개로 나눠 —
  낙찰만 하면 따로 돈 낼 거 없는 물건과 아닌 물건을 바로 구분".
- 판정: courtauction_detail.**summarize()→RightsBadge** — clean(명세서 확인, 인수신호 없음) /
  burden(대항력·인수문구·특수권리·인수금액, 금액 미상이면 amount_unknown=+α). 판정 재료는 기존
  courtauction_rights 파서 재사용, 보수 원칙(애매=burden).
- 목록: **예상 투입 컬럼 신설**(입찰가+취득세+인수금) — clean="이게 전부(인수 없음)" /
  금액파싱="인수금 포함"(빨강) / 미상="+α 보증금 인수 별도"(빨강) / 미크롤="인수 미확인".
  칩 3분류(✓인수 없음 초록 / ⚠인수 금액·+α 빨강 / 권리미확인). **예상 차익 = 시세−예상 투입**
  으로 인수금 반영(미상은 "−α 인수 미반영⚠" 병기), 반영 후 ≤0이면 dim. 모바일 3-stat도 투입 중심.
  **"인수 없음만" 필터 체크박스**(clean=1, 미확인은 보수적으로 제외). 히어로에도 칩.
- 상세: 히어로 차익에 인수금 차감 반영(+계산식에 "−인수금액"/"−α" 항), 미상이면
  "실제 차익은 이보다 작습니다" 경고.
- 배선: store.fetch_all_rights / store_rest.load_all_rights(TTL캐시)+upsert_rights 캐시무효 /
  web._rights_badges()(court|case_no|item_no 키) → index·digest 렌더에 badges 전달.
- 크롤: 상위 200건 배치 완료(194행, 실패 0). 판정 분포 = **clean 99 / 인수+금액 33 / 인수+미상 62**
  — 상위 차익의 절반이 인수 부담(기능 존재 이유 실증). Supabase auction_listing_rights 미러
  194건(사용자 DDL Run 후, 재조회 대조 일치).
- 검증: 육안 — 8위 반월당효성 인수 5.00억 반영 → 차익 2억대→**−2.96억 손실 반전**(dim),
  11위 트윈스 인수 1.3억→0.63억 축소. clean=1 → 정확히 99건. 테스트 신규 3건(summarize
  clean/burden금액/burden미상) — 전체 **387 passed**. 프로덕션 배포·라이브 확인(투입 컬럼 전 행·
  보증금별도 62·clean필터 99·상세 인수경고).
- 다음: 크롤 커버리지 확대(일 500 cap 내 스케줄러), 명세서 임차인 표(보증금 금액) 추가 수집 검토.

## 2026-07-10 13:50 KST — ⚖️ 권리분석 연결: 법원 매각물건명세서 요지 + 기일 역사 (사용자 요구)
- 배경(사용자): "유찰 10회짜리 말도 안 되는 물건들 — 권리분석 왜 연결 안 했냐. 딱 보자마자 권리
  내역(근저당 언제, 임차 등) 나오게 해달라."
- 데이터 원천 확보: 법원경매정보 물건상세 JSON API **실측 발굴**(Playwright XHR 캡처 →
  `POST /pgj/pgj15B/selectAuctnCsSrchRslt.on`, 미니멀 페이로드 csNo+cortOfcCd+dspslGdsSeq+pgmId
  로 requests 동작 검증). 응답에 매각물건명세서 요지(ndstrcRghCtt 인수권리 /
  tprtyRnkHypthcStngDts 최선순위=말소기준 / sprfcExstcDts 유치권)·청구금액·배당요구종기·기일역사.
  기일 결과/종류 공식 코드표(sccd/list.on)도 실측 확보. ※등기부 전체 역사는 인터넷등기소 유료라 제외.
- 구현: courtauction_client.case_detail(기존 스로틀·서킷·kill-switch 경유, _post url/validator
  파라미터화) · **src/courtauction_detail.py** 신규(normalize→CaseRights, 코드표 매핑, 기일 최신순)
  · store.py listing_rights 테이블+save/load · store_rest upsert_rights/fetch_rights ·
  **deploy/crawl_rights.py** 배치(우선순위=보수차익 양수→유찰多, 재실행 안전, 차단시 저장 후 중단)
  · web.py property_detail: rights 로드(SQLite/REST)→listing 권리필드 실채움(rights_verified 해제)
  · detail.html STEP3: §명세서 요지 패널(⛔인수권리 빨간배너/최선순위/유치권/비고/청구액/종기)
  + ↻기일 내역 테이블(유찰 역사, 결과 색).
- 🐛 실측 미탐 수정: 1위 물건(2025타경669, 유찰10회) 원문 "매수인에게 대항할 수 있는 …
  임차권등기 … 매수인이 인수함"이 기존 _OPPOSABLE_PHRASES 미매칭(조사 차이) →
  tenant_opposable=False 로 초록 "✓치명적 인수권리 미발견" 오도. phrase 3종 보강
  ("매수인에게 대항할 수 있는"/"매수인이 인수"/"임차권등기") + STEP3 에 '대항력 임차인 주의'
  위험 분기 신설(하드게이트 아님이어도 초록 체크 금지).
- 검증: 라이브 3건 크롤→1위 임차권등기·2위 2021.4.28.근저당·3위 2016.1.14.근저당 정확 추출.
  로컬 렌더 스크린샷(명세서 배너+기일 12건 8.3억→0.23억 유찰 계단). 테스트 신규 6건
  (normalize·코드매핑·라운드트립·실측미탐 회귀) — 전체 **384 passed**.
- 진행: 상위 200건 배치 크롤 백그라운드 실행 중(3~8s 스로틀). deploy/supabase_rights.sql 준비
  (auction_listing_rights, 사용자 SQL Editor Run 대기) → 미러 → Vercel 재배포 예정.

## 2026-07-10 12:06 KST — 🎨 UX 개편: 목록 단순화 + 상세 '가격 지도' (사용자 가독성 피드백)
- 배경(사용자 피드백): ①목록 게이지 막대 의미 불명("초록 칸이 뭔지") ②용어 혼란("원가"? 초록 가격이
  유찰가인지 시세인지, "최저가"?) ③해법 = 목록은 간단히, 눌러 들어간 상세에서 밴드·시세·호가·유찰가
  시각화를 제대로.
- 목록(listings.html): gm2 게이지 전면 제거. 데스크톱 컬럼 = 단지/소재지 · **최저입찰가**(이번 회차
  시작가) · **예상 시세**(실거래 검증 하한) · **예상 차익**(시세−최저입찰가+취득세) · 매각기일 — 헤더에
  용어 힌트 병기. 모바일 카드 = 라벨 붙은 3-stat. 히어로 = 게이지 대신 최저입찰가→예상 시세 스탯.
  "원가"·무라벨 초록 숫자 목록에서 소멸.
- 상세(detail.html): 히어로에 **가격 지도(pxm)** 신설 — 한 축 위에 감정가(→유찰 N회 −저감%),
  최저입찰가, 총 취득원가(=최저입찰가+취득세), 실거래 검증 밴드, 호가 점을 전부 이름+금액 라벨로 배치,
  범례+계산식(검증 시세(하한) − 취득원가) 첨부. 좌표는 신규 src/pricemap.py 서버 계산(축 클램프·edge
  정렬 힌트로 모바일 라벨 잘림 방지). web.py property_detail 이 pmap 전달.
- 🐛 테스트 전역 오염 수정: run.py CLI 가 .env 를 environ 에 로드(원래 동작) + 어제 .env 에 SUPABASE_*
  추가 + 오늘 클라우드에 3,622건 적재 → run.py 호출 테스트 이후 모든 테스트의 _scored() 가 클라우드
  실데이터를 읽어 21건 연쇄 실패(어제까지는 빈 테이블 폴백으로 잠복). **tests/conftest.py 신설** —
  autouse 로 매 테스트 AUCTION_DB/SUPABASE_* 삭제(라이브 0 원칙, 명시 setenv 한 테스트만 백엔드 사용).
- 테스트: test_pricemap.py 신규 6건(순서·경계·보수 gain·폴백·클램프). trust_copy 카피 1건 갱신
  ("건 기준"→"근거 표본", 불변식 유지). 전체 **379 passed**.
- 증거: Playwright 스크린샷 4장(scratchpad ux_*.png) — 데스크톱 목록/상세, 모바일 목록카드/상세
  (edge-l 적용 후 취득원가 라벨 잘림 해소 확인). 로컬 :8100 재기동 검증.
- 평가자: -
- 다음: Vercel prod 재배포 → 라이브 검증.

## 2026-07-10 11:23 KST — 🚀 Vercel 프로덕션 라이브 (실 Supabase 데이터, 노트북 독립)
- 무엇: DDL 실행(사용자, 테이블 생성 rows:0 확인) 후 이관→프로덕션 배포→전수 검증 완료.
- 이관: `python -m deploy.migrate_to_supabase` → scored_listings 3,622건 Supabase 업서트, 클라우드 재조회 대조 일치.
- 배포: `vercel deploy --prod`(hyunwoo-jang-s-projects/auction-arbitrage). 프로덕션 도메인
  https://auction-arbitrage-hyunwoo-jang-s-projects.vercel.app — 공개(ssoProtection=null).
- 🐛 상세페이지 404 버그 수정: Vercel 이 PATH_INFO 를 URL-디코딩 없이 원본(%ED%83%80…)으로 넘김
  → Werkzeug 가 리터럴 매칭 실패 → 한글 사건번호 상세만 404(퍼센트 없는 목록/통계는 정상이던 이유).
  진단(?diag=1 환경덤프)으로 확정 후 api/index.py 에 _DecodePathInfo WSGI 미들웨어 추가
  (unquote_to_bytes→latin-1, '%' 있는 경로만). 재배포 후 상세 200 확인.
- 검증: 11개 라우트(/,stats,guide,calendar,map,methodology,watchlist,compare,api/listings,geojson,health)
  전부 200 + X-Data-Source: db. 상세 3건(범어월드메르디앙/금오아파트/…) 200. API 건수 3,622.
- 증거: curl 라이브 응답(위 라우트/상세 http=200, X-Data-Source db).
- 평가자: -
- 상태: 서빙 클라우드 완료(노트북 꺼도 링크 유지). 수집=로컬 스케줄러(--live 미러링) 미등록 → 후속.
- 미완: (1)로컬 새로고침 스케줄러에 Supabase 미러링 등록(econ 방식). (2)관심목록 Vercel /tmp 휘발
  →추후 Supabase 백업 승격. (3)코드 커밋/푸시 미실시(사용자 요청 시). (4)Tailscale :8443 서브는 이제 중복.

## 2026-07-10 00:2x KST — ☁ Supabase+Vercel 클라우드 이관 (코드·배포검증 완료, DDL 대기)
- 무엇: 노트북 독립 영구 링크 위해 서빙을 Vercel(Supabase REST 읽기)로 전환하는 이관 구현.
  수집=로컬 스케줄러 유지(사용자 결정), 서빙=클라우드. DB접근=REST-only(econ service키 재사용, 새 비번 X).
- 스키마: 별도 auction 스키마 대신 `public.auction_scored_listings`(prefix 격리) — econ 공유
  PostgREST 설정 무변경, 즉시 REST 작동. raw_listings는 로컬 유지, scored만 클라우드. +refreshed_at(만료삭제용).
- 코드: src/store_rest.py 신규(PostgREST 읽기 페이지네이션+120s TTL캐시, has_rows content-range,
  upsert 청킹, replace_all=업서트후 lt.stamp 만료삭제). web.py _scored/_probe_source 백엔드 분기
  (우선순위 AUCTION_DB>SUPABASE_URL>sample, REST 있으면 라이브 'db'). run.py 라이브적재 시 Supabase
  자동 미러링(--no-cloud로 차단, 실패해도 로컬 보존). api/index.py(WSGI 진입,/tmp 상태경로)+vercel.json
  (includeFiles templates,data + rewrite)+.vercelignore(pyproject 제외=uv회피→pip).
- 테스트: tests/test_store_rest.py 신규 7건(모킹). 기존 test_web 백엔드 미설정 케이스 SUPABASE도 clear.
  전체 **373 passed**.
- 배포검증: vercel 링크(hyunwoo-jang-s-projects/auction-arbitrage) + env 3종(SUPABASE_* Production).
  preview 빌드 성공(pyproject 제외로 uv→pip 우회 후), 진입점 스모크=모든 페이지 200(REST 오류시 샘플 폴백,
  500 없음). ssoProtection=null로 공개 전환(경매=공개정보, service키는 서버 env). preview 공개 200 확인.
- 증거: preview https://auction-arbitrage-4gwbnlgay-hyunwoo-jang-s-projects.vercel.app (200, 샘플).
- 평가자: -
- 미완(사용자/후속): (1)★사용자가 deploy/supabase_setup.sql 을 Supabase SQL Editor Run(테이블 생성).
  (2)그 후 `python -m deploy.migrate_to_supabase`(3,751건 이관). (3)`vercel deploy --prod`(실데이터 링크).
  (4)로컬 스케줄러에 --live 미러링 등록. 코드 커밋/푸시 미실시(사용자 요청 시).
- 다음: 사용자 DDL Run 확인 → 이관 → prod 배포 → 최종 공개 링크 제공.

## 2026-07-09 23:52 KST — 🔗 라이브 링크 개통 (Tailscale HTTPS :8443, 다람 공존)
- 무엇: 재디자인 반영본을 노트북 밖에서 접속 가능하게 노출. 경매 사이트를 8100 포트로 waitress 서빙
  (`AUCTION_DB=auction.db AUCTION_PORT=8100 python -m src.serve`, scored_listings 3,751건 라이브)
  + `tailscale serve --bg --https=8443 http://127.0.0.1:8100`.
- 링크: https://notebiz53.tail4271f6.ts.net:8443/ (tailnet-only, hyunwoojang1@ 개인계정 디바이스만)
- 다람 공존: 기본 443→:8000(다람 healthkr)은 그대로 두고 경매는 별도 HTTPS 포트 8443로 격리해 URL 충돌 회피.
- 증거: curl https://…:8443/ → HTTP 200 6.46MB, <title>아파트 경매 1차 필터</title> 렌더. /guide 200.
- 평가자: -
- 한계: 노트북+Tailscale 상시 ON 의존(백그라운드 서버 죽으면 링크 끊김). 영구 무의존은 Supabase+Vercel(B안) 남음.
- 다음: 사용자 접속 확인 → (택1) 상시화(스케줄러/서비스 등록) 또는 Supabase 스키마 SQL 재생성→클라우드 이관.

## 2026-07-09 15:19 KST — 🔬 ultracode 전수 감사 확정 18건 수정 (다관점+적대적검증)
- 무엇: 6관점 병렬 감사(33에이전트, 27발견→적대적반증→18확정) 결과 전부 수정.
- HIGH: (1)일정 모바일 table→카드 전환(오버플로/차익값 클립 해소, 신호색·필칩 포함 calendar.html
  전면 재작성+base.html .cal-* CSS) (2)ink-400(#98a2b3 2.6:1) 텍스트 대비 WCAG AA 미달 8개 규칙
  →ink-500(gm2-lab·pk-sub·pk-case·pk-rank·rank-head·dtitle .case·hist__x).
- MEDIUM: (3)일정 차익 양/음 신호색 (4)지도 사이드바 차익 강조 size+gain/risk색 (5)picks2 860~928px
  '신뢰'열 클립→overflow-x:auto+min-width (6)칩 클래스충돌(.chips .chip가 필 덮음)→히어로 컨테이너
  .chiprow 분리 (7)정렬 라벨 고정('차익 큰 순')→filters.sort 분기 (8)상세 탭 ARIA(role=tab/tablist/
  tabpanel·aria-selected) (9)KPI 단위색(gain-line 1.45:1)→gain/ink-500.
- LOW: (10)관심 빈상태 .empty2 카드화 (11)지도 범례 nowrap+wrap (12)히어로 '1위'표기 오류→'추천'+
  순번 loop.index (13)ink-300 NA텍스트(1.67:1)→ink-500 (14).chip.none 4.05:1→ink-600 (15)스킵링크
  추가(#main) (16)비교 차익 양/음색 (17)뱃지→필칩 통일(compare·watchlist) (18)가이드·방법론 데이터
  배지 실제출처 반영(_mark_source).
- 증거: pytest 366 passed(일정 라벨 불변식 유지 위해 cal-pf 서브라인에 '보수/기준 시세 차익' 라벨
  복원). Playwright 재검증: 일정 모바일 카드·차익색·필칩, 목록 히어로 필칩·대비, 관심 빈카드 확인.
- 커밋: feat/ui-redesign 3차 커밋 예정.
- 다음: (선택) 잔여 반증 9건은 실제 문제 아님. 사용자 지시 시 push.

## 2026-07-09 14:53 KST — 🎨 잔여 보조페이지 헤더 일관성 패스 (방법론·일정·관심·지도·비교·물건선택)
- 무엇: 아직 옛 헤더(.page-h/.page-sub)를 쓰던 보조 템플릿 6종을 새 스케일(.page-h2/.page-lead)로
  스왑해 재디자인 페이지와 코히런트하게. 본문 컴포넌트(.formula·.params 표·.chips·.note-box·핀 색)는
  토큰 별칭 매핑으로 이미 새 색·그림자·라운드를 상속 중이라 그대로 유지. 대상: methodology·calendar·
  watchlist·compare·map·choose_item.
- 증거: 전 템플릿 grep으로 잔여 .page-h/.page-sub 0 확인. pytest -q → 366 passed. Playwright 스크린샷
  (방법론·관심·비교·지도 데스크톱) — 새 헤더+새 토큰 컴포넌트 코히런트, 무깨짐 확인. 방법론 nav pill=
  '샘플 데이터'(백테스트 sample) 정상.
- 커밋: feat/ui-redesign 2차 커밋 예정(6개 보조 템플릿 + versions.md).
- 다음: ultracode 전수 감사(다관점 디자인·회귀·접근성) → 확정 이슈 수정.

## 2026-07-09 14:45 KST — 🎨 가이드 페이지 재디자인 + 재디자인 묶음 커밋 (Claude Design · guide)
- 무엇: 가이드 페이지를 에디토리얼 디자인으로 이식(참조=flask guide, 내 아키텍처·전체 콘텐츠 보존).
  base.html에 가이드 CSS 추가(.doc·.jump·.sec-head·.check 체크리스트카드·.callout 다크/라이트·.gtable
  색상표·.inline-warn·.guide-tint·.twobox). 구성: 히어로+점프칩 → ①5문 체크리스트(번호카드) →
  ②권리분석(틴트밴드: 말소기준/대항력 4상한표/서류 두칸/유치권, 확정일자 노트 보존) → ③세금(두세계·
  양도세율표·비교과세표·표면차익 경고·실거주vs재고). **앵커 #checklist/#rights/#tax 유지**(상세 딥링크 보존).
- 회귀: pytest -q → 366 passed. Playwright 스크린샷 확인. 앵커 3종 grep 확인.
- 커밋: 재디자인 묶음(토큰·nav·목록·상세·통계·가이드)을 feat/ui-redesign 브랜치에 커밋 예정
  (main 직접 커밋 회피). 대상=src/web.py·templates/{base,listings,detail,stats,guide}.html·tests 4종·
  versions.md·PROGRESS.md. 제외=info.md(공부로그)·ARCHITECTURE.md(타세션)·coords_cache(런타임)·design-refs.
- 다음: 잔여 보조페이지(방법론·일정·관심·지도) 재디자인 → 2차 커밋.

## 2026-07-09 14:27 KST — 🎨 통계 페이지 재디자인 이식 (Claude Design · flask/stats)
- 무엇: 아직 옛 스타일이던 보조 페이지 '통계'를 새 디자인 시스템으로 이식(디자인 참조=flask/templates/
  stats.html, 내 아키텍처·실데이터로 번역). base.html에 통계 CSS 추가(.grid-2·.scard·.bars 수평바·
  .hist 히스토그램·.donut conic-gradient·.legend). 구성: KPI 4카드(전체·차익양수·평균차익·권리미확인
  비율) → 지역별 바(시도) + 차익 스코어 히스토그램(80+ 초록강조) → 권리 도넛 + 유찰 회차 바 → 용도별 바.
  실데이터=d.overview·by_sido·by_property_type·score_distribution·fail_count_distribution·grade_distribution
  전부 활용(백엔드 무변경).
- 참고: 앞서 결정대로 생성 Flask 스캐폴드(app.css/BEM)는 미채택, 내 통합 아키텍처로 이식. list/detail
  flask preview는 이미 구현완료라 스킵(중복).
- 회귀: 통계 h1 변경으로 1개 테스트 실패 → 불변식 유지·셀렉터 갱신("권리 상태 비율" 검사). pytest → 366 passed.
- 증거: Playwright 스크린샷(데스크톱) — KPI·지역바·히스토그램·도넛·유찰바·용도바 실데이터 렌더 확인.
- 커밋: - (미커밋)
- 다음: 가이드 페이지 재디자인(마지막) 또는 지도/일정/관심/방법론 잔여 보조페이지 + 전체 커밋.

## 2026-07-09 13:55 KST — 🎨 상세 페이지 재디자인 이식 (Claude Design 4단계 · Detail Page.dc.html)
- 무엇: Claude Design `Detail Page.dc.html`을 순수 HTML+CSS+Jinja로 번역, detail.html 전면 재작성.
  base.html에 상세 CSS 추가(.dtitle·.dhero 게이지·.gatebar 통합배너·.dflow 번호흐름·.dstep-num·.dpanel·
  .drow·.dbanner·.dtags). 구성: 제목+관심버튼 → **히어로 게이지**(52px 차익+gm2 lg 갭미터) → 통합 게이트
  배너(앰버 1종) → 탭 → **번호 판단 흐름** ①얼마남는가(차익근거) ②세금·비용(취득세+세금가이드링크)
  ⛔사도되는가(권리게이트: 미확인/위험/통과 3분기+권리가이드링크) ④실물확인(카카오맵). 실데이터·탭JS·
  워치·게이트로직·호가·표본게이트·미지원유형 정책문구 전부 보존.
- 회귀: 라벨 변경으로 2개 테스트 실패 → 불변식 유지하며 갱신(권리안전성→"사도 되는가", 히어로 "건 기준"
  포함, 미지원유형 "시세를 추정하지 않습니다" 문구 hero에 유지). pytest -q → **366 passed**.
- 증거: Playwright 스크린샷 — 데스크톱/모바일 상세(히어로 게이지·통합배너·번호흐름·미확인 게이트) 확인.
  진단 #6(판단 흐름)·#8(경고 통합) 해결.
- 평가자: - (회귀+스크린샷 통과)
- 커밋: - (미커밋)
- 다음: Claude Design 가이드(Guide) 컴포넌트 → 이식(마지막). 이후 전체 커밋 검토.

## 2026-07-09 13:38 KST — 🎨 목록 페이지 재디자인 이식 (Claude Design 3단계 · List Page.dc.html)
- 무엇: Claude Design `List Page.dc.html`(DC런타임)을 순수 HTML+CSS+Jinja로 번역, listings.html 전면
  재작성. base.html에 List Page CSS 추가(.kpi·.filterbar·.hero2·.chip·.gm2 갭미터·.picks2 그리드·
  .mcards·.trustbox). 구성: 타이틀 → KPI 4카드(거대 숫자·gain 초록) → 필터바 → **히어로**(52px 차익+
  갭미터 lg) → 랭킹(데스크톱 CSS그리드 / 모바일 카드). 갭미터=Jinja 매크로로 ScoredListing 필드(밴드
  하한/상단·취득원가)에서 기하 계산(밴드 없으면 시세 폴백·음수차익 가드). 실데이터·필터·워치리스트(★)·
  legacy 배너·hero 게이트 전부 보존. 활성링크는 nav(2단계)에서 처리.
- 회귀: 재디자인으로 마크업 바뀌어 4개 테스트(옛 셀렉터 .tablewrap/.hero-caption/라벨) 실패 →
  불변식 유지하며 셀렉터·카피 정렬(hero '근거 표본 N건 기준', pk-sub '근거 표본', hero2/rank-head
  앵커). pytest -q → **366 passed**.
- 증거: Playwright 스크린샷 — 데스크톱(KPI·히어로 갭미터·랭킹 그리드 갭미터 가독), 모바일(KPI 2열·
  필터 스택·히어로·랭킹 카드) 확인. 진단 #2·#3·#5·#7 해결.
- 평가자: - (회귀+스크린샷 통과)
- 커밋: - (미커밋)
- 다음: Claude Design 상세(Detail) 컴포넌트 → 이식. 이후 가이드.

## 2026-07-09 13:27 KST — 🎨 헤더/네비 재디자인 이식 (Claude Design 2단계 · Header Nav.dc.html)
- 무엇: Claude Design `Header Nav.dc.html`(DC런타임 sc-if/DCLogic)을 순수 HTML+CSS+바닐라JS로 번역해
  base.html 이식. 데스크톱=로고마크 'A'+브랜드명+가로nav(현재페이지 브랜드틴트 .on)+상태 pill(라이브/샘플).
  모바일(<860px)=nav 접어 햄버거→전체폭 드로어(48px 터치항목, 슬라이드 애니메이션, X 전환). 활성표시=
  Jinja request.path. 토글=바닐라JS(nav-open 클래스, 링크클릭·리사이즈≥860·ESC 시 닫힘, aria-expanded).
  옛 560px 미디어쿼리의 .nav 충돌 규칙 제거.
- 증거: pytest -q → 366 passed. Playwright: 데스크톱 nav(매물 활성·pill), 모바일 닫힘(브랜드+햄버거,
  겹침 없음), 모바일 열림(드로어·48px·X) 스크린샷 확인. **진단 #1(모바일 nav 붕괴) 해결.**
- 평가자: - (회귀+스크린샷 통과)
- 커밋: - (미커밋)
- 다음: Claude Design 목록(Listings) 컴포넌트 → 이식. 이후 상세 → 가이드.

## 2026-07-09 13:12 KST — 🎨 디자인 시스템 토큰 이식 (Claude Design 1단계)
- 무엇: Claude Design 프로젝트(2a7bcb15…)의 `Design System.dc.html`(토큰 제안) 검증 후 base.html에 이식.
  검증=진단 문제 정조준(색 예산제·타이포 극적대비·라운드/섀도 위계·갭미터 승격·모바일nav 예고).
  이식 방식=새 토큰(--page·--ink-900~300·--brand·gain/risk/caution/pending·--r-xs~xl·--e1~e4·--sans/mono)
  전량 도입 + **하위호환 별칭 레이어**(--paper·--muted·--accent·--pos·--unv·--t-*·--r-card 등 기존 변수명을
  새 토큰에 매핑) → 전 템플릿 무회귀 적용. 핵심 가시효과=권리미확인 보라 강등(#7048e8→#585d7a 저채도),
  카드 라운드 14px·3단 그림자, 쿨-중립 팔레트, 본문 15px.
- 증거: pytest -q → 366 passed. Playwright 스크린샷(목록·상세 데스크톱) 무깨짐 + 권리미확인 뱃지 강등·
  카드 위계 개선 확인.
- 평가자: - (회귀+스크린샷 통과)
- 커밋: - (미커밋)
- 다음: Claude Design에서 컴포넌트 순서대로(헤더/nav 모바일햄버거 → 목록 → 상세 → 가이드) 받으면
  base.html/템플릿에 이식. 컴포넌트는 새 토큰명 직접 사용.

## 2026-07-09 11:48 KST — 🎨 UI 재디자인 준비 — 현 UI 진단 + 디자인 레퍼런스 스크린샷
- 무엇: 재디자인(방향 B=아실 라이트 강화) 전 현 UI 진단. Playwright로 목록·상세·가이드 데스크톱/
  모바일 6장 캡처 → 문제 9건 진단(치명=모바일nav붕괴·시각위계부재 / 높음=테이블밀도·색예산낭비·타이포
  납작 / 중=상세흐름·데이터시각화·경고톤·가이드벽텍스트). Claude 디자인용 프롬프트 작성(클립보드).
- 산출물: `design-refs/` 폴더에 스크린샷 6장(목록·상세·가이드 × 데스크톱·모바일) 저장. 코드 변경 없음.
- 다음: Claude 디자인이 토큰안 제시 → 확인 후 nav→목록→상세→가이드 순으로 base.html 인라인 CSS 이식.

## 2026-07-09 11:31 KST — 🧭 경매 필수지식 사이트 반영 — 가이드 페이지 + 상세 연동
- 무엇: info.md 강의 지식(권리분석 4·5·6강 + 세금 9강)을 사이트에 반영. 사용자 갭답변=둘 다(독립
  페이지+상세 연동)·콘텐츠=권리분석+세금·물건데이터 연동.
  ① `templates/guide.html` 신규(낙찰 전 5문 체크리스트 + 권리분석 가이드[말소기준·대항력 4상한표·
  명세서 두 칸·유치권] + 세금 가이드[양도세 단기세율표·매매사업자 비교과세표·표면차익 한계·실거주
  vs 재고]). ② `src/web.py` `/guide` 라우트 추가(methodology 위). ③ `base.html` nav '가이드' 링크.
  ④ `detail.html` 3탭 연동: 차익근거→"표면차익, 진짜순수익은 세금가이드", 세금탭→/guide#tax,
  권리탭→/guide#rights(+rights_verified=False면 "직접 확인" 문구 조건부).
- 증거: 스모크(app.test_client) — /guide 200(rights·tax·checklist 앵커+말소기준·대항력·비교과세·
  1세대1주택 8/8), /methodology·/ nav '가이드' 링크 확인, /property/2024타경66168 상세에 /guide#tax·
  #rights 링크 주입 확인. `pytest -q` → **366 passed**. `ruff check src/web.py` 클린.
- 평가자: - (스모크+회귀 통과)
- 커밋: - (미커밋, 사용자 커밋시점 대기)
- 다음: (선택) 낙찰전 체크리스트를 물건별 인터랙티브 체크로 확장 / 용어 툴팁 / docs 지식문서 렌더링.

## 2026-07-09 11:15 KST — 📚 10강 사고 사례 연구 — 코스 완주(10/10)
- 무엇: 마지막 10강. 4~9강이 각각 막는 사고를 종합 복습. A보증금몰수형(권리펑크·입찰오기·대출불발)·
  B인수함정형(선순위임차인·미말소권리·유치권·관리비)·C명도현금흐름형·D수익오판형(시세뻥튀기·고가낙찰·
  세금오판) + 낙찰전 5문 체크리스트 + 개념형 확인질문.
- 이정표: **커리큘럼 10강 전 과정 완주.** info.md 커리큘럼 표 10강 ✅.
- 기록: info.md "2026-07-09 10강" 섹션 삽입. 미커밋—공부 로그.
- 다음: 심화편 후보(특수권리 시리즈 / 오피스텔·상가 특화) 사용자 요청 시, 또는 8·9·10강 확인질문 채점.

## 2026-07-09 11:05 KST — 📚 문답: 매매업 중 실거주 아파트 취득 (재고 vs 실거주)
- 무엇: "단타하다 실거주 아파트 사면?" 질문에 재고자산 vs 실거주 세금·비용 차이 정리(원문검증
  heumtax·nts·PwC·택슬리).
- 핵심: ①매도세금 전환(재고=종소세/실거주=양도세+1세대1주택 비과세 12억↓ 0) ②★재고주택이 주택수
  포함→실거주 비과세 깸(매도시 재고0 필요, 양도세엔 1억↓ 특례 없음) ③취득세 주택수 합산 중과이나
  시가표준 1억↓ 제외 특례 有 ④생애최초 이미 소멸(9강) ⑤종부세 합산배제 신청 가능. 결론=매매업과
  실거주는 세법상 상극→시점분리("돈은 단타 종소세, 내집은 재고턴뒤 1주택 2년 비과세").
- 기록: info.md "2026-07-09 문답: 매매업 중 실거주" 섹션 삽입. 미커밋—공부 로그.
- 다음: 10강(사고 사례) — 코스 마지막.

## 2026-07-09 10:58 KST — 📚 9강 실전 계산 — 사용자 실제 가정 총수익
- 무엇: 사용자 입력(낙찰7,700·매도9,700·차익2,000·현금1,500·대출6,200@5~6%·3개월·명도100·온라인11·
  세무사9)로 총수익 산출.
- 결과: 비용합 398 → 세전이익 1,602 → 종소세(15%)264 → **세후 순이익 ≈1,338만**. 현금수익률 3개월
  ~72%(실투입현금~1,850). ⚠️사업성 부인 시 양도세70%로 재분류→세후 ~403만(935만 증발). 수리 100
  넣으면 세후 ~1,254. 건보료는 사업소득 2천↓이라 대개 없음.
- 기록: info.md "2026-07-09 9강 실전 계산" 섹션 삽입. 미커밋—공부 로그.
- 다음: 10강(사고 사례) 또는 9강 확인질문 채점.

## 2026-07-09 10:50 KST — 📚 9강 최종 — 사용자 상황 전용(비규제 주택·1억↓·연1~2건·근로5천↓)
- 무엇: 사용자 갭 4개(물건=비규제 주택/근로 5천↓/물건가 1억↓/연 1~2건) 확정 후 상황 specific 9강.
- 핵심: ①비규제 주택+매매업=비교과세 X=종소세 15%대(70% 없음) ②★주택 선택=생애최초 4종 소멸(특강
  "비주거" 결론 뒤집음, 재고주택도 소유주택, 갈림길=생애최초 vs 단타수익 택1) ③실계산: 낙찰8,000·
  매도1억 딜 순수익 개인양도세 115만 vs 매매업 893만(7~8배) ④연1~2건 리스크=사업성 부인→양도세
  재분류 위험+취득세 다주택중과(재고3채↑ 8%) ⑤트레이드오프표(주택=세율·취득세·부가세 유리, 생애
  최초만 잃음).
- 기록: info.md "2026-07-09 9강 최종" 섹션 삽입. 미커밋—공부 로그.
- 다음: 10강(사고 사례) 또는 9강 확인질문 채점.

## 2026-07-09 10:35 KST — 📚 9강 §2 정정 — 비교과세 대상 원문검증
- 무엇: 사용자 지적("비규제지역인데 그래도 70%랑 비교하냐?")에 소득세법 64조 원문 검증(casenote+
  택슬리 WebFetch). 결과: 64조 비교과세 대상=104조1항 1호(분양권한정)·8호(비사업용토지)·10호(조정
  지역 다주택 중과)·7항(미등기). **단기세율(2호·3호 70/60%)은 비교과세 대상 아님.**
- 정정: 아까 "주택 단타=매매사업자여도 70% 그대로"는 오류(규제지역 다주택에만 해당). **비규제지역
  주택 단기+비주거 = 종소세 6~45%만, 70% 비교 안 함.** info.md 9강 재수강 §2에 정정 반영.
- 다음: 사용자 갭(물건유형·근로소득·물건가격대·연회전수) 수집 후 상황 specific 9강 최종본 작성 예정.

## 2026-07-09 10:20 KST — 📚 9강 재수강 — 개인 매매사업자·단기 버전
- 무엇: 사용자 전략 확정("무조건 개인 매매사업자, 단기 위주")에 따라 9강을 매매사업자 관점 재구성.
  원문검증(택슬리·kicpa·법제처 소득세법64조·karnews·homeknock) 후 작성.
- 핵심: ①매매사업자=사업소득 종합소득세 6~45%(단기70% 개념 소멸) ②★비교과세(소득세법64조)=주택·
  분양권·비사업용토지는 양도세 중과·단기세율 못피함/**비주거(오피스텔·상가)는 6~45%만** ③이점=필요
  경비 전면인정(명도비·이자·관리비 공제, 본강 이중고 해소)·결손금통산 ④반대급부=근로소득합산·부가세·
  건보료·장부신고 ⑤같은딜 재계산: 양도세 순수익 315만 → 매매업 ~2,520만(단독)/1,750~2,100만(합산)
  ⑥전략종합=비주거 매매업 플립은 주택 미접촉→생애최초 4종 보존=더블윈(특강 트레이드오프 해소).
- 기록: info.md "2026-07-09 9강 재수강" 섹션(질문 보관함 직전) 삽입. 미커밋—공부 로그.
- 평가자: -
- 커밋: -
- 다음: 10강(사고 사례) 또는 사용자 실제 급여·자본 넣어 합산 세율 맞춤 계산.

## 2026-07-09 10:05 KST — 📚 9강 수익 계산 — 강의 완료·기록
- 무엇: 9강(수익 계산) 완료. §1 착시(매입할인≠수익)·§2 취득비용(오피스텔 취득세 4.6%)·§3 보유비용
  (공용관리비 인수·경락대출이자)·§4 매도비용(★양도세 1년미만70%/1~2년60%, 명도비·이자·관리비는
  필요경비 불인정)·§5 실제정산 예시(낙찰1.4억·매도1.9억·6개월→gross 5,000만인데 순수익 ≈315만,
  범인=양도세 2,541만)·§6 저울(단타 vs 실거주 비과세ⓒ→양도세 0→순수익 2,800만대, 특강 결론
  숫자 증명) + 개념형 확인질문.
- 기록: info.md "2026-07-09 9강" 섹션(질문 보관함 직전) 삽입 + 커리큘럼 표 9강 ✅. 미커밋—공부 로그.
- 평가자: -
- 커밋: -
- 다음: 10강(사고 사례 연구=보증금 날리는 전형 패턴) 또는 사용자 실제 숫자로 §6 저울 맞춤 계산.

## 2026-07-09 09:52 KST — 📚 8강 보충 문답 — 강제집행 비용 부담 주체
- 무엇: 사용자 질문("노무비+창고비 내가 내?") → 원칙(민집법 53조 채무자 부담) vs 현실(낙찰자
  예납·채무자 무자력으로 회수 0=실질 낙찰자 부담) 설명. §6 "협상이 정답"의 근거로 연결, 명도비를
  9강 수익계산에 반영해야 하는 이유 확립.
- 기록: info.md 8강 섹션에 "보충 문답 — 강제집행 비용" 삽입. 미커밋—공부 로그.
- 다음: 8강 확인질문 채점 또는 9강.

## 2026-07-09 09:48 KST — 📚 8강 낙찰 후 절차·명도 — 강의 완료·기록
- 무엇: 8강(낙찰 후 절차) 강의 완료. §1 큰그림(1산 소유권/2산 명도)·§2 타임라인(잔금 낸 순간
  소유권=민법187조·인도명령 자격 발생)·§3 명도 갈림길(5강 대항력 연결)·§4 인도명령(6개월 내·
  대항력 없는 점유자)·§5 강제집행 비용(200만 안팎)·§6 이사비 협상(명도확인서 지렛대·점유이전
  금지가처분)·§7 요약 + 확인질문(3/1 잔금·10월 버팀→인도명령 6개월 도과).
- 기록: info.md "2026-07-09 8강" 섹션(질문 보관함 직전) 삽입 + 커리큘럼 표 8강 ✅. 미커밋—공부 로그.
- 평가자: -
- 커밋: -
- 다음: 9강(수익 계산 — 사용자 실제 숫자로 단타 세후 vs ③④ 옵션가치 저울) 또는 8강 확인질문 채점.

## 2026-07-08 05:45 KST — 📚 확인질문 3건 채점 + 5·6·7강 리뷰
- 무엇: 밀려있던 5·6·7강 확인질문을 사용자가 답 → 채점(Q1 정답·Q2 모름→정답제시·Q3 부분정답)
  후 5·6·7강 리뷰 진행. 미답 확인질문 3건 전부 정산 완료.
- 핵심: Q1(전입 하루 늦으면 낙찰자 부담 0·서류=명세서)·Q2(명세서 최선순위설정 vs 전입일 비교
  →대항력 판정)·Q3(보증금 최저가기준=입찰가 비밀·중립성). 진단=5강 4상한↔6강 명세서 연결고리 망각.
- 기록: info.md "2026-07-08 확인질문 채점+리뷰" 섹션(질문 보관함 직전) 삽입. 미커밋—공부 로그.
- 다음: 8강(명도) 또는 심화 요청 대기.

## 2026-07-07 16:42 KST — 📚 7강 입찰 실무 — 강의 완료·기록
- 무엇: 7강(입찰 전 기일확인·준비물/대리·공동입찰·기일입찰표 작성규칙[물건번호 누락=
  무효, 가격칸 정정불가, 0 하나 더 사고]·보증금[최저가 10%·재매각 20~30%]·개찰·
  차순위매수신고 실무판단·9강 예고) info.md 기록 + 커리큘럼 표 7강 ✅. 확인질문 1건
  (보증금이 최저가 기준인 설계 이유) 미답 보관.
- 증거: info.md (7강 섹션, 커리큘럼 표)
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 사용자 "다음" 시 8강(낙찰 후 절차·명도)

## 2026-07-07 14:22 KST — 📚 특강 보충7 검증각주 — 단기세율 70/60 국세청 원문 재확인
- 무엇: 사용자 "확실해?"에 국세청 세율표 행 단위 재fetch(70/60 확정, 지역 열 부재,
  조정지역은 다주택 중과 행에만)+심플택스 교차+소득세법 104조 본칙 근거. 1차 fetch
  "60/40"은 요약 오류로 판명·투명 공개. info.md 보충7에 검증각주 추가.
- 증거: info.md (보충7 각주), 국세청 nts.go.kr 세율 페이지
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 사용자 "다음" 시 7강(입찰 실무)

## 2026-07-07 14:18 KST — 📚 특강 보충7 — 단기 양도세 전국 공통 + 규제지역이 가르는 것
- 무엇: 단기세율(70/60·비주택 50/40)=전국·가격무관 확정. 지역 변수는 별개 4종(다주택
  중과 5.10 부활·비과세 거주요건·대출규제·토허구역[서울 전역+경기12곳, ~2026.12.31]
  플립 원천봉쇄, 경매낙찰 허가불요 특례但출구막힘). 결론=지방 소액 플립의 적은 단기세율
  뿐. info.md 보충7.
- 증거: info.md (특강 보충 문답 7)
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 사용자 "다음" 시 7강(입찰 실무)

## 2026-07-07 14:08 KST — 📚 특강 보충6 — 플립 1사이클 기간·연 회전수
- 무엇: 사이클 해부(낙찰→허가7일→확정7일→잔금→명도0~3개월→매도) = 베스트 3~4개월·통상
  5~7개월, 연 회전 이론3/현실2/부업1~2회, 병목=평일 입찰 출석×패찰률, 고수 회전=자본
  병렬 공식. 단기양도세와 ③④ 옵션가치 저울 프레임 확정, 9강 실측 예약. info.md 보충6.
- 증거: info.md (특강 보충 문답 6)
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 사용자 "다음" 시 7강(입찰 실무)

## 2026-07-07 14:02 KST — 📚 특강 보충5 — 결혼 시나리오(소각 후 무주택 배우자와 구매)
- 무엇: ①②③=부부 연좌제(혼전 이력 포함, 세대분리 회피 불가)로 아내 명의도 불가,
  ④특공만 생존(2024.3.25 개정, 배우자 혼전 이력 배제 — 아내 신청, 조건 4개). 신혼특공
  동일 논리. 핵심 타이밍 규칙 도출: 플립 소각·매도는 혼인신고 전 완결. info.md 보충5.
- 증거: info.md (특강 보충 문답 5)
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 사용자 "다음" 시 7강(입찰 실무)

## 2026-07-07 13:56 KST — 📚 특강 보충4 — "주거 경매취득=4종 소멸" 법령 재검증
- 무엇: 사용자 요청 사실확인 — 지특법 36조의3 예외 5종(20㎡ 이하 1채 포함)·주택공급규칙
  53조 예외 12종(소형저가 9호·전세사기 낙찰 한정 '경매' 예외)·금융권 무예외 확인.
  명제 유지(일반 아파트=전멸), 실전 구멍 2개(20㎡ 이하 주택=①④ 생존, 소형저가 아파트=
  ④만 생존) 식별. 비주거 플립 결론 불변. info.md 보충4 병합.
- 증거: info.md (특강 보충 문답 4), 출처 링크는 대화 로그
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 사용자 "다음" 시 7강(입찰 실무)

## 2026-07-07 13:48 KST — 📚 특강 보충3 — 소거법: 생애최초 4종 유지 플립 종목
- 무엇: "4개 유지하며 경매 재미 볼 종목" 소거 — 주택 계열 전멸, 생존=비주거 전부.
  현실 순위 1위 오피스텔(업무용 유지 조건)·2위 소형 상가·3위 토지, 비주거 단기세율
  50/40% 비교. 결론=오피스텔 업무용 플립. 심화편 후보(상임법·오피스텔 함정·상가
  가치평가) 보관함 추가. info.md 특강 문답 병합.
- 증거: info.md (특강 보충 문답 3 + 질문 보관함)
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 사용자 "다음" 시 7강(입찰 실무)

## 2026-07-07 13:40 KST — 📚 특강 보충2 — "혜택 안 쓰면 아껴지나" 오해 교정
- 무엇: 경매 아파트 취득+미거주 시 생애최초 보존 여부 질문에 "아니오" 확정 — 자격=소유
  이력 기준(쿠폰 아닌 상태), 영구 소멸형은 4종 전부(이력 기준), 유일 구멍=④특공 소형·
  저가 간주(청약 한정). 플립 1호 원가=낙찰가+카드 소각비 프레임 + 선택지 3개(포기 플립/
  실거주 양수겸장/비주거 플립) 제시, 9강 숫자 비교 예약. info.md 특강 문답에 병합.
- 증거: info.md (특강 보충 문답 2)
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 사용자 "다음" 시 7강(입찰 실무)

## 2026-07-07 13:34 KST — 📚 특강 보충 — 생애최초 4종 재정리 + 실거주 의무 강도
- 무엇: 사용자 혼란 해소 문답 — 4종 표 재정리(④특공=청약통장 직결), 실거주 의무 비교
  (①3개월+3년 최강 / ②수도권 6개월 / ③1개월+1년 / ④신청요건만), 정정 2건(디딤돌
  신혼전용 아님·미혼 단독세대주 만30세 제한→현재는 청년주택드림이 해당 트랙), 자격
  유지 최소행동=주택 안 사기. info.md 특강 문답에 보충 병합. 코드 변경 없음.
- 증거: info.md (특강 문답 보충)
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 사용자 "다음" 시 7강(입찰 실무)

## 2026-07-07 13:28 KST — 📐 ARCHITECTURE.md 신설 — 코드베이스 지도 문서
- 무엇: 루트에 표준 템플릿(11섹션) 기반 아키텍처 개요 신설 — 구조/파이프라인 다이어그램/스코어 엔진(T1~T5)/auction.db 스키마/외부연동 표(V-World 미사용·건축물대장 미배선 명시)/배포·보안/테스트(pytest 366)/로드맵(권리분석 라이브 배선 최우선)/용어집. 코드 무변경.
- 증거: ARCHITECTURE.md (레포 전수 탐색 에이전트 분석 기반, main cf2f749 기준)
- 평가자: -
- 커밋: (미커밋 — 운영자 푸시 정책)
- 다음: 구조 변경 시 이 문서 동반 갱신

## 2026-07-07 13:23 KST — 📚 특강 문답 — 생애최초 혜택 4종 vs 경매 단기차익 전략
- 무엇: 사용자 실전 질문(28세·청약통장, 경매하면 생애최초 날아가나)에 웹 검증 기반 답변
  기록 — 생애최초=별개 4제도(취득세 감면·LTV 우대·디딤돌·특공), 6.27 대책 반영,
  물건종류별 영향(비주거=보존/오피스텔=청약무주택/주택=영구소멸+24.12.18 무주택 간주
  예외), 단기 양도세 70/60% 현실, 양수겸장 경로 제안. 코드 변경 없음.
- 증거: info.md (특강 문답 섹션)
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 사용자 "다음" 시 7강(입찰 실무). 특공 이력 판정 범위는 국토부 확인 항목

## 2026-07-07 11:58 KST — 📚 7강 전 문답 — "1~6강이 이론 전부인가? 특수권리는?"
- 무엇: 커리큘럼 구조 문답 기록 — 1~6강=일반물건 기본편(전부 아님), 특수물건 목록
  (유치권·법정지상권·지분경매·선순위 가등기/가처분·토지별도등기·가장임차인 등)은
  심화편(11강~) 후보로 질문 보관함에 추가. "초보는 특수물건 거른다=사이트 보수 원칙"
  연결. 코드 변경 없음.
- 증거: info.md
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 사용자 "다음" 시 7강(입찰 실무)

## 2026-07-06 17:53 KST — 📚 6강(서류 3종 읽기) 기록 + 사이트 권리파서 현황 문답
- 무엇: "어디서 확인하냐" 질문에 법원경매정보·인터넷등기소 안내 + 우리 사이트 현황
  실코드 확인(courtauction_rights.py 파서 존재·테스트 통과, 상세 3문서 라이브 배선만
  미연결 → 전 물건 권리미확인 보수강등이 정상동작임을 확인). 6강 기록: 명세서
  3핵심칸(최선순위 설정=말소기준 답안지·점유자표·비고란=지뢰공시판), 등기부
  갑구/을구 병합정렬·말소사항 포함, 현황조사서 불일치=위험신호, 5분 크로스체크 루틴.
  커리큘럼 6강 ✅. 코드 변경 없음.
- 증거: info.md
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 6강 확인질문 또는 "다음" 시 7강(입찰 실무)

## 2026-07-06 17:25 KST — 📚 5강 §7 — 대항력의 상대성 + 행사 형태 문답
- 무엇: "후순위도 전입하면 대항력 생기는 거 아니냐" 혼란 해소 — 대항력=날짜 찍힌
  방패(상대효), 발생일 이후 취득자에게만 유효, 선순위 근저당의 매수인에겐 불통→소멸,
  매매vs경매 차이. 행사 형태 4종(점유 버티기·임대인 지위 승계·인도명령 기각·동시이행)
  + 후순위의 실질 보호는 소액 최우선변제. 코드 변경 없음.
- 증거: info.md
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 5강 확인질문 답변 또는 "다음" 시 6강

## 2026-07-06 17:19 KST — 📚 5강 보충 — 선순위 임차인이 태어나는 현실 경로 4가지
- 무엇: 사용자 질문("대출로 산 집이면 근저당이 먼저인데 어떻게 임차인이 선순위?")에
  현실 시나리오 4종 기록 — ①무대출 집에 전세 후 나중 담보대출 ②갭투자(전세끼고 매수+
  추가대출, 최다 공급처) ③기존 대출 상환·말소 후 재대출 ④무담보 집 가압류→강제경매.
  공통 원리 = 말소기준은 "살아있는 등기 중 최고참"이라 임차인보다 젊을 수 있음.
  코드 변경 없음.
- 증거: info.md
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 5강 확인질문 답변 또는 "다음" 시 6강

## 2026-07-06 17:02 KST — 📚 5강(임차인·대항력) 기록 — 사용자 질문 트리거로 진행
- 무엇: "대항력 있는 임차인이 배당 신청했다는 게 뭔 소리냐" 질문에 5강 전체로 응답 —
  주임법 배경(이사+전입=다음날 0시 대항력), 선순위/후순위 운명, 3종 무기(대항력=방패·
  확정일자=창·배당요구=방아쇠), 낙찰자 관점 4상한 매트릭스, 시세5억·보증금3억 인수
  사고 숫자예시, 실전 체크 5순서. 커리큘럼 5강 ✅. 코드 변경 없음.
- 증거: info.md
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 5강 확인질문(하루 늦은 전입) 또는 "다음" 시 6강(서류 3종 읽기)

## 2026-07-06 16:57 KST — 📚 4강 부록2 — 배당 순위 + "못 받으면 내가 주나?" 문답
- 무엇: 배당 순위 6단계(집행비용→최우선변제·임금→당해세→날짜순 본게임→일반채권
  안분), 가처분은 배당 불참, 낙찰가 3.5억 숫자 예시(카드사 2,300만 부족·지인 0원),
  소멸주의의 진짜 의미(미배당 잔액은 채무자 개인 채무로 존속, 낙찰자 면책) +
  유일한 예외=인수 권리. 코드 변경 없음.
- 증거: info.md
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: "다음" 시 5강(임차인·대항력)

## 2026-07-06 16:47 KST — 📚 4강 부록 — 용어 사전 + 예시 타임라인의 '드라마' 해설
- 무엇: 사용자 질문(근저당·가압류·가처분·말소기준 등 단어 뜻 + 예시에서 오간 행위)에
  대한 용어 사전 11개(등기부 갑구/을구, 근저당=한도담보, 가압류=돈 임시동결,
  가처분=소유권 다툼, 가등기 2종, 전세권, 말소, 배당, 개시결정등기)와 예시 A/B의
  사건 서사(김씨 몰락 연대기, 김OO 소유권 분쟁) 기록. 코드 변경 없음.
- 증거: info.md
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 4강 확인질문 답변 또는 "다음" 시 5강

## 2026-07-06 16:44 KST — 📚 4강 보충 — "앞/뒤" 시간축 문답 기록
- 무엇: 사용자 질문("말소기준 '뒤'가 미래냐 과거냐") 답변 기록 — 뒤=미래(나중 등기)=
  후순위=말소, 앞=과거=선순위=인수. 동일 권리·날짜만 다른 타임라인 예시 A(깨끗)/B(지뢰)
  대비표 + "은행은 선순위 아니면 대출 안 함" 암기법. 코드 변경 없음.
- 증거: info.md
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 4강 확인질문 답변 또는 "다음" 시 5강

## 2026-07-06 16:27 KST — 📚 4강(말소기준권리) 기록 + 보관함 헤딩 복구 + 계산문제 금지 규칙
- 무엇: 사용자 피드백(계산 문제 금지) 수업방식에 추가, 3강 확인질문 스킵 처리, 4강
  심화(소멸주의/말소기준 후보 5종/판별 알고리즘/선순위 위험권리/등기부 밖 복병/아파트
  통계 현실) 기록. 3강 기입 때 실수로 지운 "질문 보관함" 헤딩 복구. 코드 변경 없음.
- 증거: info.md
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 4강 확인질문(가처분vs임차인) 답변 또는 "다음" 시 5강(임차인·대항력)

## 2026-07-06 13:46 KST — 📚 2강 종료(정답) + 3강 심화 재수강 기록
- 무엇: 2강 확인질문 정답 처리(잔금=소유권). 사용자 "더 딥하게" 요청 → 3강 심화판 기록:
  감정평가 3방식·감정가 4대 한계·감정가율, 저감 복리 수학(20%vs30% 법원 비교)·재매각
  보증금 상향, 낙찰가율·경쟁 역학(예상 낙찰가로 시뮬레이션), 유찰의 이중신호·역선택,
  차익 공식화와 사이트 공백(취득가≠예상낙찰가). 질문 보관함에 백로그 후보 2건 추가
  (예상 낙찰가 모델·저감률 표시). 코드 변경 없음.
- 증거: info.md
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 3강 확인질문(a~d) 답변 대기 → "다음" 시 4강(말소기준권리)

## 2026-07-06 13:42 KST — 📚 2강 재수강 기록 — 절차 흐름 상세판(8막 구성)
- 무엇: 2강을 "한 물건의 일생" 서사로 상세 재수업 — 신청·개시/배당요구 종기/매각준비
  서류3종/공고·입찰·유찰/허가 2주/잔금=소유권/배당 순위/명도 8막 + 취하·변경으로 물건이
  증발하는 지점 명시. 커리큘럼 2강 "재수강 완료" 갱신. 코드 변경 없음.
- 증거: info.md
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 2강 확인질문(소유권 취득 시점) 답변 대기 → 사용자 "다음" 시 3강 재수강

## 2026-07-06 11:26 KST — 📚 수업 방식 개편 — 사용자 피드백 반영(한 강씩 천천히)
- 무엇: 사용자 피드백("꼬리물기 말고 목차대로 천천히 하나씩") 반영 — info.md 상단에 수업
  방식 3원칙 명문화, 속성으로 지나간 2·3강을 "재수강 대상"으로 상태 변경. 코드 변경 없음.
- 증거: info.md
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 사용자 선택(2강 재수강 vs 4강 진행) 후 한 강씩 진행

## 2026-07-06 11:23 KST — 📚 경매 공부 info.md 갱신 — 2강 숙제 채점 + 3강(감정가·최저가·유찰) 기록
- 무엇: 2강 숙제 답변 채점(시차 정답 + "감정가=첫 가격" 명제와 모순 아님 해설), 3강
  "가격 3종(감정가/최저가/시세)·저감 계단·반값 착시" 수업 기록, 커리큘럼 3강 ✅.
  사이트의 독립 시세 밴드·보수 차익이 3강 이론의 구현체임을 명시. 코드 변경 없음.
- 증거: info.md
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 3강 숙제(인천 30% 2유찰 최저가 계산 + 비교 기준) 답변 후 4강(말소기준권리)

## 2026-07-06 09:17 KST — 📚 경매 공부 info.md 갱신 — 1강 숙제 채점 + 2강(절차 흐름) 기록
- 무엇: 1강 확인질문 사용자 답변·채점(①완전/②부분/③미계량) 기입, 2강 "경매 절차 전체
  흐름"(4단계·취하/변경·매각허가 2주) 수업 기록, 커리큘럼 2강 ✅, 질문 보관함에 "응찰자 수
  기반 경쟁 강도 계량화" 백로그 후보 추가. 코드 변경 없음.
- 증거: info.md
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 2강 숙제(감정가≠시세 이유) 답변 후 3강(감정가·최저가·유찰 구조)

## 2026-07-06 09:15 KST — 📚 경매 공부 로그 info.md 신설 (1강 기록)
- 무엇: 사용자와의 경매 스터디(전문가 역할극) 기록용 info.md 생성 — 10강 커리큘럼 표,
  1강 "경매란 무엇인가" Q&A 전문, 사이트 연결 메모, 숙제(확인 질문) 포함. 코드 변경 없음.
- 증거: info.md (레포 루트)
- 평가자: -
- 커밋: (미커밋 — 공부 로그, 추후 일괄 커밋)
- 다음: 사용자 숙제 답변 후 2강(경매 절차 흐름) 진행, 강마다 info.md 갱신

## 2026-07-03 17:15 KST — 📏 아실 개편 M5: 시그니처 '가격 밴드 게이지' — 문서 11장 다이어그램의 제품화
- 무엇: frontend-design 스킬의 '시그니처 요소' 원칙 적용 — 이 제품의 본질(가격 밴드 + 보수 차익)을
  대표 비주얼로. 근거 문서 11장 세로 다이어그램(밴드·호가 점·취득원가 ▼)의 가로판.
  - report.py `_band_gauge`: 검증 하한~기준가 밴드(두 선) 위 취득원가 ▼ 마커. 3상태 —
    원가<하한=초록 보수차익 폭 / 밴드 안=호박 '보수 차익 없음' / 기준가 초과=적색.
    호가는 검증 보조 점(T6 원칙). `gap_meter_html` 디스패치 — 밴드 없는 레거시 행은 기존
    갭미터 자동 폴백(내일 새로고침부터 전 행 게이지 전환).
  - 목록 갭 컬럼·목록 히어로·상세 히어로 전부 게이지로(템플릿 변경 0 — 디스패치 덕).
    상세는 호가 점까지 배선. 권리미확인 강등은 초록만 중립 블루로(.unv).
  - scripts/serve_sample_8001.py — 샘플 모드 디자인 미리보기 서버(스크린샷 검증용).
- 증거: 스크린샷(shot_sample_list/detail.png) — 해운대 '▼원가 5.82억 → 밴드 9.50~9.55억,
  보수 차익 3.68억' 행 단위 가독, 광교는 원가가 밴드 턱밑(0.02억)으로 정직 표시.
- 게이트: pytest 366 passed(+11 tests/test_band_gauge.py), ruff 클린.
- 평가자: 스크린샷 자가검증.
- 커밋: (이 항목과 함께 커밋)
- 다음: 운영자 육안 확인(현 실DB는 레거시 폴백 — 샘플 미리보기 `scripts/serve_sample_8001.py` 또는
  내일 새벽 새로고침 후 본서버에서 확인).

## 2026-07-03 17:05 KST — 🧰 아실 개편 M4: 공식 웹디자인 스킬 5종 설치 + 스크린샷 자가검증 루프 가동
- 무엇: 운영자 지시("깃헙에서 웹디자인 스킬 가져와") — anthropics/skills(**157.8k★ 공식**)에서
  frontend-design·theme-factory·canvas-design·web-artifacts-builder·webapp-testing 5종을
  ~/.claude/skills에 설치. frontend-design 지침으로 자가진단: **이전 웜페이퍼·헤어라인 신문
  스타일이 'AI 디폴트 3대 룩'에 정확히 해당**(운영자 지적이 옳았음을 스킬이 확인).
  - **webapp-testing 기반 시각 루프**: Playwright+Chromium 설치 → 목록/상세/지도 스크린샷을
    직접 보며 수정(이제 장님 디자인 아님).
  - Pretendard Variable 웹폰트 CDN 로드 — Windows 맑은고딕 폴백 탈출(토스체감 절반이 폰트).
  - 스크린샷 진단 수정 4건: ①목록 상단 4줄 설명문 → 1줄 압축 ②권리미확인 강등이 갭 미터를
    회색 벽돌로 죽이던 것 → 중립 블루(#d7e4f7, 안전신호 아님) ③신뢰 칩 전행 초록 반복 →
    조용한 아웃라인(색 예산 회수) ④/map 헤더 '샘플 데이터' 오표시 → _probe_source 명시 탐지.
- 게이트: pytest 355 passed, ruff 클린. 스크린샷 재촬영으로 육안 확인.
- 평가자: 스크린샷 자가검증(shot_list/detail/map.png).
- 커밋: (이 항목과 함께 커밋)
- 다음: 시그니처 요소(가격 밴드 게이지 — 하한~기준가 밴드 위 취득원가 마커) 검토, 운영자 피드백.

## 2026-07-03 16:45 KST — 🎨 아실 개편 M3: 레이어드 서피스 — ui-ux-pro-max 스킬 기반 재조정
- 무엇: 운영자 피드백("배경 단색 하나로 밀지 마라 — 토스/아실은 층이 있다") → ui-ux-pro-max
  스킬(90.5k★) 로드, Data-Dense Dashboard 스타일 × Banking/Traditional Finance 팔레트 조회 적용.
  - **레이어링 도입**: 페이지 캔버스 #f4f6f9(회색) 위에 흰 서피스 카드(#fff + 1px 보더 +
    은은한 그림자 --shadow-card) — 토스/아실 구조. M2의 '전부 흰색 단판' 문제 해소.
  - 인사이트 스트립 → **KPI 카드 행**(항목별 흰 카드), 필터 → 흰 카드 바, 테이블 → 흰 카드
    컨테이너(radius+shadow, 헤더 surface-2), 상세 섹션 → 흰 카드 복원(이번엔 캔버스 위 레이어),
    히어로 → 흰 카드+블루 좌측 룰, params 테이블 카드화.
  - 팔레트 미세조정: accent #2563eb, pos #16a34a, risk #dc2626 (스킬 Financial 팔레트 정합).
- 게이트: pytest 355 passed, ruff 클린. 서버 재기동(db).
- 평가자: - (운영자 실시간 확인 중)
- 커밋: (이 항목과 함께 커밋)
- 다음: 운영자 육안 피드백 반영 반복.

## 2026-07-03 16:40 KST — 🎨 아실 개편 M2: 디자인 시스템 교체 — Warm-Paper 폐기 → Data-Thin
- 무엇: 운영자 피드백("얇고 깔끔하게, 색 단조·네모 스택") 반영 — base.html 디자인 토큰 전면 교체.
  - 팔레트: 웜페이퍼 베이지 → **화이트 + 1px 헤어라인 + 블루(#2b6de4) 액센트**. 의미색 분리:
    차익=green/위험=red/경고=amber/**권리미확인=보라**(경고와 즉시 구분)/신뢰=teal.
  - 타이포: 세리프 숫자 폐기(--font-serif도 Pretendard로 재정의 — 전 템플릿 일괄 적용),
    스케일 축소(히어로 2.5rem→1.7rem, 행높이 46→40px, 셀 패딩 축소) — 데이터 밀도 상승.
  - 구조: **박스 스택 해체** — 카드=투명+상단 2px 룰(신문식 섹션), 테이블=외곽 상자 제거+상단
    굵은 룰+호버 블루 틴트, 인사이트=박스→얇은 상하 헤어라인 스트립, 경고 박스=좌측 3px 룰
    콜아웃(면적 1/3), 히어로=거대 카드→얇은 블루 틴트 밴드, 필터=30px 컴팩트 바, 헤더=48px 스티키.
  - detail.html: **탭 바 도입**(전체/차익 근거/세금/권리/현장 — 기본 '전체'라 정보 접근성·테스트 불변).
- 게이트: pytest 355 passed(전 회귀 무변화 — 클래스명 유지·CSS만 교체), ruff 클린.
- 평가자: - (운영자 실시간 확인 중 — 서버 재기동 완료)
- 커밋: (이 항목과 함께 커밋)
- 다음: 운영자 피드백 반영 반복(색·밀도·구조). 남은 후보 = 목록 갭미터 열 다이어트·모바일 확인.

## 2026-07-03 16:27 KST — 🗺️ 아실 개편 M1: 지도 뷰(/map) — KATEC 좌표계 판별로 Q1 해소
- 무엇: 운영자 "아실처럼 심플+지도" 지시 → 6일 막혀 있던 좌표계 문제(Q1) 해결하고 지도 구현.
  - **좌표계 판별**: courtauction xCordi/yCordi는 EPSG 표준이 아니라 **KATEC(TM128, 옛 다음지도)**.
    실크롤 3,239건 주소-시도 전수 대조 — KATEC 98.4% 일치, EPSG 후보(5174/5179/5185/5186 등)
    전부 ≤16%. 과거 "역산 시 경도 128.2°" 미스터리 규명(중부원점 가정이 오답이었음).
  - src/coords.py: pyproj 변환 + **시도 bbox 검증**(오좌표 핀 차단, 1.6% 탈락) + 좌표 캐시
    (uid 키 + 레거시용 case: 폴백 키). requirements에 pyproj.
  - /api/listings.geojson(목록과 동일 필터·decision_profit·conservative 플래그) + /map
    (Leaflet+OSM 캔버스 렌더, 키 불요) — 아실 스타일: 지도 주인공 + 좌측 슬림 패널(상위 200)
    + 등급 색 핀(위험=적/경고=황/차익후보=청/무광=회) + 팝업 상세 링크. 내비 '지도' 추가.
  - run.py courtauction 경로에 좌표 캐시 빌드 배선(새로고침마다 갱신). 즉시 1회 빌드:
    4,749/4,826건 → 실DB 조인 **2,493핀**(2,521행 중 28건만 좌표 없음).
  - 브랜드 문구 "차익 큐레이션"→"아파트 경매 1차 필터"(base.html, T7 잔여).
- 증거: evidence/map_coords.txt(판별표+캐시 통계) Read 확인. 광주 수완지구 등 좌표 정확.
- 게이트: pytest 355 passed(+7 tests/test_coords.py), ruff 클린.
- 평가자: - (운영자 실시간 확인 예정 — 서버 재시작 후 /map 안내)
- 커밋: (이 항목과 함께 커밋)
- 다음: 아실 개편 M2 = 목록·상세 심플화(탭 분리·라벨 다이어트). QUESTIONS Q1 해결됨 처리.

## 2026-07-03 15:59 KST — 🔎 신뢰루프 사이클 #8(최종): T8 다관점 감사 + 확정 14건 전부 수정 → 루프 종료
- 무엇: 5관점 병렬 감사(기능·침묵실패·도메인안전·디자인UX·데이터정합, 에이전트 39개) + CRITICAL/HIGH는
  반박자 2인 적대 검증. 확정 CRITICAL 1·HIGH 13(중복 제거 후 수정 묶음 9개) 전부 즉시 수정.
  - **①히어로 게이트 통일**(감사 1·4·5·10): digest.passes_recommend_gates 공용화 — 히어로는
    strict(레거시 통과 없음): 보수차익 양수+같은단지같은평형+basis≥5+비위험. 구 DB에선 히어로 미표시.
  - **②복합키 소비계층**(감사 2·3, B14 핵심): /property·/api/listings/<case_no>가 다물건 사건에서
    선택 페이지(choose_item.html)/300 Multiple Choices 반환, ?item=&court= 로 특정. 전 템플릿 상세
    링크에 item 파라미터. (감사 실측: 최근 전국 크롤 4,826건 중 사건번호 충돌 801그룹·유형 혼재 107건 —
    내일 05:30 정기 새로고침부터 실데이터에 등장할 임박 결함이었음.)
  - **③'오늘의 콕 집은 매물' 캡션 제거**(감사 6 CRITICAL): base.html CSS ::before가 상세 페이지
    판정 헤더(위험·미지원 포함)에까지 추천 캡션을 붙이던 결함 — 마크업 캡션("보수 기준 1위 — 검증
    게이트 통과 물건")으로 교체, 목록 히어로 전용.
  - **④레거시 라벨-값 불일치**(감사 7·13): 전량 구 데이터일 때 '구버전 채점 데이터' 배너 + 컬럼
    라벨 '차익(기준 시세·구 데이터)' 동적 전환. compare '(검증 하한가 기준)' 주석도 동적.
  - **⑤매칭 vs 근거 표본 라벨 분리**(감사 9): '실거래 N건' 중복 명명 → '매칭 N건'(신뢰칩)과
    '근거 표본 N건'(차익 부제·히어로·비교) 분리. methodology에 차이 설명.
  - **⑥calendar·watchlist·stats T7 전환**(감사 11): 보수 기준 표기 통일(stats는 라벨 명시).
  - **⑦미지원유형 상세 정리**(감사 12): '표본 부족' 오표기 → '정책상 미추정' 분기, 시세 없는 물건의
    '표본 낮은 신뢰' 경고·0건 행 제거(web.py sample_gate_low에 est 조건).
  - **⑧방법론 페이지 보수 산식 공개**(감사 8): 밴드(하한가/기준가)·보수차익·basis 게이트 정의 신설.
  - **⑨run.py dryrun 분리 확대**(감사 14): 비라이브 실행은 소스 무관 전부 .dryrun.db — 샘플 6건이
    서빙 DB에 적재돼 X-Data-Source: db로 위장되던 구멍 봉쇄.
  - 반박 기각 3건·MEDIUM/LOW 29건은 docs/audit-t8-20260703.json 전문 보존 → B18 등록.
- 증거: evidence/t8_audit_fixes.txt(회귀 13개 PASS) + docs/audit-t8-20260703.json Read 확인.
- 게이트: pytest 348 passed(+13), ruff 클린.
- 평가자: 감사 패널(39 에이전트) 종합 — 확정 발견 전부 수정·회귀가드 확보.
- 커밋: (이 항목과 함께 커밋)
- 다음: **신뢰루프 종료**(T1~T8 전부 완료). 운영자 액션: ①내일 05:30 정기 새로고침(활성 확인됨)이
  신규 게이트 필드를 채움 — 이후 보수 기준 전면 발효 ②아침 리뷰 후 push ③QUESTIONS Q1(지도 좌표)·
  Q2(호가 수급) 답변 대기.

## 2026-07-03 15:23 KST — ✍️ 신뢰루프 사이클 #7: T7 UI 메시지·포지셔닝 전환 (NEEDS_WORK 1회 → PASS)
- 무엇: 단정 표현 제거·보수 기준 중심 전환(문서 6장·15장 7단계) — 마지막 구현 단계.
  - query.py: decision_profit(profit_low 우선) — 목록 정렬·min_profit 필터 보수 기준화.
  - digest.py: 추천 TOP에서 '위험'(하드게이트) 제외(3회 반복 지적 해소) + markdown 보수차익/근거 열
    + "권리 확인 완료 전까지 최종 판단 금지" 푸터.
  - listings.html: h1 "아파트 경매 1차 필터", 히어로 스포트라이트 서버측 선정(비위험 1위, web.py)
    + 보수 라벨·근거 병기, 테이블 헤더·셀·푸터 산식 전환. detail/compare 산식·근거 표본 행.
  - README: "차익이 확실한"·"사라고 콕 집어주는 엔진" 제거 → "초보자를 위한 아파트 경매 1차 필터"
    포지셔닝 + 신뢰 게이트 설명. 타이틀·백링크 6곳 "1차 필터" 네이밍.
  - **평가 왕복**: 1차 NEEDS_WORK(히어로가 위험 물건의 낙관 차익 3.73억을 '예상 차익'으로 헤드라인
    + 헤더 라벨-값 불일치 + 산식 3곳 잔존) → 전부 수정 + 히어로 회귀가드 테스트 2개 → 재판정 PASS
    (5건 전부 해소·회귀 없음 확인).
- 증거: evidence/t7_ui_copy.txt — 21/21 OK(히어로 7항목 포함). 평가자 실측 렌더 재검증.
- 게이트: pytest 335 passed(+10), ruff 클린.
- 평가자: PASS. 신규 LOW 2건(rank 열 의미 변화·report.py CLI/HTML 잔존) → B17 이월.
- 커밋: (이 항목과 함께 커밋)
- 다음: **T1~T7 전체 완료** → 사이클 #8 = T8 최종 다관점 감사(기능·코드품질·침묵실패·도메인안전·
  디자인/UX 병렬 + 적대적 검증) → CRITICAL/HIGH 즉시 수정 → 루프 종료·운영자 보고.

## 2026-07-03 14:30 KST — 📍 신뢰루프 사이클 #6: T6 호가 스텁 (점 표시·밴드 검증 보조)
- 무엇: 호가를 밴드의 주재료가 아닌 '검증 보조 점'으로 표시하는 구조 완성(문서 11~12장).
  - src/asking.py 신규 — AskingPrice 모델, 수동 입력 파일(data/asking_prices.json) 로드(없으면
    완전 무표시 계약, 손상·무효 행은 logger 경고 후 skip), asking_points(밴드 대비 아래/안/위),
    band_overstated(최저 호가 < 검증 하한가 → 실거래 밴드 과대 가능성).
  - web.py 상세 라우트 배선 + detail.html '현재 호가(체결가 아님 — 참고용 점)' 행·과대 경고 박스.
  - **크롤 금지 준수**: 네트워크 코드 0(평가자 grep 확인). 실데이터 수급 경로는 QUESTIONS.md Q2
    등록(A 수동입력 추천 / B 제휴·오픈API / C 보류 — 논블로킹).
  - 표시 전용 보장: matcher/score/digest 무접촉 — 시세·점수·추천에 영향 없음(평가자 확인).
- 증거: evidence/t6_asking_stub.txt — 파일 없음→무표시, 주입→점·위치·과대경고 렌더, 크롤 부재.
- 게이트: pytest 325 passed(+13), ruff 클린.
- 평가자: PASS. LOW 3건(무효 행 침묵 skip → logger.warning / float 호가 거부 → int·float 허용
  bool 배제 / 무표시 테스트의 실파일 환경 의존 → monkeypatch 격리) 전부 즉시 수정.
- 커밋: (이 항목과 함께 커밋)
- 다음: 사이클 #7 = T7 UI 메시지·포지셔닝 전환(보수 차익 중심 문구 + '위험' 등급 추천 표면 분리 —
  평가자 3회 반복 지적 반영) → 이후 T8 최종 다관점 감사.

## 2026-07-03 14:18 KST — 🚧 신뢰루프 사이클 #5: T5 표본 부족 시 추천 금지 게이트
- 무엇: 소표본 통계 흉내 차단(문서 10장) — 밴드 실기반 표본수(basis: 최근성+트림 후 실사용 건수) 기준
  3단계 게이트. matched(트림 전)가 아니라 basis를 봐서 부풀린 표본 통과를 막음(T4 평가자 권고 반영).
  - matcher.py: basis<band_min_basis(3) → 밴드 생성 금지·시세근거 부족(est None). MarketEstimate.basis.
  - score.py: basis<band_confident_basis(5) → 상위 등급 캡('관심'). market_sample_basis 저장.
  - digest.py: _enough_basis — 추천 TOP은 basis≥5만(레거시 None은 하위호환 통과).
  - config.py: 임계값 외부화(band_min_basis/band_confident_basis + env + 단조 방어).
  - store.py v6 아님 v5: market_sample_basis ALTER. 실DB 2521건 보존.
  - detail.html '밴드 근거 표본' 행+낮은신뢰 경고 박스, methodology '표본 게이트' 문단.
  - 샘플 fixture 단지당 3건→7건 현실화(새 게이트에서 데모가 전부 '근거부족'으로 비는 문제 —
    게이트 자체는 합성 소표본 테스트로 검증, 평가자가 '조작 아닌 현실화'로 판정).
- 증거: evidence/t5_sample_gate.txt — 경계표(매칭 2/3/4/5/7 → basis 2/3/2/3/5 → 금지/낮은신뢰/금지/
  낮은신뢰/정상), 실DB v5 이관, 추천 TOP 전부 basis≥5, 웹 노출 True.
- 게이트: pytest 312 passed(+16), ruff 클린.
- 평가자: PASS (이중게이트 사각지대 없음·elif 순서 무해·fixture 정당). ⚠운영 노트 — 실DB 전행
  basis=None이라 **전량 새로고침 전까지 실데이터에서 T5 게이트 미발효**(스케줄러/수동 새로고침 필요).
  MEDIUM('위험' 등급 추천 TOP 노출 — 3회 반복 지적) → T7 완료 정의에 명시 추가. LOW(confidence·basis
  불일치) → B16 등록.
- 커밋: (이 항목과 함께 커밋)
- 다음: 사이클 #6 = T6 호가 스텁(옵셔널 모델·점 표시·밴드 검증 보조, 실데이터는 QUESTIONS 운영자 대기).

## 2026-07-03 14:03 KST — 📊 신뢰루프 사이클 #4: T4 시세 단일값 → 2선 가격 밴드
- 무엇: 단일 중앙값의 '정답 가격' 과신 방지(문서 7~11장) — 검증 하한가/기준가 2선 + 보수차익 기준 추천.
  - matcher.py: MarketEstimate에 band_low(트림 후 최저 평단가)·band_high(트림 후 중앙값=est 호환).
    P25/P50 분위수는 소표본 통계 흉내라 기각(문서 9장), 트림 후 최저/중앙값 채택.
  - score.py: profit_low/high 계산·저장. '차익없음' 게이트 확장 — 하한가로 차익 안 나면 기준가
    차익이 있어도 추천 제외. 점수(gap/arb)는 기준가 기준 무회귀(밴드가 점수를 바꾸지 않음 테스트).
  - digest.py: 추천 TOP 정렬·min_profit·포함 여부 전부 보수차익(profit_low) 기준. 비양수 보수차익 제외.
  - store.py v4: 밴드·차익 4컬럼 ALTER. 실DB 2521건 보존(user_version=4).
  - detail.html: '시장 가격 밴드(하한가~기준가)'·'보수 가격 기준 차익' 행.
- 증거: evidence/t4_band.txt — 실DB v4 이관, 물건별 밴드, **광교호반베르디움 보수차익 -0.01억 →
  '차익없음' 강등·추천 TOP 제외 실증**(보수 게이트 실작동), 웹 표기 True.
- 게이트: pytest 296 passed(+14), ruff 클린.
- 평가자: PASS. ⚠운영 노트 — 실DB 레거시 행은 profit_low NULL이라 **다음 전량 새로고침 전까지
  라이브 digest는 기준차익 폴백으로 동작**(새로고침 시 보수 기준 발효). LOW: matched_trades가
  트림 전 수라 T5 게이트는 밴드 실기반 표본수 기준 설계 권고(GOAL T5 노트 반영).
- 커밋: (이 항목과 함께 커밋)
- 다음: 사이클 #5 = T5 표본 부족 시 추천 금지 게이트(5건/3~4건/0~2건, config 외부화).

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
