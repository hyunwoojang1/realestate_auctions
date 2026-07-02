"""결과 출력 — 콘솔 랭킹표 / CSV / HTML (잉크블루+시그널그린 팔레트)."""
from __future__ import annotations

import csv
import html
import json
from collections.abc import Iterable
from pathlib import Path

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


def to_json(items: Iterable[ScoredListing], path: str | Path | None = None) -> str:
    """채점 결과를 JSON 문자열로(필요 시 파일 저장). 웹 API·CLI --json에서 재사용."""
    data = [s.to_row() for s in items]
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if path is not None:
        Path(path).write_text(text, encoding="utf-8")
    return text


_GRADE_COLOR = {
    "차익 유력": "var(--g4)", "양호": "var(--g3)", "관심": "var(--g2)",
    "주의": "var(--risk)", "위험": "var(--risk)",
    "권리미확인": "var(--muted)",   # 초록(안전) 아님 — 권리 미검증 중립 신호
    "차익없음": "var(--muted)", "시세추정불가": "var(--muted)",
}


def _score_badge(s: ScoredListing) -> str:
    color = _GRADE_COLOR.get(s.grade, "var(--muted)")
    val = "—" if s.arb_score is None else f"{s.arb_score:.0f}"
    return f'<span class="scorebadge" style="--c:{color}">{val}</span>'


def _gap_meter(s: ScoredListing) -> str:
    """최저가 vs 추정시세 비교 막대. 차익=초록, 시세 초과(손실)=적색, 시세없음=점선.

    감정가 눈금(gm-appr)은 제거 — 트랙 overflow에 잘려 보이지 않고 title-only라 접근 불가였음.
    감정가는 상세페이지 kv에서 확인.
    """
    base = max(s.min_bid_price, s.est_market_price or 0)
    if base <= 0:
        return '<div class="gm-na2">—</div>'
    min_pct = s.min_bid_price / base * 100
    if not s.est_market_price:
        # 시세추정불가 — 초록 갭 없이 최저가 막대만.
        return (
            '<div class="gapmeter"><div class="gm-track">'
            f'<div class="gm-min" style="width:{min_pct:.1f}%"></div></div>'
            f'<div class="gm-lab"><span>최저 {_won(s.min_bid_price)}</span>'
            '<span class="gm-na2">시세추정불가</span></div></div>'
        )
    mkt_pct = s.est_market_price / base * 100
    if s.min_bid_price >= s.est_market_price:
        # 최저가 ≥ 시세 = 차익 없음/손실. 초록 대신 적색 초과구간으로 정직하게.
        over_pct = max(0.0, min_pct - mkt_pct)
        return (
            '<div class="gapmeter"><div class="gm-track">'
            f'<div class="gm-min" style="width:{mkt_pct:.1f}%"></div>'
            f'<div class="gm-over" style="left:{mkt_pct:.1f}%;width:{over_pct:.1f}%"></div></div>'
            f'<div class="gm-lab"><span>시세 {_won(s.est_market_price)}</span>'
            f'<span class="gm-loss">최저 {_won(s.min_bid_price)} · 차익없음</span></div></div>'
        )
    gap_pct = max(0.0, mkt_pct - min_pct)
    return (
        '<div class="gapmeter"><div class="gm-track">'
        f'<div class="gm-min" style="width:{min_pct:.1f}%"></div>'
        f'<div class="gm-gap" style="left:{min_pct:.1f}%;width:{gap_pct:.1f}%"></div></div>'
        f'<div class="gm-lab"><span>최저 {_won(s.min_bid_price)}</span>'
        f'<span class="gm-gv">{_pct(s.gap_rate)} 갭 · 시세 {_won(s.est_market_price)}</span></div></div>'
    )


# 웹 템플릿(src/web.py)에서 재사용하는 공개 별칭 — 갭미터·스코어뱃지·금액 포맷 로직 공유
score_badge_html = _score_badge
gap_meter_html = _gap_meter
won = _won
pct = _pct


def to_html(items: list[ScoredListing], path: str | Path) -> Path:
    path = Path(path)
    rows_html = []
    for i, s in enumerate(items, 1):
        # 크롤/외부 문자열이 정적 HTML에 그대로 들어가므로 이스케이프(데이터 품질 이슈로 태그가 섞여도 안전).
        e_name = html.escape(s.apt_name or "")
        e_addr = html.escape(s.address or "")
        e_grade = html.escape(s.grade or "")
        e_type = html.escape(s.property_type or "")
        rows_html.append(f"""<tr>
  <td class="num rank">{i}</td>
  <td>{_score_badge(s)}</td>
  <td><b>{e_grade}</b><div class="ty">{e_type} · {s.area_m2:.0f}㎡ · 유찰{s.fail_count}</div></td>
  <td class="name">{e_name}<div class="addr">{e_addr}</div></td>
  <td class="meter">{_gap_meter(s)}</td>
  <td class="num profit">{_won(s.expected_profit)}</td>
  <td class="num conf">{s.confidence:.2f}</td>
</tr>""")
    doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>차익 큐레이션 — PoC 결과</title>
<style>
:root{{--ink:#1C2230;--brand:#2B4A9B;--paper:#FCFCFD;--muted:#6B7280;--border:#E3E5EB;
--g2:#5FBF93;--g3:#1F9D6B;--g4:#178a5a;--risk:#D1453B;--sunk:#F4F5F8;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--paper);color:var(--ink);
font-family:"Pretendard",-apple-system,"Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif;padding:clamp(20px,4vw,40px)}}
h1{{font-size:clamp(22px,3vw,28px);letter-spacing:-.02em;margin:0 0 4px}}
.sub{{color:var(--muted);font-size:14px;margin:0 0 24px;max-width:760px;line-height:1.5}}
.tag{{display:inline-block;background:#EAF7F0;color:var(--g4);font-size:12px;font-weight:700;
padding:4px 10px;border-radius:99px;margin-left:8px}}
.scroll{{overflow-x:auto;border:1px solid var(--border);border-radius:14px}}
table{{border-collapse:collapse;width:100%;min-width:720px;font-size:13.5px;background:#fff}}
th,td{{padding:13px 14px;border-bottom:1px solid var(--border);text-align:left;vertical-align:middle}}
tbody tr:last-child td{{border-bottom:none}}
thead th{{background:var(--sunk);font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);position:sticky;top:0}}
tbody tr:hover{{background:var(--sunk)}}
.num{{font-variant-numeric:tabular-nums;font-family:"SFMono-Regular",ui-monospace,Consolas,monospace}}
.rank{{color:var(--muted);width:28px}}
.name{{font-weight:700}}
.addr{{color:var(--muted);font-size:11.5px;font-weight:400}}
.ty{{color:var(--muted);font-size:11px;font-weight:400;margin-top:2px}}
.profit{{color:var(--g4);font-weight:800}}
.conf{{color:var(--muted)}}
.scorebadge{{display:inline-flex;align-items:center;justify-content:center;width:46px;height:46px;
border-radius:50%;background:var(--c);color:#fff;font-weight:800;font-size:16px;
font-variant-numeric:tabular-nums;font-family:"SFMono-Regular",ui-monospace,Consolas,monospace;
box-shadow:0 2px 6px color-mix(in oklab, var(--c) 45%, transparent)}}
.meter{{min-width:200px}}
.gapmeter{{min-width:190px}}
.gm-track{{position:relative;height:13px;background:var(--sunk);border-radius:99px;overflow:hidden}}
.gm-min{{position:absolute;left:0;top:0;height:100%;background:var(--brand)}}
.gm-gap{{position:absolute;top:0;height:100%;background:linear-gradient(90deg,var(--g2),var(--g4))}}
.gm-appr{{position:absolute;top:-2px;width:2px;height:17px;background:var(--ink);opacity:.45}}
.gm-lab{{display:flex;justify-content:space-between;gap:8px;font-size:10.5px;color:var(--muted);margin-top:4px}}
.gm-gv{{color:var(--g4);font-weight:700;font-variant-numeric:tabular-nums}}
.gm-na2{{color:var(--muted);font-weight:600}}
.legend{{display:flex;gap:16px;flex-wrap:wrap;margin:14px 2px 0;font-size:11.5px;color:var(--muted)}}
.legend span{{display:inline-flex;align-items:center;gap:6px}}
.sw{{width:13px;height:10px;border-radius:3px;display:inline-block}}
.foot{{margin-top:18px;color:var(--muted);font-size:12px;line-height:1.6}}
</style></head><body>
<h1>차익 큐레이션 — PoC 결과<span class="tag">샘플 데이터</span></h1>
<p class="sub">최저입찰가 vs 추정 실거래시세 차익 스코어(0~100) 높은 순. 갭미터의 <b style="color:var(--g4)">초록 구간</b>이
최저가→시세 사이 예상 차익이며, 부대비용(취득세·명도·수리·인수금액)을 차감한 순차익을 표시합니다.</p>
<div class="scroll"><table>
<thead><tr><th>#</th><th>스코어</th><th>등급</th><th>단지 / 소재지</th>
<th>갭미터 (최저가 → 시세)</th><th>예상차익</th><th>신뢰</th></tr></thead>
<tbody>
{''.join(rows_html)}
</tbody></table></div>
<div class="legend">
  <span><i class="sw" style="background:var(--brand)"></i>최저입찰가(실질취득)</span>
  <span><i class="sw" style="background:linear-gradient(90deg,var(--g2),var(--g4))"></i>예상 차익(시세 갭)</span>
  <span><i class="sw" style="background:var(--ink);opacity:.45;width:2px"></i>감정가</span>
</div>
<p class="foot">⚠ PoC 샘플 데이터 기반. 차익·권리는 투자판단 보조이며 전문가 상담을 대체하지 않습니다.
라이브 시세는 국토부 실거래가 API 키 연동(F10) 후 적용됩니다.</p>
</body></html>"""
    path.write_text(doc, encoding="utf-8")
    return path
