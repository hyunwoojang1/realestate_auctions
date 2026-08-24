# Architecture Overview

이 문서는 에이전트/신규 참여자가 이 코드베이스를 빠르게 파악하기 위한 살아있는 문서다. 코드가 진화하면 이 문서도 갱신한다. (전면 재작성: 2026-08-24 — 구판(2026-07-07)은 "클라우드 없음·올로컬"이었으나 실제는 **로컬 수집 + 클라우드 서빙** 하이브리드가 된 지 오래라 전부 다시 썼다.)

> ⚠️ 절대규칙: 이 레포의 어떤 파일이든 편집하면 턴 종료 전 `versions.md` 맨 위에 KST 타임스탬프 항목 1건 추가 (`CLAUDE.md` #0). push했으면 같은 턴에 `bash scripts/deploy_prod.sh`로 배포까지(#0.5). 표본으로 전체를 단언하지 말 것(#0.7).

## 1. 한 장 요약 — 무엇이 어디서 도는가

```
[노트북 (수집·채점·원본 보관)]                     [클라우드 (서빙 전용)]
                                                 
 매일 05:30 작업스케줄러                            Vercel (icn1, api/index.py = Flask)
  └ refresh-daily.ps1                              │  ← 웹/폰이 보는 유일한 경로
     ├ [0/5] 백업 3계층 (backup_db.py)             │  https://auction-arbitrage-
     ├ [1/5] run.py 전국 크롤+채점                  │   hyunwoo-jang-s-projects.vercel.app
     ├ [2/5] 권리 크롤 (crawl_rights)              │
     ├ [3/5] 임차인 크롤                            ├─ Supabase Postgres (REST로만 접근)
     ├ [4/5] 네이버 증분 (crawl_naver)             │   auction_scored_listings·rights·
     └ [5/5] 재채점 + 사진 도달성 체크               │   tenants·survey·naver·building·sold
                                                  │
 auction.db (SQLite 481MB — 원본·이력 전량)  ──────┤   ↑ store_rest.py 가 크롤 후 미러
 data/*.json 캐시 (molit·naver·좌표)              │   (품질 게이트 10종 all PASS 시에만)
                                                  │
                                                  └─ Cloudflare R2 — 물건사진 3.8만장
                                                      (**유일 서빙 경로**, Supabase 원본 삭제됨)
```

**핵심 계약**: 클라우드는 로컬의 **미러**다. 진실은 항상 로컬 `auction.db`에 먼저 쓰이고,
품질 게이트를 통과한 것만 Supabase로 나간다. 웹은 Supabase만 읽는다(Vercel엔 DB 파일이 없다).
사진은 R2가 원본이자 서빙 경로다(2026-08 Supabase Storage에서 이전 완료, 원본 삭제).

## 2. Project Structure

```
auction-arbitrage/
├── run.py                  # CLI 엔트리 — 크롤→채점→적재→게이트→미러 파이프라인
├── api/index.py            # Vercel 함수 엔트리 (src.web create_app 위임, maxDuration 60)
├── auction.db              # 로컬 SQLite (481MB, WAL) — 원본·이력의 단일 진실
├── src/                    # 46모듈
│   # 크롤: courtauction_client(대법원)·courtauction_detail/fields/rights(상세·정규화·권리 파서)
│   #       molit_client·molit_extra_client(국토부 6종)·naver_client(KB시세·호가)·building_info(대장)
│   # 엔진: score(차익)·matcher(시세 매칭·2선밴드)·tax(취득세)·bidsim(입찰가 시뮬)·query(파생)
│   # 적재: store(SQLite)·store_rest(Supabase REST 미러)·data_gates(게이트 10종)·photo_store(R2)
│   # 서빙: web.py(Flask 1,872줄 — blueprint 분리 예정)·serve(waitress 로컬용)
├── deploy/                 # 배치 크롤러 (crawl_rights·crawl_naver·migrate_photos_to_r2 등)
├── templates/              # Jinja2 15개 (목록/지도/상세/낙찰/워치리스트/캘린더/통계/방법론…)
├── tests/                  # pytest 84파일 ~1,196케이스 — pre-commit 게이트가 전체 실행
├── scripts/                # 운영 PowerShell/bash — refresh-daily·deploy_prod.sh·backup_db.py·
│                           #   watchdog·notify·install-scheduler·verify_claims(보고 검증)
├── harness/                # LOOP.md·BACKLOG·QUESTIONS·STEER·DOC_SYNC_QUEUE·ALERTS.log·audit/
├── docs/                   # 감사 리포트·QA·naver-incremental·tax 지식·references
└── versions.md             # append-only 작업로그 (KST 최신순) — 규칙상 모든 편집이 남는다
```

## 3. 수집 파이프라인 (노트북)

- **매일 05:30** `AuctionArbitrage-DailyRefresh`(작업스케줄러) → `refresh-daily.ps1 -Live`.
  0단계로 **백업 3계층**(pre-refresh 3슬롯·daily 7슬롯·주간 D:드라이브+R2 4슬롯)이 먼저 돈다.
- **대법원 크롤**: 17개 시도 샤딩, 밴 회피(concurrency 1·지터·일일상한 500·403 즉시중단·
  kill-switch `COURTAUCTION_STOP`). 캐시 diff로 신규/변경/소멸 판정.
- **권리·임차인·사진**: `crawl_rights`(일 300 + stale 재보강 + 현황조사서 120) — 사진은 여기서
  R2로 업로드. **낙찰 보존**: 소멸분 중 매각기일 경과분을 `sold_listings`로 스냅샷(exit 4 계약).
- **시세**: 국토부 실거래 6종(1순위) + 네이버 KB시세·호가(폴백·확정 comps, 증분 갱신).
- **적재 안전장치**: 전량교체 4중 가드(부분수집 강등·커버리지 플로어·0건 보존·고아 정리) →
  **품질 게이트 10종** + 침묵실패 카나리(필드 정상비율·PII 미등재 경고·MOLIT 실패율) —
  all PASS일 때만 Supabase 미러. 상세는 README 해당 절이 단일 출처.

## 4. 서빙 (Vercel + Supabase + R2)

- **Vercel**: `api/index.py`가 Flask 앱 전체를 서빙(리전 icn1, maxDuration 60). 배포는
  **CLI 전용** — GitHub 자동배포 없음. `scripts/deploy_prod.sh`가 지정 커밋의 깨끗한 git
  worktree에서 `vercel deploy --prod` 실행 후 `/health` 확인(구 stash 방식은 데이터 소실
  사고로 2026-08-24 폐기, deploy.ps1은 위임 래퍼만 남음).
- **Supabase**(프로젝트 ref `trajmfklbyarbkiljogj`): Postgres를 REST(PostgREST)로만 접근.
  `store_rest.py`가 미러 쓰기·서빙 읽기 모두 담당. 컬럼 집합은 `store._COLS` 단일 출처
  (드리프트는 `tests/test_store_schema_drift.py` 계약이 커밋 게이트에서 잡는다).
- **R2**: 물건사진 37,850장+ 의 원본이자 유일 서빙 경로. `photo_store._r2_request`(SigV4).
  백업 zip도 `backups/` 경로로 R2에 올라간다.
- **신선도 정직화**: `/health`가 `data_asof`·`data_age_hours`·`data_stale`(>36h) 노출, 전
  페이지 36h 배너. 클라우드 구성인데 샘플 폴백이면 **503 degraded**(조용한 낡은 데이터 금지).
- **성능**: 콜드 완화용 `warm_caches()` 병렬 워밍(홈+API 계열), WarmPing 작업이 5분마다 핑.
  서비스워커 v2(캐시 정직성 계약), SQLite 보조 인덱스 3종.

## 5. 보안

- **인증**: 워치리스트 5라우트 + `/find` 라이브 조회는 `AUCTION_ADMIN_KEY` 가드 —
  `/admin/login?key=…` 1회 방문으로 1년 쿠키(`aak`). 클라우드에서 키 미설정이면 **fail-closed**.
- **Rate limit**: 비운영자 120req/60s (`AUCTION_RL_MAX`/`AUCTION_RL_WIN`).
- **PII 3층 방어**: 키 제거(sanitize_row)·자유텍스트 성명 마스킹(적재 전)·현황조사서 미접근.
  커밋 게이트에 PII 잔여 스캔 포함. 상세·사고 연대기는 README PII 절.
- **Secrets**: `.env` (MOLIT/VWORLD/SUPABASE_URL·SECRET_KEY/R2_* 5종/AUCTION_ADMIN_KEY/
  SUPABASE_ACCESS_TOKEN). 코드 하드코딩 없음. 에이전트의 `.env` 수정 금지(LOOP.md).

## 6. 운영 자동화 (하네스)

| 장치 | 무엇 | 실패 시 |
|---|---|---|
| pre-commit 게이트 | scratch/DB 차단→문서 드리프트→ruff→PII 스캔→**전체 pytest** (~40초) | 커밋 차단 |
| DOC_SYNC_QUEUE | 크롤러/판정 코드가 문서 없이 커밋되면 자동 적재 — 세션이 소비 | 큐에 적체 |
| Watchdog (30분) | DailyRefresh 정체·크롤 ABORT·서빙 다운·push 밀림 감시 | ntfy 푸시 |
| notify.ps1 | 파이프라인 종료·게이트 FAIL 알림 (ntfy + harness/ALERTS.log) | — |
| 백업 3계층 | pre-refresh 3·daily 7·weekly D:+R2 4슬롯 (`backup_db.py`) | 알림 |
| verify_claims.py | "고쳤다" 보고 전 모집단 전수 검증(--prod 지원) — CLAUDE.md §0.7 | 보고 금지 |

스케줄러 4작업(DailyRefresh 05:30·WarmPing 5분·Watchdog 30분·RightsCrawl-Once 수동)은
2026-08-24 폴더 이전(`장현우\개인-프로젝트\경매\`)으로 전부 재등록, 배터리 옵션 포함.

## 7. 알려진 부채 (다음 대수술 후보)

- `web.py` 1,872줄 단일 파일 → blueprint 분리 (감사 코드품질 CRITICAL)
- `store.py`/`store_rest.py` 이중화 → Protocol 통합 (현재는 `_COLS` 단일 출처 + 드리프트
  계약 테스트로 봉합)
- PII 마스킹이 사후 패치 누적 구조 (현재는 카나리로 미등재 패턴 조기 발견만)
- 알림 채널이 ntfy 단일 (2차 채널 없음)

## 8. Project Identification

- **Project Name**: auction-arbitrage — 부동산 경매 차익 큐레이션 ("시세 > 최저입찰가" 갭 자동 발굴)
- **Repository**: https://github.com/hyunwoojang1/realestate_auctions
- **Production**: https://auction-arbitrage-hyunwoo-jang-s-projects.vercel.app
  (⚠️ 무접미사 `auction-arbitrage.vercel.app`은 **남의 앱**)
- **Primary Contact**: 장현우 (개인 프로젝트)
- **Date of Last Update**: 2026-08-24

## 9. Glossary

- **2선 밴드**: 시세를 band_low(트림 최저)~band_high(중앙값) 두 선으로 제시. 추천 기준은 보수
  차익 `profit_low = band_low − 취득원가 − 인수금액`.
- **표본 게이트(basis)**: 최근성+트림 후 실사용 비교 거래 수. 3 미만 밴드 금지, 5 미만 추천 제외.
- **복합 PK**: `(court, case_no, item_no)` — 사건번호는 법원별 독립 채번, 한 사건 여러 물건.
- **미러**: 로컬 SQLite → Supabase 단방향 복제. 게이트 FAIL이면 로컬만 갱신되고 클라우드는 동결.
- **KATEC**: 대법원 좌표계. pyproj 로컬 변환(coords_cache.json) — 외부 호출 0.
- **dry-run 격리**: 비라이브 산출물은 `*.dryrun.db/json` 분리, 클라우드 금지.
