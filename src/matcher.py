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
import re
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
SCOPE_SHARE_SALE = "share_sale"                          # 지분 매각 — 온전가 비교 무의미(추정 안 함)
SCOPE_APPRAISAL_MISMATCH = "appraisal_mismatch"          # 시세가 감정가와 괴리 — 비교군 불신(추정 무효)

# 감정가 교차검증 상한(2026-07-10 실사고 계열 방어): 비교군 시세가 감정가의 이 배수를 넘으면
# 비교군이 잘못됐다는 신호로 보고 시세를 말하지 않는다. 감정평가사가 2.5배 저평가할 확률은
# 사실상 0이며, 실제 사고(같은 동 '다른 단지' 신축과 오매칭 4.5~11배·나대지 오분류 3.5배)는
# 전부 이 선을 크게 넘었다. same_complex 도 안전망으로 동일 적용.
EST_VS_APPRAISAL_MAX = 2.5
# 폴백(같은 동 다른 단지) 전용 강화 가드(서빙감사 2026-07-12 #8·#10): 같은 단지 비교가 아니라
# 왜곡 폭이 크므로 상·하한을 좁게. 감정가의 1.5배 초과(신축 부풀림)·0.6배 미만(구축 끌어내림) 무효.
FALLBACK_EST_MAX = 1.5
FALLBACK_EST_MIN = 0.6


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
    # 상세 시간축 차트용 개별 실거래 점 [(deal_ym, price)] — 최근성 필터 전 전체 매칭에서
    # 날짜 있는 건만, 최신순 COMPS_CAP개. 밴드 산정과 무관(맥락 표시용) — 다년치까지 담아
    # 차트가 "언제부터 어떻게" 형성됐는지 보이게 한다. 성공 추정 시에만 채운다.
    comps: tuple[tuple[str, int], ...] = ()


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


# 단지명 한↔영 브랜드 표기 통일(재검증 감사 idx28 HIGH — '신천엘에이치' vs 'LH신천' 등
# 표기 차이로 같은 단지·같은 평형 실거래를 놓치고 fallback 참고치로 새던 문제).
# 정규화 후(공백 제거·소문자) 기준의 치환 테이블 — 영문을 한글 표기로 통일.
_BRAND_ALIASES = (
    ("lh", "엘에이치"),
    ("gs", "지에스"),
    ("sk", "에스케이"),
    ("kcc", "케이씨씨"),
    ("e편한세상", "이편한세상"),
    ("xi", "자이"),
)


def _norm(s: str) -> str:
    out = s.replace(" ", "").lower()
    for eng, kor in _BRAND_ALIASES:
        out = out.replace(eng, kor)
    return out


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


# 단지 식별자 — '3단지'·'8단지'·'2차'·'제3차' 등. 마을/지구명 부분일치가 여러 단지를
# 같은 단지로 오인하는 것을 잡는다(서빙감사 2026-07-12 #2: 갑오마을 3단지 vs 8단지).
_COMPLEX_ID_RE = re.compile(r"(\d+)\s*(?:단지|차)")


def _complex_ids(name: str) -> frozenset[str]:
    return frozenset(m.group(1) for m in _COMPLEX_ID_RE.finditer(name or ""))


def _multi_complex(comps: list["Trade"]) -> bool:
    """매칭 comps 가 서로 다른 단지 식별자(N단지/N차)를 2개 이상 포함하면 True — '같은 단지'
    라벨을 붙이면 안 된다. 마을·지구명 부분일치가 이웃 단지를 끌어온 신호."""
    # (감사 2026-07-15) 단지별 식별자 집합끼리 비교한다. 한 이름에 토큰이 둘인 경우
    # (예: '힐스테이트 2차 3단지' → {2,3})는 그 단지 고유 표기일 뿐 '여러 단지'가 아니다.
    # 서로 다른 식별자 집합이 2개 이상일 때만 이웃 단지 혼입으로 본다.
    sets = {ids for t in comps if (ids := _complex_ids(t.apt_name))}
    return len(sets) >= 2


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
    # 지역 스코프(감사 2026-07-10 CRITICAL): trades 는 전국 풀인데 아래 매칭이 '동 이름'만
    # 비교하면 타지역 동명(서울 신정동↔대구 신정동)의 실거래가 comps 로 혼입돼 시세가 왜곡된다.
    # 물건의 시군구(lawd_cd)가 있으면 같은 시군구 거래만 풀에 남긴다(레거시 빈 값은 통과).
    lawd = (listing.lawd_cd or "").strip()
    pool = [t for t in trades if _kind_ok(t, want)
            and (not lawd or not t.lawd_cd or t.lawd_cd == lawd)]
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
    if same_area and not _multi_complex(same_area):
        return same_area, SCOPE_SAME_COMPLEX_SAME_AREA
    near_area = _by_name(band)
    if near_area and not _multi_complex(near_area):
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
COMPS_CAP = 60               # 차트용 개별 실거래 점 최대 보관수(최신순) — 저장·렌더 비용 상한


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


def _pack_comps(matched: list[Trade]) -> tuple[tuple[str, int], ...]:
    """차트용 개별 실거래 점 — 날짜(deal_ym) 있는 건만 최신순 COMPS_CAP개 [(ym, price)].

    밴드 산정과 독립(최근성 필터 전 전체 매칭에서 뽑아 다년치 맥락까지 담는다).
    """
    dated = [(t.deal_ym, int(t.price)) for t in matched
             if _ym_to_int(t.deal_ym) is not None and t.price]
    dated.sort(key=lambda x: _ym_to_int(x[0]) or 0, reverse=True)
    return tuple(dated[:COMPS_CAP])


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
    _sr = listing.special_rights or []
    if "지분" in _sr or "대지권미등기" in _sr:
        # 온전 소유권이 아닌 매각(실사고 2026-07-10 + 감사 L1): 지분 매각은 소유권 일부,
        # 대지권미등기는 대지 지분 없는 전유부 — 둘 다 '온전 물건 실거래' 비교가 무의미해
        # 시세를 말하지 않는다(페널티 점수만으로는 허상 차익이 남는다).
        logger.debug("지분/대지권미등기 물건(%s) — 온전가 시세 추정 금지", listing.case_no)
        return MarketEstimate(None, 0, SCOPE_SHARE_SALE)
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
    # 감정가 교차검증 — 상한(전 scope 공통 2.5배) + 폴백 전용 강화 상·하한(서빙감사 #8·#10).
    # same_dong_fallback 은 '같은 동 다른 단지'라 상·하향 왜곡이 크다(신축이 est 부풀림 1.5~2.2배,
    # 구축·소형이 est 끌어내림 ≤0.6배). 감정평가사가 이 정도를 놓칠 확률은 사실상 0 → 무효화.
    if listing.appraisal_price > 0:
        ratio = est / listing.appraisal_price
        is_fallback = scope == SCOPE_SAME_DONG_FALLBACK
        hi = FALLBACK_EST_MAX if is_fallback else EST_VS_APPRAISAL_MAX
        if ratio > hi or (is_fallback and ratio < FALLBACK_EST_MIN):
            logger.warning("감정가 괴리(%s): est %s vs 감정 %s (배율 %.2f, scope=%s) — 시세 무효화",
                           listing.case_no, est, listing.appraisal_price, ratio, scope)
            return MarketEstimate(None, matched_count, SCOPE_APPRAISAL_MISMATCH, basis=basis)
    # (T4) 2선 밴드 — 하한가: 트림 후 최저 평단가(보수), 기준가: 트림 후 중앙값(=est, 호환 유지).
    band_low = int(round(min(ppm2_list) * listing.area_m2))
    return MarketEstimate(est, matched_count, scope, band_low=band_low, band_high=est,
                          basis=basis, comps=_pack_comps(matched))


def estimate_market_price(listing: AuctionListing, trades: list[Trade]) -> tuple[int | None, int]:
    """하위호환 래퍼 — (추정시세_원, 매칭건수). scope까지 필요하면 estimate_market."""
    m = estimate_market(listing, trades)
    return m.est, m.matched
