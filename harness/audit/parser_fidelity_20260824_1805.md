# 크롤 파서 충실성 감사 — 20260824_1805

- 대조 표본: 보존 원본 pgj15B **8373건** · curst **3667건**
  (원본 보존 배선 2026-07-23 이후 크롤분만 — 소급 불가, 재크롤에 따라 커버리지 증가)

## A. 변형 — 원문 재파생 값 vs 저장 값 불일치

✅ **불일치 0건** — 저장된 8373건 전부 원문 재파생과 일치(파서 결정론적·멱등 확인).

## B. 중복 — 같은 내용 2회 저장

| 컬럼 | 중복/대조가능 |
|---|---|
| `remark` | 2887/4027 (72%) |

- 보존 원본에서 `gdsSpcfcRmk` == `dspslGdsRmk` 인 경우: 2934/8373건

### DB 전수 구조 증거 (표본 부족과 무관하게 확인 가능)
- remark 비어있지 않은 **9635건** 중 같은 문장 2회 = **7237건** (75%)
- 그중 중간에 개행 있는 것 **7237건** / 개행 없는 것 **0건**

> **원인 확정**: 파서는 `remark = '\n'.join([gdsSpcfcRmk, dspslGdsRmk])` 로 두 필드를 무조건
> 이어붙인다(`src/courtauction_detail.py: normalize`). 저장값이 예외 없이 `X<개행>X` 구조라는 것은
> **법원이 두 칸에 같은 값을 넣어줄 때 그대로 2회 저장된다**는 뜻(개행 없는 중복이 0건이므로 다른 경로 아님).
> 수정안(미적용): 이어붙이기 전 정규화 비교로 동일 문장 제거.

## C. 누락 — 원문에 값이 있는데 어떤 컬럼에도 안 들어가는 필드

| 원문 필드 | 값 있는 건수 | 샘플 |
|---|---|---|
| `csBaseInfo.cortSptNm` | 7186/8373 | 강릉지원 |
| `csBaseInfo.csNm` | 7186/8373 | 부동산임의경매 |
| `csBaseInfo.csRcptYmd` | 7186/8373 | 20231114 |
| `csBaseInfo.csCmdcYmd` | 7186/8373 | 20231122 |
| `csBaseInfo.auctnSuspStatCd` | 7186/8373 | 03 |
| `csBaseInfo.ultmtDvsCd` | 7186/8373 | 000 |
| `csBaseInfo.csProgStatCd` | 7186/8373 | 0002100001 |
| `csBaseInfo.mvprpRletDvsCd` | 7186/8373 | 00031R |
| `csBaseInfo.jdbnCd` | 7186/8373 | 1002 |
| `csBaseInfo.jdbnTelno` | 7186/8373 | 033-640-1132 |
| `csBaseInfo.execrCsTelno` | 7186/8373 | 033-647-4811,648-4811 |
| `csBaseInfo.cortTypCd` | 7186/8373 | 70 |
| `csBaseInfo.lwstDvsCd` | 7186/8373 | 2 |
| `dspslGdsDxdyInfo.auctnGdsStatCd` | 7186/8373 | 01 |
| `dspslGdsDxdyInfo.auctnGdsUsgCd` | 7186/8373 | 01 |
| `dspslGdsDxdyInfo.aeeEvlAmt` | 7186/8373 | 280000000 |
| `dspslGdsDxdyInfo.fstPbancLwsDspslPrc` | 7186/8373 | 196000000 |
| `dspslGdsDxdyInfo.bidDvsCd` | 7186/8373 | 000331 |
| `dspslGdsDxdyInfo.dspslDxdyYmd` | 7186/8373 | 20260831 |
| `dspslGdsDxdyInfo.fstDspslHm` | 7186/8373 | 1000 |
| `dspslGdsDxdyInfo.dspslDcsnDxdyYmd` | 7186/8373 | 20260907 |
| `dspslGdsDxdyInfo.dspslGdsSpcfcEcdocId` | 7186/8373 | A70D52B9AC19061E0725AFA6A9BD3C68 |
| `dspslGdsDxdyInfo.prchDposRate` | 7186/8373 | 10 |
| `dspslGdsDxdyInfo.auctnDxdyGdsStatCd` | 7186/8373 | 00 |
| `dspslGdsDxdyInfo.dspslPlcNm` | 7186/8373 | 입찰,경매법정(1층) |

## D. 현황조사서(curst) 대조

- 임차인 행수 불일치: **397/3667건**
- **조사 서술 원문(`dlt_ordTsRlet[].rletLstRmk`)이 원문에는 있는데 저장 컬럼 없음: 104/3667건**
  → 블라인드 판독관이 '판정 확정에 필요하다'고 지목한 바로 그 서술문("소유자와의 관계를 알 수 없는 …")이 파싱되지 않는다.

## 판정 규칙
- A(변형)는 파서 버그로 직결 → 발견 즉시 fixture 동결 + 회귀 테스트.
- C(누락)는 전부 문제가 아니다. '안 써도 되는 이유'가 문서화되면 정당한 미사용.
