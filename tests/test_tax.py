"""취득세 엔진 테스트 — docs/tax-auction-knowledge.md 수치를 코드에 고정한다.

이 테스트가 깨지면 (a) 세법 개정 반영 중이거나 (b) 실수로 세율이 변형된 것.
어느 쪽이든 지식문서와 tax.py를 함께 갱신해야 한다.
"""
import json

import pytest

from src import tax
from src.tax import BuyerProfile, acquisition_tax, acquisition_tax_breakdown, effective_rates

P1 = BuyerProfile()                                     # 1주택·비조정·개인 (기본)
P2_REG = BuyerProfile(houses_after=2, regulated_area=True)    # 조정 2주택 → 8%
P3_REG = BuyerProfile(houses_after=3, regulated_area=True)    # 조정 3주택 → 12%
P3_UNREG = BuyerProfile(houses_after=3, regulated_area=False)  # 비조정 3주택 → 8%
P4_UNREG = BuyerProfile(houses_after=4, regulated_area=False)  # 비조정 4주택 → 12%
CORP = BuyerProfile(is_corporation=True)                # 법인 → 12%


def _total_rate(price, ptype, area, profile):
    return sum(effective_rates(price, ptype, area, profile))


# ---- 주택 기본세율 (합계: 취득+교육+농특) ----

def test_housing_base_under_600m():
    # 6억 이하 85㎡ 이하 = 1% + 0.1% + 0 = 1.1%
    assert _total_rate(500_000_000, "아파트", 84.9, P1) == pytest.approx(0.011)
    assert _total_rate(600_000_000, "아파트", 84.9, P1) == pytest.approx(0.011)


def test_housing_base_over_85m2_adds_rural():
    # 85㎡ 초과 농특세 0.2%p 가산 → 1.3%
    assert _total_rate(500_000_000, "아파트", 101.0, P1) == pytest.approx(0.013)


def test_housing_base_600m_to_900m_statutory_formula():
    # 법정 산식 (가액×2/3억−3)%: 7.5억 → 본세 2%, 교육세 0.2% → 2.2%
    assert _total_rate(750_000_000, "아파트", 84.9, P1) == pytest.approx(0.022)
    # 산식 연속성: 6억 바로 위는 1%에 근접(불연속 절벽 없음 — 구간 flat 모델과의 차이)
    just_over = _total_rate(600_000_001, "아파트", 84.9, P1)
    assert 0.0109 < just_over < 0.0112
    # 9억 직전은 3%에 근접
    near_900 = _total_rate(899_999_999, "아파트", 84.9, P1)
    assert 0.0325 < near_900 < 0.0331


def test_housing_base_over_900m():
    # 9억 초과 = 3% + 0.3% = 3.3% (85 초과면 +0.2%p)
    assert _total_rate(1_000_000_000, "아파트", 84.9, P1) == pytest.approx(0.033)
    assert _total_rate(1_000_000_000, "아파트", 120.0, P1) == pytest.approx(0.035)


# ---- 다주택·법인 중과 (지식문서 §1-2·§1-4) ----

def test_surcharge_8pct_regulated_2houses():
    # 8% + 0.4% (+85 초과 0.6%) = 8.4% / 9.0%
    assert _total_rate(500_000_000, "아파트", 84.9, P2_REG) == pytest.approx(0.084)
    assert _total_rate(500_000_000, "아파트", 101.0, P2_REG) == pytest.approx(0.090)


def test_surcharge_12pct_regulated_3houses_and_corp():
    # 12% + 0.4% (+85 초과 1.0%) = 12.4% / 13.4%
    assert _total_rate(500_000_000, "아파트", 84.9, P3_REG) == pytest.approx(0.124)
    assert _total_rate(500_000_000, "아파트", 101.0, P3_REG) == pytest.approx(0.134)
    assert _total_rate(500_000_000, "아파트", 84.9, CORP) == pytest.approx(0.124)


def test_surcharge_unregulated_ladder():
    # 비조정: 2주택=기본, 3주택=8%, 4주택+=12%
    two = BuyerProfile(houses_after=2, regulated_area=False)
    assert _total_rate(500_000_000, "아파트", 84.9, two) == pytest.approx(0.011)
    assert _total_rate(500_000_000, "아파트", 84.9, P3_UNREG) == pytest.approx(0.084)
    assert _total_rate(500_000_000, "아파트", 84.9, P4_UNREG) == pytest.approx(0.124)


# ---- 비주택 (오피스텔·상가·토지 = 4.6%, 주택 수 무관) ----

def test_nonhousing_flat_46():
    for ptype in ("오피스텔", "상가", "토지", "근린생활시설", "임야"):
        assert _total_rate(500_000_000, ptype, 84.9, P1) == pytest.approx(0.046)
    # 다주택이어도 비주택은 중과 없음
    assert _total_rate(500_000_000, "오피스텔", 30.0, P3_REG) == pytest.approx(0.046)


# ---- 분해·합계 ----

def test_breakdown_parts_sum_to_total():
    b = acquisition_tax_breakdown(750_000_000, "아파트", 101.0, P1)
    assert b["취득세"] + b["지방교육세"] + b["농어촌특별세"] == b["합계"]
    assert b["합계"] == acquisition_tax(750_000_000, "아파트", 101.0, P1)
    assert b["취득세"] == round(750_000_000 * 0.02)
    assert b["농어촌특별세"] == round(750_000_000 * 0.002)


# ---- 프로필 로드 ----

def test_load_profile_default_when_missing(tmp_path):
    p = tax.load_profile(tmp_path / "없는파일.json")
    assert p == BuyerProfile()
    assert p.label() == "1주택·비조정"


def test_load_profile_from_json(tmp_path):
    f = tmp_path / "buyer_profile.json"
    f.write_text(json.dumps({"houses_after": 3, "regulated_area": True}), encoding="utf-8")
    p = tax.load_profile(f)
    assert p.houses_after == 3 and p.regulated_area is True
    assert p.label() == "3주택·조정"


def test_load_profile_malformed_falls_back(tmp_path):
    f = tmp_path / "buyer_profile.json"
    f.write_text("{잘못된 json", encoding="utf-8")
    assert tax.load_profile(f) == BuyerProfile()
