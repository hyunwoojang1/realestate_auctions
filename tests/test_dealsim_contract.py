"""딜 시뮬 엔진(static/dealsim.js) 계약 테스트 — 황금 케이스는 전부 손 검산(2026-07-24).

엔진은 JS 단일 구현(D9)이고 세율은 data/dealsim_rules.json 단일 출처다. 파이썬 쪽에서
node 로 실제 JS 를 실행해 수기 검산값과 대조한다 — 세율표가 바뀌면 여기가 먼저 깨져야 한다.
개인 컬럼의 비용모델(인지세·등기·중개보수·장특공)은 src/bidsim.py 와 1:1 —
그 정합은 test_dealsim_crosscheck.py 가 별도로 지킨다.
이 테스트가 깨지면 (a) 세율 데이터가 바뀌었거나 (b) 엔진 로직 회귀 — 확인 후 황금값을 갱신하라.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node 미설치 — 계약 테스트 불가(설치 필요)")


def run(fn: str, *args):
    req = json.dumps({"fn": fn, "args": list(args)}, ensure_ascii=False)
    p = subprocess.run([NODE, str(ROOT / "tests" / "run_dealsim.js")],
                       input=req.encode("utf-8"), capture_output=True, timeout=30)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    return json.loads(p.stdout.decode("utf-8"))


R = "__RULES__"  # 하네스가 data/dealsim_rules.json 으로 치환


# ── 취득세 (지방세법 §11·§13조의2 검증본) ──────────────────────────────

def test_acq_individual_1home_3eok():
    """3억 · 1주택 · 84㎡: 1% + 교육세 0.1% = 330만."""
    r = run("acquisitionTax", R, {"price": 300_000_000, "isHousing": True, "profile": "individual",
                                  "adjusted": False, "housesAfter": 1, "areaM2": 84})
    assert r["breakdown"] == {"main": 3_000_000, "edu": 300_000, "farm": 0}
    assert r["total"] == 3_300_000 and r["surcharged"] is False


def test_acq_individual_linear_zone_over85():
    """7.5억(6~9억 선형구간 → 2%) · 85㎡ 초과: 농특세 0.2% 가산 = 1,800만."""
    r = run("acquisitionTax", R, {"price": 750_000_000, "isHousing": True, "profile": "individual",
                                  "adjusted": False, "housesAfter": 1, "areaM2": 101})
    assert r["breakdown"] == {"main": 15_000_000, "edu": 1_500_000, "farm": 1_500_000}
    assert r["total"] == 18_000_000


def test_acq_individual_2nd_home_adjusted():
    """조정지역 2주택째 4억: 8% 중과 + 교육세 0.4% = 3,360만."""
    r = run("acquisitionTax", R, {"price": 400_000_000, "isHousing": True, "profile": "individual",
                                  "adjusted": True, "housesAfter": 2, "areaM2": 59})
    assert r["breakdown"] == {"main": 32_000_000, "edu": 1_600_000, "farm": 0}
    assert r["total"] == 33_600_000 and r["surcharged"] is True


def test_acq_corp_housing():
    """법인 주택 3억 · 84㎡: 12% + 0.4% = 3,720만 (실효 12.4%)."""
    r = run("acquisitionTax", R, {"price": 300_000_000, "isHousing": True, "profile": "corp",
                                  "adjusted": False, "housesAfter": 1, "areaM2": 84})
    assert r["total"] == 37_200_000 and r["surcharged"] is True


def test_acq_non_housing():
    """비주택(상가) 2억: 일률 4.6% = 920만."""
    r = run("acquisitionTax", R, {"price": 200_000_000, "isHousing": False, "profile": "individual",
                                  "adjusted": False, "housesAfter": 1, "areaM2": 200})
    assert r["total"] == 9_200_000


# ── 거래 부대비 (§5 — bidsim 동일 상수) ───────────────────────────────

def test_stamp_tax_brackets():
    assert run("stampTax", R, 10_000_000) == 0
    assert run("stampTax", R, 50_000_000) == 40_000
    assert run("stampTax", R, 300_000_000) == 150_000
    assert run("stampTax", R, 1_500_000_000) == 350_000


def test_agent_fee_brackets():
    assert run("agentFee", R, 40_000_000, "housing", 84) == 240_000       # 0.6%, 한도 25만 미달
    assert run("agentFee", R, 50_000_000, "housing", 84) == 250_000       # 경계: <5천만 탈락 → 0.5%·한도 80만
    assert run("agentFee", R, 356_000_000, "housing", 84) == 1_424_000    # 0.4%
    assert run("agentFee", R, 1_000_000_000, "housing", 84) == 5_000_000  # 0.5%
    assert run("agentFee", R, 300_000_000, "officetel", 84) == 1_500_000  # 주거용 오피 0.5%
    assert run("agentFee", R, 200_000_000, "nonhousing", 0) == 1_800_000  # 0.9%


def test_long_term_deduction():
    assert run("longTermDeductionRate", R, 30) == 0        # 3년 미만
    assert run("longTermDeductionRate", R, 36) == 0.06
    assert run("longTermDeductionRate", R, 200) == 0.30    # 상한


# ── 소득세 브래킷 (소법 §55 · 법인세 2026 구간 검증본) ────────────────

def test_basic_income_tax_brackets():
    assert run("basicIncomeTax", R, 14_000_000) == 840_000            # 6%
    assert run("basicIncomeTax", R, 50_000_000) == 6_240_000          # 15% − 126만
    assert run("basicIncomeTax", R, 100_000_000) == 19_560_000        # 35% − 1,544만
    assert run("basicIncomeTax", R, 0) == 0


def test_corp_income_tax_2026_brackets():
    assert run("corpIncomeTax", R, 150_000_000) == 15_000_000         # ≤2억 10%
    assert run("corpIncomeTax", R, 300_000_000) == 40_000_000         # 2억×10% + 1억×20%


# ── 매도 세금: 프로필 3종의 갈림(이 기능의 심장) ──────────────────────
# 공통 딜: 3억 낙찰 → 3.56억 매도 · 6개월 · 비조정 · 1주택.
# 부대비 검산: 인지세 15만 · 등기 50만 · 중개보수 142.4만 · 이자 577.5만(2.1억×5.5%×½)

_SALE_COMMON = {"salePrice": 356_000_000, "holdMonths": 6, "housingForTransfer": True,
                "adjusted": False, "housesAfter": 1, "bid": 300_000_000,
                "acqTaxTotal": 3_300_000, "stamp": 150_000, "registryCost": 500_000,
                "agentFee": 1_424_000, "assumedAmount": 0, "evictCost": 1_000_000,
                "unpaidMgmt": 0, "repairCost": 0, "interest": 5_775_000,
                "applyCorpAuctionExclusion": False}


def test_sale_individual_short_70pct():
    """개인: 경비=취득세+인지세+등기+중개보수(명도·이자 불산입) → 과표 4,813만 × 70%."""
    r = run("saleTax", R, {**_SALE_COMMON, "profile": "individual"})
    # gain = 356M − 300M − 5,374,000 = 50,626,000 → −기본공제 250만 → 48,126,000 × 0.7
    assert r["national"] == 33_688_200 and r["local"] == 3_368_820
    assert "70%" in r["method"]


def test_sale_dealer_basic_rate_flip():
    """매매사업자 같은 딜: 중과대상 아님 → 기본세율 종합과세(비교과세 미적용 — §64 원문 확정).

    경비 폭넓게(명도·이자·중개보수 포함) → 차익 43,851,000 → 15% 구간.
    개인 70%(3,706만) 대비 1/6 수준 — 단기 매매사업자 전략의 세법상 핵심 이점을 고정한다.
    """
    r = run("saleTax", R, {**_SALE_COMMON, "profile": "dealer"})
    assert r["national"] == 5_317_650 and r["total"] == 5_849_415
    ind = run("saleTax", R, {**_SALE_COMMON, "profile": "individual"})
    assert r["total"] < ind["total"] / 5


def test_sale_dealer_comp_tax_when_multi_adjusted():
    """조정지역 + 세대 2주택 → 비교과세. §64 비교 후보의 과표는 **양도소득 방식**이다.

    (감사 2026-07-24 확정) 광의 사업경비 차익으로 비교하면 세액 과소(낙관) — 주택등매매차익 =
    매매가액 − §97 필요경비(취득세·인지세·등기·중개보수·인수금) − 기본공제 250만 = 48,126,000
    → ×70% = 개인 단기세액과 동일해야 한다.
    """
    r = run("saleTax", R, {**_SALE_COMMON, "profile": "dealer", "adjusted": True, "housesAfter": 2})
    assert r["national"] == 33_688_200
    ind = run("saleTax", R, {**_SALE_COMMON, "profile": "individual"})
    assert r["national"] == ind["national"]  # 같은 양도방식 과표 × 같은 70%
    assert "비교과세" in r["method"]


def test_sale_corp_with_and_without_addtax():
    """법인: 본세는 광의 경비 차익, **추가과세 20%p 는 양도가액−장부가액**(§55조의2).

    (감사 2026-07-24 확정) 이자·중개보수·명도비는 장부가액이 아니다 — 추가과세 과표
    = 356M − (낙찰가+취득세+인지세+등기) 303.95M = 52,050,000.
    """
    r = run("saleTax", R, {**_SALE_COMMON, "profile": "corp"})
    assert r["national"] == 4_385_100 + 10_410_000  # 본세(≤2억 10%) + 52,050,000×20%
    r2 = run("saleTax", R, {**_SALE_COMMON, "profile": "corp", "applyCorpAuctionExclusion": True})
    assert r2["national"] == 4_385_100
    assert any("유권해석" in n for n in r2["notes"])


def test_sale_individual_multi_surcharge_long():
    """개인 · 조정지역 2주택 · 2년 이상: 기본세율+20%p (2026-05-10 중과 재개), 장특공 배제."""
    args = {**_SALE_COMMON, "profile": "individual", "adjusted": True, "housesAfter": 2,
            "holdMonths": 30, "salePrice": 406_300_000, "agentFee": 1_625_200}
    # gain = 406.3M − 300M − 5,575,200 = 100,724,800 → −250만 = 98,224,800 → 55% − 1,544만
    r = run("saleTax", R, args)
    assert r["national"] == round(98_224_800 * 0.55) - 15_440_000
    assert "중과" in r["method"]


# ── 재산세 약식 · 6/1 판정 · 잔금 타임라인 ────────────────────────────

def test_property_tax_approx():
    """시세 5억 → 공시 3.5억(×0.7 가정) → 과표 2.1억 → 재산세+도시지역분+교육세 = 70.8만."""
    r = run("propertyTaxApprox", R, 500_000_000)
    assert r["breakdown"] == {"main": 345_000, "city": 294_000, "edu": 69_000}
    assert r["total"] == 708_000 and r["assumption"]


def test_spans_june1():
    assert run("spansJune1", "2026-05-20", 1) is True     # 5/20→6/20, 6/1 포함
    assert run("spansJune1", "2026-06-05", 11) is False   # 다음 6/1 직전에 매도
    assert run("spansJune1", "2026-06-05", 12) is True


def test_count_june1_multi_year():
    """다년 보유는 6/1 을 여러 번 지난다 — 재산세는 연도 수만큼 (감사 2026-07-24 확정)."""
    assert run("countJune1", "2026-05-20", 1) == 1
    assert run("countJune1", "2026-05-20", 30) == 3       # 2026·2027·2028년 6/1
    assert run("countJune1", "2026-06-05", 12) == 1
    # simulate 경로에서도 곱해지는지 — 30개월 재산세 = 6개월 재산세 × 3
    f = {**_FACTS, "saleDate": "2026-04-01"}              # 잔금 ~5/15 → 6/1 걸림
    r6 = run("simulateProfile", R, f, _INPUTS, "individual", 6)
    r30 = run("simulateProfile", R, f, _INPUTS, "individual", 30)
    assert r6["propTax"] > 0 and r30["propTax"] == r6["propTax"] * 3


def test_timeline_dates():
    r = run("timeline", "2026-08-10")
    assert r["permit_date"] == "2026-08-17"
    assert r["confirm_date"] == "2026-08-24"
    assert r["payment_deadline"] == "2026-09-23"  # KST 자정→UTC 하루밀림 버그 회귀 가드


# ── 통합: simulate / breakeven ────────────────────────────────────────

_FACTS = {"isHousingAcq": True, "housingForTransfer": True, "feeKind": "housing", "areaM2": 84,
          "assumedAmount": 0, "burdenUnknown": False, "saleDate": "2026-08-10",
          "regulated": {"adjusted": False, "landPermit": False}}
_INPUTS = {"bidPrice": 300_000_000, "loanRatio": 0.70, "interestRate": 0.055, "holdMonths": 6,
           "salePrice": 356_000_000, "housesOwned": 0, "evictCost": 1_000_000, "unpaidMgmt": 0,
           "repairCost": 0, "applyCorpAuctionExclusion": False}


def test_simulate_structure_and_dealer_advantage():
    r = run("simulate", R, _FACTS, _INPUTS)
    assert set(r["matrix"].keys()) == {"individual", "dealer", "corp"}
    assert [m["holdMonths"] for m in r["matrix"]["individual"]] == [6, 18, 30]
    cur = r["current"]
    # 비조정 · 1주택 단기: 매매사업자 세후익 > 개인 (기본세율 vs 70%)
    assert cur["dealer"]["netProfit"] > cur["individual"]["netProfit"]
    # 법인은 취득세 12.4%가 선반영되어 실투자금 자체가 큼
    assert cur["corp"]["totalIn"] > cur["individual"]["totalIn"]
    assert any("세무 조언 아님" in w for w in r["warnings"])


def test_simulate_burden_unknown_warning():
    r = run("simulate", R, {**_FACTS, "burdenUnknown": True}, _INPUTS)
    assert any("하한이 아님" in w for w in r["warnings"])


def test_breakeven_is_zero_crossing():
    """손익분기 매도가에서 실제 순익이 ~0 이어야 한다(역산 정합)."""
    be = run("breakeven", R, _FACTS, _INPUTS, "dealer", 6)
    r = run("simulateProfile", R, _FACTS, {**_INPUTS, "salePrice": be}, "dealer", 6)
    assert abs(r["netProfit"]) < 50_000
    # 개인은 세율이 높아 손익분기 매도가가 더 높다
    be_ind = run("breakeven", R, _FACTS, _INPUTS, "individual", 6)
    assert be_ind > be


def test_breakeven_bracket_expansion():
    """근이 초기 상한 밖(큰 인수액 + 매도가 0)이어도 정확히 수렴 — 상한 배증 확장.

    (감사 2026-07-24 확정) 종전엔 상한값(임의 캡)이 그대로 반환돼 손익분기가 1억+ 낮게 표시.
    시세추정 없는 물건의 초기 상태(sm-sell=0, 인수액 선채움)가 실제 트리거였다.
    """
    facts = {**_FACTS, "assumedAmount": 250_000_000}
    inputs = {**_INPUTS, "bidPrice": 50_000_000, "salePrice": 0}
    be = run("breakeven", R, facts, inputs, "individual", 6)
    assert be is not None
    r = run("simulateProfile", R, facts, {**inputs, "salePrice": be}, "individual", 6)
    assert abs(r["netProfit"]) < 50_000               # 캡 반환이 아니라 진짜 0-교차
    assert be > 250_000_000                            # 인수액보다 큰 참값 영역


def test_vat_warning_covers_nonhousing_any_size():
    """부가세 경고는 85㎡ 초과 주택만이 아니라 상가·업무 오피스텔(면적 무관)도 (감사 확정)."""
    f = {**_FACTS, "isHousingAcq": False, "housingForTransfer": False,
         "feeKind": "nonhousing", "areaM2": 60}
    r = run("simulate", R, f, _INPUTS)
    assert any("부가세" in w for w in r["warnings"])


def test_rights_unverified_warning():
    """권리분석 미확인 물건은 '기본 가정 계산' 경고 필수 — 무경고 낙관 금지 (감사 확정)."""
    r = run("simulate", R, {**_FACTS, "rightsUnverified": True}, _INPUTS)
    assert any("권리분석 미확인" in w for w in r["warnings"])
