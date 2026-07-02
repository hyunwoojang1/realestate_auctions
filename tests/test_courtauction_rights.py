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
    assert base.occupant_type == "공실"
    # 새 객체에 반영
    assert out.assumed_amount == 150_000_000
    assert out.special_rights == ["유치권"]
    assert out.tenant_opposable is True
    assert out.occupant_type == "임차인"
    # 나머지 필드 보존
    assert out.case_no == base.case_no
    assert out.min_bid_price == base.min_bid_price


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
