"""리스팅 완전성·충실성 감사 (Layer 1+2, 결정론적·크롤 0·LLM 0).

질문: "법원이 공짜로 주는 데이터를 우리가 다 가져오고 저장하는가?"

방법:
  1. raw_listings.raw_json(법원 목록 응답 원문)의 **모든 키**를 전수 조사 — 채움율·샘플값.
  2. 각 키가 코드(src/, deploy/)에서 **한 번이라도 참조되는지** 정적 검사.
  3. 채움율 높은데 코드가 한 번도 안 읽는 키 = **버리는 무료 데이터**(mulBigo 클래스).
  4. 저장 충실성: 저장된 컬럼 값이 raw 원문과 다른 경우(변형·중복) 탐지.

사용:  .venv/Scripts/python.exe scripts/audit_listing_fields.py
출력:  harness/audit/listing_fields_<YYYYMMDD_HHMM>.md
"""
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 이 키들은 우리가 이미 명시적으로 쓰거나(코드 참조 확인됨) 내부 제어용이라 '미사용'이어도 정상.
CODE_DIRS = ["src", "deploy", "scripts"]
SAMPLE_CHARS = 60


def code_corpus() -> str:
    """src/·deploy/·scripts/ 전체 소스를 한 덩어리 문자열로(키 참조 여부 정적 검사용)."""
    buf = []
    for d in CODE_DIRS:
        base = os.path.join(ROOT, d)
        for dirpath, _dirs, files in os.walk(base):
            if "__pycache__" in dirpath:
                continue
            for fn in files:
                if fn.endswith((".py", ".html", ".ps1")):
                    try:
                        with open(os.path.join(dirpath, fn), encoding="utf-8") as f:
                            buf.append(f.read())
                    except (OSError, UnicodeDecodeError):
                        continue
    return "\n".join(buf)


def flatten(obj, prefix="", out=None, depth=0):
    """dict/list를 'a.b' 키 경로로 평탄화(깊이 2까지 — 법원 응답은 얕다)."""
    if out is None:
        out = {}
    if depth > 2:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                flatten(v, key, out, depth + 1)
            else:
                out[key] = v
    elif isinstance(obj, list) and obj:
        flatten(obj[0], prefix + "[]", out, depth + 1)   # 리스트는 첫 원소로 스키마 대표
    return out


def dup_half(t: str) -> bool:
    """'같은 문장 2회 반복' 저장 탐지(비고란 중복 클래스)."""
    t = (t or "").strip()
    if len(t) < 10:
        return False
    h = len(t) // 2
    return t[:h].strip() == t[h:].strip() != ""


def main():
    conn = sqlite3.connect(os.path.join(ROOT, "auction.db"))
    conn.row_factory = sqlite3.Row
    corpus = code_corpus()

    present = defaultdict(int)
    nonempty = defaultdict(int)
    samples = {}
    total = 0
    for row in conn.execute("SELECT raw_json FROM raw_listings"):
        try:
            obj = json.loads(row["raw_json"] or "{}")
        except json.JSONDecodeError:
            continue
        total += 1
        for k, v in flatten(obj).items():
            present[k] += 1
            s = "" if v is None else str(v).strip()
            if s and s not in ("0", "null", "[]", "{}"):
                nonempty[k] += 1
                if k not in samples:
                    samples[k] = s[:SAMPLE_CHARS]

    # 코드 참조 여부 — 키의 마지막 조각(리스트 표기 제거)이 소스에 문자열로 등장하는지
    def referenced(key: str) -> bool:
        leaf = key.split(".")[-1].replace("[]", "")
        if len(leaf) < 3:
            return True          # 너무 짧은 키는 오탐 방지로 '참조됨' 취급
        return bool(re.search(re.escape(leaf), corpus))

    unused = []
    used = []
    for k in sorted(present, key=lambda x: -nonempty[x]):
        rate = nonempty[k] / total if total else 0
        (used if referenced(k) else unused).append((k, nonempty[k], rate, samples.get(k, "")))

    # 저장 충실성 — 비고/특이사항 계열의 중복 저장
    dup_remark = conn.execute(
        "SELECT COUNT(*) FROM listing_rights WHERE remark IS NOT NULL AND TRIM(remark)<>''").fetchone()[0]
    dups = sum(1 for r in conn.execute("SELECT remark FROM listing_rights") if dup_half(r["remark"]))

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = os.path.join(ROOT, "harness", "audit")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"listing_fields_{stamp}.md")

    L = [f"# 리스팅 완전성·충실성 감사 — {stamp}", "",
         f"- 표본: raw_listings {total}건(법원 목록 응답 원문 전수)",
         f"- 발견 키: {len(present)}개 (코드 참조 {len(used)} / **미참조 {len(unused)}**)", "",
         "## 1. 버리는 무료 데이터 후보 (채움율 높은데 코드가 한 번도 안 읽는 키)", "",
         "| 키 | 채움 건수 | 채움율 | 샘플 |", "|---|---|---|---|"]
    for k, n, rate, s in unused:
        if rate >= 0.01:
            L.append(f"| `{k}` | {n} | {rate:.0%} | {s.replace('|', '/')} |")
    if not any(r >= 0.01 for _, _, r, _ in unused):
        L.append("| (없음 — 채움율 1% 이상 미참조 키 없음) | | | |")

    L += ["", "## 2. 참조되는 키 (상위 40 — 채움율 순)", "",
          "| 키 | 채움 건수 | 채움율 | 샘플 |", "|---|---|---|---|"]
    for k, n, rate, s in used[:40]:
        L.append(f"| `{k}` | {n} | {rate:.0%} | {s.replace('|', '/')} |")

    L += ["", "## 3. 저장 충실성 — 중복 저장(같은 문장 2회) 탐지", "",
          f"- listing_rights.remark 비어있지 않은 행: {dup_remark}",
          f"- **그중 '같은 문장 2회 반복' 저장: {dups}건 ({dups/dup_remark:.0%})**" if dup_remark else "- (대상 없음)",
          "", "> 판정: 중복 저장은 등급에 영향 없으나 원문 충실성 위반. 파서의 append 중복 의심 —",
          "> 원문(listing_detail_raw)이 쌓이면 원문 대조로 확정 가능.",
          "", "## 다음 단계", "- 1번 표에 오른 키 = '공짜인데 안 쓰는 데이터' 후보 → 파싱·표출 검토(무료 격차 해소).",
          "- 확정 오류는 harness/AUDIT_CASEBOOK.md 기재 후 클래스 수정."]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L[:6]))
    print(f"\n미참조 키(채움율 1%+): {sum(1 for _, _, r, _ in unused if r >= 0.01)}개")
    print(f"remark 중복 저장: {dups}/{dup_remark}")
    print(f"리포트: {path}")
    conn.close()


if __name__ == "__main__":
    main()
