"""채점 결과 필터·정렬 (순수 함수 — 테스트 용이, CLI·웹 양쪽에서 재사용).

기본 정렬 = 보수 차익(profit_low, 없으면 기준 차익 폴백 — T7). 갭률(gap) 토글 지원.
점수(score)는 UI에서 제거됐지만 내부/백테스트 호환을 위해 정렬 키로는 남겨둔다(사용자 결정 #7).
"""
from __future__ import annotations

import datetime as _dt

from .models import ScoredListing
from .region import matches_region, sido_of

SORT_KEYS = ("profit", "profit_asc", "gap", "score", "score_asc", "recent", "old")
DEFAULT_SORT = "profit"

# 검색 우선 홈(2026-07): 기본 화면은 '평가 가능한' 물건만 — 시세 추정치가 있는 것.
# 미지원유형(빌라·상가·토지 ~70%)·시세추정불가(~20%)는 값이 전부 '—'라 기본에서 숨기고
# '전체 탐색'(all=1)에서만 노출한다(노이즈에 신호가 묻히지 않게).
HIGH_PROFIT_THRESHOLD = 200_000_000   # '고차익' 빠른진입 칩 기준(2억)
SOON_DAYS = 7                         # '매각기일 임박' 빠른진입 칩 기준(일)


_PYEONG = 3.3058   # 1평 = 3.3058㎡

# 홈 검색 면적 브래킷(전용면적 평대) → (min_㎡, max_㎡). max=None 은 상한 없음.
AREA_BRACKETS = {
    "~20": (None, 20 * _PYEONG),        # 20평 미만
    "20": (20 * _PYEONG, 30 * _PYEONG),  # 20평대
    "30": (30 * _PYEONG, 40 * _PYEONG),  # 30평대
    "40": (40 * _PYEONG, 50 * _PYEONG),  # 40평대
    "50plus": (50 * _PYEONG, None),      # 50평 이상
}


def area_bounds(bracket: str | None) -> tuple[float | None, float | None]:
    """면적 브래킷 키 → (min_㎡, max_㎡). 알 수 없는 키·빈값은 (None, None)."""
    return AREA_BRACKETS.get(bracket or "", (None, None))


def is_evaluable(s: ScoredListing) -> bool:
    """이 도구가 시세를 추정한(=평가 가능한) 물건인가. 검색 우선 홈의 기본 노출 기준."""
    return s.est_market_price is not None


def days_until(sale_date: str, today: _dt.date | None = None) -> int | None:
    """매각기일까지 남은 일수. 파싱 실패 시 None(임박 필터에서 제외)."""
    today = today or _dt.date.today()
    try:
        d = _dt.date.fromisoformat((sale_date or "")[:10])
    except ValueError:
        return None
    return (d - today).days


def is_soon(s: ScoredListing, today: _dt.date | None = None, within: int = SOON_DAYS) -> bool:
    """매각기일이 오늘부터 within일 이내(지난 기일 제외)."""
    d = days_until(s.sale_date, today)
    return d is not None and 0 <= d <= within


# (2026-07-24) 당일 입찰 마감 컷오프 — 데이터엔 개시시각(maeHh1)만 있고 실제 마감은 법원별로
# 개시 후 1~1.8h(11:00~11:50)로 다르다. 개시 + BID_CLOSE_BUFFER_MIN(기본 120분=정오 근방,
# 최장 마감+개찰을 덮음)을 넘으면 '오늘 매각 종료'로 본다. 유효 물건을 실수로 숨기지 않도록
# 버퍼는 넉넉히 잡는다(조정은 이 상수 한 줄).
BID_CLOSE_BUFFER_MIN = 120
_KST = _dt.timezone(_dt.timedelta(hours=9))


def now_kst() -> _dt.datetime:
    """KST 벽시계(naive). sale_date·sale_time(maeHh1)은 KST 기준 원문이라, 서버가 UTC로 떠도
    (Docker/Vercel 기본) 컷오프가 9시간 밀리지 않게 KST로 고정한 뒤 tz 정보를 떼어 naive 비교한다.
    프로젝트 다른 모듈(store·crawl_rights)의 KST 하드코딩과 같은 규약."""
    return _dt.datetime.now(_KST).replace(tzinfo=None)


def bidding_closed(s: ScoredListing, now: _dt.datetime | None = None) -> bool:
    """매각 '오늘' 물건의 입찰 마감(추정)이 지났는가.

    오늘 기일이 아니면(과거·미래·미상) False — 과거 제외는 is_soon/split_upcoming의 날짜 판정이 맡는다.
    개시시각(sale_time "HHMM") 파싱 실패·미상·비정상 시각이면 통상 개시 10:00을 가정한다.
    now 미지정 시 KST 벽시계(now_kst) — 서버 TZ와 무관하게 일관.
    """
    now = now or now_kst()
    if days_until(s.sale_date, now.date()) != 0:
        return False
    hh, mm = 10, 0
    t = (s.sale_time or "").strip()
    if len(t) == 4 and t.isdigit():
        h, m = int(t[:2]), int(t[2:])
        if h < 24 and m < 60:   # 비정상 시각("2530" 등)은 기본 10:00 유지 — 전 페이지 500 방지
            hh, mm = h, m
    close = _dt.datetime.combine(now.date(), _dt.time(hh, mm)) + _dt.timedelta(minutes=BID_CLOSE_BUFFER_MIN)
    return now > close


def is_high_profit(s: ScoredListing, threshold: int = HIGH_PROFIT_THRESHOLD) -> bool:
    """보수 기준 차익이 threshold(기본 2억) 이상."""
    p = decision_profit(s)
    return p is not None and p >= threshold

# 비교군 신뢰 티어(감사 2026-07-10 MEDIUM): T3 정책상 same_dong_fallback 은 '참고치 — 추천
# 금지'인데 기본 정렬이 scope 를 안 봐 상위 50 의 84%를 fallback 허상 차익이 독식했다.
# 기본(차익) 정렬은 [검증 비교군 → 참고치 → 시세 없음] 티어 안에서 차익 내림차순.
# 0 = 같은 단지(추천 인정·인접 평형), 1 = 레거시(스코프 미기록 — 구 DB 하위호환),
# 2 = 동 폴백(참고치), 3 = 시세 없음/무효.
_SCOPE_TIER = {
    "same_complex_same_area": 0,
    "same_complex_near_area": 0,
    "": 1,
    "same_dong_fallback": 2,
}


def scope_tier(s: ScoredListing) -> int:
    if decision_profit(s) is None:
        return 3
    return _SCOPE_TIER.get(s.market_scope, 3)


def decision_profit(s: ScoredListing) -> int | None:
    """판단용 차익 — 보수 차익(profit_low) 우선, 없으면(레거시 행) 기준 차익.

    (T7, 문서 15장 7단계) 사용자에게 보이는 '차익 큰 순'과 필터는 보수 가격 기준이다.
    """
    return s.profit_low if s.profit_low is not None else s.expected_profit


def matches_query(s: ScoredListing, q: str) -> bool:
    """단지명·주소에 검색어(공백 무시, 대소문자 무시)가 부분일치하는가.

    '내가 본 그 아파트' 이름 검색용. 사용자가 '○○아파트' 또는 '○○동' 어느 쪽으로 쳐도
    잡히게 단지명과 주소 양쪽을 대상으로 한다. 검색어의 공백은 제거해 '롯데 캐슬'↔'롯데캐슬'
    표기차를 흡수한다.
    """
    needle = "".join(q.split()).lower()
    if not needle:
        return True
    hay = f"{s.apt_name or ''} {s.address or ''}"
    return needle in "".join(hay.split()).lower()


def apply_filters(items: list[ScoredListing], min_score: float | None = None,
                  property_type: str | None = None, region: str | None = None,
                  min_profit: int | None = None, burden_of=None,
                  max_bid: int | None = None, min_bid: int | None = None,
                  evaluable_only: bool = False,
                  min_area: float | None = None, max_area: float | None = None,
                  min_fails: int | None = None, q: str | None = None) -> list[ScoredListing]:
    """물건종류·지역·최소차익(원, 보수 기준)·예산(최저입찰가 상/하한)·평가가능·면적·유찰·이름 필터.

    burden_of: 물건 → 인수금액(원). 주어지면 최소차익 비교도 인수 차감 후 값으로
    (감사 2026-07-10: 필터·정렬은 저장 차익, 화면은 차감 차익 — 불일치 해소).
    max_bid/min_bid: 예산 필터(최저입찰가 상한/하한, 원). evaluable_only: 시세 추정된 물건만.
    min_area/max_area: 전용면적(㎡) 하/상한. min_fails: 최소 유찰 횟수(가격 저감된 물건).
    q: 단지명·주소 텍스트 검색어('내가 본 그 아파트' 찾기).
    """
    out = items
    if q:
        out = [s for s in out if matches_query(s, q)]
    if evaluable_only:
        out = [s for s in out if is_evaluable(s)]
    if min_area is not None:
        out = [s for s in out if s.area_m2 is not None and s.area_m2 >= min_area]
    if max_area is not None:
        out = [s for s in out if s.area_m2 is not None and s.area_m2 < max_area]
    if min_fails is not None:
        out = [s for s in out if (s.fail_count or 0) >= min_fails]
    if min_profit is not None:
        def _eff(s):
            p = decision_profit(s)
            return None if p is None else p - (burden_of(s) if burden_of else 0)
        out = [s for s in out if _eff(s) is not None and _eff(s) >= min_profit]
    if min_score is not None:   # 내부/API 하위호환용 — UI는 min_profit 사용
        out = [s for s in out if s.arb_score is not None and s.arb_score >= min_score]
    if property_type:
        out = [s for s in out if s.property_type == property_type]
    if region:
        out = [s for s in out if matches_region(s.address, region)]
    if max_bid is not None:
        out = [s for s in out if s.min_bid_price and s.min_bid_price <= max_bid]
    if min_bid is not None:
        out = [s for s in out if s.min_bid_price and s.min_bid_price >= min_bid]
    return out


def positive_only(items: list[ScoredListing], burden_of=None, uncertain_of=None
                  ) -> list[ScoredListing]:
    """효과 차익(보수 차익 − 인수금액) > 0 인 물건만 — 지도 '차익 양수만' 기본.

    인수금액 미상(uncertain_of=True)인 물건은 제외한다 — 인수 부담은 있는데 금액을
    아직 몰라(권리분석 크롤 미완) 효과 차익이 마이너스일 수 있으므로 '진짜 차익'에 넣지
    않는다. 크롤이 인수금액을 확정하면 다음 요청부터 자동으로 편입/제외된다(정적 목록 아님).
    """
    out = []
    for s in items:
        if uncertain_of is not None and uncertain_of(s):
            continue
        p = decision_profit(s)
        if p is None:
            continue
        if p - (burden_of(s) if burden_of else 0) > 0:
            out.append(s)
    return out


def count_by_sido(items: list[ScoredListing]) -> list[dict]:
    """지도 '어디에 몇 건' 집계 — 시도별 건수, 최다 지역 먼저.

    시도 미상(주소 파싱 실패)은 '기타'로 계상해 총합이 입력 건수와 일치하게 한다
    (침묵 누락 방지). 좌표 유무와 무관 — 후보 물건 전수 기준.
    """
    counts: dict[str, int] = {}
    for s in items:
        sd = sido_of(s.address) or "기타"
        counts[sd] = counts.get(sd, 0) + 1
    return [{"sido": k, "count": v}
            for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def sort_items(items: list[ScoredListing], key: str = DEFAULT_SORT,
               burden_of=None, uncertain_of=None) -> list[ScoredListing]:
    """정렬 — 기본(profit)은 [비교군 신뢰 티어 + 인수 불확실 티어] → 유효 차익 내림차순.

    검증 비교군(같은 단지) 물건이 폴백 참고치보다 항상 위 — '추천 금지 참고치'의 부풀린
    차익이 첫 화면 헤드라인을 차지하지 않게 한다(감사 2026-07-10).
    burden_of 가 주어지면 정렬 차익에서 인수금을 차감 — 화면 표시(p_adj)와 순위 일치.
    uncertain_of(서빙감사 2026-07-12 #1·#9): 인수 부담인데 금액 미상(+α)이라 차감 못 한
    물건은 별도 하위 티어로 강등 — 유찰 다회·임차권 미소멸 물건이 검증 clean 물건 위에
    무차감으로 랭크되던 문제 해소('−α' 표시와 정합).
    """
    def _eff(s):
        p = decision_profit(s)
        return None if p is None else p - (burden_of(s) if burden_of else 0)

    if key == "gap":
        return sorted(items, key=lambda s: (s.gap_rate is None, -(s.gap_rate or 0)))
    if key == "score":
        # (사용자 2026-07-21) 권리 미확인 물건은 '점수 없음'으로 취급 — 인수금액을 0으로 가정한
        # 함정(유찰 다회·대항력 임차인)이 갭 큰 arb_score로 최상위에 뜨는 것 차단. 권리 확정된
        # 물건만 점수순 상위. (채점층이 arb_score=None으로 굳혀도 정합, 여기선 저장 플래그로 즉효.)
        return sorted(items, key=lambda s: (
            s.arb_score is None or not s.rights_verified, -(s.arb_score or 0)))
    if key == "score_asc":   # 점수 낮은순
        return sorted(items, key=lambda s: (s.arb_score is None, (s.arb_score or 0)))
    if key == "profit_asc":  # 차익 낮은순 — 손해(마이너스 차익) 물건까지 오름차순 노출
        return sorted(items, key=lambda s: (_eff(s) is None, (_eff(s) or 0)))
    if key == "recent":      # 매각기일 최신(늦은)순
        return sorted(items, key=lambda s: s.sale_date or "", reverse=True)
    if key == "old":         # 매각기일 오래된(빠른)순
        return sorted(items, key=lambda s: s.sale_date or "9999-99-99")

    # 기본(profit): 검증 비교군 → 인수 불확실/권리미확인 강등 → 유효 차익 내림차순.
    # (사용자 2026-07-21) 권리 미확인도 불확실 티어로 강등 — 미상 인수금(0 가정)으로 부풀린
    # 차익이 검증 물건 위로 올라오지 않게(무조건 treat).
    return sorted(items, key=lambda s: (
        scope_tier(s),
        1 if ((uncertain_of and uncertain_of(s)) or not s.rights_verified) else 0,
        -(_eff(s) or 0)))


# ── 낙찰 결과(sold_listings) 검색·정렬 ────────────────────────────────────────
# (2026-07-27 사용자 요청) 낙찰 결과 페이지도 홈과 같은 검색 카드를 쓴다. 낙찰 기록은
# ScoredListing 이 아니라 스냅샷 dict(sold_listings 행)라 전용 함수를 둔다 — 필드가 없는
# 가짜 객체로 감싸 apply_filters 에 넣으면 rights_verified 등 부재 속성에서 조용히 깨진다.
# 필터 의미(지역·예산·면적·유찰·종류)는 홈과 동일하게 유지한다.

SOLD_SORT_KEYS = ("recent", "old", "price", "price_asc", "rate",
                  "profit", "profit_asc", "score", "score_asc")
# (사용자 결정 2026-07-27, 2차) 기본 = 매각기일 최신순. 점수 기본은 철회했다 — 과거 낙찰
# 기록은 법원 원문 백필이라 채점 정보가 없어 점수 정렬이 사실상 무동작이었다. 기일은 모든
# 행이 반드시 갖는 값이라 언제나 의미 있는 순서가 된다.
SOLD_DEFAULT_SORT = "recent"


def matches_sold_query(row: dict, q: str) -> bool:
    """단지명·주소·**사건번호** 부분일치. 홈 검색과 달리 사건번호도 대상 —
    낙찰 결과에서 특정 사건을 바로 찾을 수 있어야 하기 때문(진행 중 경매는 /find 가 담당)."""
    needle = "".join(q.split()).lower()
    if not needle:
        return True
    hay = f"{row.get('apt_name') or ''} {row.get('address') or ''} {row.get('case_no') or ''}"
    return needle in "".join(hay.split()).lower()


def filter_sold(rows: list[dict], q: str | None = None, region: str | None = None,
                property_type: str | None = None, max_bid: int | None = None,
                min_bid: int | None = None, min_area: float | None = None,
                max_area: float | None = None,
                min_fails: int | None = None) -> list[dict]:
    """낙찰 기록 필터 — 홈 검색 카드와 같은 조건 집합."""
    out = rows
    if q:
        out = [r for r in out if matches_sold_query(r, q)]
    if property_type:
        out = [r for r in out if r.get("property_type") == property_type]
    if region:
        out = [r for r in out if matches_region(r.get("address") or "", region)]
    if min_area is not None:
        out = [r for r in out if r.get("area_m2") is not None and r["area_m2"] >= min_area]
    if max_area is not None:
        out = [r for r in out if r.get("area_m2") is not None and r["area_m2"] < max_area]
    if min_fails is not None:
        out = [r for r in out if (r.get("fail_count") or 0) >= min_fails]
    if max_bid is not None:
        out = [r for r in out if r.get("min_bid_price") and r["min_bid_price"] <= max_bid]
    if min_bid is not None:
        out = [r for r in out if r.get("min_bid_price") and r["min_bid_price"] >= min_bid]
    return out


def _appraisal_rate(row: dict) -> float | None:
    """낙찰가 ÷ 감정가. 둘 중 하나라도 없으면 None(정렬 맨 뒤)."""
    sold, appr = row.get("sold_price"), row.get("appraisal_price")
    if not sold or not appr:
        return None
    return sold / appr


# (감사 CRITICAL 2026-07-28) 낙찰가가 감정가의 이 비율 미만이면 **온전한 물건의 거래로 보지
# 않는다** — 지분·대지권만 매각, 심각한 물리적·권리적 하자 등으로 물건 자체가 특수한 경우다.
# 그런 낙찰가를 온전한 물건의 시세와 나란히 놓으면 "시세보다 2.5억 싸게 샀다"는 허구가 만들어진다.
# 실측(2026-07-28): 차익 높은순 상위 8건 중 4건이 감정가율 1~14%(19회·13회·6회 유찰)였고
# 2위가 동래에코하임(낙찰 351만 vs 시세 2.52억 = 감정가율 1.4%)이었다.
# 임계 0.30 근거: 실데이터 분포에 20~30% 구간이 **비어 있어**(0건) 자연스러운 절단면이 있고,
# 통상 유찰 저감(회당 20~30%)으로 3회까지 내려와도 34% 수준이라 정상 범위를 자르지 않는다.
SOLD_ABNORMAL_PRICE_RATIO = 0.30


def sold_price_ratio(row: dict) -> float | None:
    """낙찰가 ÷ 감정가. 둘 중 하나라도 없으면 None(판정 불가 — 차단하지 않는다)."""
    ap, sold = row.get("appraisal_price"), row.get("sold_price")
    if not ap or not sold:
        return None
    return sold / ap


def sold_comparable(row: dict) -> bool:
    """이 낙찰 거래를 온전한 물건 시세와 비교해도 되는가(감정가율 이상치 게이트).

    감정가를 모르면 판정할 수 없으므로 True(차단하지 않음) — 모름을 이유로 정보를 지우지는
    않되, 확실히 이상한 것만 막는다.
    """
    ratio = sold_price_ratio(row)
    return ratio is None or ratio >= SOLD_ABNORMAL_PRICE_RATIO


def sold_gap(row: dict) -> int | None:
    """낙찰 물건의 차익 = **시세 검증 하한 − 실낙찰가**. 둘 중 하나라도 없으면 None.

    출처가 신뢰 대상(같은 단지 확정 실거래)이 아니면 값이 있어도 None 을 준다 — 이중 방어다.
    저장 단계(store.apply_sold_market_policy)가 이미 비우지만, 정책 도입 **전에 적재된 행**이나
    미러 지연으로 폴백 시세가 남아 있으면 화면이 그걸 확정값처럼 보여주게 된다.

    홈의 '보수 차익'(검증 하한 − 최저입찰가 − 취득세)과 **정의가 다르다**. 여기선 실제로
    얼마에 팔렸는지가 알려져 있으므로 최저입찰가 기준 차익은 의미가 약하다 — "그 낙찰자가
    시세 대비 얼마에 샀나"가 이 페이지의 질문이다. 화면 라벨도 그렇게 쓴다.

    ⚠ 시점 주의: 시세는 **지금** 기준이고 낙찰가는 그 기일의 값이라 기간 차이가 섞인다.
    ⚠ 지분·대지권만 매각된 물건은 낙찰가가 온전한 물건 시세와 비교 불가라 차익이 부풀려진다
       (실측: 동래에코하임 낙찰 0.04억 vs 시세 2.52억). 목록의 지분 라벨로 구분한다.
    """
    from .store import SOLD_TRUSTED_SCOPES  # noqa: PLC0415 — 순환 import 회피
    scope = (row.get("market_scope") or "").strip()
    # 저장 단계(store.apply_sold_market_policy)와 **같은 판정** — 빈 출처도 불신(fail-closed).
    if scope not in SOLD_TRUSTED_SCOPES:
        return None
    # 시세가 신뢰 출처여도 **거래 쪽이 특수**하면 비교가 성립하지 않는다(감정가율 이상치).
    if not sold_comparable(row):
        return None
    band, sold = row.get("market_band_low"), row.get("sold_price")
    if band is None or sold is None:
        return None
    return band - sold


def sold_sort_available(rows: list[dict], key: str) -> bool:
    """이 결과 집합에서 그 정렬이 실제로 의미를 갖는가(값을 가진 행이 하나라도 있는가).

    과거 낙찰 기록은 법원 원문에서 백필한 것이라 시세·차익·점수가 비어 있을 수 있다.
    값이 전무한 목록에 '점수 높은순'이라고 써 두면 실제로는 아무 정렬도 일어나지 않은
    화면을 정렬된 것처럼 보여주게 된다 — 라우트가 이 값을 보고 기일 최신순으로 강등하고
    그 사실을 화면에 밝힌다(정렬된 척 금지).
    """
    if key in ("profit", "profit_asc"):
        return any(sold_gap(r) is not None for r in rows)
    field = {"score": "arb_score", "score_asc": "arb_score",
             "price": "sold_price", "price_asc": "sold_price"}.get(key)
    if not field:      # 기일·감정가율은 값이 없어도 정렬 자체는 성립
        return True
    return any(r.get(field) is not None for r in rows)


def sort_sold(rows: list[dict], key: str = SOLD_DEFAULT_SORT) -> list[dict]:
    """낙찰 기록 정렬. 값이 없는 행(점수 미채점·낙찰가 미공개)은 항상 맨 뒤로 — 0 으로
    치환해 섞으면 '0점·0원'처럼 보여 오독된다."""
    if key == "score_asc":
        return sorted(rows, key=lambda r: (r.get("arb_score") is None, r.get("arb_score") or 0))
    if key == "old":      # 매각기일 오래된순 — 날짜 없는 행은 맨 뒤로 밀어 둔다
        return sorted(rows, key=lambda r: (not r.get("sale_date"), r.get("sale_date") or ""))
    if key == "price":       # 낙찰가 높은순
        return sorted(rows, key=lambda r: (r.get("sold_price") is None,
                                           -(r.get("sold_price") or 0)))
    if key == "price_asc":   # 낙찰가 낮은순 — 미공개(None)는 0원이 아니므로 맨 뒤
        return sorted(rows, key=lambda r: (r.get("sold_price") is None,
                                           r.get("sold_price") or 0))
    if key == "rate":   # 감정가율 낮은순 = 싸게 낙찰된 순
        return sorted(rows, key=lambda r: (_appraisal_rate(r) is None, _appraisal_rate(r) or 0))
    if key == "profit":      # 차익 높은순(시세 − 낙찰가)
        return sorted(rows, key=lambda r: (sold_gap(r) is None, -(sold_gap(r) or 0)))
    if key == "profit_asc":  # 차익 낮은순 — 손해(음수)까지 오름차순 노출
        return sorted(rows, key=lambda r: (sold_gap(r) is None, sold_gap(r) or 0))
    if key == "score":
        return sorted(rows, key=lambda r: (r.get("arb_score") is None,
                                           -(r.get("arb_score") or 0)))
    # 기본: 매각기일 최신순 — 날짜 없는 행은 빈 문자열이라 자연히 맨 뒤
    return sorted(rows, key=lambda r: r.get("sale_date") or "", reverse=True)
