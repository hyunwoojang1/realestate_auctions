"""크롤 파서 충실성 감사 (Layer 2 — 크롤링 '코드'를 감사, 결정론적·크롤 0·LLM 0).

지향점(harness/AUDIT_CHARTER.md §4): 앞선 감사들이 "결과가 맞나"를 물었다면 이건
**"수집→저장 과정 자체가 정직한가"**를 묻는다. 보존된 원본(listing_detail_raw)과
저장된 컬럼을 같은 물건에 대해 1:1 대조한다.

검사 3종:
  A. **변형**  — 원문 값과 저장 값이 다른가 (파서가 값을 바꿨나)
  B. **중복**  — 저장 값에 같은 내용이 두 번 들어갔나
  C. **누락**  — 원문에 값이 있는데 어떤 컬럼에도 안 들어간 필드는 무엇인가

사용:  .venv/Scripts/python.exe scripts/audit_parser_fidelity.py
출력:  harness/audit/parser_fidelity_<YYYYMMDD_HHMM>.md
"""
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import store  # noqa: E402
from src.courtauction_detail import normalize, parse_curst_survey  # noqa: E402

# pgj15B 원문에서 우리가 컬럼으로 옮기는 필드(= 파싱 대상). 이 목록 밖의 값 있는 필드 = 누락 후보.
PARSED_PGJ = {
    "csBaseInfo": {"clmAmt", "cortAuctnJdbnNm", "userCsNo", "csNo", "cortOfcCd", "cortOfcNm"},
    "dspslGdsDxdyInfo": {"ndstrcRghCtt", "tprtyRnkHypthcStngDts", "sprfcExstcDts",
                         "gdsSpcfcRmk", "dspslGdsRmk", "gdsSpcfcWrtYmd", "csNo",
                         "cortOfcCd", "dspslGdsSeq"},
}
LIST_PARSED = {"gdsDspslDxdyLst", "aeeWevlMnpntLst", "csPicLst", "dstrtDemnInfo"}


def norm_txt(s):
    return " ".join((s or "").split())


def dup_half(t):
    t = norm_txt(t)
    if len(t) < 10:
        return False
    h = len(t) // 2
    return t[:h].strip() == t[h:].strip() != ""


def main():
    conn = store.connect(os.path.join(ROOT, "auction.db"))
    raws = conn.execute(
        "SELECT court, case_no, item_no, doc_type FROM listing_detail_raw ORDER BY doc_type").fetchall()

    n_pgj = n_curst = 0
    mismatch = Counter()          # 컬럼별 변형 건수
    mismatch_ex = defaultdict(list)
    dup_cols = Counter()
    dup_same_src = 0              # 두 원천 필드가 같은 문장 → 중복의 원인
    # (자기감사 2026-07-23) 대조 가능 표본 수 — 값이 빈칸이면 검사가 자동 통과돼 '✅ 0건'이라는
    # 거짓 안전을 낸다. 실제로 보존 250건 전부 remark 빈칸이라 B섹션이 무의미하게 통과했다.
    # 표본이 0이면 '판정 불가'로 보고한다(침묵 통과 금지).
    dup_testable = Counter()
    missing = Counter()           # 누락 후보 필드: 값 있는데 파싱 안 함
    missing_ex = {}
    tenant_mismatch = 0
    curst_narr_missing = 0        # 조사 서술 원문(rletLstRmk)이 어디에도 저장 안 됨

    for r in raws:
        court, case_no, item_no, dt = r["court"], r["case_no"], r["item_no"], r["doc_type"]
        raw = store.load_detail_raw(conn, court, case_no, item_no, dt)
        if not raw:
            continue

        if dt == "pgj15B":
            n_pgj += 1
            got = conn.execute(
                "SELECT * FROM listing_rights WHERE court=? AND case_no=? AND item_no=?",
                (court, case_no, item_no)).fetchone()
            if got is None:
                continue
            exp = normalize(raw, court=court, case_no=case_no, item_no=item_no)

            # A. 변형 — 원문 재파생 값 vs 저장 값
            for col, val in (("surviving_rights", exp.surviving_rights),
                             ("senior_lien", exp.senior_lien),
                             ("lien_note", exp.lien_note),
                             ("remark", exp.remark),
                             ("claim_amt", exp.claim_amt),
                             ("spec_write_ymd", exp.spec_write_ymd),
                             ("demand_end", exp.demand_end)):
                a, b = got[col], val
                if isinstance(b, str):
                    same = norm_txt(a) == norm_txt(b)
                else:
                    same = (a or 0) == (b or 0)
                if not same:
                    mismatch[col] += 1
                    if len(mismatch_ex[col]) < 2:
                        mismatch_ex[col].append(f"{court} {case_no}: 저장={str(a)[:40]!r} vs 원문={str(b)[:40]!r}")

            # B. 중복 (대조 가능 = 값이 있는 행만 카운트)
            for col in ("remark", "surviving_rights", "lien_note"):
                if norm_txt(got[col]):
                    dup_testable[col] += 1
                    if dup_half(got[col]):
                        dup_cols[col] += 1
            g = raw.get("dspslGdsDxdyInfo") or {}
            if norm_txt(g.get("gdsSpcfcRmk")) and norm_txt(g.get("gdsSpcfcRmk")) == norm_txt(g.get("dspslGdsRmk")):
                dup_same_src += 1

            # C. 누락 — 값이 있는데 파싱 대상 목록에 없는 필드
            for sec in ("csBaseInfo", "dspslGdsDxdyInfo"):
                for k, v in (raw.get(sec) or {}).items():
                    s = str(v).strip() if v is not None else ""
                    if s and s not in ("0", "N", "null") and k not in PARSED_PGJ[sec]:
                        missing[f"{sec}.{k}"] += 1
                        missing_ex.setdefault(f"{sec}.{k}", s[:50])
            for k, v in raw.items():
                if isinstance(v, list) and v and k not in LIST_PARSED:
                    real = [x for x in v if isinstance(x, dict) and any(
                        str(y).strip() not in ("", "None", "0") for y in x.values())]
                    if real:
                        missing[f"{k}[]"] += 1
                        missing_ex.setdefault(f"{k}[]", str(list(real[0].keys())[:6]))

        elif dt == "curst":
            n_curst += 1
            exp_t = parse_curst_survey(raw)
            got_t = conn.execute(
                "SELECT COUNT(*) FROM listing_tenants WHERE court=? AND case_no=? AND item_no=?",
                (court, case_no, item_no)).fetchone()[0]
            if len(exp_t) != got_t:
                tenant_mismatch += 1
            for occ in (raw.get("dlt_ordTsRlet") or []):
                if norm_txt(occ.get("rletLstRmk")):
                    curst_narr_missing += 1
                    break

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = os.path.join(ROOT, "harness", "audit", f"parser_fidelity_{stamp}.md")
    L = [f"# 크롤 파서 충실성 감사 — {stamp}", "",
         f"- 대조 표본: 보존 원본 pgj15B **{n_pgj}건** · curst **{n_curst}건**",
         "  (원본 보존 배선 2026-07-23 이후 크롤분만 — 소급 불가, 재크롤에 따라 커버리지 증가)", "",
         "## A. 변형 — 원문 재파생 값 vs 저장 값 불일치", ""]
    if mismatch:
        L += ["| 컬럼 | 불일치 | 예시 |", "|---|---|---|"]
        for col, n in mismatch.most_common():
            L.append(f"| `{col}` | {n}/{n_pgj} | {(mismatch_ex[col][0] if mismatch_ex[col] else '').replace('|','/')} |")
    else:
        L.append(f"✅ **불일치 0건** — 저장된 {n_pgj}건 전부 원문 재파생과 일치(파서 결정론적·멱등 확인).")

    # DB 전수 구조 검사 — 보존 표본이 부족해도 중복 패턴 자체는 확인 가능(X\n X 구조)
    db_dup = db_dup_nl = db_nonempty = 0
    for row in conn.execute("SELECT remark FROM listing_rights WHERE remark IS NOT NULL AND TRIM(remark)<>''"):
        db_nonempty += 1
        if dup_half(row["remark"]):
            db_dup += 1
            if "\n" in (row["remark"] or "").strip():
                db_dup_nl += 1

    testable = dup_testable.get("remark", 0)
    L += ["", "## B. 중복 — 같은 내용 2회 저장", ""]
    if testable == 0:
        L.append(f"⚠ **판정 불가 — 대조 가능 표본 0건** (보존 {n_pgj}건 전부 remark 빈칸). "
                 "'발견 0건'은 안전의 증거가 아니다. remark 있는 물건이 보존되면 재판정.")
    elif dup_cols:
        L += ["| 컬럼 | 중복/대조가능 |", "|---|---|"]
        for col, n in dup_cols.most_common():
            t = dup_testable.get(col, 1)
            L.append(f"| `{col}` | {n}/{t} ({n/t:.0%}) |")
    else:
        L.append(f"✅ 중복 0건 (대조 가능 {testable}건 기준)")
    L += ["",
          f"- 보존 원본에서 `gdsSpcfcRmk` == `dspslGdsRmk` 인 경우: {dup_same_src}/{n_pgj}건",
          "", "### DB 전수 구조 증거 (표본 부족과 무관하게 확인 가능)",
          (f"- remark 비어있지 않은 **{db_nonempty}건** 중 같은 문장 2회 = **{db_dup}건**"
           f" ({db_dup/db_nonempty:.0%})") if db_nonempty else "- (대상 없음)",
          f"- 그중 중간에 개행 있는 것 **{db_dup_nl}건** / 개행 없는 것 **{db_dup - db_dup_nl}건**",
          "",
          "> **원인 확정**: 파서는 `remark = '\\n'.join([gdsSpcfcRmk, dspslGdsRmk])` 로 두 필드를 무조건",
          "> 이어붙인다(`src/courtauction_detail.py: normalize`). 저장값이 예외 없이 `X<개행>X` 구조라는 것은",
          "> **법원이 두 칸에 같은 값을 넣어줄 때 그대로 2회 저장된다**는 뜻(개행 없는 중복이 0건이므로 다른 경로 아님).",
          "> 수정안(미적용): 이어붙이기 전 정규화 비교로 동일 문장 제거.",
          "", "## C. 누락 — 원문에 값이 있는데 어떤 컬럼에도 안 들어가는 필드", "",
          "| 원문 필드 | 값 있는 건수 | 샘플 |", "|---|---|---|"]
    for k, n in missing.most_common(25):
        L.append(f"| `{k}` | {n}/{n_pgj} | {str(missing_ex.get(k,'')).replace('|','/')} |")

    L += ["", "## D. 현황조사서(curst) 대조", "",
          f"- 임차인 행수 불일치: **{tenant_mismatch}/{n_curst}건**",
          f"- **조사 서술 원문(`dlt_ordTsRlet[].rletLstRmk`)이 원문에는 있는데 저장 컬럼 없음: {curst_narr_missing}/{n_curst}건**",
          "  → 블라인드 판독관이 '판정 확정에 필요하다'고 지목한 바로 그 서술문"
          "(\"소유자와의 관계를 알 수 없는 …\")이 파싱되지 않는다.",
          "", "## 판정 규칙",
          "- A(변형)는 파서 버그로 직결 → 발견 즉시 fixture 동결 + 회귀 테스트.",
          "- C(누락)는 전부 문제가 아니다. '안 써도 되는 이유'가 문서화되면 정당한 미사용."]

    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\n리포트: {out}")
    conn.close()
    # (2026-08-24 감사) 무인 실행 배선용 종료코드 계약 — A(변형)는 파서 버그 직결이므로
    # 발견 시 exit 3(권리크롤의 '파서 점검 필요' 코드와 동일 의미). B/C 는 리포트로만.
    return 3 if mismatch else 0


if __name__ == "__main__":
    sys.exit(main())
