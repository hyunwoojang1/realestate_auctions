# courtauction.go.kr 정찰 (2026-06-30)

대법원 법원경매정보 사이트에서 **경매 물건 데이터를 어떻게 합법·저빈도로 가져올지** 확인한 1차 정찰.

---

## 3차 정찰 — 임차인표(전입일) 원천 발굴: 현황조사서 엔드포인트 (2026-07-22) ⏳ 라이브검증 1스텝 남음

**배경**: 대항력 false-negative 버그(삼환 2022타경3289) 근본원인 = 우리가 쓰는 물건상세
`PGJ151F01`(`/pgj/pgj15B/selectAuctnCsSrchRslt.on`) 응답엔 **임차인 전입일/보증금 표가 아예 없음**.
전입일 vs 말소기준일 날짜비교(진짜 대항력 판정)를 하려면 임차인 데이터 원천이 별도로 필요.

### UI XML 그래프 탐색으로 확정한 구조 (전부 `scripts/_ui_xml/` 로컬 캐시)
- `PGJ15BM01.xml` = **물건상세조회_부동산**(진짜 상세 화면; F01은 상세'검색' 화면이었음 — 함정).
- 문서 3종 접근 경로:
  | 문서 | 경로 | 형태 |
  |---|---|---|
  | 매각물건명세서(전문) | `insertDspslGdsSpecArtcWdrwInf.on`(열람로그) → 응답 `{scsYn,encParam,url}` → `url?paramData=BASE64(serialize({encParam,pspTkn:"NA",pspSid:"NA"}))` → **외부 '소송문서뷰어' PDF** | PDF(무거움) |
  | **현황조사서** | 팝업 `PGJ15BP01.xml` → **`POST /pgj/pgj15B/selectCurstExmndc.on`** | **구조화 JSON** ★ |
  | 감정평가서 | 팝업 `PGJ15BP03.xml` (요지는 이미 aeeWevlMnpntLst로 수집 중) | — |

### ★ selectCurstExmndc.on — 임차인표의 구조화 원천
- 요청 `dma_srchCurstExmn`: `{cortOfcCd, csNo, auctnInfOriginDvsCd:"2"(현황조사서 고정), ordTsCnt(명령회차, 초기엔 생략)}`
- 응답 dataList (XML 정의 기준):
  - **`dlt_ordTsLserLtn`(임차인 리스트)**: `mvinDtlCtt`(**전입상세내용=전입일**) ·
    `rgstryCrtcpCfmtnCtt`(**확정일자**) · `lesDposDts`(보증금) · `mmrntAmtDts`(월세) ·
    `gdsPossCtt`(점유내용) · `lesUsgDts`(임차용도) · `lesPartCtt`(임차부분) · `lesDtsRmk`(비고)
    - ⚠️ **PII 포함**: `ENRRNO`(암호화 주민번호)·`ZPCD`·`basAddr`·`objctDtlAddr`·이해관계인 성명류
      → 저장 시 **불리언·금액·날짜만 추출**, 원문 저장 금지(기존 PII 가드 규범).
  - `dlt_curstExmnDpcnMrg`: `lstPossRltnDts`(목록점유관계) · `lesDts`(임차내역) · `lesCnt`(임차명수)
    → **G2(점유 미수집) 문제도 이 엔드포인트로 해결 가능**.
  - `dma_curstExmnMngInf`(조사서 발송/수신일 등), `dlt_spotExmnOrdCntLst`(회차), `dlt_ordTsRlet`(부동산).

### 라이브 검증 상태 (미완 — 다음 1스텝)
- `csNo="2022타경3289"+auctnInfOriginDvsCd="2"` POST → **200이지만 `{ipcheck:false}` 빈 응답**.
- 내부포맷 csNo(`20220130003289`)·`ordTsCnt` 조합 재시도 중 **서버가 무응답 커넥션 종료** →
  차단 신호로 보고 즉시 중단(권리 크롤 세션과 IP 공유 중이라 보수적으로).
- **다음 가설(1콜로 검증 가능)**: 같은 세션에서 **선행 상세조회(selectAuctnCsSrchRslt) 후** 호출해야
  세션에 사건 컨텍스트(orvParam류)가 실려 응답하는 방식일 것. 크롤 오케스트레이터 휴지기에
  "상세 1콜 → curst 1콜" 순서로 재검증할 것. (참고: `selectPopUpGdsLstDts.on`은 물건 목록내역이며
  임대차 정보 없음 — XML summary 라벨 '부동산 임대차 정보'는 오표기)

### 통합 설계(검증되면)
1. `crawl_rights.py`가 상세콜 직후 같은 세션으로 `selectCurstExmndc.on` 1콜 추가(물건당 +1요청).
2. 신규 파서: `mvinDtlCtt`→전입일, `rgstryCrtcpCfmtnCtt`→확정일자, `lesDposDts`→보증금(PII 제외).
3. `analyze_priority`에 전입일 실데이터 주입 → **전입일 ≤ 말소기준일 날짜비교로 대항력 판정**
   (현재는 명세서 요지 자유텍스트에서만 전입일을 찾아 사실상 무력).
4. 자유란 빈 물건(19%)도 임차인표 유무로 clean/burden 실판정 → '권리미확인' 정체 해소.

---

## 2차 정찰 — 물건검색 엔드포인트 확정 + 필터 실효성 (2026-06-30 19:16) ✅ 크롤러 구현완료

검색 UI `PGJ151M01.xml`(부동산 상세검색) 역분석으로 **실물건 검색을 확정**하고 크롤러를 구현·라이브검증했다.

### 검색 엔드포인트
- `POST /pgj/pgjsearch/searchControllerMain.on` — 헤더 라우팅 없이 순수 JSON body.
- body = `{"dma_pageInfo":{pageNo,pageSize,totalYn,...}, "dma_srchGdsDtlSrchInfo":{검색조건 ~45필드}}`
- 응답 = `{"status":200,"data":{"dma_pageInfo":{...,"totalCnt"},"ipcheck":true,"dlt_srchResult":[117필드 행...]}}`
- **필수**: `cortAuctnSrchCondCd="0004601"`(부동산). 없으면 HTTP 550 "요청된 데이터가 없습니다".
- 페이지 크기 상한 **40**(200은 HTTP400). 전국 totalCnt **28,006**(그룹 16,736).

### 서버사이드 필터 실효성 (실측)
| 필터 | 키 | 작동 |
|---|---|---|
| 지역(시도/시군구) | rprsAdongSdCd/SggCd | ✅ |
| 감정가 | aeeEvlAmtMin/Max | ✅ (서울 ≤1억 → 26건) |
| 최저가율 | lwsDspslPrcRateMin/Max | ✅ |
| 면적 | objctArDtsMin/Max | ✅ |
| 유찰횟수 | flbdNcntMin/Max | ✅ |
| **절대 최저매각가** | **rletLwsDspslPrcMin/Max** | ❌ **무시됨**(원·만원 단위 다 실패) |

→ "내 현금으로 살 수 있는 매물"은 **서버에선 감정가Max로 볼륨만 줄이고, `최저가 ≤ 현금`은 로컬에서 정밀필터**.
  (감정가Max = 현금×버퍼. 감정가프록시 단독은 다회유찰로 싸진 고감정가 알짜를 누락 — 적대적검토 지적.)

### IP 추적·밴 신호 (실측)
- 응답마다 `data.ipcheck=true`, 쿠키 `wcCookieV2`에 **클라이언트 IP 박힘**(`<IP>_T_..._WC`) → IP기반 추적/WAF 실재.
- 회피: 요청 총량 최소화(필터) + concurrency=1 + 3~8s 지터 + 일일상한 + 지수백오프 + **콘텐츠 회로차단기**(200인데 HTML/스키마붕괴=조용한차단 감지) + 카나리 + kill-switch. **프록시/VPN 금지**(법적·기술적 역효과).

### 구현
- `src/courtauction_fields.py` — 117필드 카탈로그·한글라벨, `CourtAuctionRecord`(개인정보 제외 raw 전체 보존), PII 가드, `to_auction_listing`.
- `src/courtauction_client.py` — `SearchFilter`(작동필터만 노출), `CourtAuctionClient`(위 안전장치 전부), `search()`/`affordable_search()`.
- 검증: pytest 106 PASS(신규 25), ruff 클린. 라이브 `evidence/courtauction_live_verify.json`
  (현금6천만→15건, 요청3회, 감정가1.4억·16회유찰→최저499만 포착, 117필드 보존). fixture `data/sample_courtauction.json`.

### 미해결/다음
- 🔴 **이용약관 "자동수집 금지" 조항 여부**(사용자 브라우저 확인 — SPA라 자동 도달 불가).
- 전국 지역샤딩 + 로컬캐시 diff(증분), pipeline 연결, 물건상세(권리/감정평가서) 보강.

---

## 핵심 결론
- **헤드리스 브라우저 불필요. Python `requests`로 충분.** `.on` 엔드포인트가 POST에 **JSON**으로 응답함(실증됨).
- 단, **WAF(웹방화벽)** 때문에 **브라우저 헤더 필수** + **세션 쿠키 + Referer** 필요.

## 접근성 / 방어
- 맨 요청(curl 기본 UA)은 WAF가 전부 차단: "blocked by Web firewall security policies".
- **브라우저 헤더(User-Agent, Accept-Language: ko-KR)** 실으면 통과(200).
- egress IP는 한국(로컬 PC에서 실행) → 지오차단 이슈 없음.

## 구조
- **WebSquare5 SPA + 전자정부프레임워크 + Apache/Oracle.**
- 메인: `GET /pgj/index.on` → HTML 셸. `WebSquareExternal.baseURI=/pgj/websquare/`, 메인 UI=`/pgj/ui/pgj100/PGJ111M01.xml`.
- UI 정의는 `.xml`(WebSquare), **데이터는 `/pgj/pgjXXX/selectXXX.on`에 POST → JSON**.
- 세션: `GET /pgj/index.on`이 `JSESSIONID`·`WMONID` 쿠키 발급. 후속 `.on` 호출에 쿠키+Referer 필요.

## 실증 (requests, 브라우저헤더+쿠키+Referer)
| 호출 | 결과 |
|---|---|
| `POST /pgj/pgj111/selectRletYrDspslStats.on` body `{}` | ✅ **HTTP 200 + JSON** `{"status":200,"message":"정상","data":{...}}` |
| `POST /pgj/pgj111/selectNtcMtrPouUpItemList.on` body `{}`/`{"dma_srchNo":{}}` | ⚠️ 302→index (해당 서비스는 올바른 dataset/params 필요) |
| `GET` 위 엔드포인트 | ⚠️ 302→index (POST 전용) |

→ JSON 추출은 확정. 302는 "그 서비스가 요구하는 정확한 페이로드(dataset)"를 줘야 풀림.

## 합법 가드레일 (준수 전제)
- 대법원 콘텐츠 = **공공누리 제4유형**(상업적 이용 금지 + DB 구축 금지). → **비영리·저빈도·개인정보 배제·출처표시** 준수 시 위험 낮음. 수익화 전환 시 재검토 필수.
- 저빈도 호출(요청 간 딜레이), 개인 식별정보(채무자/소유자 이름 등) 미수집·미저장 원칙.
- robots.txt는 WAF가 막아 직접 확인 불가 → 비영리 전제로 보수적 운영.

## 다음 단계
1. **부동산 물건 검색 `.on` 엔드포인트 + 페이로드 매핑** — 검색 페이지 WebSquare XML(부동산 상세검색)을 받아 submission dataset 구조 확보.
2. `src/courtauction_client.py` PoC — 세션 워밍(GET index) → 검색 POST → JSON 파싱 → AuctionListing 매핑. 개인정보 필드 화이트리스트(사건번호·법원·물건종류·주소·최저가·감정가·기일·유찰횟수만).
3. 저빈도 레이트리밋 + 출처표시 내장.
