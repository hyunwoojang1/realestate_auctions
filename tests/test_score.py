"""차익 스코어 엔진 단위테스트."""
from src.models import AuctionListing
from src import score


def _base(**kw) -> AuctionListing:
    d = dict(
        case_no="T", court="c", address="서울 노원구 상계동", lawd_cd="11350",
        dong="상계동", apt_name="X", property_type="아파트", area_m2=84.9,
        appraisal_price=600_000_000, min_bid_price=400_000_000, fail_count=2,
        sale_date="2026-07-01",
    )
    d.update(kw)
    return AuctionListing(**d)


def test_acquisition_tax_brackets():
    assert score.acquisition_tax(500_000_000) == round(500_000_000 * 0.011)
    assert score.acquisition_tax(700_000_000) == round(700_000_000 * 0.022)
    assert score.acquisition_tax(1_000_000_000) == round(1_000_000_000 * 0.033)


def test_gap_score_interpolation():
    assert score.gap_score_from_rate(0.0) == 0.0
    assert score.gap_score_from_rate(0.40) == 100.0
    assert score.gap_score_from_rate(-0.5) == 0.0
    # 30%→80, 40%→100, 35%는 중간 89~91
    mid = score.gap_score_from_rate(0.35)
    assert 88 <= mid <= 92


def test_clean_listing_scores_high():
    lst = _base(min_bid_price=397_000_000, occupant_type="공실")
    s = score.score_listing(lst, est_market_price=630_000_000, matched_trades=3)
    assert s.arb_score is not None and s.arb_score >= 80
    assert s.grade == "확실한 차익"
    assert s.expected_profit > 0
    assert s.confidence == 1.0


def test_hard_gate_high_assumed_amount():
    """인수금액 비율 > 30% → 권리 점수 0 (하드게이트)."""
    lst = _base(min_bid_price=576_000_000, assumed_amount=200_000_000, tenant_opposable=True,
                occupant_type="임차인")
    assert score.rights_score(lst) == 0.0
    s = score.score_listing(lst, est_market_price=950_000_000, matched_trades=2)
    assert s.rights_score == 0.0
    assert s.arb_score < 60  # 표면 갭 커도 권리 폭탄이면 상위 못 옴


def test_fatal_special_right_hard_gate():
    lst = _base(special_rights=["유치권"])
    assert score.rights_score(lst) == 0.0


def test_no_market_estimate_is_honest():
    lst = _base(property_type="다세대")
    s = score.score_listing(lst, est_market_price=None, matched_trades=0)
    assert s.arb_score is None
    assert s.grade == "시세추정불가"
    assert s.confidence == 0.60


def test_negative_gap_is_labeled_no_profit():
    """순차익 0 이하면 권리·환금이 좋아도 '차익없음'으로 표기(오해 방지)."""
    lst = _base(min_bid_price=550_000_000, occupant_type="공실")
    s = score.score_listing(lst, est_market_price=560_000_000, matched_trades=3)
    assert s.expected_profit < 0
    assert s.grade == "차익없음"


def test_confidence_ladder():
    assert score.confidence_from_matches(3) == 1.0
    assert score.confidence_from_matches(2) == 0.85
    assert score.confidence_from_matches(1) == 0.70
    assert score.confidence_from_matches(0) == 0.60
