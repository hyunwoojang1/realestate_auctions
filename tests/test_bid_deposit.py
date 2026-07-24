"""U-01 회귀 — 입찰보증금 비율을 법원 명세서에서 읽는다.

UX 감사(2026-07-23)에서 3개 페르소나가 독립 지적한 **실제 금전 손실 경로**:
재매각·특별매각조건 물건은 보증금이 최저매각가의 20~30%인데 화면이 늘 10%로 계산해,
사용자가 절반만 준비해 법정에 가면 **입찰이 무효** 처리된다.
법원은 이 비율을 명세서 비고에 문장으로 주고 있다(실측 609건).

**모름과 10%를 구분한다**: 명시가 없으면 None(=미상)이며, 호출부가 '통상 10% 가정'으로
쓰되 추정임을 화면에 밝혀야 한다.
"""
import pytest

from src.courtauction_rights import bid_deposit, parse_deposit_rate

# 실측 상위 표기 변형(DB 609건에서 추출)
REAL = [
    ("특별매각조건 매수신청보증금 최저매각가격의 20%", 20),
    ("재매각임. 매수신청보증금은 최저매각가격의 20%", 20),
    ("재매각임. 매수신청보증금 최저매각가격의 20%", 20),
    ("- 특별매각조건 매수신청보증금 최저매각가격의 20%", 20),
    ("1. 재매각임. 매수신청보증금은 최저매각가격의 20%임", 20),
    ("1. 재매각, 매수신청보증금은 최저매각가격의 20%", 20),
    ("3. 특별매각조건 매수신청보증금 최저매각가격의 30%", 30),
]


@pytest.mark.parametrize(("text", "expected"), REAL)
def test_real_phrasings(text, expected):
    assert parse_deposit_rate(text) == expected


def test_missing_returns_none_not_ten():
    """명시가 없으면 None — '10%'로 단정하지 않는다(모름≠기본값)."""
    assert parse_deposit_rate("") is None
    assert parse_deposit_rate("아파트로 이용중이고, 관리비가 약 118만원 미납") is None
    assert parse_deposit_rate(None) is None


def test_picks_highest_when_multiple():
    """여러 비율이 언급되면 보수적으로 큰 값(준비할 현금을 과소평가하지 않는다)."""
    assert parse_deposit_rate("매수신청보증금 최저매각가격의 20%", "보증금 30%") == 30


def test_rejects_out_of_range():
    """상식 밖 값은 오탐으로 보고 버린다(다른 비율이 '보증금' 근처에 있던 경우)."""
    assert parse_deposit_rate("보증금 대비 배당률 5%") is None
    assert parse_deposit_rate("보증금 반환채권의 80%") is None


def test_bid_deposit_uses_stated_rate():
    """명시된 20%면 실제 금액이 2배 — 이 물건(최저가 8.96억)은 0.90억이 아니라 1.79억."""
    amount, rate, stated = bid_deposit(896_000_000, "재매각. 매수신청보증금은 최저매각가격의 20%임")
    assert (rate, stated) == (20, True)
    assert amount == 179_200_000


def test_bid_deposit_defaults_to_ten_but_flags_unstated():
    """명시가 없으면 10%로 계산하되 stated=False — 화면은 '통상 10% 가정'을 밝혀야 한다."""
    amount, rate, stated = bid_deposit(896_000_000, "")
    assert (rate, stated) == (10, False)
    assert amount == 89_600_000


# ────────────────────────── 웹 배선 (상세페이지 KPI) ──────────────────────────

def _seed_and_client(tmp_path, remark, min_bid=896_000_000, fail_count=1):
    """비고에 보증금 비율이 있는 물건 1건을 담은 임시 DB + 클라이언트."""
    import json
    import os

    from src import store
    from src.models import ScoredListing
    db = tmp_path / "dep.db"
    conn = store.connect(str(db))
    s = ScoredListing(
        case_no="2025타경507316", apt_name="보증금테스트", address="인천 연수구",
        property_type="아파트", area_m2=129.1, appraisal_price=1_280_000_000,
        min_bid_price=min_bid, fail_count=fail_count, sale_date="2026-07-30",
        est_market_price=1_370_000_000, matched_trades=5, confidence=1.0,
        real_acquisition_cost=910_000_000, expected_profit=460_000_000, gap_rate=0.35,
        gap_score=90.0, rights_score=85.0, liquidity_score=80.0, arb_score=86.0,
        grade="차익 유력", court="인천지방법원", item_no="1", rights_verified=True,
        market_band_low=1_300_000_000, profit_low=390_000_000,
    )
    store.replace_all(conn, [s])
    store.save_rights(conn, [{
        "court": "인천지방법원", "case_no": "2025타경507316", "item_no": "1",
        "surviving_rights": "", "senior_lien": "2021. 5. 3. 근저당권", "lien_note": "",
        "remark": remark, "claim_amt": None, "demand_end": "", "spec_write_ymd": "2026-04-06",
        "court_dept": "", "schedule": json.dumps([], ensure_ascii=False),
        "appraisal_notes": "[]", "fetched_at": "2026-07-23",
    }])
    conn.close()
    os.environ["AUCTION_DB"] = str(db)
    from src.web import create_app
    return create_app().test_client()


def test_detail_uses_court_stated_rate(tmp_path):
    """법원이 20%를 명시하면 화면 금액이 179,200,000원(=8.96억×20%)이어야 한다."""
    body = _seed_and_client(tmp_path, "- 재매각. 매수신청보증금은 최저매각가격의 20%임").get(
        "/property/2025타경507316").get_data(as_text=True)
    assert "법원 명시 20%" in body
    assert "179,200,000원" in body   # 실제 필요액 — 상세 본문은 원 단위 콤마(2026-07-24)
    assert "(통상 10%)" not in body  # 옛 하드코딩 라벨이 남아 있으면 안 됨


def test_detail_no_assumption_label_when_not_stated(tmp_path):
    """비율 미명시 일반 물건은 10%로 계산하되 상시 라벨은 없다(사용자 결정 2026-07-24)."""
    body = _seed_and_client(tmp_path, "아파트로 이용중임").get(
        "/property/2025타경507316").get_data(as_text=True)
    assert "통상 10% 가정" not in body
    assert "89,600,000원" in body


def test_detail_warns_reauction_when_rate_not_stated(tmp_path):
    """비율 미명시 **재매각**(유찰 0회+저감)은 보증금이 실제 20~30%일 수 있어 경고를 유지한다."""
    body = _seed_and_client(tmp_path, "아파트로 이용중임", fail_count=0).get(
        "/property/2025타경507316").get_data(as_text=True)
    assert "재매각 — 20~30% 가능" in body
