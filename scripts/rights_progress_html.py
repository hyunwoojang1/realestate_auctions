"""권리 크롤 진행률 대시보드 HTML 생성 — 5분 점검 루프가 매번 호출해 최신화.

auction.db를 읽어 (크롤가능 물건 대비 권리 크롤 완료 %)를 계산하고 harness/rights_progress.html
을 덮어쓴다. 이전 스냅샷(rights_progress_state.json)과 비교해 시간당 처리율·ETA도 산출.
브라우저는 <meta refresh>로 60초마다 자동 리로드하므로, 파일만 갱신되면 화면도 갱신된다.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from src import store  # noqa: E402

KST = timezone(timedelta(hours=9))
DB = os.path.join(ROOT, "auction.db")
OUT = os.path.join(ROOT, "harness", "rights_progress.html")
STATE = os.path.join(ROOT, "harness", "rights_progress_state.json")
def _counts(conn, now: datetime):
    """단일 테이블 카운트만 사용(빠름·크롤과 DB경합 최소). 전체 물건 대비 권리 크롤 %.

    boCd 크롤가능 판정은 json_extract라 비싸고 크롤 중 경합으로 타임아웃 → denominator를
    scored 전체로 단순화. 일부 uncrawlable(boCd無)이 섞여 100%엔 살짝 못 미칠 수 있음(정상).
    """
    done = conn.execute("SELECT count(*) FROM listing_rights").fetchone()[0]
    scored = conn.execute("SELECT count(*) FROM scored_listings").fetchone()[0]
    total = scored
    done = min(done, total)
    return total, done, done, scored


def _rate(done: int, now: datetime):
    """이전 스냅샷 대비 시간당 처리율. 첫 실행이면 None."""
    prev = None
    if os.path.exists(STATE):
        try:
            prev = json.load(open(STATE, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = None
    json.dump({"done": done, "ts": now.isoformat()}, open(STATE, "w", encoding="utf-8"))
    if not prev:
        return None
    try:
        dt = (now - datetime.fromisoformat(prev["ts"])).total_seconds()
        dd = done - int(prev["done"])
        if dt <= 0 or dd < 0:
            return None
        return dd / dt * 3600  # per hour
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    now = datetime.now(KST)
    conn = store.connect(DB)
    total, done, rights_rows, scored = _counts(conn, now)
    conn.close()
    pct = (done / total * 100) if total else 0.0
    remaining = total - done
    rate = _rate(done, now)
    eta = None
    if rate and rate > 0 and remaining > 0:
        hrs = remaining / rate
        eta = f"약 {hrs:.1f}시간 (~{(now + timedelta(hours=hrs)).strftime('%m/%d %H:%M')})"

    rate_s = f"{rate:.0f} 건/시간" if rate is not None else "계산 중…"
    eta_s = eta or "계산 중…"
    bar_pct = f"{pct:.1f}"

    html = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>권리 크롤 진행률</title>
<style>
:root{{--bg:#0f1720;--card:#18222e;--ink:#e6edf3;--sub:#8899a6;--accent:#3fb950;--track:#22303c;--warn:#e3b341}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:'Pretendard',-apple-system,'Segoe UI',sans-serif;background:var(--bg);color:var(--ink);
 display:flex;min-height:100vh;align-items:center;justify-content:center;padding:24px}}
.wrap{{width:100%;max-width:640px}}
h1{{font-size:15px;font-weight:600;color:var(--sub);letter-spacing:.02em;margin:0 0 20px;text-transform:uppercase}}
.big{{font-size:76px;font-weight:800;line-height:1;letter-spacing:-.02em;margin:0}}
.big span{{font-size:32px;color:var(--sub);font-weight:600}}
.track{{height:16px;border-radius:99px;background:var(--track);overflow:hidden;margin:22px 0 8px}}
.fill{{height:100%;border-radius:99px;background:linear-gradient(90deg,#2ea043,#3fb950);width:{bar_pct}%;
 transition:width .6s ease}}
.subline{{color:var(--sub);font-size:14px;margin:0 0 28px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.card{{background:var(--card);border-radius:14px;padding:16px 18px}}
.card .k{{color:var(--sub);font-size:12px;margin:0 0 6px}}
.card .v{{font-size:24px;font-weight:700;margin:0}}
.foot{{color:var(--sub);font-size:12px;margin-top:24px;display:flex;justify-content:space-between;align-items:center}}
.dot{{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--accent);margin-right:6px;
 animation:pulse 1.6s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
</style></head><body><div class="wrap">
<h1>부동산 경매 · 권리 크롤 진행률</h1>
<p class="big">{bar_pct}<span>%</span></p>
<div class="track"><div class="fill"></div></div>
<p class="subline">크롤가능 물건 <b style="color:var(--ink)">{total:,}</b>건 중 <b style="color:var(--accent)">{done:,}</b>건 권리 확인 완료</p>
<div class="grid">
  <div class="card"><p class="k">완료</p><p class="v" style="color:var(--accent)">{done:,}</p></div>
  <div class="card"><p class="k">남은 물건</p><p class="v">{remaining:,}</p></div>
  <div class="card"><p class="k">처리 속도</p><p class="v">{rate_s}</p></div>
  <div class="card"><p class="k">예상 완료(ETA)</p><p class="v" style="font-size:16px">{eta_s}</p></div>
</div>
<div class="foot">
  <span><span class="dot"></span>진행 중 · listing_rights {rights_rows:,}행 · scored {scored:,}</span>
  <span>갱신 {now.strftime('%Y-%m-%d %H:%M:%S')} KST · 60초 자동새로고침</span>
</div>
</div></body></html>"""

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[progress] {pct:.1f}% ({done:,}/{total:,}) rate={rate_s} → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
