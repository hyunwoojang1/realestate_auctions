"""courtauction_rights — 물건상세 권리필드 파서(오프라인 fixture 전용, 네트워크 없음)."""
from __future__ import annotations

from pathlib import Path

from src.courtauction_rights import (
    ParsedRights,
    apply_rights,
    detect_appraisal_amount,
    detect_assumed_amount,
    detect_occupant_type,
    detect_special_rights,
    detect_tenant_opposable,
    gate_reasons,
    parse_rights,
)
from src.models import AuctionListing

FIX = Path(__file__).resolve().parent / "fixtures"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ---- fixture 로딩 ----------------------------------------------------------
def test_fixtures_exist():
    for n in (
        "detail_maegak_myeongsaeseo.txt",
        "detail_hyeonhwang_josaseo.txt",
        "detail_gamjeong_pyeongga.txt",
        "detail_special_rights.txt",
        "detail_clean_vacant.txt",
    ):
        assert (FIX / n).exists(), n


# ---- 대항력 임차인 케이스(첫 번째 fixture 세트) ---------------------------
def test_parse_opposable_tenant_case():
    r = parse_rights(
        myeongsaeseo=_read("detail_maegak_myeongsaeseo.txt"),
        hyeonhwang=_read("detail_hyeonhwang_josaseo.txt"),
        gamjeong=_read("detail_gamjeong_pyeongga.txt"),
    )
    assert r.tenant_opposable is True
    assert r.occupant_type == "임차인"
    assert r.appraisal_amount == 95_000_000
    # 대항력 임차인 보증금(150,000,000)이 인수금액 후보로 잡힘
    assert r.assumed_amount == 150_000_000
    assert r.special_rights == []            # 이 물건엔 특수권리 없음
    assert not r.is_partial                   # 세 문서 모두 제공


def test_appraisal_cross_check():
    assert detect_appraisal_amount(_read("detail_gamjeong_pyeongga.txt")) == 95_000_000
    assert detect_appraisal_amount("") == 0
    assert detect_appraisal_amount("감정평가액 없음") == 0


# ---- 특수권리 다수 케이스 --------------------------------------------------
def test_parse_special_rights_case():
    r = parse_rights(myeongsaeseo=_read("detail_special_rights.txt"))
    # 유치권/법정지상권/지분/분묘기지권/위반건축물 검출(정의 순서)
    assert "유치권" in r.special_rights
    assert "법정지상권" in r.special_rights
    assert "지분" in r.special_rights
    assert "분묘기지권" in r.special_rights
    assert "위반건축물" in r.special_rights
    # 소유자 점유(임차 없음)
    assert r.occupant_type == "소유자점유"
    # 인수금액 80,000,000(유치권 공사대금/인수합계)
    assert r.assumed_amount == 80_000_000
    assert r.is_partial                       # 현황조사서/감정평가서 미제공


def test_special_rights_labels_match_config_keys():
    from src.config import CONFIG
    r = parse_rights(myeongsaeseo=_read("detail_special_rights.txt"))
    # 파서가 뱉는 라벨은 전부 페널티 사전 키에 있어야 스코어에 반영됨
    for label in r.special_rights:
        assert label in CONFIG.special_penalty, label


# ---- 공실/무권리 케이스 ----------------------------------------------------
def test_parse_clean_vacant_case():
    r = parse_rights(myeongsaeseo=_read("detail_clean_vacant.txt"))
    assert r.occupant_type == "공실"
    assert r.special_rights == []
    assert r.tenant_opposable is False
    assert r.assumed_amount == 0


# ---- 개별 detector 단위 검증 ----------------------------------------------
def test_detect_special_rights_dedup_and_order():
    txt = "지분매각이며 유치권 신고. 유치권 성립여부 불분명. 지분 2분의1."
    out = detect_special_rights(txt)
    assert out == ["유치권", "지분"]          # 중복 제거 + 정의 순서


# ---- 감사 2026-07-20 오판 수정 회귀 테스트 -------------------------------
def test_special_rights_detects_gadeungi_gacheobun_byeoldodeungi():
    # C1: 가등기·가처분·토지별도등기가 사전에 없어 배지=clean 오판하던 것 수정
    assert "가등기" in detect_special_rights("선순위 소유권이전청구권가등기 있음. 낙찰로 소멸되지 않음.")
    assert "가처분" in detect_special_rights("소유권 관련 가처분 등기 있음.")
    assert "토지별도등기" in detect_special_rights("토지별도등기 있음(대지권 관련).")


def test_new_special_labels_in_config():
    # 새 라벨도 페널티 사전 키에 있어야 스코어에 반영됨(무배선 방지)
    from src.config import CONFIG
    for label in ("가등기", "가처분", "토지별도등기"):
        assert label in CONFIG.special_penalty, label


def test_special_rights_ignores_nonexistence_context():
    # C2: '유치권 신고 없음'·'성립 여지 없음'을 위험으로 오탐→하드게이트 직행하던 것 수정
    assert detect_special_rights("유치권 신고 없음. 법정지상권 성립 여지 없음.") == []
    # 진짜 신고는 여전히 잡는다(무회귀) — 다른 절의 부존재 표현에 영향받지 않음
    assert "유치권" in detect_special_rights("유치권 신고 있음(공사대금). 법정지상권 성립 여지 없음.")


def test_tenant_opposable_comma_mixed_clause():
    # C4: 한 문장에 인수-해소와 진짜 인수가 콤마로 섞이면 인수신호를 통째로 삼키던 것 수정
    txt = "김철수는 배당으로 전액 충당되어 인수하지 아니하고, 이영희는 대항력 있는 임차인으로 매수인에게 인수됨."
    assert detect_tenant_opposable(txt) is True


def test_tenant_opposable_budam_phrasing():
    # H5: '인수' 대신 '부담' 표현을 쓴 명세서에서 대항력 미탐하던 것 수정
    assert detect_tenant_opposable("배당받지 못한 보증금은 매수인이 부담함.") is True


def test_summarize_extinguished_lease_is_clean():
    # M1(감사 2026-07-20): '임차권등기(다만 말소동의 확약서 제출됨)' = 소멸 예정 → burden 아님(clean).
    # has_risk_text 단독으로 burden 오판해 clean 물건이 차익추천서 빠지던 것 수정.
    from src.courtauction_detail import CaseRights, summarize
    cr = CaseRights(surviving_rights="을구 순위 10번 주택임차권등기(다만 서울보증보험 주식회사의 말소동의 확약서가 제출됨)")
    b = summarize(cr)
    assert b.status == "clean", (b.status, b.special, b.assumed)
    # 진짜 인수 문구는 여전히 burden(무회귀)
    cr2 = CaseRights(surviving_rights="을구 5번 임차권 보증금 100,000,000원 매수인이 인수함")
    assert summarize(cr2).status == "burden"


def test_senior_jeonse_label_penalty_wired():
    # L2: 선순위전세권 라벨이 페널티 사전에 명시돼 default(10) 침묵폴백을 안 타는지
    from src.config import CONFIG
    assert CONFIG.special_penalty.get("선순위전세권") == 20


def test_detect_occupant_priority():
    assert detect_occupant_type("임차인이 점유하며 소유자도 일부 점유") == "임차인"
    assert detect_occupant_type("다수 점유, 임차인 여럿") == "다수점유"
    assert detect_occupant_type("소유자가 점유") == "소유자점유"
    assert detect_occupant_type("점유자 없음(공실)") == "공실"
    # 정보 없음 → 보수적으로 비용 큰 소유자점유
    assert detect_occupant_type("") == "소유자점유"


def test_detect_tenant_opposable_ignores_boilerplate():
    boilerplate = (
        "※ 최선순위 설정일자보다 대항요건을 먼저 갖춘 주택·상가건물 임차인의 "
        "임차보증금은 매수인에게 인수되는 경우가 발생할 수 있고, ..."
    )
    # 표준 경고문만 있으면 대항력 '존재'로 보지 않음
    assert detect_tenant_opposable(boilerplate) is False
    # 구체적 인수 문구가 추가되면 True
    assert detect_tenant_opposable(boilerplate + "\n대항력 있는 임차인 있음.") is True


def test_detect_assumed_amount_picks_max_in_context():
    txt = (
        "임대차 보증금 30,000,000원.\n"
        "매수인이 인수하는 금액 합계 약 금120,000,000원."
    )
    # 인수 문맥 줄의 큰 금액을 택함(30,000,000은 인수문맥 아님)
    assert detect_assumed_amount(txt) == 120_000_000
    assert detect_assumed_amount("인수사항 없음") == 0


def test_detect_assumed_amount_ignores_negation_and_extinguished():
    # '인수' 문맥이지만 부정('없음')/소멸('말소') → 인수액 아님 → 0 (안전물건 오판 방지)
    assert detect_assumed_amount(
        "인수할 권리 없음. 근저당권 채권최고액 300,000,000원 전액 말소 예정.") == 0
    assert detect_assumed_amount("가압류 150,000,000원 소멸(인수 대상 아님).") == 0
    # 진짜 인수 줄은 여전히 잡는다(무회귀).
    assert detect_assumed_amount("매수인이 인수하는 금액 금80,000,000원.") == 80_000_000


# ---- AuctionListing 반영(불변) --------------------------------------------
def _base_listing() -> AuctionListing:
    return AuctionListing(
        case_no="2025타경1352", court="서울중앙지방법원", address="서울 관악구 신림동",
        lawd_cd="11620", dong="신림동", apt_name="파로스프라자", property_type="오피스텔",
        area_m2=33.56, appraisal_price=95_000_000, min_bid_price=76_000_000,
        fail_count=2, sale_date="2026-07-01",
    )


def test_apply_rights_is_immutable_and_maps_fields():
    base = _base_listing()
    r = ParsedRights(
        assumed_amount=150_000_000, special_rights=["유치권"],
        tenant_opposable=True, occupant_type="임차인",
    )
    out = apply_rights(base, r)
    # 원본 불변
    assert base.assumed_amount == 0
    assert base.special_rights == []
    assert base.tenant_opposable is False
    assert base.occupant_type == "소유자점유"   # 모델 기본값(미상은 보수적으로 점유 가정)
    # 새 객체에 반영
    assert out.assumed_amount == 150_000_000
    assert out.special_rights == ["유치권"]
    assert out.tenant_opposable is True
    assert out.occupant_type == "임차인"
    # 권리분석 반영 → rights_verified True(원본은 False 유지)
    assert base.rights_verified is False
    assert out.rights_verified is True
    # 나머지 필드 보존
    assert out.case_no == base.case_no
    assert out.min_bid_price == base.min_bid_price


def test_apply_rights_preserves_maejibun_share_label():
    """(2026-07-24) maejibun 검출 '지분'은 요지 기반 대체 병합에도 보존 — 죽전자이 부활 방지."""
    import dataclasses as _dc
    base = _dc.replace(_base_listing(), special_rights=["지분"])
    out = apply_rights(base, ParsedRights(special_rights=["가처분"]))
    assert "지분" in out.special_rights
    assert "가처분" in out.special_rights
    # 요지에도 지분이 있으면 중복 추가하지 않는다
    out2 = apply_rights(base, ParsedRights(special_rights=["지분"]))
    assert out2.special_rights.count("지분") == 1


def test_apply_rights_backfills_missing_appraisal():
    base = _base_listing()
    base.appraisal_price = 0                    # 리스트에서 감정가 누락 상황
    r = ParsedRights(appraisal_amount=88_000_000)
    out = apply_rights(base, r)
    assert out.appraisal_price == 88_000_000    # 감정평가서 값으로 보정
    # 리스트에 값이 있으면 감정평가서로 덮어쓰지 않음
    base2 = _base_listing()
    out2 = apply_rights(base2, ParsedRights(appraisal_amount=1))
    assert out2.appraisal_price == 95_000_000


# ---- 하드게이트 사유 -------------------------------------------------------
def test_gate_reasons_fatal_and_ratio():
    # 유치권 = 치명 특수권리
    r = ParsedRights(special_rights=["유치권"], assumed_amount=0)
    reasons = gate_reasons(r, min_bid_price=76_000_000)
    assert any("유치권" in x for x in reasons)
    # 인수비율 초과(assumed_ratio_gate 기본 0.30)
    r2 = ParsedRights(assumed_amount=50_000_000)
    reasons2 = gate_reasons(r2, min_bid_price=76_000_000)  # 50/76 = 66% > 30%
    assert any("인수금액 비율" in x for x in reasons2)
    # 깨끗하면 사유 없음
    assert gate_reasons(ParsedRights(), min_bid_price=76_000_000) == []


# ---- 실제 스코어 파이프라인 연동(무회귀 확인) ------------------------------
def test_apply_rights_feeds_score_gate():
    """파싱된 위험이 실제 score.score_listing 하드게이트를 발동시키는지."""
    from src import score
    base = _base_listing()
    r = parse_rights(myeongsaeseo=_read("detail_special_rights.txt"))
    risky = apply_rights(base, r)
    # 유치권 포함 → 권리점수 0, 게이트 상한 적용
    assert score.rights_score(risky) == 0.0
    assert score.is_hard_gated(risky) is True


# ---- 불확실('알 수 없음') vs 부존재('없음') 구분 (2026-07-24 실사고) ------------------

def test_uncertain_is_not_absence_daejikwon():
    """번영로하늘채 실문구 — '대지권 유무는 알 수 없음'의 '없음'이 광역 부정어에 걸려
    부존재로 오분류 → 대지권미등기 미탐 → 온전가 comps로 '차익 유력'(차익 1.17억) 서빙.
    '알 수 없음'은 부존재가 아니라 **불확실** = 위험 신호다(§7 모름≠없음)."""
    t = "대지권 미등기이며 대지권 유무는 알 수 없음. 최저매각가격에 적정대지권을 포함한 가격임."
    assert "대지권미등기" in detect_special_rights(t)


def test_uncertain_variant_rosehill():
    """로즈힐 실문구(장문·복합절) — 같은 클래스."""
    t = ("본건 구문건물의 대지권의 목적인 토지의 표시 중 6 내지 10 토지가 추가되었으나 "
         "이에 관하여 대지권 미등기이며, 대지권 유무는 알 수 없음. "
         "최저매각가격에 위 6 내지 10 토지를 대지권 가격에 포함하여 평가함")
    assert "대지권미등기" in detect_special_rights(t)


def test_true_absence_still_excluded():
    """진짜 부존재 명시는 여전히 미검출 — 불확실 우선 규칙이 오탐을 만들면 안 된다."""
    assert "대지권미등기" not in detect_special_rights("대지권 등기 완료. 해당사항없음.")
    assert "유치권" not in detect_special_rights("유치권 신고 없음")
    assert "유치권" not in detect_special_rights("유치권 성립 여지 없음")


def test_uncertain_lien_is_flagged():
    """유치권도 동일 규범 — '성립 여부는 알 수 없음'은 위험(불확실)이지 부존재가 아니다."""
    assert "유치권" in detect_special_rights("유치권 신고가 있으나 그 성립 여부는 알 수 없음")


# ---- '선순위 전입' 약신호 (2026-07-24 한울아파트 실사고) ------------------------------

def test_senior_movein_weak_signal():
    """한울아파트 실문구 — 법원이 명세서 비고에 '선순위 전입 임차인 있음'을 적었는데
    사전에 없어 clean 통과('양호' 서빙). 해소 표현이 없으면 대항력 여지로 본다."""
    assert detect_tenant_opposable("선순위 전입 임차인 있음") is True
    assert detect_tenant_opposable("선순위전입 세대 있음") is True


def test_senior_movein_released_is_safe():
    """해소 표현(대항력 포기 등)이 있으면 약신호는 발동하지 않는다 — HUG 포기조건 보호."""
    assert detect_tenant_opposable(
        "선순위 전입 임차인 있음. 임차인 및 임차권승계인 주택도시보증공사의 "
        "매수인에 대한 대항력 포기조건 매각") is False
