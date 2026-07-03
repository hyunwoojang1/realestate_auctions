"""경매 물건 ↔ 인근 실거래 매칭 → 추정 시세 산출.

전략(T3 — 비교군 scope 계층화):
 1) 같은 단지명 + 같은 평형(±SAME_AREA_BAND)  → scope=same_complex_same_area (v1 추천 인정)
 2) 같은 단지명 + 인접 평형(±area_band)       → scope=same_complex_near_area (참고치)
 3) 같은 법정동 + 같은 유형 + 면적대           → scope=same_dong_fallback (참고치 — 추천 금지)
추정시세 = 중앙값(평단가) × 물건 전용면적. 어떤 집합에서 나온 값인지(scope)를 함께 반환한다 —
비교군이 틀리면 통계 방식이 아무리 좋아도 결과는 틀리므로(문서 13장), scope가 신뢰 등급의 근거가 된다.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass

from .models import AuctionListing, Trade

logger = logging.getLogger(__name__)

AREA_BAND = 0.10  # ±10% 전용면적 (기본값; 실제 사용값은 config.SAMPLE.area_band)
SAME_AREA_BAND = 0.03  # '같은 평형' 판정(±3%) — 84㎡ 타입 내 소수점 편차 허용, 59↔84 혼입 차단

# ---- 비교군 scope (T3) — 시세 추정치가 "어떤 집합"에서 나왔는지 ----
SCOPE_SAME_COMPLEX_SAME_AREA = "same_complex_same_area"  # 같은 단지·같은 평형 (v1 추천 인정)
SCOPE_SAME_COMPLEX_NEAR_AREA = "same_complex_near_area"  # 같은 단지·인접 평형 (참고치)
SCOPE_SAME_DONG_FALLBACK = "same_dong_fallback"          # 같은 법정동 폴백 (참고치 — 추천 금지)
SCOPE_UNSUPPORTED = "unsupported"                        # v1 미지원 유형 (추정 안 함)
SCOPE_NO_COMPS = "no_comps"                              # 지원 유형이나 표본 없음


@dataclass(frozen=True)
class MarketEstimate:
    """시세 추정 결과 — 값(est)만이 아니라 근거 집합(scope)과 가격 밴드까지가 결과다.

    (T4) 단일 추정가는 '정답 가격'처럼 보여 과신을 유발한다(문서 7장). 두 선으로 말한다:
      band_high = 검증 기준가 — 이상치 트림 후 중앙값(일반적인 시장 가격). est와 동일(호환).
      band_low  = 검증 하한가 — 이상치 트림 후 최저 평단가(보수적으로 볼 수 있는 낮은 가격).
    표본수(matched)는 트림 전 원 매칭 수 — market_sample_count로 그대로 저장된다.
    """
    est: int | None
    matched: int
    scope: str
    band_low: int | None = None
    band_high: int | None = None
    # (T5) 밴드 실기반 표본수 — 최근성 필터 + 이상치 트림 후 실제 밴드 계산에 쓰인 건수.
    # matched(트림 전 원 매칭수)와 다르다: 게이트는 이 값을 본다(부풀려진 표본수로 통과 방지).
    basis: int = 0


def _area_band() -> float:
    """현재 유효 면적밴드. config.SAMPLE로 튜닝 가능(기본=AREA_BAND)."""
    from . import config as _cfg  # noqa: PLC0415 — 런타임 monkeypatch(SAMPLE 교체) 반영
    return _cfg.SAMPLE.area_band if _cfg.SAMPLE else AREA_BAND


def _band_min_basis() -> int:
    """밴드 생성 최소 표본수(T5). 미만이면 '시세근거 부족' — 추정 자체를 안 한다."""
    from . import config as _cfg  # noqa: PLC0415
    return _cfg.SAMPLE.band_min_basis if _cfg.SAMPLE else 3


def band_confident_basis() -> int:
    """추천 인정 최소 표본수(T5). 미만이면 낮은 신뢰 — 추천 제외 + 경고."""
    from . import config as _cfg  # noqa: PLC0415
    return _cfg.SAMPLE.band_confident_basis if _cfg.SAMPLE else 5

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


def match_trades_scoped(listing: AuctionListing, trades: list[Trade]) -> tuple[list[Trade], str]:
    """계층 매칭 — (비교군, scope). 좁고 강한 집합을 우선하고, 어느 층에서 나왔는지 밝힌다.

    1) 같은 단지 + 같은 평형(±SAME_AREA_BAND) → same_complex_same_area
    2) 같은 단지 + 인접 평형(±area_band)      → same_complex_near_area
    3) 같은 법정동 + 같은 유형 + 면적대        → same_dong_fallback
    면적 허용밴드는 config.SAMPLE.area_band(기본 ±10%)로 튜닝 가능.
    """
    band = _area_band()
    want = expected_kind(listing.property_type)
    pool = [t for t in trades if _kind_ok(t, want)]
    name = _norm(listing.apt_name)
    dong = _norm(listing.dong)

    def _by_name(eff_band: float) -> list[Trade]:
        # 단지명 부분일치 + 면적 + '같은 법정동' 제약. 동명이단지(다른 지역 같은 이름)를
        # 시세 comps로 끌어오는 것을 막는다. 거래에 dong이 없으면(하위호환) 동 제약은 통과시킨다.
        return [
            t for t in pool
            if len(name) >= 2 and _norm(t.apt_name)
            and (name in _norm(t.apt_name) or _norm(t.apt_name) in name)
            and _area_ok(t.area_m2, listing.area_m2, eff_band)
            and (not _norm(t.dong) or _norm(t.dong) == dong)
        ]

    # 운영자가 area_band를 3% 미만으로 좁혔다면 그 값을 '같은 평형' 기준으로 존중.
    same_area = _by_name(min(SAME_AREA_BAND, band))
    if same_area:
        return same_area, SCOPE_SAME_COMPLEX_SAME_AREA
    near_area = _by_name(band)
    if near_area:
        return near_area, SCOPE_SAME_COMPLEX_NEAR_AREA
    # 폴백: 같은 법정동 + 면적대 (유형 분리는 유지 — 다세대↔아파트 혼입 방지)
    by_dong = [
        t for t in pool
        if dong and _norm(t.dong) == dong and _area_ok(t.area_m2, listing.area_m2, band)
    ]
    if by_dong:
        return by_dong, SCOPE_SAME_DONG_FALLBACK
    return [], SCOPE_NO_COMPS


def match_trades(listing: AuctionListing, trades: list[Trade]) -> list[Trade]:
    """하위호환 래퍼 — 비교군 리스트만. scope가 필요하면 match_trades_scoped."""
    matched, _ = match_trades_scoped(listing, trades)
    return matched


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


def estimate_market(listing: AuctionListing, trades: list[Trade]) -> MarketEstimate:
    """시세 추정 — 값·표본수·비교군 scope를 함께 반환 (T3).

    매칭 → 최근성 필터 → 평단가 이상치 트림 → 중앙값 × 전용면적.
    신뢰계수 산정용 매칭건수는 트림 전 원 매칭 수를 유지한다(품질 보정이 신뢰를 부풀리지 않게).
    """
    if not is_estimation_supported(listing.property_type):
        # (T2) v1 미지원 유형(빌라/다세대/단독/상가/토지/유형불명) — 비교군 자체를 만들지 않는다.
        # '같은 법정동+비슷한 면적' 중앙값은 이들 유형에서 실제 시세와 크게 어긋날 수 있다(과신 유발).
        logger.debug("미지원 유형 물건(%s, %s) — 시세추정불가(v1 정책)",
                     listing.case_no, listing.property_type)
        return MarketEstimate(None, 0, SCOPE_UNSUPPORTED)
    if listing.area_m2 <= 0:
        # 면적 파싱 실패(0/미상)면 comps 매칭이 무조건 비어 '시세추정불가'가 된다.
        # 진짜 comps 부재와 파싱실패를 구분할 수 있도록 로그를 남긴다(침묵실패 방지).
        logger.debug("면적 0/미상 물건(%s) — comps 매칭 불가 → 시세추정불가", listing.case_no)
        return MarketEstimate(None, 0, SCOPE_NO_COMPS)
    matched, scope = match_trades_scoped(listing, trades)
    if not matched:
        return MarketEstimate(None, 0, SCOPE_NO_COMPS)
    matched_count = len(matched)
    recent = filter_recent(matched)
    ppm2_list = [t.price_per_m2() for t in recent if t.area_m2 > 0]
    if not ppm2_list:
        return MarketEstimate(None, 0, SCOPE_NO_COMPS)
    # 이상치 방어(문서화된 규칙): 표본 4건 이상이면 평단가 최소·최대 1건씩 제거(trim_outliers).
    # 가족거래 저가·신고가성 고가 같은 특수 거래 1건이 밴드 양끝을 왜곡하는 것을 막는다.
    ppm2_list = trim_outliers(ppm2_list)
    basis = len(ppm2_list)   # (T5) 밴드 실기반 표본수 — 최근성+트림 후 실제 사용 건수
    if basis < _band_min_basis():
        # (T5) 표본 게이트: 실기반 표본이 기준(기본 3건) 미만이면 밴드 생성 금지 —
        # 2건짜리 '중앙값'을 시세로 말하지 않는다. 과감하게 '시세근거 부족'(문서 10장).
        logger.debug("표본 부족 물건(%s) — 실기반 %d건 < %d → 시세근거 부족",
                     listing.case_no, basis, _band_min_basis())
        return MarketEstimate(None, matched_count, scope, basis=basis)
    median_ppm2 = statistics.median(ppm2_list)
    est = int(round(median_ppm2 * listing.area_m2))
    # (T4) 2선 밴드 — 하한가: 트림 후 최저 평단가(보수), 기준가: 트림 후 중앙값(=est, 호환 유지).
    band_low = int(round(min(ppm2_list) * listing.area_m2))
    return MarketEstimate(est, matched_count, scope, band_low=band_low, band_high=est,
                          basis=basis)


def estimate_market_price(listing: AuctionListing, trades: list[Trade]) -> tuple[int | None, int]:
    """하위호환 래퍼 — (추정시세_원, 매칭건수). scope까지 필요하면 estimate_market."""
    m = estimate_market(listing, trades)
    return m.est, m.matched
