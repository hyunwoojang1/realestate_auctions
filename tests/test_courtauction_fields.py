"""courtauction_fields — 실데이터 fixture 파싱·개인정보 가드·모델변환 검증(네트워크 없음)."""
from __future__ import annotations

import json
from pathlib import Path

from src.courtauction_fields import (
    CourtAuctionRecord,
    classify_property_type,
    is_personal_field,
    mask_personal_names,
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
    # (재검증 감사 idx15) 최저입찰가 = 다가오는 기일의 공고가(notifyMinmaePrice1=60.8M).
    # fixture의 minmaePrice 76M은 '직전 회차' 가격 — 과거엔 이를 오용했다.
    assert rec.min_bid_price == 60_800_000
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


# --- 감사 2026-07-15: maejibun PII 누락 + 비고 권리검출 미호출 --------------------


def test_maejibun_is_masked_name_first_word_order():
    """매각지분은 '홍길동 지분'처럼 **이름이 앞**에 온다 — 역할라벨 패턴으로는 미탐이었다.

    실측 1,870행이 무방비 저장돼 있던 회귀(감사 2026-07-15).
    """
    clean = sanitize_row({"maejibun": "공유자지분 중 100분의 15 최선웅 지분"})
    assert "최선웅" not in clean["maejibun"]
    assert "[성명] 지분" in clean["maejibun"]
    assert "100분의 15" in clean["maejibun"]     # 지분 비율(공시정보)은 보존

    clean2 = sanitize_row({"maejibun": "한웅희 소유"})
    assert "한웅희" not in clean2["maejibun"]


def test_maejibun_masking_keeps_legal_terms():
    """'소유권'·'전원의'·'대지권 비율'은 이름 자리에 와도 성명이 아니다(오탐 방지)."""
    for text in ("전 소유권 지분 중 2분의 1", "공유자 전원의 지분 전부", "대지권 비율 500분의 21.7849"):
        assert sanitize_row({"maejibun": text})["maejibun"] == text


def test_name_starting_with_excluded_term_is_still_masked():
    """제외어 접두일치 버그 회귀 — '전원'이 제외어라고 **전원철**(실명)을 놓치면 안 된다.

    실측 누출(2026-07-15, 천안 2025타경11313): "임차인 전원철, 박화란" 무마스킹으로 Supabase
    미러까지 나갔다. 제외 판정은 정확일치+조사분리로만 한다.
    """
    from src.courtauction_fields import _looks_like_name

    for real_name in ("전원철", "소유진", "토지원", "명의찬"):
        assert _looks_like_name(real_name) is True
    for term in ("전원", "전원의", "소유권", "미상", "있으며"):
        assert _looks_like_name(term) is False


def test_masks_comma_separated_name_list():
    """'임차인 전원철, 박화란' — 역할라벨 뒤 쉼표 나열의 둘째 이후 성명도 마스킹."""
    out = mask_personal_names(
        "주택도시보증공사(임차인 전원철, 박화란)는 경매신청채권자로 "
        "임차인 전원철, 박화란의 임차보증금반환채권 양수")
    assert "전원철" not in out
    assert "박화란" not in out
    assert "[성명]의 임차보증금반환채권" in out       # 조사 보존
    assert "주택도시보증공사" in out                  # 법인명은 개인정보 아님 — 보존


def test_rights_normalize_masks_names():
    """listing_rights(=Supabase 미러 대상) 저장 경로도 마스킹돼야 한다.

    실측: 클라우드 auction_listing_rights.remark 297행에 실명이 올라가 있었다.
    """
    from src.courtauction_detail import normalize

    cr = normalize({
        "dspslGdsDxdyInfo": {
            "gdsSpcfcRmk": "유치권신고인 윤용섭로부터 공사대금채권 금 229,900,000원",
            "ndstrcRghCtt": "대항력 있는 임차인 홍길동",
            "sprfcExstcDts": "",
            "tprtyRnkHypthcStngDts": "2021.4.28.근저당권",
        },
    })
    assert "윤용섭" not in cr.remark
    assert "홍길동" not in cr.surviving_rights
    assert "229,900,000" in cr.remark                 # 금액(공시정보)은 보존
    assert cr.senior_lien == "2021.4.28.근저당권"     # 최선순위는 무손상


def test_bigo_masks_lien_claimant_and_keeps_josa():
    """유치권신고인·임차권자 등 역할어 뒤 성명도 마스킹하되 조사는 원문 유지."""
    clean = sanitize_row({"mulBigo": "유치권신고인 윤용섭로부터 공사대금채권 금 229,900,000원"})
    assert "윤용섭" not in clean["mulBigo"]
    assert "유치권신고인 [성명]로부터" in clean["mulBigo"]   # '[성명]부터'로 훼손되지 않음
    assert "229,900,000" in clean["mulBigo"]


def test_to_auction_listing_reads_opposable_and_assumed_from_bigo():
    """비고에 대항력·인수금액이 있으면 Tier-0 힌트로 반영돼 하드게이트가 발동해야 한다.

    (감사 2026-07-15) detect_special_rights 만 부르고 나머지 둘을 안 불러, batch 전 물건이
    assumed_amount=0·tenant_opposable=False 로 적재되던 회귀.
    """
    from src.score import is_hard_gated

    rec = parse_row({
        "srnSaNo": "2025타경1", "jiwonNm": "테스트지원", "maemulSer": "1",
        "notifyMinmaePrice1": "35535000", "gamevalAmt": "300000000",
        "mulBigo": ("매수인에게 대항할 수 있는 을구 순위 4번 임차권등기 있음. 배당에서 보증금이"
                    " 전액 변제되지 아니하면 잔액 155,000,000원을 매수인이 인수함"),
    })
    lst = to_auction_listing(rec)
    assert lst.tenant_opposable is True
    assert lst.assumed_amount == 155_000_000
    assert is_hard_gated(lst) is True          # 인수비율 436% > 30% 게이트


def test_to_auction_listing_clean_bigo_stays_unflagged():
    """깨끗한 비고는 종전대로 무플래그 — 오탐으로 정상 물건을 죽이지 않는다."""
    rec = parse_row({
        "srnSaNo": "2025타경2", "jiwonNm": "테스트지원", "maemulSer": "1",
        "notifyMinmaePrice1": "100000000", "gamevalAmt": "200000000",
        "mulBigo": "특별매각조건 매수신청보증금 최저매각가격의 20%",
    })
    lst = to_auction_listing(rec)
    assert lst.tenant_opposable is False
    assert lst.assumed_amount == 0


def test_to_auction_listing_maps_to_pipeline_model():
    rec = parse_row(_rows()[0])
    lst = to_auction_listing(rec)
    assert isinstance(lst, AuctionListing)
    assert lst.case_no == "2025타경1352"
    assert lst.lawd_cd == "11620"
    assert lst.appraisal_price == 95_000_000
    assert lst.min_bid_price == 60_800_000   # 공고가(notify1) 기준 — idx15
    assert lst.area_m2 == 33.56


def test_all_rows_parse_without_error():
    recs = [parse_row(r) for r in _rows()]
    assert all(isinstance(r, CourtAuctionRecord) for r in recs)
    # 모든 행이 affordable(감정가<=1억 쿼리였음) — 최저가 양수
    assert all(r.min_bid_price > 0 for r in recs)


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
