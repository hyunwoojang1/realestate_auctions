"""사진 이전 진행률 대시보드 — DB를 폴링해 자동 새로고침 HTML을 덮어쓴다.

migrate_photos_to_storage 와 독립 실행(읽기 전용). 브라우저로 migrate_status.html 을 열면
meta-refresh 로 10초마다 스스로 갱신 → 에이전트가 멈춰도 진행 상황이 계속 보인다.
완료(대기 0)되면 마지막 프레임을 쓰고 종료.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import store  # noqa: E402

OUT = ROOT / "migrate_status.html"


def _bar(pct: int, n: int = 30) -> str:
    fill = pct * n // 100
    return "█" * fill + "░" * (n - fill)


def render(done: int, total: int, rate: float, started: bool) -> str:
    pend = total - done
    pct = 100 * done // total if total else 100
    eta = int(pend / rate / 60) if rate > 0 and pend else 0
    finished = pend == 0
    refresh = "" if finished else '<meta http-equiv="refresh" content="10">'
    status = "✅ 이전 완료" if finished else ("⏳ 이전 진행 중" if started else "대기")
    color = "#22c55e" if finished else "#3b82f6"
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">{refresh}
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>사진 이전 진행률</title>
<style>
  body{{margin:0;background:#0b1120;color:#e2e8f0;font:16px/1.5 -apple-system,'Segoe UI',sans-serif;
       display:flex;align-items:center;justify-content:center;min-height:100vh}}
  .card{{background:#111827;border:1px solid #1f2937;border-radius:20px;padding:44px 52px;
        box-shadow:0 20px 60px rgba(0,0,0,.4);width:min(560px,92vw)}}
  h1{{margin:0 0 4px;font-size:20px;letter-spacing:-.02em}}
  .sub{{color:#64748b;font-size:13px;margin-bottom:28px}}
  .status{{font-size:15px;font-weight:600;color:{color};margin-bottom:18px}}
  .bartrack{{background:#1e293b;border-radius:999px;height:22px;overflow:hidden}}
  .barfill{{background:linear-gradient(90deg,#3b82f6,{color});height:100%;width:{pct}%;
           border-radius:999px;transition:width .6s ease}}
  .pct{{font-size:44px;font-weight:800;letter-spacing:-.03em;margin:22px 0 2px;
       font-variant-numeric:tabular-nums}}
  .nums{{color:#94a3b8;font-size:14px}}
  .grid{{display:flex;gap:14px;margin-top:26px}}
  .kpi{{flex:1;background:#0b1120;border:1px solid #1f2937;border-radius:12px;padding:14px 16px}}
  .kpi .k{{color:#64748b;font-size:12px}}
  .kpi .v{{font-size:19px;font-weight:700;margin-top:3px;font-variant-numeric:tabular-nums}}
  .mono{{font-family:ui-monospace,'Cascadia Code',monospace;letter-spacing:1px;color:#3b82f6;
        font-size:15px;margin-top:6px}}
</style></head><body><div class="card">
  <h1>사진 Storage 이전</h1>
  <div class="sub">base64 → Supabase Storage · auction-arbitrage</div>
  <div class="status">{status}</div>
  <div class="bartrack"><div class="barfill"></div></div>
  <div class="mono">{_bar(pct)}</div>
  <div class="pct">{pct}<span style="font-size:22px">%</span></div>
  <div class="nums">{done:,} / {total:,} 장 이전</div>
  <div class="grid">
    <div class="kpi"><div class="k">남은 사진</div><div class="v">{pend:,}</div></div>
    <div class="kpi"><div class="k">속도</div><div class="v">{rate:.1f}/s</div></div>
    <div class="kpi"><div class="k">예상 남은시간</div><div class="v">{'완료' if finished else f'~{eta}분'}</div></div>
  </div>
</div></body></html>"""


def _counts():
    # 매 폴링 새 커넥션 — WAL 읽기 스냅샷이 카운트를 고정시키는 문제 회피(오탐 idle 방지).
    c = store.connect(str(ROOT / "auction.db"))
    try:
        total = c.execute("SELECT COUNT(*) FROM listing_photos").fetchone()[0]
        done = c.execute("SELECT COUNT(*) FROM listing_photos WHERE photo_url!=''").fetchone()[0]
        return done, total
    finally:
        c.close()


def main() -> int:
    _, total = _counts()
    prev_done, prev_t = None, None
    rate = 0.0
    idle = 0
    while True:
        done, total = _counts()
        now = time.monotonic()
        if prev_done is not None and now > prev_t:
            inst = (done - prev_done) / (now - prev_t)
            rate = inst if rate == 0 else rate * 0.6 + inst * 0.4  # 평활
            # 멈춤 감지는 '직전 관측값(prev_done)'과 비교 — prev_done 갱신 전에 검사해야 한다.
            idle = idle + 1 if done == prev_done else 0
        prev_done, prev_t = done, now
        OUT.write_text(render(done, total, rate, started=done > 0), encoding="utf-8")
        if total - done <= 0:
            break
        if idle > 40:  # ~7분 무변화면 중단(멈춤 감지)
            break
        time.sleep(10)
    print(f"[dashboard] 종료 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
