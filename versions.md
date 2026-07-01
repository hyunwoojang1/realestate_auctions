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
