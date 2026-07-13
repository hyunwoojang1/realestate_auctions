"""상세 페이지 '가격-시간 차트' 데이터 빌더 (UX 개편 — 스냅샷 막대 → 시간축).

이전 pricemap(가로 한 축 스냅샷)의 한계: 실거래·호가·밴드가 '언제'인지 알 수 없어
현재 시세인지 과거 시세인지 구분 불가였다. 시간축 차트는 모든 값에 시점을 부여한다:
  - 개별 실거래 체결을 날짜(월)별로 점으로 — 밴드가 언제 어떻게 형성됐는지 보인다.
  - 호가는 관측일에, 유찰 저감은 기일 이력을 따라 계단으로.
  - 시세밴드는 롤링(시간대별) 음영대로.

정확도 원칙: **시세밴드/차익은 최근 창(recency_months)으로만** 계산한다(matcher와 동일 정책).
다년치 comps 는 '맥락(hist)'으로만 회색 표시하고 밴드 산정엔 절대 섞지 않는다(2020~ 폭등·조정
사이클이 현재 시세 추정을 오염시키지 않게 — 문서 시세 신선도 규칙).

순수함수(I/O 없음)로 JSON-직렬화 dict 를 만든다. SVG 렌더·호버는 클라이언트 JS 가 맡는다.
"""
from __future__ import annotations

import statistics

from .models import ScoredListing

# 롤링 밴드 계산 창(개월) — 각 시점의 밴드를 직전 N개월 comps 로 산정.
ROLLING_WINDOW_MONTHS = 12
# 롤링 밴드 한 점을 만드는 최소 표본 — 미만이면 그 시점은 건너뛴다(2건짜리 밴드 금지).
ROLLING_MIN_SAMPLES = 3
# 이상치 트림 하한 표본 — 이상 시 정렬 후 양끝 1건씩 제거(matcher.trim_outliers 정책).
TRIM_MIN_SAMPLES = 4


def _ym_int(ym: str) -> int | None:
    """'YYYYMM' 또는 'YYYY-MM' → 연*12+월 정수. 파싱 실패 시 None."""
    s = (ym or "").replace("-", "")
    if len(s) < 6:
        return None
    try:
        return int(s[:4]) * 12 + int(s[4:6])
    except ValueError:
        return None


def _ym_dash(ym: str) -> str:
    """'YYYYMM'/'YYYY-MM' → 'YYYY-MM' 표준화."""
    s = (ym or "").replace("-", "")
    return f"{s[:4]}-{s[4:6]}" if len(s) >= 6 else (ym or "")


def _trim(values: list[float]) -> list[float]:
    if len(values) < TRIM_MIN_SAMPLES:
        return values
    return sorted(values)[1:-1]


def _rolling_band(comps: list[tuple[int, int]], window: int) -> list[dict]:
    """시간대별 롤링 밴드 — 각 comp 월을 앵커로 직전 window개월 표본에서 (lo, hi) 산정.

    comps: [(ym_int, price)] 정렬 불필요. lo=트림 후 최저가, hi=트림 후 중앙값.
    표본 부족(ROLLING_MIN_SAMPLES 미만)한 앵커는 건너뛴다.
    """
    if not comps:
        return []
    anchors = sorted({m for m, _ in comps})
    env: list[dict] = []
    for a in anchors:
        win = [p for m, p in comps if a - window < m <= a]
        if len(win) < ROLLING_MIN_SAMPLES:
            continue
        trimmed = _trim([float(p) for p in win])
        lo = int(round(min(trimmed)))
        hi = int(round(statistics.median(trimmed)))
        yr, mo = divmod(a - 1, 12)
        env.append({"ym": f"{yr:04d}-{mo + 1:02d}", "lo": lo, "hi": hi})
    return env


def _build_step(s: ScoredListing, schedule: list[dict] | None) -> list[dict]:
    """유찰 저감 계단 — 감정가에서 시작해 각 기일 최저가를 거쳐 현재 최저가로 끝난다.

    기일 이력(schedule: [{ymd, price, ...}])이 있으면 날짜별 저감을 따라가고,
    없으면 감정가·현재 최저가 2점만(중간 유찰일 미상). 마지막 점 label='현재'.
    """
    appr = s.appraisal_price or 0
    minbid = s.min_bid_price or 0
    sale = s.sale_date or ""
    dated: list[tuple[str, int]] = []
    for ev in schedule or []:
        d = str(ev.get("ymd") or "")
        p = ev.get("price")
        if d and isinstance(p, (int, float)) and not isinstance(p, bool) and p > 0:
            dated.append((d, int(p)))
    dated.sort(key=lambda x: x[0])
    # 감정가를 첫 점으로(첫 기일보다 이르거나 같은 값이면 중복 회피).
    step: list[dict] = []
    if appr > 0:
        first_date = dated[0][0] if dated else sale
        step.append({"date": first_date, "price": appr, "label": "감정가"})
    labels = ["1차", "2차", "3차", "4차", "5차", "6차"]
    li = 0
    for d, p in dated:
        if step and step[-1]["price"] == p and step[-1]["date"] == d:
            continue
        step.append({"date": d, "price": p, "label": labels[li] if li < len(labels) else "회차"})
        li += 1
    # 마지막을 현재 최저가로 확정(중복이면 라벨만 교체).
    if minbid > 0:
        if step and step[-1]["price"] == minbid:
            step[-1] = {**step[-1], "label": "현재", "date": step[-1]["date"] or sale}
        else:
            step.append({"date": sale, "price": minbid, "label": "현재"})
    return step


def build_timechart(
    s: ScoredListing,
    comps: list[tuple[str, int]],
    schedule: list[dict] | None = None,
    asks: list[dict] | None = None,
    recency_months: int = ROLLING_WINDOW_MONTHS,
    assumed: int = 0,
) -> dict:
    """상세 시간축 차트 렌더 데이터(JSON-직렬화 dict).

    comps: [(deal_ym, price)] — 같은 단지·평형 매칭 실거래(가격은 실제 체결가).
    schedule: 기일 이력(유찰 저감용). asks: 호가 [{price, observed_at, label, position}].

    반환:
      trades/hist : 최근 창 안/밖 실거래 점 [{ym, price}] (창 밖은 맥락·밴드 제외)
      asks        : 호가 점 [{date, price, label, position}]
      band_env    : 시간대별 롤링 밴드 [{ym, lo, hi}]
      band_now    : 현재 검증 밴드 {lo, hi} | None
      step        : 유찰 저감 계단 [{date, price, label}]
      levels      : {appraisal, cost, minbid}
      gain        : {cost, band_lo, amount} | None (밴드하한 > 취득원가일 때만)
      cheap_pct   : 시세 대비 저가율(%) | None
      sale_date, recency_months
    """
    parsed = [(_ym_int(ym), int(p), _ym_dash(ym))
              for ym, p in (comps or [])
              if _ym_int(ym) is not None and p]
    trades: list[dict] = []
    hist: list[dict] = []
    if parsed:
        newest = max(m for m, _, _ in parsed)
        cutoff = newest - recency_months
        for m, p, dash in sorted(parsed, key=lambda x: x[0]):
            (trades if m > cutoff else hist).append({"ym": dash, "price": p})

    band_env = _rolling_band([(m, p) for m, p, _ in parsed], recency_months)

    band_now = None
    if s.market_band_low and s.market_band_high and s.market_band_high > 0:
        band_now = {"lo": int(s.market_band_low), "hi": int(s.market_band_high)}

    cost = int(s.real_acquisition_cost or 0)
    # (서빙감사 2026-07-12 #19) 인수금액(명세서 인수 권리)을 취득원가에 더한 '유효 취득원가'
    # 기준으로 차익을 계산 — 히어로가 인수 반영 음수인데 차트만 초록 '차익 +N억'으로 모순되던
    # 것을 막는다. 유효원가가 밴드하한 이상이면 차익 구간을 그리지 않는다(assumed_neg 로 고지).
    eff_cost = cost + max(0, int(assumed or 0))
    gain = None
    if band_now and eff_cost > 0 and band_now["lo"] > eff_cost:
        gain = {"cost": eff_cost, "band_lo": band_now["lo"], "amount": band_now["lo"] - eff_cost}
    assumed_neg = bool(assumed) and bool(band_now) and band_now["lo"] <= eff_cost

    cheap_pct = None
    if band_now and s.min_bid_price and s.min_bid_price > 0:
        mid = (band_now["lo"] + band_now["hi"]) / 2
        if mid > 0:
            cheap_pct = round((1 - s.min_bid_price / mid) * 100)

    asks_out = [
        {"date": str(a.get("observed_at") or ""), "price": int(a["price"]),
         "label": str(a.get("label") or ""), "position": str(a.get("position") or "")}
        for a in (asks or []) if a.get("price")
    ]

    return {
        "trades": trades,
        "hist": hist,
        "asks": asks_out,
        "band_env": band_env,
        "band_now": band_now,
        "step": _build_step(s, schedule),
        "levels": {
            "appraisal": int(s.appraisal_price or 0),
            "cost": cost,
            "minbid": int(s.min_bid_price or 0),
        },
        "gain": gain,
        "assumed": max(0, int(assumed or 0)),
        "assumed_neg": assumed_neg,
        "cheap_pct": cheap_pct,
        "sale_date": s.sale_date or "",
        "recency_months": recency_months,
    }
