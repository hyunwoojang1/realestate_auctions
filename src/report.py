"""결과 출력 — 콘솔 랭킹표 / CSV / HTML (잉크블루+시그널그린 팔레트)."""
from __future__ import annotations

import csv
import html
import io
import json
from collections.abc import Iterable
from pathlib import Path

from .models import ScoredListing


def _won(v) -> str:
    if v is None:
        return "-"
    # (서빙감사 2026-07-12 #23) −50만~0원 구간이 '-0.00억'으로 렌더되던 음수 제로 방지 —
    # 억 단위 반올림이 0이면 부호를 떼어 '0.00억'으로 정규화.
    if round(v / 1e8, 2) == 0:
        return "0.00억"
    return f"{v/1e8:.2f}억"


def _pct(v) -> str:
    return "-" if v is None else f"{v*100:.0f}%"


_PYEONG = 3.3058   # 1평 = 3.3058㎡ (query._PYEONG 와 동일 상수)


def _pyeong(m2) -> str:
    """전용면적 ㎡ → '12.5평' 문자열(1평=3.3058㎡). 값이 없으면 빈 문자열."""
    if not m2 or m2 <= 0:
        return ""
    return f"{m2 / _PYEONG:.1f}평"


# 경고 등급만 콘솔/HTML에 표기(사용자 결정 #6 — 긍정 판정 표기는 폐지, 점수는 내부용).
_WARN_GRADES = ("권리미확인", "위험", "시세추정불가", "차익없음", "미지원유형")


def _warn_of(s: ScoredListing) -> str:
    return s.grade if s.grade in _WARN_GRADES else ""


def to_console(items: list[ScoredListing]) -> str:
    lines = []
    h = f"{'순위':<4}{'예상차익':>10}{'갭':>6} {'경고':<10}{'단지':<16}{'유형':<6}{'최저가':>9}{'추정시세':>10}{'신뢰':>6}"
    lines.append(h)
    lines.append("-" * 96)
    for i, s in enumerate(items, 1):
        lines.append(
            f"{i:<4}{_won(s.expected_profit):>10}{_pct(s.gap_rate):>6} {_warn_of(s):<10}"
            f"{s.apt_name[:14]:<16}{s.property_type:<6}"
            f"{_won(s.min_bid_price):>9}{_won(s.est_market_price):>10}{s.confidence:>6.2f}"
        )
    return "\n".join(lines)


def csv_text(items: Iterable[ScoredListing], badges: dict | None = None) -> str:
    """채점 결과를 CSV 문자열로. 웹 다운로드(/export.csv)·파일 저장(to_csv) 공통 소스.

    badges 가 주어지면 인수 부담 3열 병기(재검증 감사 idx4 HIGH — 인수 미반영 profit 만
    export 돼 실질 음수 물건이 양수로 나가던 문제): 인수부담(없음/있음/미확인)·인수금액(원)·
    유효차익(보수차익−인수금액; 금액 미상 burden 은 공란 = 판단 불가).
    빈 목록이면 빈 문자열. 줄바꿈은 CSV 표준 CRLF.
    """
    rows = []
    for s in items:
        row = s.to_row()
        if badges is not None:
            b = badges.get(f"{s.court}|{s.case_no}|{s.item_no}")
            p = s.profit_low if s.profit_low is not None else s.expected_profit
            if b is None:
                row["인수부담"], row["인수금액"], row["유효차익"] = "미확인", "", ""
            elif b.is_clean:
                row["인수부담"], row["인수금액"] = "없음(명세서 확인)", 0
                row["유효차익"] = p if p is not None else ""
            else:
                row["인수부담"] = "있음" + ("(금액 미상)" if b.amount_unknown else "")
                row["인수금액"] = b.assumed or ""
                row["유효차익"] = (p - b.assumed) if (p is not None and b.assumed) else ""
        rows.append(row)
    if not rows:
        return ""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def to_csv(items: Iterable[ScoredListing], path: str | Path) -> Path:
    path = Path(path)
    # newline="" + utf-8-sig: 엑셀 한글 인식. 내용은 csv_text 재사용(중복 구현 금지).
    path.write_text(csv_text(items), encoding="utf-8-sig", newline="")
    return path


def to_json(items: Iterable[ScoredListing], path: str | Path | None = None) -> str:
    """채점 결과를 JSON 문자열로(필요 시 파일 저장). 웹 API·CLI --json에서 재사용."""
    data = [s.to_row() for s in items]
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if path is not None:
        Path(path).write_text(text, encoding="utf-8")
    return text


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


def _band_gauge(s: ScoredListing, askings: list | None = None) -> str:
    """가격 밴드 게이지 — 이 제품의 시그니처 비주얼(근거 문서 11장 다이어그램의 가로판).

    검증 하한가~기준가 밴드(두 개의 선) 위에 취득원가 ▼ 마커를 찍는다:
      원가 < 하한가  → 원가→하한가 구간 초록(보수 차익 — 하한가로 팔아도 남는 폭)
      하한가 ≤ 원가 ≤ 기준가 → 하한가→원가 구간 호박색(보수 기준 차익 없음)
      원가 > 기준가  → 기준가→원가 구간 적색(기준가로도 손실)
    호가(askings, [{price, position}])는 검증 보조 점으로만 찍는다(T6 원칙).
    """
    low, high, cost = s.market_band_low, s.market_band_high, s.real_acquisition_cost
    vals = [low, high, cost] + [a["price"] for a in (askings or []) if a.get("price")]
    lo, hi = min(vals) * 0.96, max(vals) * 1.04
    span = hi - lo or 1

    def x(v: float) -> float:
        return max(0.0, min(100.0, (v - lo) / span * 100))

    parts = [f'<div class="bg-band" style="left:{x(low):.1f}%;width:{x(high) - x(low):.1f}%"></div>']
    if cost < low:
        parts.append(f'<div class="bg-profit" style="left:{x(cost):.1f}%;width:{x(low) - x(cost):.1f}%"></div>')
        verdict = f'<span class="gm-gv">보수 차익 {_won(low - cost)}</span>'
    elif cost <= high:
        parts.append(f'<div class="bg-inband" style="left:{x(low):.1f}%;width:{x(cost) - x(low):.1f}%"></div>')
        verdict = '<span class="bg-flat">취득원가 밴드 내 · 보수 차익 없음</span>'
    else:
        parts.append(f'<div class="bg-overcost" style="left:{x(high):.1f}%;width:{x(cost) - x(high):.1f}%"></div>')
        verdict = f'<span class="gm-loss">기준가 초과 {_won(cost - high)}</span>'
    parts.append(f'<div class="bg-cost" style="left:{x(cost):.1f}%" title="취득원가 {_won(cost)}"></div>')
    for a in askings or []:
        if a.get("price"):
            parts.append(f'<div class="bg-ask" style="left:{x(a["price"]):.1f}%" '
                         f'title="호가 {_won(a["price"])}"></div>')
    return (
        '<div class="gapmeter bandgauge"><div class="bg-track">' + "".join(parts) + "</div>"
        f'<div class="gm-lab"><span>▼ 원가 {_won(cost)}</span>{verdict}'
        f'<span class="bg-bandlab">밴드 {_won(low)}~{_won(high)}</span></div></div>'
    )


def gap_meter_html(s: ScoredListing, askings: list | None = None) -> str:
    """미터 디스패치 — 밴드가 있으면 시그니처 밴드 게이지, 레거시 행은 기존 갭미터."""
    if (s.market_band_low and s.market_band_high and s.real_acquisition_cost > 0
            and s.market_band_high >= s.market_band_low):
        return _band_gauge(s, askings)
    return _gap_meter(s)


# 웹 템플릿(src/web.py)에서 재사용하는 공개 별칭 — 금액 포맷 로직 공유
won = _won
pct = _pct
pyeong = _pyeong


def to_html(items: list[ScoredListing], path: str | Path) -> Path:
    path = Path(path)
    rows_html = []
    for i, s in enumerate(items, 1):
        # 크롤/외부 문자열이 정적 HTML에 그대로 들어가므로 이스케이프(데이터 품질 이슈로 태그가 섞여도 안전).
        e_name = html.escape(s.apt_name or s.property_type or "")
        e_addr = html.escape(s.address or "")
        e_warn = html.escape(_warn_of(s))
        e_type = html.escape(s.property_type or "")
        rows_html.append(f"""<tr>
  <td class="num rank">{i}</td>
  <td class="num profit">{_won(s.expected_profit)}<div class="ty">{_pct(s.gap_rate)}</div></td>
  <td><b>{e_warn}</b><div class="ty">{e_type} · {s.area_m2:.0f}㎡ · 유찰{s.fail_count}</div></td>
  <td class="name">{e_name}<div class="addr">{e_addr}</div></td>
  <td class="meter">{_gap_meter(s)}</td>
  <td class="num">{_won(s.min_bid_price)}</td>
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
<h1>차익 큐레이션 — 결과</h1>
<p class="sub">예상 차익 = 추정 실거래시세 − (최저입찰가 + 취득세). 금액 큰 순.
<b style="color:var(--g4)">초록 구간</b> = 최저가→시세 구간의 예상 차익 (명도·수리·인수 등 변동비 제외).</p>
<div class="scroll"><table>
<thead><tr><th>#</th><th>예상차익</th><th>경고</th><th>단지 / 소재지</th>
<th>갭미터 (최저가 → 시세)</th><th>최저가</th><th>신뢰</th></tr></thead>
<tbody>
{''.join(rows_html)}
</tbody></table></div>
<div class="legend">
  <span><i class="sw" style="background:var(--brand)"></i>최저입찰가</span>
  <span><i class="sw" style="background:linear-gradient(90deg,var(--g2),var(--g4))"></i>예상 차익(시세 갭)</span>
</div>
<p class="foot">⚠ 예상 차익은 취득 시점 표면차익(취득세만 반영, 변동비·양도세 제외).
차익·권리는 투자판단 보조이며 전문가 상담을 대체하지 않습니다.</p>
</body></html>"""
    path.write_text(doc, encoding="utf-8")
    return path
