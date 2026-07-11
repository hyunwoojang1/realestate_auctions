"""상세 페이지 '가격 지도' 좌표 계산 (UX 개편 — 목록 단순화 · 상세 시각화 강화).

목록의 압축 게이지가 "초록 칸이 뭔지 모르겠다"는 피드백으로 제거되면서,
상세 페이지가 가격 구조를 한눈에 보여주는 유일한 시각화가 된다.
하나의 가로 축 위에 감정가 → (유찰 저감) → 최저입찰가 → 취득원가 → 실거래 밴드 → 호가를
전부 이름·금액 라벨과 함께 배치할 좌표(%)를 여기서 계산한다.

템플릿에 수식을 두지 않기 위해 서버에서 dict 로 만들어 넘긴다(테스트 가능).
"""
from __future__ import annotations

from .models import ScoredListing

# 축 양끝 여백 — 마커·라벨이 경계에 붙어 잘리는 것을 방지.
_PAD_RATIO = 0.07


def _pct(price: float, lo: float, hi: float) -> float:
    return round((price - lo) / (hi - lo) * 100, 1)


def _lab(pct: float) -> float:
    """라벨 anchor 클램프 — 축 양끝에서 텍스트가 화면 밖으로 잘리는 것을 방지."""
    return min(90.0, max(10.0, pct))


def _edge(pct: float) -> str:
    """축 끝 라벨 정렬 힌트 — 'l'/'r'이면 템플릿이 중앙정렬 대신 안쪽으로 붙인다(잘림 방지)."""
    if pct < 12:
        return "l"
    if pct > 88:
        return "r"
    return ""


def build(s: ScoredListing, ask_points: list | None = None) -> dict | None:
    """가격 지도 렌더 데이터. None = 그릴 시세 정보가 없음(템플릿이 폴백 문구 표시).

    반환 dict:
      appraisal / minbid / cost : {pct, price} (+minbid 는 fail_count·cut_pct)
      band : {lo_pct, w_pct, lo, hi} | None   — 실거래 검증 밴드(하한~기준)
      est  : {pct, price} | None              — 밴드 없을 때 단일 추정 시세선
      gain : {lo_pct, w_pct, amount} | None   — 취득원가→비교 시세, 양수일 때만(예상 차익 구간)
      asks : [{pct, price}]                   — 현재 호가(체결가 아님)
    """
    cost = s.real_acquisition_cost
    if not cost or cost <= 0:
        return None
    band = None
    est = None
    if s.market_band_low and s.market_band_high and s.market_band_high > 0:
        band = (s.market_band_low, s.market_band_high)
    elif s.est_market_price and s.est_market_price > 0:
        est = s.est_market_price
    if band is None and est is None:
        return None  # 시세 정보가 전혀 없으면 축을 그릴 수 없다

    asks = [p.price for p in (ask_points or []) if getattr(p, "price", 0) and p.price > 0]
    prices = [cost] + ([s.min_bid_price] if s.min_bid_price and s.min_bid_price > 0 else [])
    if s.appraisal_price and s.appraisal_price > 0:
        prices.append(s.appraisal_price)
    prices += list(band) if band else [est]
    prices += asks

    lo_raw, hi_raw = min(prices), max(prices)
    span = (hi_raw - lo_raw) or hi_raw * 0.1 or 1
    axis_lo = lo_raw - span * _PAD_RATIO
    axis_hi = hi_raw + span * _PAD_RATIO

    c_pct = _pct(cost, axis_lo, axis_hi)
    out: dict = {
        "cost": {"pct": c_pct, "lab": _lab(c_pct), "edge": _edge(c_pct), "price": cost},
        "band": None, "est": None, "gain": None, "appraisal": None, "minbid": None,
        "asks": [{"pct": _pct(a, axis_lo, axis_hi), "price": a} for a in asks],
    }
    if s.min_bid_price and s.min_bid_price > 0:
        cut = (1 - s.min_bid_price / s.appraisal_price) if s.appraisal_price else None
        m_pct = _pct(s.min_bid_price, axis_lo, axis_hi)
        out["minbid"] = {"pct": m_pct, "lab": _lab(m_pct), "edge": _edge(m_pct),
                         "price": s.min_bid_price, "cut": cut}
    if s.appraisal_price and s.appraisal_price > 0:
        a_pct = _pct(s.appraisal_price, axis_lo, axis_hi)
        out["appraisal"] = {"pct": a_pct, "lab": _lab(a_pct), "edge": _edge(a_pct),
                            "price": s.appraisal_price}
    if band:
        lo_p, hi_p = _pct(band[0], axis_lo, axis_hi), _pct(band[1], axis_lo, axis_hi)
        mid = (lo_p + hi_p) / 2
        out["band"] = {"lo_pct": lo_p, "w_pct": round(hi_p - lo_p, 1),
                       "lab": _lab(mid), "edge": _edge(mid), "lo": band[0], "hi": band[1]}
        compare = band[0]           # 보수 기준: 차익 구간은 검증 하한까지
    else:
        e_pct = _pct(est, axis_lo, axis_hi)
        out["est"] = {"pct": e_pct, "lab": _lab(e_pct), "edge": _edge(e_pct), "price": est}
        compare = est
    if compare > cost:
        c_p = out["cost"]["pct"]
        out["gain"] = {"lo_pct": c_p,
                       "w_pct": round(_pct(compare, axis_lo, axis_hi) - c_p, 1),
                       "amount": compare - cost}
    return out
