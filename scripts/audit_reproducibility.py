"""재현성 분석 (블라인드 라운드 간 일관성 · 새 클래스 발생 추적).

지향점(harness/AUDIT_CHARTER.md §0 공통 성공기준):
**불일치 건수가 아니라 '새로운 유형(클래스)'의 감소가 신뢰의 지표다.**
라운드를 거듭해도 같은 클래스만 나온다면 감사가 수렴한 것이고,
계속 새 클래스가 나오면 아직 바닥을 못 본 것이다.

분석:
  1. 라운드별 판정 분포(일치/CRITICAL/HIGH/불안정) — 비율이 재현되는가
  2. 발견을 **원인 클래스**로 분류 — 라운드별 클래스 집합의 변화(신규 클래스 등장 여부)
  3. 판독 안정성 — 3판독 합의 실패(불안정) 비율

사용:  .venv/Scripts/python.exe scripts/audit_reproducibility.py
출력:  harness/audit/reproducibility_<YYYYMMDD_HHMM>.md
"""
import os
import re
import sys
from collections import Counter
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT = os.path.join(ROOT, "harness", "audit")

# 원인 클래스 분류 규칙 — 판독 근거 문구에서 '왜 우리와 갈렸는가'를 뽑는다.
# 순서가 곧 우선순위(먼저 맞는 것으로 분류).
CLASSES = [
    # (2026-07-23 자기수정) 첫 실행에서 3건이 'C-기타'로 빠져 신규 클래스처럼 보였으나, 실물 확인 결과
    # 전부 기존 클래스의 **다른 표현**이었다(전세권 인수 / '자료가 전무'). 정규식을 넓혀 오탐 제거.
    ("C-인수명시", r"임차권등기|전세권|인수를 명시|인수 권리|잔액.{0,6}인수|인수될 수 있|인수된다|인수됨|인수한다"),
    ("C-확약완화", r"확약서|말소동의|포기"),
    ("C-조사서부재",
     r"현황조사서.{0,12}(미제공|부재|없|전무)|전입세대.{0,12}(미제공|부재|없|전무)|점유자 자료.{0,6}전무"),
    ("C-임대미상", r"임대관계.{0,4}미상|관계.{0,4}미상"),
    ("C-시장신호", r"유찰|대금미납|미납"),
    ("C-물건불일치", r"공부상.{0,10}현황|호수|불일치"),
    ("C-관리비", r"관리비"),
    ("C-말소기준개시", r"경매개시결정.{0,10}말소기준|말소기준.{0,10}경매개시"),
]


def classify(text):
    hits = [name for name, pat in CLASSES if re.search(pat, text or "")]
    return hits or ["C-기타"]


def parse_report(path):
    """REPORT.md 표에서 (등급, 케이스, 근거) 행을 추출."""
    rows = []
    head = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"- 결과: 일치 (\d+) · \*\*CRITICAL (\d+)\*\* · HIGH (\d+) · 불안정 (\d+) · 판독부족 (\d+)", line)
            if m:
                head = dict(zip(("일치", "CRITICAL", "HIGH", "불안정", "판독부족"),
                                (int(x) for x in m.groups()), strict=False))
            if line.startswith("| ") and "|" in line[2:]:
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if len(cells) >= 6 and cells[0] in ("CRITICAL", "HIGH", "불안정", "일치", "판독부족"):
                    rows.append({"verdict": cells[0], "case": cells[1], "reason": cells[5]})
    return head, rows


def main():
    rounds = sorted(d for d in os.listdir(AUDIT)
                    if d.startswith("round_") and os.path.exists(os.path.join(AUDIT, d, "REPORT.md")))
    if not rounds:
        sys.exit("라운드 리포트 없음")

    per_round, cls_per_round, all_rows = {}, {}, []
    for r in rounds:
        head, rows = parse_report(os.path.join(AUDIT, r, "REPORT.md"))
        per_round[r] = (head, rows)
        c = Counter()
        for row in rows:
            if row["verdict"] in ("CRITICAL", "HIGH"):
                for k in classify(row["reason"]):
                    c[k] += 1
        cls_per_round[r] = c
        all_rows += [(r, row) for row in rows]

    L = [f"# 재현성 분석 — {datetime.now():%Y%m%d_%H%M}", "",
         "> 성공 기준(헌장 §0): 불일치 **건수**가 아니라 **새 클래스의 감소**.", "",
         "## 1. 라운드별 판정 분포", "",
         "| 라운드 | 표본 | 일치 | CRITICAL | HIGH | 불안정 | CRITICAL 비율 |", "|---|---|---|---|---|---|---|"]
    for r in rounds:
        h, rows = per_round[r]
        n = sum(h.get(k, 0) for k in ("일치", "CRITICAL", "HIGH", "불안정", "판독부족")) or len(rows)
        crit = h.get("CRITICAL", 0)
        L.append(f"| {r.replace('round_2026','')} | {n} | {h.get('일치',0)} | **{crit}** | "
                 f"{h.get('HIGH',0)} | {h.get('불안정',0)} | {crit/n:.0%} |" if n else "")

    # 2. 클래스 추적
    first_seen, cum = {}, set()
    L += ["", "## 2. 원인 클래스 — 신규 등장 추적", "",
          "| 라운드 | 등장 클래스 | **신규 클래스** |", "|---|---|---|"]
    for r in rounds:
        cs = set(cls_per_round[r])
        new = cs - cum
        for k in new:
            first_seen[k] = r
        cum |= cs
        L.append(f"| {r.replace('round_2026','')} | {', '.join(sorted(cs)) or '-'} | "
                 f"{'**' + ', '.join(sorted(new)) + '**' if new else '없음 ✅'} |")

    L += ["", "### 클래스별 누적 빈도", "", "| 클래스 | 총 건수 | 최초 등장 |", "|---|---|---|"]
    tot = Counter()
    for r in rounds:
        tot += cls_per_round[r]
    for k, n in tot.most_common():
        L.append(f"| {k} | {n} | {first_seen.get(k,'?').replace('round_2026','')} |")

    # 3. 판독 안정성
    unstable = sum(per_round[r][0].get("불안정", 0) for r in rounds)
    total_cases = sum(len(per_round[r][1]) for r in rounds)
    L += ["", "## 3. 판독 안정성", "",
          f"- 전체 {total_cases}건 중 판독 합의 실패(불안정) **{unstable}건** "
          f"({unstable/total_cases:.0%})" if total_cases else "- (없음)",
          "- 불안정은 오류 선언에 쓰지 않는다(자료 부족이 원인일 수 있음 — 대개 현황조사서 부재).", "",
          "## 4. 판정", ""]

    last_new = [k for k, v in first_seen.items() if v == rounds[-1]]
    if not last_new:
        L.append("✅ **마지막 라운드에서 신규 클래스 0건** — 이 표본 성격(클린 서빙 물건)에 대해서는 "
                 "감사가 수렴하기 시작했다. 다음은 **표본 성격을 바꿔**(예: 서빙 등급 기준·다른 등급대) 재확인.")
    else:
        L.append(f"⚠ 마지막 라운드에서 **신규 클래스 {len(last_new)}건**({', '.join(last_new)}) — 아직 수렴 전. "
                 "같은 성격의 표본으로 라운드를 더 돌려야 한다.")
    L += ["",
          "> 주의: 이 분석은 판독 근거 **문구의 정규식 분류**다. 문구가 달라지면 분류가 흔들릴 수 있다.",
          "> 클래스 정의는 이 스크립트 상단 `CLASSES` 에 있고, 확정 오류의 정본은 AUDIT_CASEBOOK.md 다."]

    out = os.path.join(AUDIT, f"reproducibility_{datetime.now():%Y%m%d_%H%M}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\n리포트: {out}")


if __name__ == "__main__":
    main()
