"""매칭 / 시세 추정 테스트."""
from src.matcher import estimate_market_price, match_trades
from src.models import AuctionListing, Trade


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
