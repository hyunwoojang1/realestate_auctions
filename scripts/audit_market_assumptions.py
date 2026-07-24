"""시세 가정 반증 스윕 (Layer 2 코드 감사, 결정론적·크롤 0·LLM 0).

docs/시세_가정_명세서.md 의 위험 '상' 가정들에 대해 "이 가정이 깨진 실물이 DB에 몇 건인가"를 센다.
가정이 깨진 건수 = 그 가정 위에 서 있는 시세가 흔들리는 물건 수.

사용:  .venv/Scripts/python.exe scripts/audit_market_assumptions.py
출력:  harness/audit/market_assumptions_<YYYYMMDD_HHMM>.md
"""
import json
import os
import sqlite3
import statistics
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC = ("차익 유력", "양호", "관심")


def ym_gap(ym: str, sale_date: str) -> int | None:
    """YYYYMM 과 YYYY-MM-DD 사이 개월 차."""
    try:
        y1, m1 = int(ym[:4]), int(ym[4:6])
        y2, m2 = int(sale_date[:4]), int(sale_date[5:7])
        return (y2 - y1) * 12 + (m2 - m1)
    except (ValueError, TypeError, IndexError):
        return None


def main():
    conn = sqlite3.connect(os.path.join(ROOT, "auction.db"))
    conn.row_factory = sqlite3.Row
    F = []          # (가정ID, 제목, 위반건수, 추천등급內, 비고)

    def add(aid, title, n, rec, note=""):
        F.append((aid, title, n, rec, note))

    # ---- A7: 부분 지분 매각인데 est 매겨짐(2026-07-24 죽전자이 실사고 회귀 감사) ----
    # 검출·게이트와 같은 판정 함수(is_partial_share)를 쓴다 — 세 층이 갈리면 사각이 재현된다.
    # 정상 상태 = 0건. 1건이라도 나오면 검출 우회 신규 표기가 생긴 것(표기 변형 수집 후 보강).
    import sys as _sys
    _sys.path.insert(0, ROOT)
    from src.courtauction_fields import is_partial_share  # noqa: E402
    rows = conn.execute(
        """SELECT s.grade, s.case_no, r.raw_json FROM scored_listings s
           JOIN raw_listings r
             ON r.court=s.court AND r.case_no=s.case_no AND r.item_no=s.item_no
           WHERE s.est_market_price IS NOT NULL"""
    ).fetchall()
    a7 = []
    for r in rows:
        d = json.loads(r["raw_json"])
        if is_partial_share(d.get("maejibun"), d.get("mulBigo")):
            a7.append(r)
    add("A7", "부분 지분 매각(maejibun/공유자문구)인데 온전가 시세가 매겨짐 — 0건이어야 정상",
        len(a7), sum(1 for r in a7 if r["grade"] in REC),
        f"est 보유 {len(rows)}건 전수 · 실사고=죽전자이 허구차익 3.69억")

    # ---- A4: 네이버 확정(same_complex_same_area)인데 감정가 대비 배율이 의심 구간 ----
    rows = conn.execute(
        """SELECT grade, est_market_price e, appraisal_price a FROM scored_listings
           WHERE market_scope='same_complex_same_area' AND est_market_price>0 AND appraisal_price>0"""
    ).fetchall()
    bad = [r for r in rows if not (0.5 <= r["e"] / r["a"] <= 1.8)]
    add("A4", "네이버 확정 시세가 감정가의 0.5~1.8배 밖(오매칭 의심, 하한가드 0.35는 통과)",
        len(bad), sum(1 for r in bad if r["grade"] in REC), f"확정경로 전체 {len(rows)}건 중")

    # ---- C1: 표본 최신 거래월이 매각기일 대비 6개월+ 과거(거래절벽인데 '최근 12개월'로 통과) ----
    rows = conn.execute(
        "SELECT grade, market_comps, sale_date FROM scored_listings WHERE market_comps IS NOT NULL AND sale_date<>''"
    ).fetchall()
    stale, stale_rec, have = 0, 0, 0
    for r in rows:
        try:
            comps = json.loads(r["market_comps"] or "[]")
        except json.JSONDecodeError:
            continue
        if not comps:
            continue
        have += 1
        latest = max(str(c[0]) for c in comps if c and c[0])
        gap = ym_gap(latest, r["sale_date"])
        if gap is not None and gap >= 6:
            stale += 1
            stale_rec += r["grade"] in REC
    add("C1", "표본 최신 거래월이 매각기일보다 6개월+ 과거(옛 시세가 '최근'으로 통과)",
        stale, stale_rec, f"comps 보유 {have}건 중")

    # ---- C2: 같은 (단지,평형)의 12개월 median vs 60개월 median 괴리 15%+ ----
    tr = conn.execute(
        """SELECT complex_no, area_no, trade_ymd, price FROM naver_real_trades
           WHERE deleted=0 AND price>0 ORDER BY complex_no, area_no"""
    ).fetchall()
    groups: dict[tuple, list] = {}
    for t in tr:
        groups.setdefault((t["complex_no"], t["area_no"]), []).append((str(t["trade_ymd"]), t["price"]))
    today = datetime.now()
    cut12 = f"{today.year - 1}{today.month:02d}"
    cut60 = f"{today.year - 5}{today.month:02d}"
    div, tested = 0, 0
    for _k, v in groups.items():
        p12 = [p for d, p in v if d[:6] >= cut12]
        p60 = [p for d, p in v if d[:6] >= cut60]
        if len(p12) >= 3 and len(p60) >= 3:
            tested += 1
            m12, m60 = statistics.median(p12), statistics.median(p60)
            if m12 and abs(m60 - m12) / m12 > 0.15:
                div += 1
    add("C2", "창 확장(60개월) median이 12개월 median과 15%+ 괴리(확장 시 표시 시세 왜곡)",
        div, -1, f"양쪽 3건+ 있는 (단지,평형) {tested}조 중")

    # ---- C4: 네이버 스냅샷이 180일+ 낡음 (KB/호가 폴백이 '현재 시세'로 서빙) ----
    n_stale = conn.execute(
        """SELECT COUNT(*) FROM naver_prices np JOIN scored_listings s
             ON s.court=np.court AND s.case_no=np.case_no AND s.item_no=np.item_no
           WHERE np.fetched_at < date('now','-180 day')"""
    ).fetchone()[0]
    n_stale_rec = conn.execute(
        """SELECT COUNT(*) FROM naver_prices np JOIN scored_listings s
             ON s.court=np.court AND s.case_no=np.case_no AND s.item_no=np.item_no
           WHERE np.fetched_at < date('now','-180 day') AND s.grade IN ('차익 유력','양호','관심')"""
    ).fetchone()[0]
    n_all = conn.execute("SELECT COUNT(*) FROM naver_prices").fetchone()[0]
    add("C4", "네이버(KB·호가) 스냅샷이 180일+ 낡았는데 신선도 검사 없이 사용", n_stale, n_stale_rec,
        f"naver_prices 전체 {n_all}건 중")

    # ---- D1: 만원 단위 가정 붕괴 신호(논리모순) ----
    d1a = conn.execute("SELECT COUNT(*) FROM naver_complexes WHERE min_price>0 AND max_price>0 AND min_price>max_price").fetchone()[0]
    d1b = conn.execute("SELECT COUNT(*) FROM naver_kb_history WHERE deal_low>0 AND deal_high>0 AND deal_low>deal_high").fetchone()[0]
    add("D1", "만원 단위 가정 붕괴 신호(min>max 또는 low>high 논리모순)", d1a + d1b, -1,
        f"complexes {d1a} + kb_history {d1b}")

    # ---- E2: 표본 3건(트림 미발동)인데 밴드가 넓음 → 하한이 이상치일 위험 ----
    e2 = conn.execute(
        """SELECT COUNT(*) FROM scored_listings
           WHERE market_sample_basis=3 AND market_band_low>0 AND market_band_high>0
             AND 1.0*market_band_high/market_band_low > 1.3"""
    ).fetchone()[0]
    e2r = conn.execute(
        """SELECT COUNT(*) FROM scored_listings
           WHERE market_sample_basis=3 AND market_band_low>0 AND market_band_high>0
             AND 1.0*market_band_high/market_band_low > 1.3 AND grade IN ('차익 유력','양호','관심')"""
    ).fetchone()[0]
    n3 = conn.execute("SELECT COUNT(*) FROM scored_listings WHERE market_sample_basis=3").fetchone()[0]
    add("E2", "표본 3건(트림 미발동)인데 밴드 1.3배+ → band_low가 이상치일 위험", e2, e2r,
        f"표본 3건 물건 {n3}건 중")

    # ---- E5: 표본 3~4건(통계력 취약)인데 추천등급 ----
    e5 = conn.execute(
        """SELECT COUNT(*) FROM scored_listings WHERE market_sample_basis BETWEEN 3 AND 4
             AND grade IN ('차익 유력','양호','관심')"""
    ).fetchone()[0]
    add("E5", "표본 3~4건의 median으로 추천등급 부여(통계력 취약)", e5, e5, "")

    # ---- E4/E3: 게이트가 무효화한 물건(표본 손실 은폐 여부) ----
    scopes = dict(conn.execute("SELECT market_scope, COUNT(*) FROM scored_listings GROUP BY market_scope").fetchall())
    add("E4/E3", "게이트로 시세가 무효화된 물건(appraisal_mismatch·band_too_wide 등)",
        sum(v for k, v in scopes.items() if k and k not in ("same_complex_same_area", "same_dong_fallback", "")), -1,
        " / ".join(f"{k}:{v}" for k, v in sorted(scopes.items(), key=lambda x: -x[1]) if k))

    # ---- F1: 폴백 품질 국토부가 KB보다 우선되는 모집단 ----
    f1 = conn.execute(
        "SELECT COUNT(*) FROM scored_listings WHERE market_scope='same_dong_fallback' AND est_market_price>0").fetchone()[0]
    f1r = conn.execute(
        """SELECT COUNT(*) FROM scored_listings WHERE market_scope='same_dong_fallback'
             AND est_market_price>0 AND grade IN ('차익 유력','양호','관심')""").fetchone()[0]
    add("F1", "타단지 뭉치(same_dong_fallback) 시세가 같은단지 KB보다 우선 채택되는 모집단", f1, f1r, "")

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = os.path.join(ROOT, "harness", "audit", f"market_assumptions_{stamp}.md")
    L = [f"# 시세 가정 반증 스윕 — {stamp}", "",
         "> 대상: docs/시세_가정_명세서.md 위험 '상' 가정. '위반 건수' = 그 가정이 깨진 실물 수.",
         "> (-1 = 물건 단위가 아니라 해당 없음)", "",
         "| 가정 | 내용 | 위반 | 추천등급 內 | 모집단 |", "|---|---|---|---|---|"]
    for aid, title, n, rec, note in F:
        L.append(f"| **{aid}** | {title} | **{n}** | {'-' if rec < 0 else rec} | {note} |")
    L += ["", "## 판정 규칙", "- '추천등급 內' 숫자가 사용자에게 실제로 노출 중인 위험량이다.",
          "- 확정 오류는 harness/AUDIT_CASEBOOK.md 기재 후 클래스 수정(파일럿 기간에는 기록만)."]
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\n리포트: {out}")
    conn.close()


if __name__ == "__main__":
    main()
