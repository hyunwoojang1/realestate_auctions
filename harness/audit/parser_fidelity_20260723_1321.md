# 크롤 파서 충실성 감사 — 20260723_1321

- 대조 표본: 보존 원본 pgj15B **254건** · curst **253건**
  (원본 보존 배선 2026-07-23 이후 크롤분만 — 소급 불가, 재크롤에 따라 커버리지 증가)

## A. 변형 — 원문 재파생 값 vs 저장 값 불일치

✅ **불일치 0건** — 저장된 254건 전부 원문 재파생과 일치(파서 결정론적·멱등 확인).

## B. 중복 — 같은 내용 2회 저장

⚠ **판정 불가 — 대조 가능 표본 0건** (보존 254건 전부 remark 빈칸). '발견 0건'은 안전의 증거가 아니다. remark 있는 물건이 보존되면 재판정.

- 보존 원본에서 `gdsSpcfcRmk` == `dspslGdsRmk` 인 경우: 0/254건

### DB 전수 구조 증거 (표본 부족과 무관하게 확인 가능)
- remark 비어있지 않은 **9758건** 중 같은 문장 2회 = **7419건** (76%)
- 그중 중간에 개행 있는 것 **7419건** / 개행 없는 것 **0건**

> **원인 확정**: 파서는 `remark = '\n'.join([gdsSpcfcRmk, dspslGdsRmk])` 로 두 필드를 무조건
> 이어붙인다(`src/courtauction_detail.py: normalize`). 저장값이 예외 없이 `X<개행>X` 구조라는 것은
> **법원이 두 칸에 같은 값을 넣어줄 때 그대로 2회 저장된다**는 뜻(개행 없는 중복이 0건이므로 다른 경로 아님).
> 수정안(미적용): 이어붙이기 전 정규화 비교로 동일 문장 제거.

## C. 누락 — 원문에 값이 있는데 어떤 컬럼에도 안 들어가는 필드

| 원문 필드 | 값 있는 건수 | 샘플 |
|---|---|---|
| `csBaseInfo.cortSptNm` | 254/254 | 거창지원 |
| `csBaseInfo.csNm` | 254/254 | 부동산임의경매 |
| `csBaseInfo.csRcptYmd` | 254/254 | 20250513 |
| `csBaseInfo.csCmdcYmd` | 254/254 | 20250527 |
| `csBaseInfo.auctnSuspStatCd` | 254/254 | 00 |
| `csBaseInfo.ultmtDvsCd` | 254/254 | 000 |
| `csBaseInfo.csProgStatCd` | 254/254 | 0002100001 |
| `csBaseInfo.mvprpRletDvsCd` | 254/254 | 00031R |
| `csBaseInfo.jdbnCd` | 254/254 | 1002 |
| `csBaseInfo.jdbnTelno` | 254/254 | 055-940-7142 |
| `csBaseInfo.execrCsTelno` | 254/254 | 055-944-2613 |
| `csBaseInfo.cortTypCd` | 254/254 | 70 |
| `csBaseInfo.lwstDvsCd` | 254/254 | 2 |
| `dspslGdsDxdyInfo.auctnGdsStatCd` | 254/254 | 01 |
| `dspslGdsDxdyInfo.auctnGdsUsgCd` | 254/254 | 01 |
| `dspslGdsDxdyInfo.flbdNcnt` | 254/254 | 2 |
| `dspslGdsDxdyInfo.aeeEvlAmt` | 254/254 | 65000000 |
| `dspslGdsDxdyInfo.fstPbancLwsDspslPrc` | 254/254 | 31850000 |
| `dspslGdsDxdyInfo.bidDvsCd` | 254/254 | 000331 |
| `dspslGdsDxdyInfo.dspslDxdyYmd` | 254/254 | 20260724 |
| `dspslGdsDxdyInfo.fstDspslHm` | 254/254 | 1000 |
| `dspslGdsDxdyInfo.dspslDcsnDxdyYmd` | 254/254 | 20260731 |
| `dspslGdsDxdyInfo.dspslGdsSpcfcEcdocId` | 254/254 | 93796B66AC1986DB1FBA29560B233093 |
| `dspslGdsDxdyInfo.prchDposRate` | 254/254 | 10 |
| `dspslGdsDxdyInfo.auctnDxdyGdsStatCd` | 254/254 | 00 |

## D. 현황조사서(curst) 대조

- 임차인 행수 불일치: **0/253건**
- **조사 서술 원문(`dlt_ordTsRlet[].rletLstRmk`)이 원문에는 있는데 저장 컬럼 없음: 5/253건**
  → 블라인드 판독관이 '판정 확정에 필요하다'고 지목한 바로 그 서술문("소유자와의 관계를 알 수 없는 …")이 파싱되지 않는다.

## 판정 규칙
- A(변형)는 파서 버그로 직결 → 발견 즉시 fixture 동결 + 회귀 테스트.
- C(누락)는 전부 문제가 아니다. '안 써도 되는 이유'가 문서화되면 정당한 미사용.
