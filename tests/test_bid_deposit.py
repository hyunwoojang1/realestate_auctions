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
