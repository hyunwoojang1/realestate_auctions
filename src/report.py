"""결과 출력 — 콘솔 랭킹표 / CSV / HTML (잉크블루+시그널그린 팔레트)."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from .models import ScoredListing


def _won(v) -> str:
    if v is None:
        return "-"
    return f"{v/1e8:.2f}억"


def _pct(v) -> str:
    return "-" if v is None else f"{v*100:.0f}%"


def to_console(items: list[ScoredListing]) -> str:
    lines = []
    h = f"{'순위':<4}{'스코어':>6} {'등급':<10}{'단지':<16}{'유형':<6}{'최저가':>9}{'추정시세':>10}{'예상차익':>10}{'갭':>6}{'신뢰':>6}"
    lines.append(h)
    lines.append("-" * len(h.encode("ascii", "ignore")) if False else "-" * 96)
    for i, s in enumerate(items, 1):
        score = "-" if s.arb_score is None else f"{s.arb_score:.0f}"
        lines.append(
            f"{i:<4}{score:>6} {s.grade:<10}{s.apt_name[:14]:<16}{s.property_type:<6}"
            f"{_won(s.min_bid_price):>9}{_won(s.est_market_price):>10}"
            f"{_won(s.expected_profit):>10}{_pct(s.gap_rate):>6}{s.confidence:>6.2f}"
        )
    return "\n".join(lines)


def to_csv(items: Iterable[ScoredListing], path: str | Path) -> Path:
    path = Path(path)
    rows = [s.to_row() for s in items]
    cols = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return path


_GRADE_COLOR = {
    "확실한 차익": "var(--g4)", "양호": "var(--g3)", "관심": "var(--g2)",
    "주의": "var(--risk)", "차익없음": "var(--muted)", "시세추정불가": "var(--muted)",
}


def to_html(items: list[ScoredListing], path: str | Path) -> Path:
    path = Path(path)
    rows_html = []
    for i, s in enumerate(items, 1):
        score = "—" if s.arb_score is None else f"{s.arb_score:.0f}"
        color = _GRADE_COLOR.get(s.grade, "var(--muted)")
        rows_html.append(f"""<tr>
  <td class="num">{i}</td>
  <td><span class="score" style="background:{color}">{score}</span></td>
  <td><b>{s.grade}</b></td>
  <td>{s.apt_name}<div class="addr">{s.address}</div></td>
  <td>{s.property_type}</td>
  <td class="num">{s.area_m2:.1f}㎡</td>
  <td class="num">{_won(s.min_bid_price)}</td>
  <td class="num">{_won(s.est_market_price)}</td>
  <td class="num profit">{_won(s.expected_profit)}</td>
  <td class="num">{_pct(s.gap_rate)}</td>
  <td class="num">유찰 {s.fail_count}</td>
  <td class="num">{s.confidence:.2f}</td>
</tr>""")
    html = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>차익 큐레이션 — PoC 결과</title>
<style>
:root{{--ink:#1C2230;--brand:#2B4A9B;--paper:#FCFCFD;--muted:#6B7280;--border:#E3E5EB;
--g2:#5FBF93;--g3:#1F9D6B;--g4:#178a5a;--risk:#D1453B;--sunk:#F4F5F8;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--paper);color:var(--ink);
font-family:"Pretendard",-apple-system,"Malgun Gothic",system-ui,sans-serif;padding:32px}}
h1{{font-size:26px;letter-spacing:-.02em;margin:0 0 4px}}
.sub{{color:var(--muted);font-size:14px;margin:0 0 24px}}
.tag{{display:inline-block;background:#EAF7F0;color:var(--g4);font-size:12px;font-weight:700;
padding:4px 10px;border-radius:99px;margin-left:8px}}
table{{border-collapse:collapse;width:100%;font-size:13.5px;background:#fff;
border:1px solid var(--border);border-radius:12px;overflow:hidden}}
th,td{{padding:11px 12px;border-bottom:1px solid var(--border);text-align:left;vertical-align:middle}}
thead th{{background:var(--sunk);font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}}
tbody tr:hover{{background:var(--sunk)}}
.num{{font-variant-numeric:tabular-nums;font-family:"SFMono-Regular",Consolas,monospace}}
.score{{display:inline-block;min-width:34px;text-align:center;color:#fff;font-weight:800;
padding:3px 8px;border-radius:7px;font-family:"SFMono-Regular",Consolas,monospace}}
.addr{{color:var(--muted);font-size:11.5px}}
.profit{{color:var(--g4);font-weight:700}}
.foot{{margin-top:18px;color:var(--muted);font-size:12px}}
</style></head><body>
<h1>차익 큐레이션 — PoC 결과<span class="tag">샘플 데이터</span></h1>
<p class="sub">최저입찰가 vs 추정 실거래시세 차익 스코어 (0~100) · 높은 순 · 부대비용(취득세·명도·수리·인수금액) 차감 후 순차익</p>
<table>
<thead><tr><th>#</th><th>스코어</th><th>등급</th><th>단지 / 소재지</th><th>유형</th><th>전용</th>
<th>최저가</th><th>추정시세</th><th>예상차익</th><th>갭</th><th>유찰</th><th>신뢰</th></tr></thead>
<tbody>
{''.join(rows_html)}
</tbody></table>
<p class="foot">⚠ PoC 샘플 데이터 기반. 차익·권리는 투자판단 보조이며 전문가 상담을 대체하지 않습니다.
라이브 시세는 국토부 실거래가 API 키 연동(F10) 후 적용됩니다.</p>
</body></html>"""
    path.write_text(html, encoding="utf-8")
    return path
