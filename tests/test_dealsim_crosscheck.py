"""딜 시뮬(JS) ↔ 입찰가 시뮬레이터(bidsim.py) 교차검증 — 같은 페이지, 같은 숫자.

두 엔진은 질문이 다르지만(개인 정밀 vs 프로필 3종 비교) **개인 컬럼의 비용모델은 1:1**이어야
한다 — 한 페이지에서 두 계산기가 다른 답을 내면 신뢰가 무너진다(U-04/U-05 감사 교훈).
인수금 0 조건에서 두 구현의 결과가 일치하는지 상호 대조한다(어느 쪽이 회귀해도 여기가 깨진다).

알려진 의도적 차이(테스트 제외 조건): 인수 보증금 — dealsim 은 취득가액 인정(국심 2006서3481,
원문 검증 확정)으로 개인 필요경비에 포함하고 bidsim 은 미포함(과대 세금 쪽 보수).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src import bidsim

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node 미설치")

# round() 는 은행가 반올림, Math.round 는 반올림 — 연산 몇 번 누적 오차 허용
TOL = 2


def run_js(fn: str, *args):
    req = json.dumps({"fn": fn, "args": list(args)}, ensure_ascii=False)
    p = subprocess.run([NODE, str(ROOT / "tests" / "run_dealsim.js")],
                       input=req.encode("utf-8"), capture_output=True, timeout=30)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    return json.loads(p.stdout.decode("utf-8"))


def _pair(bid, sell, months, evict=1_000_000, repair=0, unpaid=0,
          mode=bidsim.TAX_MODE_INDIVIDUAL, js_profile="individual"):
    inp = bidsim.SimInput(
        bid_price=bid, property_type="아파트", area_m2=84.0, sell_price=sell,
        holding_months=months, assumed_amount=0, eviction_cost=evict, repair_cost=repair,
        unpaid_fees=unpaid, registry_cost=500_000, loan_ltv=0.70, loan_rate=0.055,
        tax_mode=mode)
    py = bidsim.simulate(inp)
    facts = {"isHousingAcq": True, "housingForTransfer": True, "feeKind": "housing",
             "areaM2": 84, "assumedAmount": 0, "burdenUnknown": False,
             "regulated": {"adjusted": False, "landPermit": False}}
    inputs = {"bidPrice": bid, "loanRatio": 0.70, "interestRate": 0.055,
              "salePrice": sell, "housesOwned": 0, "evictCost": evict,
              "unpaidMgmt": unpaid, "repairCost": repair, "registryCost": 500_000}
    js = run_js("simulateProfile", "__RULES__", facts, inputs, js_profile, months)
    return py, js


@pytest.mark.parametrize(("bid", "sell", "months"), [
    (300_000_000, 356_000_000, 6),    # 1년 미만 70%
    (300_000_000, 380_000_000, 18),   # 1~2년 60%
    (300_000_000, 406_300_000, 30),   # 기본세율(장특공 3년 미만 0)
    (150_000_000, 200_000_000, 40),   # 기본세율 + 장특공 6%
    (500_000_000, 480_000_000, 6),    # 손해 매도 — 양도세 0
])
def test_individual_matches_bidsim(bid, sell, months):
    py, js = _pair(bid, sell, months)
    assert js["acquisition"]["total"] == py.acquisition_tax
    assert js["stamp"] == py.stamp_tax
    assert js["agentFee"] == py.agent_fee
    assert js["totalIn"] == py.total_acquisition
    assert js["loan"] == py.loan_amount and js["cash"] == py.equity
    assert abs(js["interest"] - py.interest_total) <= TOL
    assert abs(js["saleTax"]["total"] - py.transfer_tax) <= TOL
    # 재산세는 dealsim 만 계산(6/1 걸림) — 비교는 재산세 제외 순익으로
    assert abs((js["netProfit"] + js["propTax"]) - py.net_profit) <= TOL


# ── 매매사업자 모드(2026-07-27 기본값) ────────────────────────────────────────
# 시뮬레이터 기본 세금 기준이 개인 양도세 → 매매사업자 종합과세로 바뀌었다. 파이썬 포팅이
# 이미 원문 검증된 JS dealer 분기와 어긋나면 한 페이지에서 두 계산기가 다른 답을 낸다.


@pytest.mark.parametrize(("bid", "sell", "months"), [
    (300_000_000, 356_000_000, 6),    # 개인이면 단기 70% 구간 — 여기서 차이가 가장 크다
    (300_000_000, 380_000_000, 18),   # 개인이면 60%
    (300_000_000, 406_300_000, 30),   # 2년 초과
    (150_000_000, 200_000_000, 40),
    (500_000_000, 480_000_000, 6),    # 손해 매도 — 세금 0
])
def test_dealer_matches_dealsim(bid, sell, months):
    py, js = _pair(bid, sell, months,
                   mode=bidsim.TAX_MODE_DEALER, js_profile="dealer")
    assert abs(js["saleTax"]["total"] - py.transfer_tax) <= TOL
    assert abs((js["netProfit"] + js["propTax"]) - py.net_profit) <= TOL


def test_dealer_beats_individual_on_short_hold():
    """매매사업자의 핵심 이점 — 6개월 보유에서 개인 단기 70%를 타지 않는다."""
    ind, _ = _pair(300_000_000, 356_000_000, 6)
    dea, _ = _pair(300_000_000, 356_000_000, 6,
                   mode=bidsim.TAX_MODE_DEALER, js_profile="dealer")
    assert dea.transfer_tax < ind.transfer_tax
    assert dea.net_profit > ind.net_profit


def test_none_mode_charges_no_sale_tax():
    """'세금 미계산' — 매도 세금만 0. 이자·중개보수는 그대로 차감된다(취득세는 취득원가라 유지)."""
    none, _ = _pair(300_000_000, 356_000_000, 6, mode=bidsim.TAX_MODE_NONE)
    ind, _ = _pair(300_000_000, 356_000_000, 6)
    assert none.transfer_tax == 0
    assert none.acquisition_tax == ind.acquisition_tax      # 취득세는 세금이지만 취득 원가
    assert none.interest_total == ind.interest_total > 0
    assert none.agent_fee == ind.agent_fee > 0
    # 순익 = 매도가 − 총투입 − 이자 − 중개보수 (세금 없음)
    assert none.net_profit == (356_000_000 - none.total_acquisition
                               - none.interest_total - none.agent_fee)
