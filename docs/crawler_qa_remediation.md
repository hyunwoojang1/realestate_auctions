# 크롤러 QA 종합 + 코드 보완 계획 (Layer-1 ⊕ Layer-2)

> Layer-1(크롤러 수집 파이프라인 자체 품질) + Layer-2(우리 사이트 결론 ≠ 실제 물건 상태) 두 QA를 종합.
> 2026-07-22. 근거: Layer-1=docs/crawler_qa_layer1.md(26에이전트·실측), Layer-2=타 세션 2세트 교차검증.

---

## 핵심 통찰 — Layer-2의 2건은 Layer-1과 같은 뿌리

Layer-2가 찾은 사용자 오도 2건은 **"프로덕션 Supabase 미러가 현행 엔진보다 낡음(stale)"** 하나로 수렴한다.
그리고 이건 Layer-1의 신선도·스케줄러·커버리지플로어 발견과 **정확히 같은 축**이다.

- **L2-P1(정면모순)**: 목록/홈/API/CSV는 "차익 유력/양호"+양수차익+경고칩0으로 노출하는데 상세는 ⛔미확인.
  홈 60카드 중 31카드가 경고칩0+양수차익. → 목록만 보는 사용자 오도.
- **L2-P2(스테일 미러)**: 현행 엔진이 '시세추정불가'로 강등한 100건을 프로덕션이 '양호/관심'+확신 KB시세+
  예상차익(최대 2.49억, 예: 응암푸르지오)으로 서빙. 미러 재동기화 안 됨.
- **공통 근본원인**: B-1(빈 요지 초록 오표시 차단)과 후속 채점 수정(opposability_assessable·보수차익 인수액
  차감)은 **채점 파이프라인(pipeline.apply_rights_from_rows·score_listing)에 이미 반영**됐으나,
  **프로덕션에 재채점·재미러가 안 돼** 미러가 옛 등급을 그대로 서빙. → 재채점→재미러가 두 건 동시 해소.

⚠️ 단, 무작정 재채점→replace_all은 **Layer-1 C3(커버리지 플로어 부재)** 때문에 위험(부실 샤드가 백로그 파괴).
그리고 재미러는 **Layer-1 C1(PII)** 을 그대로 클라우드로 밀어냄. → **순서가 중요**(아래 시퀀싱).

---

## 통합 문제 맵 (근본원인 축별)

### 축 A — 미러 신선도/일관성 (사용자 오도의 직접 원인)
| ID | 문제 | 레이어 | 근거 |
|---|---|---|---|
| A1 | 목록/홈/API/CSV가 상세와 모순(초록추천 vs ⛔미확인) | L2-P1 | 홈 31/60 카드 경고칩0+양수, 상세는 미확인 |
| A2 | 스테일 미러: 강등된 100건이 프로덕션서 확신등급+차익 서빙 | L2-P2 | 응암푸르지오 등, 미러 재동기 안 됨 |
| A3 | 미러 vs 로컬 컬럼/등급 불일치(재채점 미전파) | L1 | store_rest _COLS/payload 정합, replace_all→upsert 미실행 |

### 축 B — 도메인 안전(권리 오판) = 사이트 핵심가치
| ID | 문제 | 레이어 | 근거 |
|---|---|---|---|
| B1(C2) | H4 드리프트 카나리 any() 구멍 — 최고위험 필드 단독 개명 시 burden→clean 침묵오판 | L1 CRIT | courtauction_detail.py:500 |
| B2 | 대항력 판정 원재료(임차인 전입일) 미수집 — tenants=0 | L1/L2 | AUCTION_CRAWL_TENANTS 어느 스크립트도 미설정 |
| B3 | 오사건 가드가 물건번호(dspslGdsSeq) 미검증(다물건 캐시경합 오물건 혼입 가능) | L1 MED | crawl_rights.py:162, PLAUSIBLE(pgj15B 골든 필요) |

### 축 C — 개인정보(PII) 유출
| ID | 문제 | 레이어 | 근거 |
|---|---|---|---|
| C1a(C1) | senior_lien 실명 무마스킹 서빙+미러 | L1 CRIT | courtauction_detail.py:543, 47행/42사건 |
| C1b | appraisal_notes 실명 무마스킹 | L1 CRIT | courtauction_detail.py:526-532 mask 호출 없음 |
| C1c | raw_json convAddr 채무자·공유자 실명 ~1336건(잠재 주소폴백) | L1 HIGH | sanitize_row 3필드만 마스킹 |
| C1d | 마스커 역할어에 '채권자' 누락 | L1 HIGH | courtauction_fields.py:51-52 |

### 축 D — 밴/준법 안전 (모든 재크롤·골든의 전제)
| ID | 문제 | 레이어 | 근거 |
|---|---|---|---|
| D1(C4) | 요청예산 인스턴스 리셋 — 하루 6119콜(상한 12배) 단일 IP | L1 CRIT | courtauction_client.py:173 |
| D2 | _targets 중복제거 부재 — 같은 사건 최대 52배 중복 상세요청 | L1 HIGH | crawl_rights.py:46-79, raw 37528 vs distinct 26286 |
| D3 | 스케줄러가 crawl_rights 종료코드 삼킴(차단2/드리프트3 무력)+DailyRefresh Disabled | L1 HIGH | refresh-daily.ps1:114 Tee 파이프 |
| D4 | RIGHTS_STOP 킬스위치 죽은코드(COURTAUCTION_STOP만 작동) | L1 | src/deploy 참조 0 |

### 축 E — 비가역 데이터손실 / 무결성 위생
| ID | 문제 | 레이어 | 근거 |
|---|---|---|---|
| E1(C3) | replace_all 앞 커버리지 플로어 게이트 부재 — 부실 샤드가 scored+rights 백로그 파괴 | L1 CRIT | run.py:197, pipeline.py:111 |
| E2 | 고아행 photos 6578·naver 1700 (정리기 부재, rights만 있음) | L1 HIGH | store.py prune_orphan_rights만 |
| E3 | B-2 잠복버그: ipcheck=false 소프트차단→parse []→save가 tenants 전량삭제 | L1 HIGH | store.py:340-363 save_tenants |

### 축 F — 관측성/회귀보호
| ID | 문제 | 레이어 | 근거 |
|---|---|---|---|
| F1 | pgj15B 원본 미저장 — 최고위험 파서(normalize) 골든·사후감사 원천 0 | L1 HIGH | raw 37528 중 상세필드 0 |
| F2 | 건축물대장 no_bld가 진짜없음 vs 429실패 뭉갬 | L1 HIGH | status {no_bld,ok}뿐, 센티널 0 |
| F3 | 드리프트 사유 행별 미저장(stderr뿐)→빈 surviving 7849건 사후판별 불가 | L1 | crawl_rights.py:175 |

---

## 크롤링 코드 보완 계획 (파일 단위·순서대로)

### 시퀀싱 원칙
PII 마스킹(C) → 재미러가 실명 밀어내지 않게 **먼저**. 그다음 안전판(B1·E1) → 안전 재채점·재미러(A) →
밴안전(D) → 위생(E2·E3)·관측(F). **A(재채점·재미러)가 L2-P1·P2를 동시에 해소하나, C·E1 선행 필수.**

### 1단계 — PII 차단 (축 C, 최우선: 현재 라이브 유출)
- **`src/courtauction_detail.py:543`** senior_lien에 `mask_personal_names` 적용. **단 법인토큰 보존**
  (은행·주식회사·캐피탈·농협·신탁·저축은행·새마을금고 등) + 오탐가드('현장조사 당시 소유자'류 미변형).
  → `courtauction_fields.mask_personal_names`를 법인/오탐 가드 버전으로 보강(공유 함수).
- **`:526-532`** appraisal_notes 각 text에 동일 마스킹 적용.
- **`courtauction_fields.py:51-52`** 자유텍스트 역할어에 `채권자` 추가.
- **`sanitize_row`(courtauction_fields.py:44)** 에 `convAddr` 마스킹 추가(주소폴백 잠복 노출 차단).
- **백필**: 기존 listing_rights 47행/appraisal ~200행 + raw_listings convAddr를 마스킹 재적용해 갱신
  (로컬 UPDATE + Supabase 재upsert). data_gates에 **name-hit>0면 upsert_rights BLOCK** 게이트 추가.
- 골든: G4 person/corp fixtures(신한은행 보존·'현장조사 당시 소유자' 불변).

### 2단계 — 침묵 오판 안전판 (축 B1 = C2)
- **`src/courtauction_detail.py:500`** `if not any(...)` → **핵심키 전부 존재 요구**:
  `missing = [k for k in _DETAIL_CORE_KEYS if k not in gds]; if missing: return f"핵심필드 소멸: {missing}"`.
  값이 null인 건 정상(키 존재)이므로 여전히 통과.
- **`deploy/crawl_rights.py:175`** 드리프트 사유를 listing_rights 신설 컬럼(`drift_reason`)에 영속화
  (사후 판별 가능). 배치 부재율>0.3이면 exit 3(기존 로직 유지).
- 골든: G3 (단독개명→비어있지않음+키명 / 값-null→'').

### 3단계 — 비가역 손실 안전판 (축 E1 = C3)
- **`src/pipeline.py:111` 부근** NATIONWIDE_PARTIAL 세팅 조건에 **카운트 부족** 추가
  (수집 distinct 키수 < 이전 scored의 FLOOR=0.8 → PARTIAL).
- **`run.py:197` 부근** PARTIAL이면 replace_all 강등(merge)+**prune 스킵**(백로그 보존).
- 가드: `raw_scored_completeness.sql`, `coverage_floor` 게이트.

### 4단계 — 안전 재채점·재미러 (축 A, L2-P1·P2 동시 해소) ★핵심
- **재채점**: `run.py --source courtauction --nationwide`(현행 엔진=opposability_assessable+보수차익
  인수액차감 반영)로 scored 재생성 → 등급이 현행 규칙으로 재파생.
- **재미러**: replace_all(로컬)→`store_rest.upsert`(Supabase). 3단계 게이트로 안전 보장.
- **효과**: 빈 요지 물건→권리미확인, 강등 100건→시세추정불가, 상세와 목록 일치. **L2-P1·P2 해소.**
- **목록 서브타임 가드(방어심층)**: 스테일 미러가 새도 목록/API/CSV가 초록을 못 내게, 서빙 시
  `rights_verified=False` or `grade in (미확인/시세추정불가/위험/차익없음)`이면 추천배지 억제
  (web.py 목록 렌더·/api/listings·CSV export 공통).

### 5단계 — 밴/준법 안전 (축 D, 재크롤 전 필수)
- **`courtauction_client.py:173`** 요청예산을 **디스크/DB 영속·IP 인지**로: 파일(예: `.request_budget.json`)에
  당일 카운트 저장, 생성자에서 로드, IP별 분리. 상한 초과시 즉시중단.
- **`crawl_rights.py:46-79`** `_targets`에 복합키 **중복제거**(seen set) — 52배 낭비 제거.
- **`refresh-daily.ps1:114`** crawl_rights 종료코드를 같은 블록서 `$LASTEXITCODE` 캡처(Tee 파이프 밖).
- **RIGHTS_STOP** 제거 또는 배선. DailyRefresh 재활성화(사용자·약관 확인 후).

### 6단계 — 위생·관측 (축 E2·E3·F)
- **`store.py`** `prune_orphan_photos`·`prune_orphan_naver` 추가(replace_all 후 rights와 동일 배선)+
  Supabase Storage 죽은 JPEG GC. 고아 6578·1700→0.
- **`store.py:340-363` save_tenants** — parse가 ipcheck=false/errors로 []면 **DELETE 금지**
  (기존 tenants 보존). ipcheck=true+빈리스트만 삭제. (B-2 tenant 크롤 켜기 전 필수)
- **`building_info.get_building_summary`** 일시실패(429/쿼터)를 `transient` 센티널로 반환→no_bld와 구분.
- **F1(pgj15B 골든)**: 밴 휴지기에 5~10건(다물건·지분·상가) 캡처→마스킹→tests/fixtures 동결+경량
  raw_detail 아카이브(normalize 회귀보호·B3 물건번호 가드 검증 원천).

### 오버액션(쫓지 말 것 — Layer-2가 라이브 반증)
- basis=0 33건 "초록배지 상위노출" → 홈 0/33·배지 미렌더(모집단 허위).
- 미크롤/말소빈칸/profit_low≤0 추천 → 모집단 0(이미 게이트가 막음).
- Lane C 인수보증금 추천 → 임차인 크롤 후 재검토(현재 원재료 부재).

---

## 우선순위 한 줄 요약
**C(PII 마스킹+백필) → B1·E1(안전판) → A(안전 재채점·재미러 = L2-P1·P2 동시해소) → D(밴안전) → E2·E3·F(위생·관측).**
축 A가 사용자 오도를 직접 없애지만, C·E1 선행 없이는 재미러가 실명 유출·백로그 파괴를 키운다.
