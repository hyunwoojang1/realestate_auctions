"""courtauction_fields — 실데이터 fixture 파싱·개인정보 가드·모델변환 검증(네트워크 없음)."""
from __future__ import annotations

import json
from pathlib import Path

from src.courtauction_fields import (
    FIELD_LABELS,
    CourtAuctionRecord,
    classify_property_type,
    is_personal_field,
    parse_area_m2,
    parse_row,
    sanitize_row,
    to_auction_listing,
    to_won,
    ymd_to_iso,
)
from src.models import AuctionListing

DATA = Path(__file__).resolve().parent.parent / "data"


def _rows() -> list[dict]:
    j = json.loads((DATA / "sample_courtauction.json").read_text(encoding="utf-8"))
    return j["data"]["dlt_srchResult"]


def test_fixture_loads_rows():
    rows = _rows()
    assert len(rows) == 26
    assert len(rows[0]) == 117  # 전체 필드 보존 확인


def test_parse_first_row_core_fields():
    rec = parse_row(_rows()[0])
    assert rec.case_no == "2025타경1352"
    assert rec.court == "서울중앙지방법원"
    assert rec.dept == "경매8계"
    assert rec.appraisal_price == 95_000_000
    assert rec.min_bid_price == 76_000_000
    assert rec.fail_count == 2
    assert rec.area_m2 == 33.56
    assert rec.lawd_cd == "11620"          # 국토부 LAWD_CD 5자리
    assert rec.sale_date == "2026-07-01"
    assert rec.building_name == "파로스프라자"
    assert rec.dong == "신림동"


def test_raw_preserves_all_nonpii_fields():
    rec = parse_row(_rows()[0])
    # 리스트 응답엔 개인정보 없음 → 117필드 그대로 보존
    assert len(rec.raw) == 117
    assert "tel" in rec.raw            # 기관 전화는 보존
    assert rec.raw["gamevalAmt"] == "95000000"


def test_personal_info_guard_strips_natural_person_fields():
    assert is_personal_field("ownrNm")
    assert is_personal_field("debtorNm")
    assert is_personal_field("juminNo")
    assert is_personal_field("lesseeNm")
    # 키 변형도 토큰으로 차단(물건상세 확장 대비)
    assert is_personal_field("ownerNm")
    assert is_personal_field("debtorName")
    assert is_personal_field("creditorNm")
    assert is_personal_field("dpryAddr")
    assert is_personal_field("obligorNm")
    # 기관/물건 이름은 'Nm' 접미사를 공유하지만 개인정보 아님 — 보존돼야 함
    assert not is_personal_field("tel")
    assert not is_personal_field("jiwonNm")
    assert not is_personal_field("jpDeptNm")
    assert not is_personal_field("dspslUsgNm")
    assert not is_personal_field("buldNm")
    assert not is_personal_field("gamevalAmt")
    dirty = {"gamevalAmt": "1", "ownrNm": "홍길동", "juminNo": "900101-1", "tel": "02-0000"}
    clean = sanitize_row(dirty)
    assert "ownrNm" not in clean and "juminNo" not in clean
    assert clean == {"gamevalAmt": "1", "tel": "02-0000"}


def test_free_text_name_masking_in_bigo():
    # 비고 자유텍스트의 '채무자 홍길동' 류 성명만 마스킹, 나머지는 보존
    dirty = {"mulBigo": "일괄매각, 채무자 홍길동 점유, 소유자 김철수"}
    clean = sanitize_row(dirty)
    assert "홍길동" not in clean["mulBigo"]
    assert "김철수" not in clean["mulBigo"]
    assert "일괄매각" in clean["mulBigo"]      # 공시정보는 보존
    assert "채무자 [성명]" in clean["mulBigo"]


def test_discount_vs_appraisal():
    rec = parse_row(_rows()[0])
    # (95,000,000 - 76,000,000)/95,000,000 = 0.2
    assert abs(rec.discount_vs_appraisal - 0.2) < 1e-9


def test_to_auction_listing_maps_to_pipeline_model():
    rec = parse_row(_rows()[0])
    lst = to_auction_listing(rec)
    assert isinstance(lst, AuctionListing)
    assert lst.case_no == "2025타경1352"
    assert lst.lawd_cd == "11620"
    assert lst.appraisal_price == 95_000_000
    assert lst.min_bid_price == 76_000_000
    assert lst.area_m2 == 33.56


def test_all_rows_parse_without_error():
    recs = [parse_row(r) for r in _rows()]
    assert all(isinstance(r, CourtAuctionRecord) for r in recs)
    # 모든 행이 affordable(감정가<=1억 쿼리였음) — 최저가 양수
    assert all(r.min_bid_price > 0 for r in recs)


def test_labeled_dump_uses_korean_labels():
    rec = parse_row(_rows()[0])
    labeled = rec.labeled()
    assert labeled["감정평가액(원)"] == "95000000"
    assert labeled["관할법원"] == "서울중앙지방법원"
    assert FIELD_LABELS["minmaePrice"] == "최저매각가격(원)"


def test_helpers():
    assert to_won("95,000,000") == 95_000_000
    assert to_won("") == 0
    assert to_won(None) == 0
    # 소수점 포함 문자열도 파싱(침묵실패 방지) — 0으로 떨어지면 매물이 조용히 누락됨
    assert to_won("150000000.0") == 150_000_000
    assert to_won("1,234.56") == 1234
    assert to_won("N/A") == 0
    assert ymd_to_iso("20260701") == "2026-07-01"
    assert ymd_to_iso("") == ""
    assert parse_area_m2("철근콘크리트구조\n33.56㎡") == 33.56
    assert parse_area_m2("없음") == 0.0


def test_classify_property_type():
    assert classify_property_type("아파트") == "아파트"
    # (감사 2026-07-10 CRITICAL 정정) 콤마 그룹명은 코드 없인 실체 불명 → '혼합'(추정 미지원).
    # 과거 '오피스텔' 기대는 근생·공장 호실에 오피스텔 시세를 붙이던 버그를 정답으로 고정한 것.
    assert classify_property_type("상가,오피스텔,근린시설") == "혼합"
    assert classify_property_type("대지") == "토지"
    assert classify_property_type("") == "기타"


def test_verify_property_type_land_disguised_as_apartment():
    """실측 회귀(2026-07-10, 대구 2025타경7938): 나대지가 법원 용도명 '아파트'로 등록
    → 아파트 시세 오매칭 → 4.45억 허상 차익. 건물 표식 전무 + 지목 있음 → '토지' 교정."""
    from src.courtauction_fields import verify_property_type
    land_clean = {"buldNm": "", "buldList": "", "pjbBuldList": "", "jimokList": "대"}
    assert verify_property_type("아파트", land_clean) == "토지"
    # 진짜 아파트(건물 표식 있음)는 유지
    apt_clean = {"buldNm": "", "buldList": "101동 3층302호", "pjbBuldList": "", "jimokList": ""}
    assert verify_property_type("아파트", apt_clean) == "아파트"
    # 비주거 유형은 검증 대상 아님(그대로)
    assert verify_property_type("토지", land_clean) == "토지"
    assert verify_property_type("상가", {"jimokList": "대"}) == "상가"


# ---- 2026-07-10 다관점 감사 확정 발견 회귀 ----

def test_classify_by_scls_primary_source():
    """CRITICAL 회귀: dspslUsgNm 은 그룹명 — sclsUtilCd(물건 실체)가 1차 분류 소스."""
    from src.courtauction_fields import classify_property_type
    # 그룹명 '상가,오피스텔,근린시설' 안의 근생(21101)은 상가, 오피스텔(20110)만 오피스텔
    assert classify_property_type("상가,오피스텔,근린시설", "21101") == "상가"
    assert classify_property_type("상가,오피스텔,근린시설", "20110") == "오피스텔"
    assert classify_property_type("상가,오피스텔,근린시설", "22101") == "공장"
    # 카테고리 '아파트'인데 코드=오피스텔 → 오피스텔(아파트 시세 오매칭 방지, rank 10·11 사고)
    assert classify_property_type("아파트", "20110") == "오피스텔"
    assert classify_property_type("아파트", "20104") == "아파트"
    # 토지 코드
    assert classify_property_type("기타", "10108") == "토지"


def test_classify_comma_group_without_scls_is_mixed():
    """코드 미상 + 콤마 그룹명 → '혼합'(시세추정 미지원) — 틀린 시세보다 무추정."""
    from src.courtauction_fields import classify_property_type
    from src.matcher import is_estimation_supported
    assert classify_property_type("상가,오피스텔,근린시설", "") == "혼합"
    assert is_estimation_supported("혼합") is False


def test_classify_keyword_order_apartment_factory():
    """'아파트형공장'이 '아파트'로 선매칭되던 데드 룰 수정 확인."""
    from src.courtauction_fields import classify_property_type
    assert classify_property_type("아파트형공장", "") == "상가"


def test_merge_mokmul_rows_prefers_building_row():
    """HIGH 회귀: 목적물 다중 행(건물행+토지행)은 건물행으로 병합 — 순서 무관."""
    from src.courtauction_fields import merge_mokmul_rows, parse_row
    base = {"srnSaNo": "2024타경15058", "jiwonNm": "인천지방법원", "maemulSer": "1",
            "dspslUsgNm": "아파트", "sclsUtilCd": "20104", "gamevalAmt": "300000000",
            "minmaePrice": "210000000", "yuchalCnt": "1", "maeGiil": "20260801",
            "hjguDong": "구월동", "srchHjguSiguCd": "28170"}
    bld = parse_row({**base, "docid": "D1", "buldList": "가동 1층102호",
                     "pjbBuldList": "철근콘크리트 58.05㎡", "areaList": "58.05㎡"})
    land = parse_row({**base, "docid": "D2", "buldList": "", "jimokList": "대",
                      "areaList": "120㎡"})
    for rows in ([bld, land], [land, bld]):     # API 순서 양방향
        merged = merge_mokmul_rows(rows)
        assert len(merged) == 1
        assert merged[0].doc_id == "D1"          # 건물행 승리
        assert merged[0].property_type == "아파트"
