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
    assert classify_property_type("상가,오피스텔,근린시설") == "오피스텔"  # 키워드 우선순위
    assert classify_property_type("대지") == "토지"
    assert classify_property_type("") == "기타"
