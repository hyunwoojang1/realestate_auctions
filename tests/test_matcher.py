"""매칭 / 시세 추정 테스트."""
from src.matcher import estimate_market_price, filter_recent, match_trades, trim_outliers
from src.models import AuctionListing, Trade
from src.molit_client import recent_ymds


def _lst(**kw) -> AuctionListing:
    d = dict(case_no="T", court="c", address="서울 노원구 상계동", lawd_cd="11350",
             dong="상계동", apt_name="상계주공", property_type="아파트", area_m2=84.9,
             appraisal_price=6_2000_0000, min_bid_price=3_9700_0000, fail_count=2,
             sale_date="2026-07-01")
    d.update(kw)
    return AuctionListing(**d)


# T2: 유형 미태깅("") 거래는 더 이상 매칭되지 않는다 — 아파트 거래는 kind="apt" 명시.
TRADES = [
    Trade(apt_name="상계주공", area_m2=84.9, price=630_000_000, deal_ym="202604", dong="상계동", kind="apt"),
    Trade(apt_name="상계주공", area_m2=83.0, price=620_000_000, deal_ym="202605", dong="상계동", kind="apt"),
    Trade(apt_name="상계주공", area_m2=84.9, price=645_000_000, deal_ym="202605", dong="상계동", kind="apt"),
    Trade(apt_name="상계벽산", area_m2=59.8, price=490_000_000, deal_ym="202605", dong="상계동", kind="apt"),
    Trade(apt_name="다른단지", area_m2=120.0, price=900_000_000, deal_ym="202605", dong="중계동", kind="apt"),
]


def test_match_by_name_and_area():
    matched = match_trades(_lst(), TRADES)
    assert len(matched) == 3
    assert all(m.apt_name == "상계주공" for m in matched)


def test_area_band_excludes_wrong_size():
    # 59.8은 84.9 ±10% 밖 → 같은 이름이어도 제외돼야
    matched = match_trades(_lst(area_m2=84.9), TRADES)
    assert all(abs(m.area_m2 - 84.9) / 84.9 <= 0.10 for m in matched)


def test_estimate_returns_reasonable_price():
    est, n = estimate_market_price(_lst(), TRADES)
    assert n == 3
    assert est is not None
    assert 6_0000_0000 <= est <= 6_6000_0000  # 약 6.0~6.6억


def test_no_match_returns_none():
    est, n = estimate_market_price(_lst(apt_name="존재하지않는단지", dong="없는동"), TRADES)
    assert est is None and n == 0


def test_cancelled_trade_excluded_from_matching():
    """해제거래(is_cancelled)는 pool에서 제외 — comps/matched에 안 들어가 시세를 부풀리지 않는다
    (감사 2026-07-15: 국토부 cdealType='O' 신고취소 건이 +11.18% 과대추정 유발)."""
    lst = _lst()  # 상계주공 84.9
    normal = [
        Trade("상계주공", 84.9, 630_000_000, "202605", "상계동", kind="apt"),
        Trade("상계주공", 84.9, 620_000_000, "202605", "상계동", kind="apt"),
    ]
    cancelled = Trade("상계주공", 84.9, 990_000_000, "202605", "상계동", kind="apt",
                      cdeal_type="O", cdeal_day="26.05.20")
    matched = match_trades(lst, normal + [cancelled])
    assert len(matched) == 2                                  # 해제건 제외
    assert all(not m.is_cancelled for m in matched)
    assert 990_000_000 not in {m.price for m in matched}      # 고가 outlier 유입 안 됨


# ---- X1: 매칭 품질(이상치·최근성·다월) ----

def test_trim_outliers():
    assert trim_outliers([1, 2, 3, 100]) == [2, 3]   # 4건↑ → 상·하단 1건씩 제거
    assert trim_outliers([5, 10, 15]) == [5, 10, 15]  # 4건 미만 → 그대로


def test_filter_recent_excludes_old():
    ts = [Trade("X", 84.9, 600_000_000, "202605", "상계동"),
          Trade("X", 84.9, 300_000_000, "201501", "상계동")]
    recent = filter_recent(ts, window=12)
    assert len(recent) == 1 and recent[0].deal_ym == "202605"


def test_estimate_ignores_outlier():
    lst = _lst()  # 상계주공 84.9
    # T5: 트림 후 실기반 3건 이상이어야 밴드/시세 생성 → 5건 fixture(트림 후 3건)
    trades = [
        Trade("상계주공", 84.9, 630_000_000, "202605", "상계동", kind="apt"),
        Trade("상계주공", 84.9, 620_000_000, "202605", "상계동", kind="apt"),
        Trade("상계주공", 84.9, 640_000_000, "202604", "상계동", kind="apt"),
        Trade("상계주공", 84.9, 625_000_000, "202603", "상계동", kind="apt"),
        Trade("상계주공", 84.9, 1_300_000_000, "202605", "상계동", kind="apt"),  # 이상치(2배)
    ]
    est, n = estimate_market_price(lst, trades)
    assert n == 5
    assert 6.0e8 <= est <= 6.6e8   # 13억 이상치에 안 끌려감


def test_recent_ymds():
    assert recent_ymds("202605", 3) == ["202605", "202604", "202603"]
    assert recent_ymds("202602", 3) == ["202602", "202601", "202512"]  # 연도 롤오버


# ---- 유형 분리 매칭(다세대를 아파트 시세로 평가하지 않게) ----

def test_match_excludes_other_property_type():
    """화곡동 다세대는 같은 동·면적의 '아파트' 실거래를 끌어오면 안 된다."""
    villa = _lst(apt_name="화곡동 다세대", property_type="다세대",
                 dong="화곡동", lawd_cd="11500", area_m2=84.0)
    trades = [
        Trade("화곡래미안", 84.0, 750_000_000, "202605", "화곡동", kind="apt"),  # 아파트 → 제외
        Trade("화곡그린빌", 83.0, 320_000_000, "202605", "화곡동", kind="rh"),   # 빌라 → 채택
    ]
    matched = match_trades(villa, trades)
    assert len(matched) == 1
    assert matched[0].kind == "rh"


def test_match_villa_no_villa_comps_returns_empty():
    """빌라 매물인데 빌라 실거래가 없으면(아파트만 있으면) 매칭 0 → 시세추정불가."""
    villa = _lst(apt_name="화곡동 다세대", property_type="다세대",
                 dong="화곡동", lawd_cd="11500", area_m2=84.0)
    apt_only = [Trade("화곡래미안", 84.0, 750_000_000, "202605", "화곡동", kind="apt")]
    est, n = estimate_market_price(villa, apt_only)
    assert est is None and n == 0


def test_untagged_trades_no_longer_match():
    """(T2 정책 반전) kind 미태깅(="") 거래는 매칭되지 않는다 — 유형 혼입 방지.

    과거 하위호환(미태깅 통과)은 유형을 모르는 거래가 비교군을 오염시키는 경로였다.
    """
    untagged = [Trade(apt_name="상계주공", area_m2=84.9, price=630_000_000,
                      deal_ym="202605", dong="상계동")]  # kind=""
    assert match_trades(_lst(), untagged) == []


# ---- E: 확장 유형(단독/상업/토지) 매칭 ----

def test_expected_kind_extended_types():
    from src.matcher import expected_kind
    assert expected_kind("단독주택") == "sh"
    assert expected_kind("상가") == "nrg"
    assert expected_kind("토지") == "land"
    assert expected_kind("아파트") == "apt"
    assert expected_kind("모르는유형") is None


def test_land_listing_never_estimates_v1_policy():
    """(T2 정책 반전) 토지는 land 실거래가 있어도 시세추정불가 — v1 미지원 유형.

    '같은 법정동+비슷한 면적' 토지 중앙값은 도로접면·용도지역·형상 개별성을 무시한다(문서 6장).
    """
    land = _lst(apt_name="", property_type="토지", dong="역삼동", lawd_cd="11680", area_m2=200.0)
    trades = [
        Trade("", 205.0, 900_000_000, "202605", "역삼동", kind="land"),
        Trade("", 198.0, 880_000_000, "202605", "역삼동", kind="land"),
    ]
    est, n = estimate_market_price(land, trades)
    assert est is None and n == 0


# ---- 2026-07-10 실사고 방어 회귀 ----

def test_share_sale_never_estimates():
    """지분 매각(비고 힌트로 special_rights에 '지분')은 온전가 시세 추정 금지 —
    실사고: '지분매각' 비고인데 온전 아파트 시세가 붙어 허상 차익."""
    from src.matcher import SCOPE_SHARE_SALE, estimate_market
    m = estimate_market(_lst(special_rights=["지분"]), TRADES)
    assert m.est is None and m.scope == SCOPE_SHARE_SALE


def test_appraisal_mismatch_voids_estimate():
    """비교군 시세가 감정가×2.5 초과면 비교군 불신 → 시세 무효 —
    실사고: 낡은 소형 단지를 같은 동 신축 대단지와 오매칭(4.5~11배)."""
    from src.matcher import SCOPE_APPRAISAL_MISMATCH, estimate_market
    # 감정 1.4억짜리에 6억대 comps → 무효화돼야
    m = estimate_market(_lst(appraisal_price=140_000_000), TRADES)
    assert m.est is None and m.scope == SCOPE_APPRAISAL_MISMATCH


def test_appraisal_sane_estimate_passes():
    """감정가와 정합(6.2억 감정 vs 6.3억대 시세)하면 정상 추정 유지 — 오탐 방지."""
    from src.matcher import estimate_market
    m = estimate_market(_lst(), TRADES)   # 감정 6.2억, comps 6.2~6.45억
    assert m.est is not None and m.scope == "same_complex_same_area"


def test_appraisal_zero_skips_sanity():
    """감정가 0/미상 물건은 교차검증 불가 — sanity 를 건너뛰고 추정은 유지."""
    from src.matcher import estimate_market
    m = estimate_market(_lst(appraisal_price=0), TRADES)
    assert m.est is not None


def test_unregistered_land_right_never_estimates():
    """감사 L1: '대지권미등기'도 온전가 시세 추정 금지(지분과 같은 계열)."""
    from src.matcher import SCOPE_SHARE_SALE, estimate_market
    m = estimate_market(_lst(special_rights=["대지권미등기"]), TRADES)
    assert m.est is None and m.scope == SCOPE_SHARE_SALE


def test_fallback_appraisal_bounds():
    """서빙감사 #8·#10: 폴백 시세가 감정가 1.5배 초과/0.6배 미만이면 무효화."""
    from src.matcher import SCOPE_APPRAISAL_MISMATCH, estimate_market
    # 같은 동 다른 이름 comps만 있게 → fallback. 감정 1억에 comps 2억(2배) → 무효
    lst = _lst(apt_name="A동네빌", area_m2=84.9, appraisal_price=100_000_000, dong="상계동", lawd_cd="11350")
    trades = [
        Trade(apt_name="딴이름아파트", area_m2=84.9, price=200_000_000, deal_ym="202605", dong="상계동", kind="apt", lawd_cd="11350"),
        Trade(apt_name="또딴이름", area_m2=83.0, price=205_000_000, deal_ym="202605", dong="상계동", kind="apt", lawd_cd="11350"),
        Trade(apt_name="세번째", area_m2=84.9, price=198_000_000, deal_ym="202604", dong="상계동", kind="apt", lawd_cd="11350"),
    ]
    m = estimate_market(lst, trades)
    assert m.scope == SCOPE_APPRAISAL_MISMATCH and m.est is None


def test_multi_complex_demotes_from_same_area():
    """서빙감사 #2: 마을명 부분일치가 여러 단지를 끌어오면 same_complex 인정 안 함."""
    from src.matcher import SCOPE_SAME_COMPLEX_SAME_AREA, match_trades_scoped
    lst = _lst(apt_name="갑오마을", area_m2=126.48, dong="대청동", lawd_cd="48250")
    trades = [
        Trade(apt_name="갑오마을3단지대동", area_m2=126.48, price=230_000_000, deal_ym="202605", dong="대청동", kind="apt", lawd_cd="48250"),
        Trade(apt_name="갑오마을8단지대우푸르지오2차", area_m2=126.48, price=317_000_000, deal_ym="202605", dong="대청동", kind="apt", lawd_cd="48250"),
        Trade(apt_name="갑오마을8단지대우푸르지오2차", area_m2=125.0, price=320_000_000, deal_ym="202604", dong="대청동", kind="apt", lawd_cd="48250"),
    ]
    _, scope = match_trades_scoped(lst, trades)
    assert scope != SCOPE_SAME_COMPLEX_SAME_AREA   # 다단지 → 강등


def test_creditor_bid_floor_raises_min_bid():
    """서빙감사 #3: 신청채권자 매수신청액이 공고최저가보다 크면 유효 최저입찰가로."""
    from src.courtauction_fields import CourtAuctionRecord, to_auction_listing
    rec = CourtAuctionRecord(
        doc_id="D", case_no="2025타경570", court="창원지방법원", dept="", property_type="아파트",
        usage_name="아파트", address="창원", sido="", sigu="", dong="중동", lawd_cd="48120",
        jibun="", building_name="대동다숲", building_detail="101동", area_m2=84.0,
        appraisal_price=400_000_000, min_bid_price=260_400_000, fail_count=1,
        sale_date="2026-08-01", sale_place="", bid_open_date="", bid_close_date="",
        view_count=0, interest_count=0, note="신청채권자로부터 금 295,400,000원의 매수신청 및 보증이 있음.",
        tel="", x_proj="", y_proj="", item_no="1")
    lst = to_auction_listing(rec)
    assert lst.min_bid_price == 295_400_000 and "채권자매수신청" in lst.special_rights
