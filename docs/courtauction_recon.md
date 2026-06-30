# courtauction.go.kr 정찰 (2026-06-30)

대법원 법원경매정보 사이트에서 **경매 물건 데이터를 어떻게 합법·저빈도로 가져올지** 확인한 1차 정찰.

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
