"""블라인드 감사 대조관 — 판독관 3명의 판정을 수거해 합의를 만들고 우리 DB 판정과 대조한다.

3-판독 일치제(운영자 지시 2026-07-23): 케이스당 verdicts/<case>__r1..r3.md 3부.
- 2/3 이상 같은 판정 → 합의 채택, 우리 판정과 비교
- 제각각 → '판독 불안정' (오류 선언에 사용 금지, 자료 보강 후 재판독)

대조 결과 등급:
- 일치        : 합의 == 우리 판정
- CRITICAL    : 합의가 있음/위험인데 우리는 없음(클린 서빙) → false-negative 의심, 심문 개시
- HIGH        : 합의가 판단불가인데 우리는 없음 → '모름을 없음으로 서빙' 의심
- 불안정      : 판독관 간 합의 실패

사용:  .venv/Scripts/python.exe scripts/audit_compare.py            # 최신 라운드
       .venv/Scripts/python.exe scripts/audit_compare.py --round round_20260723_1116
"""
import argparse
import glob
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLIND_DIR_DEFAULT = r"C:\Users\notebiz765\장현우\auction-blind-audit"


def canon(s):
    """판정 어휘 정규화 — '위험 있음(배제 불가)' 같은 변형을 4범주로."""
    s = (s or "").strip()
    if "판단불가" in s or s == "불가":
        return "판단불가"
    if "위험" in s:
        return "위험"
    if "없음" in s:
        return "없음"
    if "있음" in s:
        return "있음"
    return "파싱실패"


def parse_verdict(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not m:
        return {"판정": "파싱실패", "핵심근거": "(6번 JSON 블록 없음)"}
    try:
        j = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {"판정": "파싱실패", "핵심근거": "(JSON 파싱 실패)"}
    j["판정"] = canon(str(j.get("판정", "")))
    return j


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", default=None)
    ap.add_argument("--blind-dir", default=BLIND_DIR_DEFAULT)
    args = ap.parse_args()

    audit_root = os.path.join(ROOT, "harness", "audit")
    rounds = sorted(os.listdir(audit_root)) if os.path.isdir(audit_root) else []
    round_id = args.round or (rounds[-1] if rounds else None)
    if not round_id:
        sys.exit("라운드 없음 — 먼저 audit_pack.py 실행")
    round_dir = os.path.join(audit_root, round_id)
    with open(os.path.join(round_dir, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)

    vdir = os.path.join(args.blind_dir, "verdicts")
    rows, counts = [], {"일치": 0, "CRITICAL": 0, "HIGH": 0, "불안정": 0, "판독부족": 0}
    for c in manifest["cases"]:
        vfiles = sorted(glob.glob(os.path.join(vdir, c["case_dir"] + "__r*.md")))
        verdicts = [parse_verdict(p) for p in vfiles]
        calls = [v["판정"] for v in verdicts if v["판정"] != "파싱실패"]
        ours = canon(c["our_call"])

        if len(calls) < 2:
            status, consensus = "판독부족", "-"
        else:
            best = max(set(calls), key=calls.count)
            if calls.count(best) >= 2:
                consensus = best
                if consensus == ours:
                    status = "일치"
                elif consensus in ("있음", "위험"):
                    status = "CRITICAL"
                elif consensus == "판단불가":
                    status = "HIGH"
                else:
                    status = "일치"  # 합의=없음, 우리=없음 외 조합은 현 표본풀에 없음
            else:
                status, consensus = "불안정", "제각각(" + "/".join(calls) + ")"
        counts[status] += 1
        reason = next((v.get("핵심근거", "") for v in verdicts if v.get("핵심근거")), "")
        rows.append((status, c, consensus, [v["판정"] for v in verdicts], reason))

    sev_order = {"CRITICAL": 0, "HIGH": 1, "불안정": 2, "판독부족": 3, "일치": 4}
    rows.sort(key=lambda r: sev_order[r[0]])

    lines = [f"# 블라인드 감사 대조 리포트 — {round_id}",
             "",
             f"- 표본: {len(manifest['cases'])}건 (적대적 샘플: 전부 '없음(클린)'으로 서빙 중인 물건)",
             f"- 결과: 일치 {counts['일치']} · **CRITICAL {counts['CRITICAL']}** · HIGH {counts['HIGH']}"
             f" · 불안정 {counts['불안정']} · 판독부족 {counts['판독부족']}",
             "",
             "| 등급 | 케이스 | 우리 판정 | 판독 3인 | 합의 | 판독 근거(대표) |",
             "|---|---|---|---|---|---|"]
    for status, c, consensus, calls, reason in rows:
        lines.append(f"| {status} | {c['case_dir']} ({c['apt_name'] or ''}) | {c['our_call']} | "
                     f"{'/'.join(calls) or '-'} | {consensus} | {reason[:120]} |")
    lines += ["",
              "## 다음 단계",
              "- CRITICAL/HIGH: 심문 개시 — 원문 재확인 후 오류 확정 시 harness/AUDIT_CASEBOOK.md 기록",
              "  + fixture 동결 + 회귀 테스트 + 클래스 수정. (1라운드는 파일럿 — 성급한 확정 금지)",
              "- 불안정: 서류 부족이 원인인지 확인(대개 현황조사서·등기부 부재) → 자료 보강 후 재판독",
              "- 일치: 통과. audit_state.json에 감사 이력 기록됨(재표본 제외)."]
    report_path = os.path.join(round_dir, "REPORT.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines[:8]))
    print(f"\n리포트: {report_path}")


if __name__ == "__main__":
    main()
