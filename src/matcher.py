"""경매 물건 ↔ 인근 실거래 매칭 → 추정 시세 산출.

전략(PoC):
 1) 같은 단지명(부분일치) + 전용면적 ±AREA_BAND 인 실거래로 평단가 중앙값 → 추정시세
 2) 1)이 부족하면 같은 법정동의 동일 면적대 실거래로 폴백(신뢰계수 자연 하락)
추정시세 = 중앙값(평단가) × 물건 전용면적.  매칭 0건이면 (None, 0).
"""
from __future__ import annotations

import statistics

from .models import AuctionListing, Trade

AREA_BAND = 0.10  # ±10% 전용면적


def _norm(s: str) -> str:
    return s.replace(" ", "").lower()


def _area_ok(a: float, b: float, band: float = AREA_BAND) -> bool:
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / b <= band


def match_trades(listing: AuctionListing, trades: list[Trade]) -> list[Trade]:
    """단지명+면적 우선, 부족하면 법정동+면적 폴백."""
    name = _norm(listing.apt_name)
    by_name = [
        t for t in trades
        if name and _norm(t.apt_name) and (name in _norm(t.apt_name) or _norm(t.apt_name) in name)
        and _area_ok(t.area_m2, listing.area_m2)
    ]
    if by_name:
        return by_name
    # 폴백: 같은 법정동 + 면적대
    dong = _norm(listing.dong)
    by_dong = [
        t for t in trades
        if dong and _norm(t.dong) == dong and _area_ok(t.area_m2, listing.area_m2)
    ]
    return by_dong


RECENCY_WINDOW_MONTHS = 12   # 최근 N개월 거래만 사용(오래된 거래는 시세 신선도↓)
TRIM_MIN_SAMPLES = 4         # 표본 4건 이상이면 상·하단 이상치 1건씩 트림


def _ym_to_int(ym: str) -> int | None:
    if not ym or len(ym) < 6:
        return None
    try:
        return int(ym[:4]) * 12 + int(ym[4:6])
    except ValueError:
        return None


def filter_recent(trades: list[Trade], window: int = RECENCY_WINDOW_MONTHS) -> list[Trade]:
    """가장 최근 거래월 기준 window개월 이내만 남긴다(날짜 없는 거래는 보존)."""
    months = [(_ym_to_int(t.deal_ym), t) for t in trades]
    valid = [m for m, _ in months if m is not None]
    if not valid:
        return trades
    cutoff = max(valid) - window
    return [t for m, t in months if m is None or m >= cutoff]


def trim_outliers(values: list[float]) -> list[float]:
    """표본이 충분하면(4건↑) 정렬 후 최소·최대 1건씩 제거해 이상치 영향 축소."""
    if len(values) < TRIM_MIN_SAMPLES:
        return values
    return sorted(values)[1:-1]


def estimate_market_price(listing: AuctionListing, trades: list[Trade]) -> tuple[int | None, int]:
    """(추정시세_원, 매칭건수) 반환. 매칭 0건이면 (None, 0).

    매칭 → 최근성 필터 → 평단가 이상치 트림 → 중앙값 × 전용면적.
    신뢰계수 산정용 매칭건수는 트림 전 원 매칭 수를 유지한다(품질 보정이 신뢰를 부풀리지 않게).
    """
    matched = match_trades(listing, trades)
    if not matched:
        return None, 0
    matched_count = len(matched)
    recent = filter_recent(matched)
    ppm2_list = [t.price_per_m2() for t in recent if t.area_m2 > 0]
    if not ppm2_list:
        return None, 0
    ppm2_list = trim_outliers(ppm2_list)
    median_ppm2 = statistics.median(ppm2_list)
    est = int(round(median_ppm2 * listing.area_m2))
    return est, matched_count
