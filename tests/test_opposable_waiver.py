"""P-14 회귀 — '대항력 포기' 확약의 **조사 변형**을 부정 신호로 인식한다.

감사 발견(2026-07-23): 부정 목록이 "대항력 포기"·"대항력을 포기"만 담고 있어
실측 최다 표기인 **"대항력은 포기"(574건)** 를 놓쳤다. 그 결과 HUG·주택금융공사가
대항력을 포기한 물건이 '대항력 있음'으로 남아 등급이 부당하게 강등됐다(248건 중 58건).
"""
import pytest

from src.courtauction_rights import detect_tenant_opposable

# 실측 원문(마스킹) — 비고란에 승계인이 대항력을 포기한다고 확약한 케이스
HUG_REMARK = ("주택도시보증공사(양도전:임차권자 [성명])로부터 2026.05.11.자에 "
              "'임차보증금에 대하여 우선변제권만 주장하고 대항력은 포기하며, "
              "전액을 변제받지 못하더라도 매수인에게 대항하지 아니한다'는 확약서가 제출됨")
HF_REMARK = ("한국주택금융공사로부터 2026.03.20.자에 '임차권을 승계받은 한국주택금융공사는 "
             "우선변제권만 주장하고 대항력은 포기하며, 임차보증금반환채권 전액을 변제받지 "
             "못하더라도 매수인에게 대항하지 아니함'을 확약함")


@pytest.mark.parametrize("waiver", [
    "대항력은 포기하며",      # 실측 최다(574건) — 종전 미탐
    "대항력 포기",
    "대항력을 포기",
    "대항력포기",              # 공백 없음(30건)
    "대항력을 전부 포기",      # 부사 삽입(2건)
])
def test_waiver_particle_variants_suppress_opposable(waiver):
    """임차권등기(약한 신호)가 있어도 포기 확약이 있으면 대항력 없음."""
    text = f"을구 3번 주택임차권등기. {waiver}한다는 확약서 제출."
    assert detect_tenant_opposable(text) is False


def test_real_hug_and_hf_remarks():
    """실측 비고 원문(HUG·주금공 승계 확약) — 임차권등기 언급이 있어도 대항력 없음."""
    for remark in (HUG_REMARK, HF_REMARK):
        assert detect_tenant_opposable("을구 5번 주택임차권등기", remark) is False


def test_waiver_does_not_swallow_real_burden_in_other_clause():
    """혼재 명세서: 한 절은 포기 확약, 다른 절은 진짜 인수 — 인수 신호는 살아남아야 한다."""
    text = ("을구 3번 임차권등기는 대항력은 포기하며 말소 예정. "
            "을구 7번 임차권등기는 매수인에게 인수됨.")
    assert detect_tenant_opposable(text) is True


def test_plain_opposable_still_detected():
    """포기 문구가 없으면 종전대로 대항력 있음(과잉 완화 방지)."""
    assert detect_tenant_opposable("매수인에게 대항할 수 있는 을구 10번 임차권등기 있음") is True
    assert detect_tenant_opposable("을구 4번 주택임차권등기") is True
