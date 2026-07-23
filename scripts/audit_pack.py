"""블라인드 감사 패커 — 케이스 샘플링(적대적+층화) → 실명 마스킹 → 서류 꾸러미 생성.

블라인드 프로토콜의 '출제자' 역할(오염 OK). 꾸러미에는 법원 공개 사실만 넣고,
우리 판정(grade·rights_verified·시세·점수)은 절대 넣지 않는다. 정답지(우리 판정
스냅샷)는 레포 쪽 harness/audit/<round>/manifest.json 에만 남긴다.

적대적 샘플링: rights_verified=1 이고 추천계열 등급(차익 유력·양호·관심)으로
"문제없음"이라 서빙 중인 물건 위주 — false negative(삼환류)가 위험한 방향이므로.
층화: 법원별 라운드로빈. 감사 이력(harness/audit_state.json)에 있는 물건은 제외.

사용:  .venv/Scripts/python.exe scripts/audit_pack.py --n 10
크롤 0 — 저장 데이터만 사용한다.
"""
import argparse
import json
import os
import random
import re
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from src.courtauction_fields import mask_personal_names  # noqa: E402

BLIND_DIR_DEFAULT = r"C:\Users\notebiz765\장현우\auction-blind-audit"
RECOMMEND_GRADES = ("차익 유력", "양호", "관심")


def won(n):
    return f"{int(n):,}원" if n else "(기재 없음)"


def mask(t):
    return mask_personal_names(t or "")


def field(t):
    t = mask(t).strip()
    return t if t else "(기재 없음)"


def load_state(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"audited_doc_ids": []}


def sample_cases(conn, n, state):
    audited = set(state.get("audited_doc_ids", []))
    rows = conn.execute(
        """SELECT s.doc_id, s.court, s.case_no, s.item_no, s.apt_name, s.address,
                  s.property_type, s.area_m2, s.appraisal_price, s.min_bid_price,
                  s.fail_count, s.sale_date, s.grade, s.rights_verified,
                  r.surviving_rights, r.senior_lien, r.lien_note, r.remark,
                  r.claim_amt, r.demand_end, r.spec_write_ymd, r.court_dept,
                  r.schedule, r.appraisal_notes
           FROM scored_listings s JOIN listing_rights r
             ON s.court=r.court AND s.case_no=r.case_no AND s.item_no=r.item_no
           WHERE s.rights_verified=1 AND s.grade IN (?,?,?)""",
        RECOMMEND_GRADES,
    ).fetchall()
    pool = [r for r in rows if r["doc_id"] not in audited]
    random.shuffle(pool)
    by_court = {}
    for r in pool:
        by_court.setdefault(r["court"], []).append(r)
    picked = []
    while len(picked) < min(n, len(pool)):
        for c in list(by_court):
            if by_court[c] and len(picked) < min(n, len(pool)):
                picked.append(by_court[c].pop())
    return picked


def case_dirname(r):
    safe_case = re.sub(r"[^0-9가-힣타경]", "", r["case_no"])
    return f"case_{r['court']}_{safe_case}_{r['item_no']}"


def write_bundle(r, tenants, inbox):
    d = os.path.join(inbox, case_dirname(r))
    os.makedirs(d, exist_ok=True)

    sched_rows = ""
    try:
        for s in json.loads(r["schedule"] or "[]"):
            sched_rows += f"| {s.get('ymd','')} | {s.get('kind','')} | {s.get('result','') or '(예정)'} | {won(s.get('price'))} |\n"
    except Exception:
        sched_rows = "| (자료 없음) | | | |\n"

    with open(os.path.join(d, "물건정보.md"), "w", encoding="utf-8") as f:
        f.write(f"""# 물건 기본정보 (법원 공고 기준)

| 항목 | 값 |
|---|---|
| 사건번호 | {r['case_no']} (물건 {r['item_no']}) |
| 법원 | {r['court']} {r['court_dept'] or ''} |
| 소재지 | {mask(r['address'])} |
| 물건 유형 | {r['property_type']} |
| 전용면적 | {r['area_m2'] or '미상'}㎡ |
| 감정가 | {won(r['appraisal_price'])} |
| 최저매각가 | {won(r['min_bid_price'])} (유찰 {r['fail_count'] or 0}회) |
| 청구금액 | {won(r['claim_amt'])} |
| 배당요구종기 | {r['demand_end'] or '(기재 없음)'} |

## 기일 내역

| 일자 | 구분 | 결과 | 최저가 |
|---|---|---|---|
{sched_rows}""")

    with open(os.path.join(d, "매각물건명세서.md"), "w", encoding="utf-8") as f:
        f.write(f"""# 매각물건명세서 (요지)

- 작성일: {r['spec_write_ymd'] or '(기재 없음)'}

| 항목 | 기재 내용 |
|---|---|
| 최선순위 설정 | {field(r['senior_lien'])} |
| 매각으로 소멸되지 아니하는 권리 (인수되는 권리) | {field(r['surviving_rights'])} |
| 유치권 등 | {field(r['lien_note'])} |
| 비고란 | {field(r['remark'])} |
""")

    if tenants:
        rows = ""
        for i, t in enumerate(tenants, 1):
            dep = won(t["deposit"]) if t["deposit"] else "(기재 없음)"
            rows += (f"| 세대 {i} (성명 비공개) | {t['movein_ymd'] or '(기재 없음)'} | "
                     f"{t['confirm_ymd'] or '(기재 없음)'} | {dep} | "
                     f"{field(t['part'])} | {field(t['usage'])} | {field(t['possession'])} |\n")
        body = f"""## 전입세대 목록

| 세대 | 전입일 | 확정일자 | 보증금 | 임차 부분 | 용도 | 점유관계 기재 |
|---|---|---|---|---|---|---|
{rows}
## 조사 서술 원문

(제공된 자료에 서술 원문 없음)
"""
    else:
        body = "(이 물건의 현황조사서 자료가 제공되지 않음)\n"
    with open(os.path.join(d, "현황조사서.md"), "w", encoding="utf-8") as f:
        f.write("# 현황조사서 (전입세대열람 결과)\n\n" + body)

    notes = []
    try:
        for a in json.loads(r["appraisal_notes"] or "[]"):
            if a.get("label") in ("이용상태", "건물 구조", "기타 참고사항"):
                notes.append(f"- {a['label']}: {mask(a.get('text',''))}")
    except Exception:
        pass
    with open(os.path.join(d, "감정평가서요지.md"), "w", encoding="utf-8") as f:
        f.write("# 감정평가서 요지 (발췌)\n\n" + ("\n".join(notes) or "(자료 없음)") + "\n")
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--db", default=os.path.join(ROOT, "auction.db"))
    ap.add_argument("--blind-dir", default=BLIND_DIR_DEFAULT)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    if args.seed is not None:
        random.seed(args.seed)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    state_path = os.path.join(ROOT, "harness", "audit_state.json")
    state = load_state(state_path)

    picked = sample_cases(conn, args.n, state)
    inbox = os.path.join(args.blind_dir, "inbox")
    os.makedirs(inbox, exist_ok=True)

    round_id = "round_" + datetime.now().strftime("%Y%m%d_%H%M")
    round_dir = os.path.join(ROOT, "harness", "audit", round_id)
    os.makedirs(round_dir, exist_ok=True)

    manifest = {"round": round_id, "track": "rights", "created": datetime.now().isoformat(),
                "protocol": "3-판독 일치제(2/3 합의)", "cases": []}
    dirs = []
    for r in picked:
        tenants = conn.execute(
            "SELECT * FROM listing_tenants WHERE court=? AND case_no=? AND item_no=? ORDER BY seq",
            (r["court"], r["case_no"], r["item_no"])).fetchall()
        d = write_bundle(r, tenants, inbox)
        dirs.append(os.path.basename(d))
        manifest["cases"].append({
            "case_dir": os.path.basename(d),
            "doc_id": r["doc_id"], "court": r["court"], "case_no": r["case_no"],
            "item_no": r["item_no"], "apt_name": r["apt_name"],
            # 정답지(우리 판정 스냅샷) — 블라인드 폴더에는 절대 복사 금지
            "our_grade": r["grade"], "our_rights_verified": r["rights_verified"],
            "our_call": "없음(클린 서빙)",  # 이 표본풀은 전부 추천등급+권리반영=인수신호 없음으로 서빙 중
            "surviving_rights_empty": not (r["surviving_rights"] or "").strip(),
            "tenants_rows": len(tenants),
        })
        state.setdefault("audited_doc_ids", []).append(r["doc_id"])

    with open(os.path.join(round_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)

    print(json.dumps({"round": round_id, "cases": dirs}, ensure_ascii=False))


if __name__ == "__main__":
    main()
