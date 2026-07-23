# PROGRESS — auction-arbitrage 배포준비(feat/deploy-prep)

> 매 세션 시작 시 이 파일을 먼저 읽는다. 한 번에 기능 하나. 완료 시 증거 확인 → 커밋 → versions.md 기록.
> 이번 세션 범위 = GOAL_DEPLOY.md 밤샘 배포준비 루프(코어 A·B·C + 스트레치 D·E·F).
> 그 이전(F1~F9, P1~P5, W1~W4, V1~V3, X1~X3, courtauction 크롤러)은 완료 상태이며 상세는 versions.md 참조.

## Done
밤샘 배포준비 루프에서 완료(전부 오프라인 검증 — courtauction/국토부 실서버 무호출, feat/deploy-prep 로컬 커밋).

- **[A] 정기 새로고침 스케줄러** (b987529) — `scripts/refresh-daily.ps1`(run.py --source courtauction
  --nationwide --cash --live 래퍼, AUCTION_DB·PYTHONUTF8, 로그를 evidence/), `install-scheduler.ps1`(작업
  스케줄러에 **Disabled 상태로** 등록, 활성화는 사용자 수동), `uninstall-scheduler.ps1`. run.py `--from-cache`
  오프라인 dry-run 경로 추가(캐시/fixture만, 프로덕션 캐시 미오염 *.dryrun.json 분리). 증거 scheduler_dryrun.txt.
- **[B] 프로덕션 서빙(waitress)** (97b1ff3) — `src/serve.py`(waitress.serve, debug/reloader 구조적 off),
  `src/web.py` __main__ 가드(AUCTION_DEBUG env flag로만 debug on), requirements.txt waitress>=3.0,
  Dockerfile CMD를 flask dev server → `python -m src.serve`로 교체, `scripts/start.ps1`(기본 127.0.0.1
  로컬전용, -BindAll 시에만 0.0.0.0). 증거 serving_health.txt — 실 서브프로세스 부팅 → GET /health 200,
  /api/listings 200 JSON, debug=False.
- **[C] 신뢰계수 표본 개선** (77b10d8) — 원인 규명(docs/confidence-analysis.md: 라이브 매칭 빈약 원인=
  matcher 면적밴드 과필터 + 수집 개월수 하드코딩. 신뢰계수 공식 자체는 이미 표본수에 단조 비감소). 튜닝
  외부화(src/config.py SampleConfig: live_months/area_band, 우선순위 CLI>env>JSON>기본값). run.py
  `--live-months`/`--area-band`. 증거 confidence_samples.txt.
- **[D] 권리필드 파서 뼈대** (스트레치) — `src/courtauction_rights.py`(물건상세 3문서→인수금액/특수권리/
  대항력/점유유형/감정가 추출, detector·apply_rights 불변패턴·gate_reasons). fixture 5종. 증거 rights_parser.txt.
- **[E] 시세유형 확대** (스트레치) — `src/molit_extra_client.py`(단독다가구 sh·상업업무 nrg·토지 land
  endpoint + 파서), `src/building_register_client.py`(건축물대장 표제부: 노후도·위반건축물). fixture 4종.
  증거 molit_types.txt.
- **[F] 터널 접속 가이드** (스트레치, d774a5c) — `docs/remote-access.md`(Tailscale 사설VPN / Cloudflare
  Tunnel 두 방식, 바인드주소·방화벽·보안·트러블슈팅), `scripts/check-tunnel.ps1`(순수 로컬 진단, 외부호출 0).
  증거 tunnel_guide.txt.

- **무회귀/린트**: `pytest -q` → **162 passed**(밤샘 시작 120대 → 신규 테스트 누적), `ruff check .` → 클린.
- **평가자**: 코어 A·B·C 신선-컨텍스트 패널 2인 모두 PASS. 스트레치 D·E·F PASS.

## In progress
- (없음 — **밤샘 UX 루프 완료·종료**, 2026-07-16 01:50. 아래 Done 참조. 재개는 QUESTIONS Q3/Q4 답변 후.)

## 최근 완료 (2026-07-23) — 신규 기능: 입찰가 시뮬레이터
- **배경**: 프로그램 전수 점검에서 최대 공백 = "그래서 얼마 써야 하나". 목록 차익은 취득세만 반영한
  표면차익이고, 대출·명도·양도세는 계산에 아예 없었다(`LTV` 코드 검색 0건).
- **범위(사용자 확정)**: 출구 = 매도만(월세 데이터 0건이라 임대수익률 불가) · **상세페이지 전용**
  (목록·스코어·등급 로직 무변경 — 표면차익 정의 유지).
- **산출물**: `docs/tax-auction-knowledge.md` §5(SSOT) · `src/bidsim.py`(순수함수 + `breakeven_bid`
  이분탐색) · `GET /api/bidsim`(DB 무접근) · `report.won_fine` · `detail.html` 시뮬레이터 UI.
- **증거**: 신규 테스트 49건 + 전체 **731 passed** · ruff 클린 · 실데이터 라이브(죽전자이2차 표면 3.69억
  → 세후 2.14억, 손익분기 5.62억) · Playwright 라이브 재계산 PASS·콘솔에러 0 · 비포/애프터 캡처.
- **상태**: 로컬 커밋 완료, **미푸시** — 사용자 배포 결정 대기(CLAUDE.md 0.5: push = 배포까지).
- **후속 후보**: B1 시장국면 카드(KB 5년 시계열 5,160행·단지정보 2,559건이 수집만 되고 화면 미사용),
  A2 낙찰결과 수집(BACKLOG B5 — 손익분기에 실제 낙찰가율 앵커 제공).

## Done (밤샘 UX 루프 = GOAL_UX.md, 2026-07-16, 로컬 커밋만·미푸시)
5사이클, **확정 수정 13건 + 오탐 기각 2건**. 전 사이클 pytest 519 pass·ruff clean. 상세 versions.md.
- **사이클1**(ca8c5e6): 사진 확대 라이트박스·빈상태('사진 미수집')·CLS 치수·sale_date 가드.
- **사이클2**(a60a595): 대비 AA(U3)·재매각 보증금 경고(U5). **U6 이중폼 오탐 기각**.
- **사이클3**(c661f73): 지도 키보드접근(U1)·차트 터치 툴팁(U2).
- **사이클4**(725d482): 홈 로딩 저위험(캐시TTL·번들 -83MB·create_app 이중생성 제거).
- **사이클5**(e95782b): 코드리뷰+Ponytail 재감사(적대검증) → 자기결함 3건(포커스트랩·터치 축감지·중복CSS).
- **오탐 2건 기각**(harness/REJECTED.md): ①사진 photo_url 컬럼부재→400(라이브 반증) ②U6 이중폼(이미 보존됨).
  재감사에선 code-review의 medium 2건을 검증관이 low로 하향(과대평가 교정). CRITICAL/HIGH·미해결오탐 0.
- **운영자/사용자 게이트(harness/QUESTIONS.md)**: Q3 "사진 특수문자" 실제 텍스트 확인(B4 대기) · Q4 홈쿼리 서버측 limit 재구조화(회귀위험).
- (이전 데이터 신뢰도 개편 루프 T1~T8 완료, 2026-07-03. 상세 GOAL_DATA_TRUST.md)

## 최근 완료 (2026-07-13)
- **홈 검색 변수 추가: 면적(평대)+유찰** (06:58 KST) — 홈 검색에 '면적(전용)' 평대 브래킷
  (~20/20/30/40/50평+)과 '유찰'(1·2·3회+) 셀렉트 추가. query.apply_filters min_area/max_area/
  min_fails + query.area_bounds(1평=3.3058㎡). 데이터 커버리지 실측 후 도입(area 682/682, fail_count
  449건). 증거: 신규 5건 통과, 전체 453 passed(1 env-fail), Playwright 실데이터(30평대+1회+ → 99~132㎡
  정확 필터). versions.md 06:58 참조. 보류: 차익률·예산하한·신뢰도(사용자가 면적·유찰까지만 지시).
- **지도 3단 스코프(차익 양수만) + pmap 청소** (00:09 KST) — ① 지도 기본을 '차익 양수만'으로 조임:
  query.positive_only(효과차익=보수차익−인수금액>0, 금액 미상은 제외) 신규, geojson 3단(profit 267/
  evaluable 861/all 7,967) + feature uncertain 플래그, map.html 3버튼 토글 + '−α(인수 미상)' 표시.
  **권리분석 크롤이 인수금액을 채우는 대로 267 집합이 요청마다 자동 재계산**(정적 목록 아님). ② 죽은
  pricemap(상세 시간축 차트로 교체됨) 제거. 증거: 신규 3건 통과, 전체 448 passed(1 env-fail),
  Playwright 실데이터(267/861/7,888핀). versions.md 00:09 참조.

## 최근 완료 (2026-07-12)
- **지도 개편 ③: 차익후보 기본 + 지역별 카운트** (23:33 KST) — 지도가 옛 방식(파라미터 없는 geojson
  통짜 호출, 7,888핀 89% 노이즈)이던 것을 홈과 동일 원칙으로 정렬. `/api/listings.geojson` 기본=차익후보
  (평가가능)만·`all=1` 전체, 응답에 `by_sido` 집계+feature `sido`. map.html에 차익후보만↔전체 토글 +
  '지역별' 카운트 칩(클릭 시 시도 필터/줌). query.count_by_sido 신규. 증거: 신규 5건 통과, 전체 453
  passed(1 env-fail), Playwright 실데이터 3장(차익후보 861·전체 7,967·서울 834). versions.md 23:33 참조.

## 최근 완료 (2026-07-09)
- **경매 필수지식 사이트 반영** (11:31 KST, 미커밋) — info.md 강의(권리분석 4·5·6강 + 세금 9강)를
  사이트에 녹임. `templates/guide.html` 신규(`/guide` 라우트, nav '가이드'): 낙찰 전 5문 체크리스트 +
  권리분석 가이드(말소기준·대항력 4상한·명세서 두 칸·유치권) + 세금 가이드(양도세 단기세율·매매사업자
  비교과세·표면차익 한계·실거주 vs 재고). `detail.html` 3탭 연동(차익근거·세금·권리 → 가이드 앵커,
  권리미확인 조건부 문구). 증거: 스모크 통과 + pytest 366 passed + ruff 클린.

## Next
**운영자 액션 대기**:
- 내일 05:30 정기 새로고침(활성 확인)이 신규 게이트 필드(scope/basis/밴드)를 채움 → 보수 기준 전면 발효.
  새로고침 후 / 화면에서 '구버전 채점 데이터' 배너가 사라지는지 확인 권장.
- 아침 리뷰 후 push (신뢰루프 커밋 9개, ab23016~).
- QUESTIONS Q1(지도 좌표계)·Q2(호가 데이터 수급) 답변.

루프 재개 시 백로그: [ready] B18(감사 MEDIUM/LOW 29건 트리아지)·B10~B17, [blocked] B3(Q1 대기).
B14는 T8에서 핵심 해소(상세 라우팅) — 잔여는 watchlist/compare 키 uid 전환.

(이전) 자율 성장 사이클 #1~#12 종료(2026-07-03 04:16): B4·B1·B2·B8·B6·B7·B9 완료.

이전 배포준비 루프의 잔여 **사람만 할 수 있는 작업**(무인 불가):
- **push** — `feat/deploy-prep`(9dc9add~d774a5c 6커밋) 아침 리뷰 후 push. **밤샘 중 push 안 함(정책).**
- **라이브 1회 검증** — 통제된 단일 라이브 실행으로 D 권리파서(물건상세 HTML)·E 확장 실거래 XML/건축물대장
  실응답 태그 구조 확인 → 파서 정규식/키워드 실데이터 보강, matcher·score에 배선.
- **터널 라이브** — Tailscale 또는 cloudflared 설치 후 폰 접속 확인(check-tunnel.ps1로 사전 진단).
- **스케줄러 활성화** — 이용약관 확인 후 `Enable-ScheduledTask`로 매일 새벽 새로고침 켜기(현재 Disabled 등록).
- **후속 튜닝** — 라이브 표본수·신뢰계수 분포 확인 후 area_band/live_months 실튜닝.

## Notes
- 환경: Windows 11, Python 3.14.5(.venv). 실행 시 `PYTHONUTF8=1` 권장. PS5.1 한글 파싱 위해 scripts/*.ps1은 UTF-8 BOM.
- 브랜치: `feat/deploy-prep`. 밤샘 정책 = build/test는 오프라인(캐시·fixture)만, 실서버 무호출. push는 아침에.
- 밤샘 정지조건(GOAL_DEPLOY): 각 코어 빌더↔평가자 최대 3회, AGENT_STOP 파일 있으면 즉시 중단. 이번 세션은
  AGENT_STOP 없이 A·B·C·D·E·F 전부 PASS로 정상 종료.
- 명령: 테스트 `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q` / 린트 `.venv/Scripts/python.exe -m ruff check .`
- 이전 단계 요약: 코어 엔진(F1~F9)·프로덕션 강화(P1~P5)·사이트화(W1~W4)·검증알림(V1~V3)·정확도운영(X1~X3)·
  courtauction 실경매 크롤러+전국샤딩+웹서빙 전부 완료(main 이전, push fac2265). 상세는 versions.md.
