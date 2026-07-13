# Architecture Overview

이 문서는 에이전트/신규 참여자가 이 코드베이스를 빠르게 파악하기 위한 살아있는 문서다. 코드가 진화하면 이 문서도 갱신한다. (작성: 2026-07-07, 코드 기준: main `cf2f749`)

> ⚠️ 절대규칙: 이 레포의 어떤 파일이든 편집하면 턴 종료 전 `versions.md` 맨 위에 KST 타임스탬프 항목 1건 추가 (`CLAUDE.md` #0, 전역 PostToolUse 훅으로 강제). 자율 루프 운영 규칙은 `harness/LOOP.md`.

## 1. Project Structure

```
auction-arbitrage/
├── run.py                  # CLI 엔트리 — 차익 큐레이션 파이프라인 (콘솔/CSV/HTML/JSON)
├── run_alerts.py           # 알림 실행
├── run_backtest.py         # 백테스트 실행
├── run_digest.py           # 다이제스트(상위 매물) 실행
├── auction.db              # 프로덕션 SQLite (27.5MB, schema v5)
├── src/                    # 애플리케이션 코드 30모듈
│   # 크롤: courtauction_client.py(대법원) · courtauction_cache.py(diff) · courtauction_rights.py(권리 파서)
│   # 시세: molit_client.py(apt/연립/오피스텔) · molit_extra_client.py(단독/상업/토지) · building_register_client.py(건축물대장, 미배선)
│   # 엔진: score.py(차익 스코어) · matcher.py(시세 매칭·2선밴드) · tax.py(취득세) · config.py
│   # 서빙: web.py(Flask) · serve.py(waitress) · store.py(SQLite) · pipeline.py(오케스트레이션)
├── templates/              # Jinja2 10개 (목록/지도/상세/다이제스트/워치리스트/캘린더/통계/비교/방법론)
├── tests/                  # pytest 33파일 366케이스 + fixtures/
├── data/                   # JSON 캐시(gitignore)·설정(score_config.json)·샘플 fixture·backup/
├── docs/                   # 리서치·감사리포트·remote-access + references/(지지옥션·탱크옥션·해외)
├── harness/                # LOOP.md(사이클) · BACKLOG.md · QUESTIONS.md · STEER.md
├── scripts/                # PowerShell 운영 (start/refresh-daily/install-scheduler/check-tunnel)
├── .github/workflows/ci.yml
├── versions.md             # append-only 작업로그 (KST, 최신순)
├── info.md                 # 경매 스터디 로그 (10강 커리큘럼, untracked)
└── requirements.txt · pyproject.toml · Dockerfile · .env(.example)
```

## 2. High-Level System Diagram

```
[매일 05:30 작업스케줄러(AuctionArbitrage-DailyRefresh)]
   └─> run.py --source courtauction --nationwide --cash --live
         │
         ├─ [대법원 courtauction.go.kr 크롤] ─ 17개 시도 샤딩, 밴회피(지터·일일상한·kill-switch)
         │      └─> 캐시 diff (신규/변경/소멸) → data/courtauction_cache.json
         ├─ [국토부 실거래 API 6종] ─ 시세 비교군 조회 (아파트·오피스텔만 추정 지원)
         ├─ [차익 스코어 엔진] ─ (갭50+권리30+환금20)×신뢰계수, 2선밴드, 표본게이트
         └─> [auction.db (SQLite)] ─ scored_listings(복합PK) + raw_listings(원본보존)
                │
                ▼
[Flask 웹앱 (waitress :8000, 로컬 바인드)] ── /​ · /map · /property · /digest · /watchlist · /stats …
                │
                ▼
[Tailscale Serve] ──> https://notebiz53.tail4271f6.ts.net (tailnet-only, 폰 접속용)
                        ※ 운영 정보 — 레포 코드/문서에는 없음. docs/remote-access.md 참조.
```

## 3. Core Components

### 3.1. Frontend
- **Name**: 경매 차익 큐레이션 웹 UI
- **Description**: 서버렌더 Jinja2. 매물 목록(차익 정렬)·지도(/map)·물건 상세·다이제스트·워치리스트·매각기일 캘린더·통계·비교·방법론 페이지. 모든 응답에 `X-Data-Source` 헤더로 데이터 출처(db/sample) 노출(침묵실패 방지).
- **Technologies**: Flask 3 + Jinja2, Leaflet+OSM(지도), Pretendard 폰트
- **Deployment**: 로컬 waitress → Tailscale Serve로 폰 노출

### 3.2. Backend Services

#### 3.2.1. 수집 파이프라인 (run.py → src/pipeline.py)
- **Description**: 대법원 실경매 전국 크롤(17개 시도 샤딩, doc_id 중복제거) → 캐시 diff → 국토부 시세 매칭 → 스코어 → DB 적재(`replace_all`).
- **밴 회피 설계**: concurrency=1, 3~8초 지터, daily_cap=500, 403/비JSON 즉시중단(`CourtAuctionBlocked`), 카나리 요청, kill-switch 파일 `COURTAUCTION_STOP`.
- **Technologies**: Python 3.11+(운영 3.12/3.14), requests

#### 3.2.2. 차익 스코어 엔진 (src/score.py + matcher.py + tax.py)
- **공식**: `score = (가격갭×0.50 + 권리×0.30 + 환금성×0.20) × 신뢰계수(0.6~1.0)`
- **취득원가** = 최저입찰가 + 취득세(주택 누진·다주택 중과·비주택 4.6%). 명도비/수리비는 객관성 위해 제외.
- **신뢰 장치(T1~T5)**: 복합PK(court+case_no+item_no) · 아파트/오피스텔 한정 추정 · 비교군 scope(같은단지 같은면적만 추천 인정) · 2선 밴드(보수 차익 `profit_low`가 추천 기준) · 표본 게이트(basis<3 밴드 금지, <5 추천 제외)
- **하드게이트**: 인수금액비율>0.30 또는 유치권 → 권리 0점 + 상한 25점 + "위험"
- **등급**: 차익 유력(80+)·양호(60~79)·관심·주의·위험·차익없음·시세추정불가·미지원유형·권리미확인

#### 3.2.3. 웹 서버 (src/web.py)
- **Routes**: `/`, `/health`, `/api/listings(.geojson)`, `/api/listings/<case_no>`(다물건 시 300), `/map`, `/property/<case_no>`, `/digest`, `/watchlist`, `/calendar`, `/stats`, `/compare`, `/methodology`, `/export.csv`
- **Deployment**: `python -m src.serve` (waitress, 기본 127.0.0.1:8000; `start.ps1 -BindAll`로만 0.0.0.0)

## 4. Data Stores

### 4.1. auction.db (SQLite, WAL, SCHEMA_VERSION=5)
- **scored_listings**: PK `(court, case_no, item_no)`, 29컬럼 — 3점수·gap_rate·grade·market_scope·band_low/high·profit_low/high·sample_basis 등. 실측 3,751행. v1→v5 자동 마이그레이션.
- **raw_listings**: PK uid, 원본 raw_json 보존(PII 제거본). 실측 6,320행.

### 4.2. JSON 캐시/설정 (data/, gitignore)
- `courtauction_cache.json`(1.4MB diff 스냅샷) · `courtauction_full_cache.json`(19MB dry-run 재생용) · `coords_cache.json`(KATEC→WGS84) · `watchlist.json` · `backtest_outcomes.json`
- **코드수정 없는 튜닝**: `data/score_config.json`(ScoreConfig 오버라이드), `sample_config.json`

### 4.3. 운영 문서(사실상 데이터)
- `versions.md`(append-only 작업로그) · `info.md`(경매 스터디) · `harness/BACKLOG.md`·`QUESTIONS.md` · `evidence/`(로컬 산출물, gitignore)

## 5. External Integrations / APIs

| 통합 | 방식 | env |
|---|---|---|
| 대법원 courtauction.go.kr | POST JSON 크롤 (세션워밍+위장헤더, 준법: 차단 시 우회금지) | 키 불필요 |
| 국토부 실거래 6종 (apt/연립/오피스텔/단독/상업/토지) | apis.data.go.kr GET XML, serviceKey 로그 마스킹 | `MOLIT_API_KEY` |
| 건축HUB 건축물대장 표제부 | 클라이언트만 존재, **파이프라인 미배선** | `MOLIT_API_KEY` 공유 |
| V-World | ⚠️ `.env`에 키만 존재, **코드 사용처 0건** (용도지역/지오코더 계획) | `VWORLD_API_KEY` |
| 지도 타일 | Leaflet+OSM, 좌표변환은 pyproj 로컬(외부호출 0) | 없음 |
| Tailscale / Cloudflare Tunnel | 원격 노출 (docs/remote-access.md) | 없음 |

## 6. Deployment & Infrastructure

- **인프라**: 클라우드 없음 — 노트북 올-로컬 운영 (상시 ON 전제)
- **서빙**: waitress :8000 (로컬 바인드) → Tailscale Serve → `https://notebiz53.tail4271f6.ts.net` (tailnet-only 비공개, hyunwoojang1@github 개인 tailnet)
- **스케줄러**: Windows 작업 `AuctionArbitrage-DailyRefresh` — 매일 05:30 `refresh-daily.ps1 -Live` (전량 새로고침 = 스키마 v5 게이트가 구 데이터를 치유하는 경로). 설치 스크립트는 Disabled로 등록하나 **현재 활성(Ready) 확인됨** (2026-07-07).
- **Docker**: `Dockerfile`(python:3.12-slim) 존재하나 주 운영경로 아님
- **CI**: GitHub Actions `ci.yml` — push/PR to main 시 ruff + pytest (Python 3.12)
- **Monitoring**: `/health` + `evidence/refresh-*.log` (tee)

## 7. Security Considerations

- **Authentication**: **웹 UI 인증 없음** — tailnet 경계(사설 VPN)로만 보호. Cloudflare 공개 URL 사용 시 접근제한 필수(docs 경고).
- **Secrets**: `.env` 평문(MOLIT/VWORLD 키) — `.gitignore`·`.dockerignore` 제외 확인됨, 커밋 이력 없음. 코드 하드코딩 없음. LOOP.md 절대금지: 에이전트의 `.env` 수정 금지.
- **크롤 준법**: 공공누리 제4유형 전제, PII 미저장(`sanitize_row`), 차단 시 즉시중단·우회금지, 유료사이트 크롤 금지(LOOP.md).
- **웹 방어**: open redirect 방어(same-host referrer), debug 기본 off(waitress), `X-Data-Source` 출처 투명성.

## 8. Development & Testing Environment

- **Local**: `pip install -r requirements.txt` → `python run.py --source sample`(오프라인) 또는 `--source courtauction --live`(실크롤, 절제 규칙 준수) → `python -m src.serve`
- **Testing**: pytest **366케이스**/33파일 — 크롤러·molit 파싱·스코어·밴드·게이트·store·web·세금·백테스트 등. CI에서 자동 실행.
- **Quality**: ruff (line-length 110, E/W/F/I/B/UP)
- **자율 루프**: `harness/LOOP.md` 사이클(레퍼런스 탐색→모방구현→감사→조이기), Default-FAIL 증거 게이트, `AGENT_STOP`/`COURTAUCTION_STOP` kill-switch, STEER.md 개입 채널. 전국크롤+상세크롤 동시 실행 금지.

## 9. Future Considerations / Roadmap

- **권리분석 라이브 배선**: `courtauction_rights.py` 파서는 완성·테스트 통과이나 물건상세 3문서 라이브 페처 미연결 → 현재 실크롤 물건 전부 "권리미확인" 보수강등. 최우선 보강 후보.
- **V-World 통합**(키만 있음) · **건축물대장 배선**(노후도/위반건축물 → 스코어 반영)
- **신뢰계수 다월보정** · **호가 데이터 수급**(QUESTIONS.md Q2 미해결 — 실거래는 후행지표)
- **DB 레거시 행 치유**: 과거 적재분 2,521행이 신 게이트 미적용(market_scope='') — 05:30 전량 새로고침 발효로 해소 설계.
- **이용약관 확인**(운영자 몫) · 알림(`run_alerts.py`) 실발송 경로 검증

## 10. Project Identification

- **Project Name**: auction-arbitrage — 부동산 경매 차익 큐레이션 ("시세 > 최저입찰가" 갭 자동 발굴, 초보자 아파트 경매 1차 필터)
- **Repository**: https://github.com/hyunwoojang1/realestate_auctions (git push는 운영자만 — LOOP.md)
- **Primary Contact**: 장현우 (개인 프로젝트)
- **Date of Last Update**: 2026-07-07

## 11. Glossary / Acronyms

- **최저가/최저입찰가**: 해당 회차 입찰 하한. 유찰 시 저감.
- **감정가**: 법원 감정평가액. **buffer**: 현금상한 대비 감정가 검색 여유율.
- **2선 밴드**: 시세 추정을 단일값이 아닌 band_low(트림 최저 평단가)~band_high(중앙값)로 제시. 보수 차익 `profit_low = band_low − 취득원가`.
- **표본 게이트(basis)**: 최근성 필터+이상치 트림 후 실사용 비교 거래 건수. 3 미만 밴드 금지, 5 미만 추천 제외.
- **하드게이트**: 인수금액비율>30% 또는 유치권 → 점수 상한 25 + "위험".
- **복합 PK**: `court+case_no+item_no` — 한 사건 여러 물건 대응.
- **KATEC**: 대법원 좌표계. pyproj로 WGS84 변환(coords_cache.json).
- **dry-run 캐시**: `*.dryrun.json` 분리 경로 — 프로덕션 캐시 미오염 원칙.
