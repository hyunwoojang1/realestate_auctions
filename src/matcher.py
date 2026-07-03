"""경매 물건 ↔ 인근 실거래 매칭 → 추정 시세 산출.

전략(PoC):
 1) 같은 단지명(부분일치) + 전용면적 ±AREA_BAND 인 실거래로 평단가 중앙값 → 추정시세
 2) 1)이 부족하면 같은 법정동의 동일 면적대 실거래로 폴백(신뢰계수 자연 하락)
추정시세 = 중앙값(평단가) × 물건 전용면적.  매칭 0건이면 (None, 0).
"""
from __future__ import annotations

import logging
import statistics

from .models import AuctionListing, Trade

logger = logging.getLogger(__name__)

AREA_BAND = 0.10  # ±10% 전용면적 (기본값; 실제 사용값은 config.SAMPLE.area_band)


def _area_band() -> float:
    """현재 유효 면적밴드. config.SAMPLE로 튜닝 가능(기본=AREA_BAND)."""
    from . import config as _cfg  # noqa: PLC0415 — 런타임 monkeypatch(SAMPLE 교체) 반영
    return _cfg.SAMPLE.area_band if _cfg.SAMPLE else AREA_BAND

# 경매 물건유형 → 국토부 실거래 API 종류(apt/rh/officetel + 확장 sh/nrg/land).
# 핵심: 다세대를 아파트 실거래로, 상가를 주택 실거래로 평가하지 않도록 유형을 분리한다.
#  apt=아파트 · officetel=오피스텔 · rh=연립/다세대 · sh=단독/다가구 · nrg=상업/업무 · land=토지
_PROPERTY_KIND = {
    "아파트": "apt",
    "오피스텔": "officetel",
    "다세대": "rh",
    "연립다세대": "rh",
    "연립": "rh",
    "빌라": "rh",
    "연립주택": "rh",
    "다세대주택": "rh",
    # 단독/다가구 → sh
    "단독주택": "sh",
    "단독": "sh",
    "다가구": "sh",
    "다가구주택": "sh",
    "단독다가구": "sh",
    # 상업/업무 → nrg
    "상가": "nrg",
    "근린상가": "nrg",
    "근린생활시설": "nrg",
    "업무시설": "nrg",
    "상업용": "nrg",
    "점포": "nrg",
    "오피스": "nrg",
    # 토지 → land
    "토지": "land",
    "대지": "land",
    "임야": "land",
    "전": "land",
    "답": "land",
    "잡종지": "land",
    "농지": "land",
    "과수원": "land",
}


def _norm(s: str) -> str:
    return s.replace(" ", "").lower()


def expected_kind(property_type: str) -> str | None:
    """물건유형 문자열 → 실거래 종류. 매핑 없는 유형은 None(=유형 불명)."""
    return _PROPERTY_KIND.get((property_type or "").strip())


# v1 시세 추정 허용 유형 (데이터 신뢰도 개편 T2 — 문서 6장).
# 아파트: 같은 단지+같은 평형 비교군이 강해 초보자에게 상대적으로 안전.
# 오피스텔: 조건부 포함(같은 건물 실거래 충분할 때만 — 표본 게이트는 T5에서 강화).
# 빌라/다세대/단독/상가/토지: 개별성이 커서 '같은 법정동+면적' 중앙값은 위험 → 시세추정불가.
SUPPORTED_ESTIMATION_KINDS = frozenset({"apt", "officetel"})


def is_estimation_supported(property_type: str) -> bool:
    """이 물건유형의 시세 추정을 v1에서 지원하는가."""
    return expected_kind(property_type) in SUPPORTED_ESTIMATION_KINDS


def _kind_ok(trade: Trade, want: str | None) -> bool:
    """유형 일치 필터 — 정확히 같은 kind만 통과.

    (T2) 과거의 '유형 불명(None)·미태깅("") 통과' 하위호환을 제거했다:
    유형을 모르면 비교군을 만들면 안 된다(아파트/빌라/상가 혼입 → 중앙값 무의미 → 차익 왜곡).
    신뢰 중심 방향은 반대다 — "유형을 모르면 시세 추정 불가".
    """
    return want is not None and trade.kind == want


def _area_ok(a: float, b: float, band: float | None = None) -> bool:
    if a <= 0 or b <= 0:
        return False
    eff = _area_band() if band is None else band
    return abs(a - b) / b <= eff


def match_trades(listing: AuctionListing, trades: list[Trade]) -> list[Trade]:
    """유형(아파트/빌라/오피스텔) 분리 → 단지명+면적 우선, 부족하면 법정동+면적 폴백.

    면적 허용밴드는 config.SAMPLE.area_band(기본 ±10%)로 튜닝 가능 — 라이브 comps가
    빈약할 때 밴드를 넓히면 같은 단지의 인접 평형까지 표본에 포함된다.
    """
    band = _area_band()
    want = expected_kind(listing.property_type)
    pool = [t for t in trades if _kind_ok(t, want)]
    name = _norm(listing.apt_name)
    dong = _norm(listing.dong)
    # 단지명 부분일치 + 면적 + '같은 법정동' 제약. 동명이단지(다른 지역 같은 이름)를
    # 시세 comps로 끌어오는 것을 막는다. 거래에 dong이 없으면(하위호환) 동 제약은 통과시킨다.
    by_name = [
        t for t in pool
        if len(name) >= 2 and _norm(t.apt_name)
        and (name in _norm(t.apt_name) or _norm(t.apt_name) in name)
        and _area_ok(t.area_m2, listing.area_m2, band)
        and (not _norm(t.dong) or _norm(t.dong) == dong)
    ]
    if by_name:
        return by_name
    # 폴백: 같은 법정동 + 면적대 (유형 분리는 유지 — 다세대↔아파트 혼입 방지)
    by_dong = [
        t for t in pool
        if dong and _norm(t.dong) == dong and _area_ok(t.area_m2, listing.area_m2, band)
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
    if not is_estimation_supported(listing.property_type):
        # (T2) v1 미지원 유형(빌라/다세대/단독/상가/토지/유형불명) — 비교군 자체를 만들지 않는다.
        # '같은 법정동+비슷한 면적' 중앙값은 이들 유형에서 실제 시세와 크게 어긋날 수 있다(과신 유발).
        logger.debug("미지원 유형 물건(%s, %s) — 시세추정불가(v1 정책)",
                     listing.case_no, listing.property_type)
        return None, 0
    if listing.area_m2 <= 0:
        # 면적 파싱 실패(0/미상)면 comps 매칭이 무조건 비어 '시세추정불가'가 된다.
        # 진짜 comps 부재와 파싱실패를 구분할 수 있도록 로그를 남긴다(침묵실패 방지).
        logger.debug("면적 0/미상 물건(%s) — comps 매칭 불가 → 시세추정불가", listing.case_no)
        return None, 0
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
