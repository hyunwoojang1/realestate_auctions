"""주장 검증 — 보고하기 전에 **모집단 전체**의 사실을 뽑는다.

## 왜 이 스크립트가 있는가

2026-07-28 사용자 지적: "왜 항상 거짓 보고를 하나."
실제 사례를 되짚으면 원인이 하나로 모인다 — **표본 1건을 확인하고 전체를 단언했다.**

  · "시뮬레이터 정상화됐습니다"  → 시세가 붙은 1건만 열어보고 단언. 실제로는 281건 중
    246건이 여전히 '거액 손해 확정'으로 보이고 있었다(다음 감사에서 CRITICAL로 발견).
  · "지분 물건은 차익이 부풀려집니다" → 각주로만 적고 실제 순위를 안 봤다. 차익 상위
    8건 중 4건이 그 물건이었다.
  · "네이버 링크 65건" → 로컬 수치를 프로덕션 수치인 양 보고. 실제 프로덕션은 57건이었다.
  · "폰 화면에 탭이 보입니다" → 뷰포트 844px 기준으로 판정. 주소창을 뺀 실효 높이를
    안 따져 결론이 뒤집혔다.

공통 실패는 **가설을 지지하는 표본을 찾고 멈춘 것**이다. 반증을 시도하지 않았다.
그래서 이 스크립트는 "되나요?"에 예/아니오로 답하지 않고, **모집단 대비 비율**과
**로컬 vs 프로덕션 대조**를 표로 뱉는다. 보고문은 이 표를 인용해서 쓴다.

## 사용

    PYTHONUTF8=1 .venv/Scripts/python.exe scripts/verify_claims.py            # 로컬만
    PYTHONUTF8=1 .venv/Scripts/python.exe scripts/verify_claims.py --prod     # 프로덕션 대조 포함

종료코드: 0 = 조사 완료(결함 유무와 무관). 이 스크립트는 게이트가 아니라 **사실 수집기**다.
게이트로 만들면 "통과했으니 괜찮다"는 또 다른 단언을 낳는다 — 판단은 사람이 한다.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROD = "https://auction-arbitrage-nine.vercel.app"


def _row(label: str, n: int, total: int, note: str = "") -> str:
    pct = f"{n / total * 100:5.1f}%" if total else "    —"
    return f"  {label:<38} {n:>6,} / {total:>6,}  {pct}  {note}"


def _q(conn, sql: str) -> int:
    return conn.execute(sql).fetchone()[0]


def local_facts(db: str) -> None:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    print("\n■ 낙찰 결과(sold_listings) — 모집단 대비 실제 비율")
    tot = _q(conn, "SELECT COUNT(*) FROM sold_listings")
    print(_row("전체", tot, tot))
    print(_row("시세 보유(차익 계산 가능)",
               _q(conn, "SELECT COUNT(*) FROM sold_listings WHERE market_band_low IS NOT NULL"),
               tot))
    print(_row("시세 없음 → 상세 '판단 불가'로 표시돼야",
               _q(conn, "SELECT COUNT(*) FROM sold_listings WHERE market_band_low IS NULL"),
               tot, "← '시뮬 정상화' 주장 시 반드시 인용"))
    print(_row("실낙찰가 보유",
               _q(conn, "SELECT COUNT(*) FROM sold_listings WHERE sold_price IS NOT NULL"), tot))
    print(_row("점수 보유(점수 정렬이 의미 있으려면)",
               _q(conn, "SELECT COUNT(*) FROM sold_listings WHERE arb_score IS NOT NULL"), tot))

    print("\n■ 시세 출처(정책 준수) — 신뢰 출처 아닌데 시세가 남아 있으면 정책 우회")
    for r in conn.execute(
            "SELECT COALESCE(NULLIF(market_scope,''),'(빈값)') sc, COUNT(*) n, "
            "COUNT(market_band_low) band FROM sold_listings GROUP BY 1 ORDER BY n DESC"):
        flag = ""
        if r["sc"] not in ("same_complex_same_area", "same_complex_near_area") and r["band"]:
            flag = "⚠ 정책 우회 — 신뢰 출처가 아닌데 시세 보유"
        print(f"  {r['sc']:<38} {r['n']:>6,}건  시세보유 {r['band']:>4}  {flag}")

    print("\n■ 차익 순위 오염 검사 — 온전한 물건이 아닌 거래가 상위를 먹는가")
    from src import query
    rows = [dict(r) for r in conn.execute("SELECT * FROM sold_listings")]
    top = query.sort_sold(rows, "profit")[:10]
    shown = [r for r in top if query.sold_gap(r) is not None]
    odd = [r for r in shown
           if (r.get("appraisal_price") or 0) and (r["sold_price"] or 0)
           and r["sold_price"] / r["appraisal_price"] < 0.30]
    print(_row("차익 상위 10 중 차익이 실제로 표시되는 건", len(shown), 10))
    print(_row("그중 감정가율 30% 미만(특수물건 의심)", len(odd), max(len(shown), 1),
               "⚠ 0이어야 정상" if odd else "✓"))

    print("\n■ 활성 목록과의 충돌 — 같은 물건이 양쪽에 동시 노출되는가")
    dup = _q(conn, "SELECT COUNT(*) FROM sold_listings s JOIN scored_listings sc "
                   "ON sc.court=s.court AND sc.case_no=s.case_no AND sc.item_no=s.item_no")
    print(_row("낙찰 기록인데 활성 목록에도 있음", dup, tot, "⚠ 0이어야 정상" if dup else "✓"))
    conn.close()


def cloud_facts(db: str) -> None:
    """로컬과 클라우드를 대조 — '로컬에서 됐다'를 '서비스된다'로 바꿔 말하지 않기 위해."""
    from src import store_rest
    if not store_rest.enabled():
        print("\n■ 클라우드 대조 — SUPABASE 미설정, 건너뜀")
        return
    conn = sqlite3.connect(db)
    print("\n■ 로컬 ↔ 클라우드 대조 (어긋나면 프로덕션은 다른 것을 보여준다)")
    pairs = [
        ("낙찰 기록", _q(conn, "SELECT COUNT(*) FROM sold_listings"),
         len(store_rest.fetch_sold(100000))),
        ("낙찰 중 시세 보유",
         _q(conn, "SELECT COUNT(*) FROM sold_listings WHERE market_band_low IS NOT NULL"),
         sum(1 for r in store_rest.fetch_sold(100000) if r.get("market_band_low"))),
    ]
    for label, loc, cloud in pairs:
        mark = "✓" if loc == cloud else f"⚠ 불일치({loc - cloud:+,})"
        print(f"  {label:<38} 로컬 {loc:>6,}  클라우드 {cloud:>6,}  {mark}")
    conn.close()


def prod_facts() -> None:
    """프로덕션 HTML 을 실제로 받아 마커를 센다 — 배포됐다는 말 대신 증거."""
    print("\n■ 프로덕션 실측 (배포 주장의 증거)")
    checks = [
        ("/health 응답", "/health", None),
        ("/sold '비교 불가(특수)' 카드", "/sold?sort=profit", '">비교 불가 <span'),
        ("/sold 네이버 시세 링크", "/sold", "new.land.naver.com"),
        ("홈 낙찰결과 메뉴", "/", 'href="/sold"'),
    ]
    for label, path, marker in checks:
        try:
            with urllib.request.urlopen(PROD + path, timeout=90) as r:
                body = r.read().decode("utf-8", "replace")
                got = f"{body.count(marker)}개" if marker else f"HTTP {r.status}"
        except Exception as e:  # noqa: BLE001 — 조사 실패도 사실로 보고한다
            got = f"실패: {type(e).__name__}"
        print(f"  {label:<38} {got}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="보고 전 모집단 사실 수집")
    ap.add_argument("--db", default=str(ROOT / "auction.db"))
    ap.add_argument("--prod", action="store_true", help="프로덕션 대조 포함(네트워크 사용)")
    args = ap.parse_args(argv)

    print("=" * 78)
    print("주장 검증 — 이 표를 인용해서 보고할 것. 표본 1건으로 전체를 단언하지 말 것.")
    print("=" * 78)
    local_facts(args.db)
    if args.prod:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                import os
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        cloud_facts(args.db)
        prod_facts()
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
