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


TRADES = [
    Trade(apt_name="상계주공", area_m2=84.9, price=630_000_000, deal_ym="202604", dong="상계동"),
    Trade(apt_name="상계주공", area_m2=83.0, price=620_000_000, deal_ym="202605", dong="상계동"),
    Trade(apt_name="상계주공", area_m2=84.9, price=645_000_000, deal_ym="202605", dong="상계동"),
    Trade(apt_name="상계벽산", area_m2=59.8, price=490_000_000, deal_ym="202605", dong="상계동"),
    Trade(apt_name="다른단지", area_m2=120.0, price=900_000_000, deal_ym="202605", dong="중계동"),
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
    trades = [
        Trade("상계주공", 84.9, 630_000_000, "202605", "상계동"),
        Trade("상계주공", 84.9, 620_000_000, "202605", "상계동"),
        Trade("상계주공", 84.9, 640_000_000, "202604", "상계동"),
        Trade("상계주공", 84.9, 1_300_000_000, "202605", "상계동"),  # 이상치(2배)
    ]
    est, n = estimate_market_price(lst, trades)
    assert n == 4
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


def test_untagged_trades_still_match_backward_compat():
    """kind 미태깅(="") 거래는 기존처럼 매칭된다(하위호환)."""
    matched = match_trades(_lst(), TRADES)  # TRADES는 kind 미지정
    assert len(matched) == 3


# ---- E: 확장 유형(단독/상업/토지) 매칭 ----

def test_expected_kind_extended_types():
    from src.matcher import expected_kind
    assert expected_kind("단독주택") == "sh"
    assert expected_kind("상가") == "nrg"
    assert expected_kind("토지") == "land"
    assert expected_kind("아파트") == "apt"
    assert expected_kind("모르는유형") is None


def test_land_listing_matches_extra_land_by_dong():
    """토지 물건은 단지명 없이 같은 법정동 land 실거래로 시세추정(dong+면적+kind 분리)."""
    land = _lst(apt_name="", property_type="토지", dong="역삼동", lawd_cd="11680", area_m2=200.0)
    trades = [
        Trade("", 205.0, 900_000_000, "202605", "역삼동", kind="land"),
        Trade("", 198.0, 880_000_000, "202605", "역삼동", kind="land"),
        Trade("아무아파트", 84.0, 1_500_000_000, "202605", "역삼동", kind="apt"),  # 유형 다름 → 제외
    ]
    est, n = estimate_market_price(land, trades)
    assert n == 2 and est is not None
