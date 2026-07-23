# 크롤 파서 충실성 감사 — 20260723_1318

- 대조 표본: 보존 원본 pgj15B **248건** · curst **247건**
  (원본 보존 배선 2026-07-23 이후 크롤분만 — 소급 불가, 재크롤에 따라 커버리지 증가)

## A. 변형 — 원문 재파생 값 vs 저장 값 불일치

✅ **불일치 0건** — 저장된 248건 전부 원문 재파생과 일치(파서 결정론적·멱등 확인).

## B. 중복 — 같은 내용 2회 저장

✅ 중복 0건

- **원인 확정**: 원문의 `gdsSpcfcRmk` 와 `dspslGdsRmk` 가 **같은 문장**인 경우 = **0/248건 (0%)**.
  파서는 `remark = gdsSpcfcRmk + '\n' + dspslGdsRmk` 로 두 필드를 무조건 이어붙인다 (`src/courtauction_detail.py: normalize`).
  → 법원이 두 칸에 같은 값을 넣어주면 그대로 2회 저장된다. **파서 버그 확정(원문 증거)**.
  수정안(미적용): 이어붙이기 전 정규화 비교로 중복 제거.

## C. 누락 — 원문에 값이 있는데 어떤 컬럼에도 안 들어가는 필드

| 원문 필드 | 값 있는 건수 | 샘플 |
|---|---|---|
| `csBaseInfo.cortSptNm` | 248/248 | 거창지원 |
| `csBaseInfo.csNm` | 248/248 | 부동산임의경매 |
| `csBaseInfo.csRcptYmd` | 248/248 | 20250513 |
| `csBaseInfo.csCmdcYmd` | 248/248 | 20250527 |
| `csBaseInfo.auctnSuspStatCd` | 248/248 | 00 |
| `csBaseInfo.ultmtDvsCd` | 248/248 | 000 |
| `csBaseInfo.csProgStatCd` | 248/248 | 0002100001 |
| `csBaseInfo.mvprpRletDvsCd` | 248/248 | 00031R |
| `csBaseInfo.jdbnCd` | 248/248 | 1002 |
| `csBaseInfo.jdbnTelno` | 248/248 | 055-940-7142 |
| `csBaseInfo.execrCsTelno` | 248/248 | 055-944-2613 |
| `csBaseInfo.cortTypCd` | 248/248 | 70 |
| `csBaseInfo.lwstDvsCd` | 248/248 | 2 |
| `dspslGdsDxdyInfo.auctnGdsStatCd` | 248/248 | 01 |
| `dspslGdsDxdyInfo.auctnGdsUsgCd` | 248/248 | 01 |
| `dspslGdsDxdyInfo.flbdNcnt` | 248/248 | 2 |
| `dspslGdsDxdyInfo.aeeEvlAmt` | 248/248 | 65000000 |
| `dspslGdsDxdyInfo.fstPbancLwsDspslPrc` | 248/248 | 31850000 |
| `dspslGdsDxdyInfo.bidDvsCd` | 248/248 | 000331 |
| `dspslGdsDxdyInfo.dspslDxdyYmd` | 248/248 | 20260724 |
| `dspslGdsDxdyInfo.fstDspslHm` | 248/248 | 1000 |
| `dspslGdsDxdyInfo.dspslDcsnDxdyYmd` | 248/248 | 20260731 |
| `dspslGdsDxdyInfo.dspslGdsSpcfcEcdocId` | 248/248 | 93796B66AC1986DB1FBA29560B233093 |
| `dspslGdsDxdyInfo.prchDposRate` | 248/248 | 10 |
| `dspslGdsDxdyInfo.auctnDxdyGdsStatCd` | 248/248 | 00 |

## D. 현황조사서(curst) 대조

- 임차인 행수 불일치: **0/247건**
- **조사 서술 원문(`dlt_ordTsRlet[].rletLstRmk`)이 원문에는 있는데 저장 컬럼 없음: 5/247건**
  → 블라인드 판독관이 '판정 확정에 필요하다'고 지목한 바로 그 서술문("소유자와의 관계를 알 수 없는 …")이 파싱되지 않는다.

## 판정 규칙
- A(변형)는 파서 버그로 직결 → 발견 즉시 fixture 동결 + 회귀 테스트.
- C(누락)는 전부 문제가 아니다. '안 써도 되는 이유'가 문서화되면 정당한 미사용.
