"""차익 스코어 엔진 단위테스트."""
from src import score
from src.models import AuctionListing


def _base(**kw) -> AuctionListing:
    d = dict(
        case_no="T", court="c", address="서울 노원구 상계동", lawd_cd="11350",
        dong="상계동", apt_name="X", property_type="아파트", area_m2=84.9,
        appraisal_price=600_000_000, min_bid_price=400_000_000, fail_count=2,
        sale_date="2026-07-01", rights_verified=True,   # 스코어 산식 검증용: 권리 검증된 물건 가정
    )
    d.update(kw)
    return AuctionListing(**d)


def test_acquisition_cost_is_bid_plus_tax_only():
    """취득원가 = 최저입찰가 + 취득세(tax.py). 명도·수리·인수 등 주관적 비용 미포함."""
    from src import tax
    lst = _base(min_bid_price=400_000_000, property_type="아파트", occupant_type="다수점유",
                assumed_amount=50_000_000, area_m2=100.0)
    cost = score.real_acquisition_cost(lst)
    assert cost == 400_000_000 + tax.acquisition_tax(400_000_000, "아파트", 100.0)  # 점유·인수 무관


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
    assert s.grade == "차익 유력"
    assert s.expected_profit > 0
    assert s.confidence == 1.0


def test_unverified_rights_never_gets_top_grade():
    """권리 미검증 물건은 갭이 커도 '권리미확인' 등급 + (사용자 2026-07-21) 점수 자체가 없다.
    인수금액 0 가정으로 부풀린 함정이 점수순 상위로 뜨는 것을 차단(arb_score=None)."""
    lst = _base(min_bid_price=397_000_000, occupant_type="공실", rights_verified=False)
    s = score.score_listing(lst, est_market_price=630_000_000, matched_trades=3)
    assert s.arb_score is None                # 점수 없음 — 함정 방어
    assert s.grade == "권리미확인"             # 허위 안전신호 금지
    assert s.rights_verified is False
    assert s.expected_profit is not None      # 차익 자체는 적재(차익순 정렬용)


def test_min_comps_gate_blocks_thin_market():
    """표본 1건이면 시세로 신뢰하지 않는다(시세추정불가). 2건이면 최상위 '차익 유력'은 못 준다."""
    lst = _base(min_bid_price=397_000_000, occupant_type="공실")
    one = score.score_listing(lst, est_market_price=630_000_000, matched_trades=1)
    assert one.arb_score is None and one.grade == "시세추정불가"   # 1건 = 시세 불신
    two = score.score_listing(lst, est_market_price=630_000_000, matched_trades=2)
    assert two.arb_score is not None
    assert two.grade != "차익 유력"                                # 신뢰계수<1.0 → 최상위 강등


def test_hard_gate_high_assumed_amount():
    """인수금액 비율 > 30% → 권리 0 + 최종 스코어 상한 + '위험' 등급."""
    lst = _base(min_bid_price=576_000_000, assumed_amount=200_000_000, tenant_opposable=True,
                occupant_type="임차인")
    assert score.is_hard_gated(lst) is True
    assert score.rights_score(lst) == 0.0
    s = score.score_listing(lst, est_market_price=950_000_000, matched_trades=2)
    assert s.rights_score == 0.0
    assert s.arb_score <= score.CONFIG.gate_ceiling   # 표면 갭 커도 상위 못 옴
    assert s.grade == "위험"


def test_fatal_special_right_hard_gate():
    """유치권: 가격갭이 아무리 커도 '위험'으로 강등되어 상위 노출 안 됨."""
    lst = _base(special_rights=["유치권"], min_bid_price=147_000_000, occupant_type="다수점유",
                property_type="다세대")
    assert score.is_hard_gated(lst) is True
    assert score.rights_score(lst) == 0.0
    s = score.score_listing(lst, est_market_price=280_000_000, matched_trades=3)
    assert s.arb_score <= score.CONFIG.gate_ceiling
    assert s.grade == "위험"


def test_no_market_estimate_is_honest():
    # T2: 지원 유형(아파트)인데 comps가 없는 경우 — '시세추정불가'(데이터 부족).
    lst = _base(property_type="아파트")
    s = score.score_listing(lst, est_market_price=None, matched_trades=0)
    assert s.arb_score is None
    assert s.grade == "시세추정불가"
    assert s.confidence == 0.60


def test_unsupported_type_is_labeled_distinctly():
    # T2: 미지원 유형(다세대)은 '정책상 미추정' — 별도 등급 '미지원유형'.
    lst = _base(property_type="다세대")
    s = score.score_listing(lst, est_market_price=None, matched_trades=0)
    assert s.arb_score is None
    assert s.grade == "미지원유형"


def test_negative_gap_is_labeled_no_profit():
    """취득원가(최저가+취득세)가 시세를 웃돌면 권리·환금이 좋아도 '차익없음'."""
    lst = _base(min_bid_price=565_000_000, occupant_type="공실")
    s = score.score_listing(lst, est_market_price=560_000_000, matched_trades=3)
    assert s.expected_profit < 0
    assert s.grade == "차익없음"


def test_confidence_ladder():
    assert score.confidence_from_matches(3) == 1.0
    assert score.confidence_from_matches(2) == 0.85
    assert score.confidence_from_matches(1) == 0.70
    assert score.confidence_from_matches(0) == 0.60


def test_load_config_applies_json_override(tmp_path):
    """data/score_config.json 형식 오버라이드가 적용되고, 미지정 필드는 기본값 유지."""
    from src import config
    p = tmp_path / "score_config.json"
    p.write_text('{"w_gap": 0.9, "gate_ceiling": 5}', encoding="utf-8")
    cfg = config.load_config(p)
    assert cfg.w_gap == 0.9
    assert cfg.gate_ceiling == 5
    assert cfg.w_rights == 0.30   # 미지정 → 기본값


def test_config_override_changes_behavior(monkeypatch):
    """CONFIG를 바꾸면 코드 수정 없이 스코어 동작이 바뀐다(튜닝 가능)."""
    from src import config
    monkeypatch.setattr(score, "CONFIG", config.ScoreConfig(gate_ceiling=10.0))
    lst = _base(special_rights=["유치권"], min_bid_price=147_000_000, property_type="다세대")
    s = score.score_listing(lst, est_market_price=280_000_000, matched_trades=3)
    assert s.arb_score <= 10.0   # 상한이 25→10으로 낮아짐
    assert s.grade == "위험"
