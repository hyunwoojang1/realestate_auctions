# Layer-1 크롤러 QA 설계 (수집 파이프라인 자체 품질)

> 범위: **크롤러가 원본을 제대로/완전히/안전하게 수집·저장하나** (Layer-1).
> 다운스트림 결론 정확성(추천·권리판정이 실제와 맞나 = Layer-2)은 별도 세션.
> 생성: 2026-07-22, 5세대×5트랙 진화형 QA 워크플로우(26 에이전트, 181 발견) 종합.
> **모든 항목 실측 근거(라이브 DB·정적 코드) 기반. 라이브 courtauction 호출 0(밴 안전).**

## 실측 스냅샷 (2026-07-22)
scored=15509, rights=11020(커버리지 71%, 백로그 4489), tenants=0, raw=37528(distinct 키 26286),
photos=23706, naver=4754. **고아행: photos 6578·naver 1700**(rights/building=0). 복합키 중복 0.
raw에 상세(pgj15B) 필드 0/37528(=목록 pgj15A만). building status={no_bld:9788, ok:5721}.

---

## 스코어카드 (심각도별, 중복 제거)

### 🔴 CRITICAL (오데이터 저장 / 밴 / PII 유출)
| # | 결함 | 근거 |
|---|---|---|
| C1 | **실명 PII 유출(현재 서빙 중)** — `senior_lien`·`appraisal_notes`에 공유자·채무자·임차인·상속인 실명(일부 생년월일)이 마스킹 없이 저장→상세페이지 서빙+Supabase 미러. 형제필드(surviving/lien_note/remark)는 마스킹되는데 이 둘만 배선 누락. | `courtauction_detail.py:543`은 `_sanitize`만, `:526-532`은 mask 호출 자체 없음. 마스커를 저장된 senior_lien에 돌리면 **47행/42사건** 변함(=실명). 형제 마스크토큰 61/1165 vs 이 둘 0 = 비대칭 확정. ⚠️단순 '전량 mask'는 법인명·'현장조사 당시 소유자' 훼손(g4 반증) → 법인토큰+오탐가드 필수 |
| C2 | **침묵 오판** — 법원이 최고위험 필드 `ndstrcRghCtt`(인수되는 권리) **하나만** 개명해도 드리프트 경보 안 울림→인수부담 물건이 조용히 `surviving_rights=''`→`clean(안전)`으로 저장·서빙. 사이트 핵심가치 붕괴. | `courtauction_detail.py:500` `if not any(k in gds for k in _DETAIL_CORE_KEYS)` — 3키 중 하나만 남아도 정상판정. 실행프로브 5세대 전부 CONFIRMED. 드리프트 사유 행별 미저장→기존 빈 surviving 7849건 사후판별 불가 |
| C3 | **비가역 데이터 손실** — 전국 크롤 중 어떤 시도 샤드가 차단없이 '정상이지만 빈/짧은' 응답을 줘도 scored 전량교체(replace_all) 실행+prune 연쇄삭제→밴예산으로 모은 권리 백로그 파괴. | `run.py:197/201/212` 전량교체 강등은 `NATIONWIDE_PARTIAL`(pipeline.py:111)이 **CourtAuctionBlocked일 때만** set. 건수부족(정상HTTP·소량)엔 게이트 없음. 카운트 커버리지 플로어 부재 |
| C4 | **밴 예산 무력** — 일일 요청상한(500)이 프로세스마다 0으로 리셋되는 인스턴스-로컬 값. 실제 하루 **6119회 상세요청(상한 12배)**이 단일 가정 IP에서 나감. | `courtauction_client.py:173` `_request_count=field(default=0)` 생성자마다 0, `:246` 인스턴스 누적만 검사. 영속·IP인지 예산 부재 |

### 🟠 HIGH (침묵실패 / 커버리지 / 무결성)
| # | 결함 | 근거 |
|---|---|---|
| H-a | 고아행 무한누적 — photos·naver 정리기 부재(rights만 있음). 고아 사진행은 Supabase Storage 죽은 JPEG 가리킴. | orphan photos=6578·naver=1700. `store.py`에 prune_orphan_rights만 |
| H-b | 최고위험 파서(권리 normalize)의 원본(pgj15B)이 어디에도 미저장→골든·사후감사 원천 전무. | raw 37528 중 상세필드 0건. 상세응답은 normalize 후 폐기 |
| H-c | `_targets` 중복제거 부재→같은 사건 최대 **52배 중복 상세요청**으로 밴 상한 낭비(커버리지 정체 원인). | `crawl_rights.py:46-79` JOIN 후 DISTINCT 없음. raw 37528 vs distinct 26286 |
| H-d | 건축물대장 no_bld(63%)가 '진짜 건물없음'과 '일시 실패(429/쿼터)'를 뭉갬→건물유형 절반 노후도·위반 데이터 관측불가 상실. | status 집합 {no_bld:9788, ok:5721}뿐, error/no_addr 센티널 0 |
| H-e | 네이버 시세가 scored와 미동기+서빙 조인에 max-age 게이트 없음→**7일 지난 시세** 무표기 조인. | naver fetched_at 07-15·07-20뿐(07-15=7일) |
| H-f | 권리 커버리지 71%, estimable 추천물건 중 **430건이 '미확인'으로 서빙**. | scored 15509/rights 11020, estimable 2284 중 무권리 430 |
| H-g | 스케줄러가 crawl_rights 종료코드를 삼켜 차단(2)·드리프트(3) 승격 무력화+DailyRefresh Disabled 실측. | `refresh-daily.ps1:114` `2>&1 \| Tee-Object`→`:121 $LASTEXITCODE`는 Tee값. RIGHTS_STOP 참조 0(죽은코드) |
| H-h | **(잠복)** 현황조사서 크롤 켜지면 침묵 데이터손실 — JSON형 소프트차단을 관대검증기가 통과→parse가 []→save가 기존 임차인 전량 DELETE로 '임차인 없음' 거짓확정. raw convAddr에 채무자·공유자 실명 ~1336건 무마스킹(잠재 주소폴백 노출). | `courtauction_client.py:371-382`+`store.py:340-363`. `sanitize_row`는 3필드만(convAddr 제외). 마스커에 채권자 역할어 누락 |

### 🟡 MEDIUM (미검증/효율)
- C6 물건번호 가드 미검증: 오사건 가드가 case_no만 대조, `dspslGdsSeq` 미검증(다물건 캐시경합 시 오물건 권리 혼입 가능). pgj15B 골든 없어 오늘 검증 불가(PLAUSIBLE).

---

## 통합 QA 워크플로우 (5트랙 병렬, 밴 안전, DB+정적+골든 우선)

**실행 순서(run_order):**
1. **STATIC**(DB 없음, 즉시): 드리프트 카나리·targets-dedup·store_rest-parity·curst-no-delete·refresh-exitcode grep가드·마스커 person/corp fixtures (pytest)
2. **DB 불변식**(읽기전용, 수초): 고아=0·복합키중복=0·PII스캔·SSN스캔·raw↔scored 완전성·date-ISO·신선도 모니터
3. **골든 리플레이**: G1(list→scored 지금 가능)·G3/G4 fixture·G2/G5는 fixture 있으면 assert 없으면 FAIL-가시
4. **LIVE**(1~3 그린 후, 인가된 휴지기에만): pgj15B/curst 골든 **캡처 전용**(QA 프로브 아님), 마스킹·동결

**밴 정책(live_probe_policy):** QA 중 courtauction 라이브 트래픽 0. 유일 인가 라이브=pgj15B 골든 캡처, 조건: (1)권리크롤 완전 OFF+새로고침 미실행 (2)COURTAUCTION_STOP 세팅 (3)수시간 유휴 후 (4)throttle 3~8s·사건당 ≤10 detail+≤1 curst. 403/카나리/소프트차단 시 즉시중단·무재시도·무우회.

**5트랙 게이트:**
- **T1 정확성/골든**: senior_lien 실명히트=0 AND list→scored 6필드 불일치=0 AND corp-preserve fixture 통과. 실명>0면 upsert_rights BLOCK.
- **T2 완전성**: raw↔scored 완전성=0 드롭 AND 커버리지 비회귀 AND naver age≤3d. building/tenants는 MONITOR(센티널·게이트 배선 전까지 비차단).
- **T3 침묵실패**: per-key 드리프트 카나리 pytest PASS(수정 후) AND ipcheck=false-무삭제 PASS AND refresh 종료코드 grep가드 PASS. 배치 부재율>0.3이면 exit 3+키명 stderr.
- **T4 멱등/무결성**: photos&naver 고아=0(prune 배선 후) AND _targets 무중복키 AND 복합키중복=0 AND 컬럼정합. 요청예산은 코드변경(보고).
- **T5 밴안전/PII/신선도**: 5개 rights 자유텍스트 컬럼 잔여 실명히트=0(>0면 upsert BLOCK) AND SSN=0(기관문서번호 화이트리스트) AND 마스커 person-mask/corp-preserve fixture 통과. 신선도+스케줄러 Disabled=push 알림.

## CI 회귀 가드 (상시 실행 가능)
1. `orphan_zero.sql` — photos·naver·rights·building·tenants 고아=0 (오늘 photos 6578·naver 1700 FAIL)
2. `composite_pk_unique.sql` — 테이블별 복합키 중복=0 (오늘 0)
3. `pii_rights_scan.py`(**upsert 전 BLOCK**) — 5개 자유텍스트에 mask 재적용해 출력≠입력이면 잔여 실명=FAIL (오늘 senior_lien 47·appraisal_notes ~200 FAIL)
4. `ssn_scan.py` — RRN 정규식=0, 기관문서번호 문맥(구청/시청/문서/사실조회) 제외
5. `test_drift_canary.py` — `detail_schema_drift(ndstrcRghCtt만 개명)!=''` AND 키명 포함; 값-null은 '' (현재 코드서 FAIL=정상, 수정 고정)
6. `test_targets_dedup.py` — `len(targets)==len(distinct 키)`
7. `test_store_rest_parity.py` — select==payload==_COLS 3집합 동일 (rights/scored/naver/photos/building)
8. `test_curst_no_delete_on_softblock.py` — ipcheck:false/errors면 기존 tenants 불변, ipcheck:true+빈리스트만 삭제
9. `coverage_floor` 게이트(run.py) — replace_all 전 최신크롤 키수 ≥ 0.8×이전 scored, 미달시 merge 강등+prune 스킵
10. `raw_scored_completeness.sql` — 최신일 raw distinct 키 == COUNT(scored) AND 누락=0 (오늘 15509==15509)
11. `test_refresh_exitcode_guard.py` — refresh-daily.ps1가 crawl_rights $LASTEXITCODE를 같은 블록서 캡처(Tee 파이프 금지)
12. `date_iso.sql` — spec_write_ymd/demand_end 비어있지 않은데 non-ISO=0
13. `freshness_monitor.py`(push, 비차단) — naver 비고아 max age≤3d, rights<48h, scored에 sale_date<오늘=0, DailyRefresh State≠Disabled

## 골든셋
- **G1 list→scored**(지금 재현 가능): 15509행을 raw_json(pgj15A)에서 재유도, appraisal/fail_count/sale_date/area 100%·min_bid≥99.9% 일치, 무부모=0.
- **G2 pgj15B detail→normalize**(캡처 필요, 최고위험·회귀보호 0): 마스킹된 실 dma_result 5~10건(다물건·지분 포함), normalize 필드매핑+summarize status/assumed 바이트안정.
- **G3 드리프트 카나리 fixture**: (a)값-null→'' (b)단일키개명→비어있지않음+키명 (c)3키개명→드리프트. (b) 현재 FAIL=의도.
- **G4 PII 마스커 person/corp fixture**: '김민정 지분…'→마스킹 / '근저당권자 신한은행'·'주식회사 우리캐피탈'→**보존** / '현장조사 당시 소유자'→**불변**(오탐가드) / '채권자 박보라'→마스킹(역할어 확장).
- **G5 curst 다회차 임차인 fixture**(캡처 전 BLOCK): 비어있지 않은 다회차 상가 조사서, 회차 합산=파싱수(회차 무단누락 0). 인프라 부재로 SKIP 아닌 FAIL-가시.

## 지금 당장 우선순위
1. **[C1·현재 실유출] senior_lien·appraisal_notes 마스킹 배선 + 백필**(로컬+Supabase). 법인토큰 예외+오탐가드 필수. data_gates에 실명히트>0면 upsert BLOCK.
2. **[C2·핵심가치 오판] drift `any()`→'핵심키 전부 존재' 요구**로 전환(단독개명 감지)+사유 행 영속화+부재율>0.3 exit 3.
3. **[C3·비가역 손실] replace_all 앞 카운트 커버리지 플로어 게이트** 추가, 미달시 강등.
4. **[밴 안전·전제] 요청예산 프로세스간 영속·IP인지 전환 + refresh 종료코드 캡처 + RIGHTS_STOP 배선/제거 + DailyRefresh 재활성화.**
5. **[저비용·고효과] prune_orphan_photos/naver 추가**(고아→0, Storage GC) + `_targets` 복합키 중복제거(밴예산 최대 52배 절약).
6. **[최고위험 파서 회귀보호] 밴 휴지기에 pgj15B 골든 5~10건 신중 캡처→마스킹→fixtures 동결 + 경량 raw_detail 아카이브.**
