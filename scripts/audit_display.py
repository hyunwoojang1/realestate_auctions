"""표출 정합 감사 (Layer 1 — 화면이 DB에 정직한가, 결정론적·크롤 0·LLM 0).

지향점(harness/AUDIT_CHARTER.md §6): DB가 맞아도 **화면이 거짓말할 수 있다.**
같은 물건에 대해 4개 표면(목록·상세·API·CSV)이 서로, 그리고 DB와 같은 말을 하는지 대조한다.
실사고: 상세는 "⛔권리미확인"인데 목록은 초록 "차익 유력"으로 40시간 노출(삼환 2022타경3289).

⚠️ 이것은 **정합** 감사다. "소비자에게 정말 편한가"(UIUX 사용성)는 별도 트랙이다.

검사(모순 클래스):
  D1 등급 불일치      — DB / API / CSV / 상세 사이의 grade 불일치
  D2 인수 신호 상실   — 명세서에 인수 문구가 있는데 API burden_status='clean'
  D3 초록 오표시      — 명세서에 인수 문구가 있는데 상세가 "치명적 인수권리 미발견"(초록)
  D4 인수금액 침묵    — 인수 문구 있고 상세가 부담을 인정하는데 API assumed_amount 가 비어 있음
  D5 표면 누락        — 목록/API/CSV 중 어느 한 곳에서만 물건이 사라짐

네트워크·서버 불요: Flask test_client 로 직접 호출한다.
사용:  .venv/Scripts/python.exe scripts/audit_display.py [--n 60]
출력:  harness/audit/display_<YYYYMMDD_HHMM>.md
"""
import argparse
import csv
import io
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DB = os.path.join(ROOT, "auction.db")
REC = ("차익 유력", "양호", "관심")
# 상세 페이지가 '치명적 인수권리 없음'을 초록으로 선언하는 문구(templates/detail.html)
GREEN_PHRASE = "치명적 인수권리 미발견"
# 명세서 인수 문구 탐지 — 인수/대항 가능 표현이 있고 '해당사항없음'이 아닌 경우
INSU_RE = re.compile(r"인수|대항할 수 있는")


def has_insu(text):
    t = (text or "").strip()
    if not t or "해당사항없음" in t:
        return False
    return bool(INSU_RE.search(t))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60, help="상세 페이지를 열어볼 표본 수")
    args = ap.parse_args()

    os.environ["AUCTION_DB"] = DB
    from src.web import create_app  # noqa: PLC0415 — env 설정 후 import

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # --- DB 진실 ---
    truth = {}
    for r in conn.execute(
        """SELECT s.court, s.case_no, s.item_no, s.grade, s.rights_verified, s.assumed_amount,
                  r.surviving_rights, r.lien_note
           FROM scored_listings s
           LEFT JOIN listing_rights r
             ON r.court=s.court AND r.case_no=s.case_no AND r.item_no=s.item_no"""):
        key = f"{r['court']}|{r['case_no']}|{r['item_no']}"
        truth[key] = {
            "grade": r["grade"], "verified": r["rights_verified"],
            "assumed": r["assumed_amount"] or 0,
            "insu": has_insu(r["surviving_rights"]) or has_insu(r["lien_note"]),
            "case_no": r["case_no"], "court": r["court"], "item_no": r["item_no"],
        }

    client = create_app().test_client()

    # --- API 표면 ---
    api_rows = json.loads(client.get("/api/listings").get_data(as_text=True))
    api = {f"{x.get('court')}|{x.get('case_no')}|{x.get('item_no')}": x for x in api_rows}

    # --- CSV 표면 ---
    csv_text = client.get("/export.csv").get_data(as_text=True).lstrip("﻿")
    csv_rows = list(csv.DictReader(io.StringIO(csv_text)))
    hdr = csv_rows[0].keys() if csv_rows else []
    kc = next((h for h in hdr if "사건" in h or h == "case_no"), None)
    gc = next((h for h in hdr if "등급" in h or h == "grade"), None)
    csv_by_case = {}
    for row in csv_rows:
        if kc:
            csv_by_case.setdefault(row.get(kc), []).append(row)

    findings = Counter()
    examples = {}

    def flag(code, key, detail):
        findings[code] += 1
        examples.setdefault(code, []).append(f"{key} — {detail}")

    # --- D2/D5: API 전수 대조 ---
    for key, t in truth.items():
        a = api.get(key)
        if a is None:
            if t["grade"] != "미지원유형":
                flag("D5", key, f"DB에는 있으나 /api/listings 에 없음(grade={t['grade']})")
            continue
        if a.get("grade") != t["grade"]:
            flag("D1", key, f"DB grade={t['grade']} vs API grade={a.get('grade')}")
        if t["insu"] and a.get("burden_status") == "clean":
            flag("D2", key, f"명세서에 인수 문구 있음 vs API burden_status=clean (grade={t['grade']})")

    # --- CSV 등급 대조 ---
    if kc and gc:
        for key, t in truth.items():
            rows = csv_by_case.get(t["case_no"])
            if not rows:
                continue
            if len(rows) == 1 and rows[0].get(gc) and rows[0].get(gc) != t["grade"]:
                flag("D1", key, f"DB grade={t['grade']} vs CSV grade={rows[0].get(gc)}")

    # --- D3/D4: 상세 페이지 표본(적대적: 인수 문구 있는 추천 물건 우선) ---
    ranked = sorted(
        truth.items(),
        key=lambda kv: (not (kv[1]["insu"] and kv[1]["grade"] in REC),
                        kv[1]["grade"] not in REC, kv[0]))
    sample = ranked[: args.n]
    detail_checked = detail_multi = 0
    for key, t in sample:
        resp = client.get(f"/property/{t['case_no']}")
        if resp.status_code != 200:
            flag("D5", key, f"상세 페이지 HTTP {resp.status_code}")
            continue
        body = resp.get_data(as_text=True)
        if "물건 선택" in body or "choose" in body[:2000].lower():
            detail_multi += 1        # 다물건 선택 페이지 — 단건 판정 불가
            continue
        detail_checked += 1
        if t["insu"] and GREEN_PHRASE in body:
            flag("D3", key, f"명세서 인수 문구 있는데 상세가 '{GREEN_PHRASE}'(초록) (grade={t['grade']})")
        if t["insu"] and ("인수 부담" in body or "인수됨" in body or "인수함" in body):
            a = api.get(key) or {}
            if not (a.get("assumed_amount") or 0):
                flag("D4", key, f"상세는 인수 부담 표시 vs API assumed_amount 비어있음 (grade={t['grade']})")
        if t["grade"] == "권리미확인" and t["grade"] not in body and "권리미확인" not in body:
            flag("D1", key, "DB grade=권리미확인인데 상세에 해당 표시 없음")

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    total_insu_rec = sum(1 for t in truth.values() if t["insu"] and t["grade"] in REC)
    L = [f"# 표출 정합 감사 — {stamp}", "",
         f"- 대조 표면: **DB / /api/listings({len(api)}건) / /export.csv({len(csv_rows)}행) / /property/<case>**",
         f"- 상세 표본: {len(sample)}건 요청 → 단건 판정 {detail_checked}건 (다물건 선택페이지 {detail_multi}건 제외)",
         f"- 적대적 표본 기준: '명세서 인수 문구 + 추천등급' 물건 우선({total_insu_rec}건 존재)", "",
         "| 코드 | 모순 | 건수 |", "|---|---|---|",
         f"| D1 | 등급 불일치(DB↔API↔CSV↔상세) | {findings['D1']} |",
         f"| D2 | 인수 문구 있는데 API burden_status='clean' | **{findings['D2']}** |",
         f"| D3 | 인수 문구 있는데 상세가 '{GREEN_PHRASE}'(초록) | **{findings['D3']}** |",
         f"| D4 | 상세는 인수 부담 표시인데 API assumed_amount 비어있음 | **{findings['D4']}** |",
         f"| D5 | 표면 누락(DB에 있으나 API/상세에 없음) | {findings['D5']} |", ""]
    for code in ("D2", "D3", "D4", "D1", "D5"):
        if findings[code]:
            L += [f"### {code} 예시 (최대 5)", ""]
            L += [f"- {e}" for e in examples[code][:5]]
            L.append("")
    if not sum(findings.values()):
        L.append("✅ 검출된 모순 0건 (표본·표면 범위 내).")
    L += ["## 한계",
          "- 상세는 표본만 연다(전건 렌더는 비용이 큼). D1/D2/D5는 API·CSV 전수 대조.",
          "- HTML 문구 매칭 기반이라 템플릿 문구가 바뀌면 탐지가 무력화된다(문구 상수는 스크립트 상단).",
          "- 다물건 사건은 상세가 선택 페이지를 반환해 단건 판정에서 제외된다.",
          "- 이것은 정합 감사다. 사용성(UIUX)은 별도 트랙."]

    out = os.path.join(ROOT, "harness", "audit", f"display_{stamp}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\n리포트: {out}")
    conn.close()


if __name__ == "__main__":
    main()
