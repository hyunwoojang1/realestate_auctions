"""P-01/P-13 회귀 — '인수 명시인데 금액 미상'은 추천계열에 들어가지 않는다.

감사 발견(2026-07-23): 명세서에 "매수인에게 대항할 수 있는 임차권등기… 잔액을 매수인이 인수함"이
명시된 물건이 assumed_amount=0 으로 계산돼 차익 유력·양호·관심으로 서빙됐다(실측 108건).
모름(금액 미상)을 없음(0원)으로 바꾸지 않는다 — 헌장 §0-②.
"""
from src.config import CONFIG
from src.courtauction_detail import CaseRights, summarize
from src.models import ScoredListing
from src.score import derive_grade

L = CONFIG.grade_labels


def test_derive_grade_caps_recommend_when_burden_amount_unknown():
    """추천계열로 계산된 등급이 '인수 명시·금액 미상'이면 '주의'로 상한된다."""
    for arb in (95.0, 80.0, 60.0):
        base = derive_grade(arb, gated=False, rights_verified=True, p_low=10_000_000)
        capped = derive_grade(arb, gated=False, rights_verified=True, p_low=10_000_000,
                              burden_amount_unknown=True)
        if base in (L["top"], L["second"], L["interest"]):
            assert capped == L["caution"], f"arb={arb}: {base} → {capped}"


def test_known_amount_is_not_capped():
    """금액을 읽은 인수는 p_low에서 이미 차감되므로 상한 대상이 아니다(과잉 강등 방지)."""
    g = derive_grade(95.0, gated=False, rights_verified=True, p_low=10_000_000,
                     burden_amount_unknown=False)
    assert g == L["top"]


def test_cap_does_not_override_stronger_states():
    """위험(하드게이트)·차익없음·권리미확인은 '주의'보다 강한 정보 — 상한이 덮지 않는다."""
    assert derive_grade(10.0, gated=True, rights_verified=True,
                        burden_amount_unknown=True) == L["risk"]
    assert derive_grade(90.0, gated=False, rights_verified=True, p_low=-1,
                        burden_amount_unknown=True) == L["no_profit"]
    assert derive_grade(90.0, gated=False, rights_verified=False, p_low=1,
                        burden_amount_unknown=True) == L["rights_unverified"]


def test_badge_amount_unknown_from_real_phrasing():
    """실측 문구(청주 2025타경51474류): 인수 명시 + 금액 미기재 → amount_unknown."""
    cr = CaseRights(
        court="청주지방법원", case_no="2025타경51474", item_no="1",
        surviving_rights="매수인에게 대항할 수 있는 을구 순위 10번 주택임차권등기 있음"
                         "(배당에서 보증금이 전액변제되지 아니하면 잔액을 매수인이 인수함)",
        senior_lien="2019. 3. 5. 근저당권")
    badge = summarize(cr)
    assert not badge.is_clean
    assert badge.amount_unknown, "인수 명시인데 금액 미상 → amount_unknown 이어야 한다"


def test_scored_listing_persists_flag():
    """서빙 폴백 재계산이 같은 규칙을 쓰려면 ScoredListing 에 영속돼야 한다."""
    s = ScoredListing(case_no="2025타경1", apt_name="x", address="y", property_type="아파트",
                      area_m2=59.0, appraisal_price=1, min_bid_price=1, fail_count=0,
                      sale_date="2026-08-01", est_market_price=None, matched_trades=0,
                      confidence=0.0, real_acquisition_cost=0, expected_profit=None,
                      gap_rate=None, gap_score=0.0, rights_score=0.0, liquidity_score=0.0,
                      arb_score=None, grade=L["caution"], burden_amount_unknown=True)
    assert s.to_row()["burden_amount_unknown"] is True
