"""데이터 커버리지 감사 (Layer 1+2, 결정론적·크롤 0·LLM 0).

지향점(AUDIT_CHARTER §0-①): 위험한 오류는 "문제없음" 쪽에 있다.
따라서 커버리지는 전체 평균이 아니라 **"우리가 추천 중인 물건"에서 얼마나 차 있는가**로 본다.
파이프(수집 경로)가 있어도 정작 위험 판정이 필요한 물건에 데이터가 없으면 그 판정은 근거가 없다.

검사:
  A. 원천별 커버리지 — 전체 vs **추천등급** (격차가 크면 수집 표적이 잘못 겨눠진 것)
  B. 추천 물건의 권리 근거 성격 — '침묵을 근거로 안전 판정'한 물건 수
  C. 수집 표적 정합성 — 백필 대상 필터와 추천등급의 교집합(0이면 구조적 사각지대)

사용:  .venv/Scripts/python.exe scripts/audit_coverage.py
출력:  harness/audit/coverage_<YYYYMMDD_HHMM>.md
"""
import os
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC = "grade IN ('차익 유력','양호','관심')"

# (이름, 조인 대상 테이블, 추가 조건) — 물건 단위 보유 여부
SOURCES = [
    ("권리 요지(매각물건명세서)", "listing_rights", ""),
    ("임차인(현황조사서)", "listing_tenants", ""),
    ("원본 보존(pgj15B)", "listing_detail_raw", "AND t.doc_type='pgj15B'"),
    ("사진", "listing_photos", ""),
    ("네이버 시세 매칭", "naver_prices", ""),
    ("건축물대장", "listing_building", "AND t.status='ok'"),
]


def main():
    conn = sqlite3.connect(os.path.join(ROOT, "auction.db"))
    conn.row_factory = sqlite3.Row
    q = lambda s: conn.execute(s).fetchone()[0]  # noqa: E731

    tot = q("SELECT COUNT(*) FROM scored_listings")
    rec = q(f"SELECT COUNT(*) FROM scored_listings WHERE {REC}")

    L = [f"# 데이터 커버리지 감사 — {datetime.now():%Y%m%d_%H%M}", "",
         f"- 모수: scored **{tot}건** / 그중 **추천등급 {rec}건**(차익 유력·양호·관심)",
         "- 판정 관점: 전체 커버리지가 아니라 **추천 물건에서의 커버리지**가 위험량을 결정한다.", "",
         "## A. 원천별 커버리지", "",
         "| 원천 | 전체 보유 | 전체 % | 추천등급 보유 | **추천 %** | 판정 |", "|---|---|---|---|---|---|"]

    for name, tbl, extra in SOURCES:
        j = (f"SELECT COUNT(DISTINCT s.court||'|'||s.case_no||'|'||s.item_no) FROM scored_listings s "
             f"JOIN {tbl} t ON t.court=s.court AND t.case_no=s.case_no AND t.item_no=s.item_no {extra}")
        n_all = q(j)
        n_rec = q(j + f" WHERE {REC.replace('grade', 's.grade')}")
        pa, pr = n_all / tot if tot else 0, n_rec / rec if rec else 0
        verdict = "⚠ **추천 물건에 사실상 없음**" if pr < 0.05 else ("주의" if pr < 0.5 else "양호")
        L.append(f"| {name} | {n_all} | {pa:.0%} | {n_rec} | **{pr:.0%}** | {verdict} |")

    # B. 추천 물건의 권리 근거 성격
    jr = ("FROM scored_listings s JOIN listing_rights r ON r.court=s.court AND r.case_no=s.case_no "
          "AND r.item_no=s.item_no")
    silent = q(f"SELECT COUNT(*) {jr} WHERE {REC.replace('grade','s.grade')} "
               "AND TRIM(COALESCE(r.surviving_rights,''))=''")
    spoken = q(f"SELECT COUNT(*) {jr} WHERE {REC.replace('grade','s.grade')} "
               "AND TRIM(COALESCE(r.surviving_rights,''))<>''")
    no_senior = q(f"SELECT COUNT(*) {jr} WHERE {REC.replace('grade','s.grade')} "
                  "AND TRIM(COALESCE(r.senior_lien,''))=''")

    L += ["", "## B. 추천 물건은 '무엇을 근거로' 안전한가", "",
          "| 근거 성격 | 건수 |", "|---|---|",
          f"| 인수권리란에 **내용 있음**(읽고 판단함) | {spoken} |",
          f"| 인수권리란 **빈칸** — 침묵을 근거로 안전 판정 | **{silent}** |",
          f"| 말소기준 빈칸(날짜 비교 자체가 불가) | {no_senior} |",
          "",
          "> **모름은 없음이 아니다**(헌장 §0-②). 빈칸을 근거로 한 안전 판정은,",
          "> 그 물건의 현황조사서를 확보하기 전까지는 **미확인**이지 안전이 아니다."]

    # C. 수집 표적 정합성
    backfill_all = q("SELECT COUNT(*) FROM scored_listings WHERE rights_verified=0 AND grade<>'미지원유형'")
    backfill_rec = q(f"SELECT COUNT(*) FROM scored_listings WHERE rights_verified=0 AND {REC}")
    L += ["", "## C. 수집 표적 정합성 — 현황조사서(B-2) 백필은 누구를 겨누는가", "",
          f"- 백필 대상(`rights_verified=0` · 미지원유형 제외): **{backfill_all}건**",
          f"- **그중 추천등급: {backfill_rec}건**",
          "",
          "> 백필 필터(`deploy/crawl_rights.py: _tenant_targets`)가 `rights_verified = 0` 을 요구하는데,",
          "> 추천등급은 전부 `rights_verified = 1` 이다. 즉 **추천 물건에는 현황조사서가 설계상 영원히**",
          "> **수집되지 않는다**(교집합 구조적 0).",
          "> 대항력 임차인은 매각물건명세서 요지가 아니라 **현황조사서**에 나타나므로,",
          "> 위 B의 '빈칸을 근거로 안전 판정'한 물건들은 검증 수단 자체가 차단돼 있다.",
          "",
          "## 판정 규칙",
          "- A에서 '추천 %'가 낮은 원천 = 그 원천에 기반한 판정이 추천 물건에서는 **근거 없음**.",
          "- C의 교집합이 0이면 수집 전략이 헌장 §0-①(문제없음을 표적으로)에 반한다.",
          "- 확정 오류는 harness/AUDIT_CASEBOOK.md 기재 후 클래스 수정(파일럿 기간엔 기록만)."]

    out = os.path.join(ROOT, "harness", "audit", f"coverage_{datetime.now():%Y%m%d_%H%M}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\n리포트: {out}")
    conn.close()


if __name__ == "__main__":
    main()
