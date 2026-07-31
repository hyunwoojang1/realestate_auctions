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


def test_c1_senior_lien_masks_coowner_names():
    """(C1 2026-07-22) senior_lien(최선순위=지분 어순 흔함)의 공유자 실명이 마스킹돼야 한다.
    실측: listing_rights 47행/42사건에 무마스킹 실명이 서빙되고 있었다."""
    from src.courtauction_detail import normalize
    cr = normalize({"dspslGdsDxdyInfo": {
        "tprtyRnkHypthcStngDts": "김민정 지분: 2023.03.23. 가압류\n권병채 지분: 2024.10.14. 강제경매개시결정",
        "ndstrcRghCtt": "", "sprfcExstcDts": "",
    }})
    assert "김민정" not in cr.senior_lien and "권병채" not in cr.senior_lien
    assert "[성명]" in cr.senior_lien
    assert "가압류" in cr.senior_lien and "2023.03.23" in cr.senior_lien   # 공시정보 보존


def test_c1_corp_names_preserved_in_senior_lien():
    """(C1) 근저당권자/채권자 뒤 법인명(은행·주식회사·캐피탈)은 보존해야 한다(공시정보)."""
    assert mask_personal_names("근저당권자 신한은행") == "근저당권자 신한은행"
    assert mask_personal_names("주식회사 우리캐피탈이 신청") == "주식회사 우리캐피탈이 신청"
    assert "농협은행" in mask_personal_names("근저당권자 농협은행 가압류권자 이순신")
    assert "[성명]" in mask_personal_names("근저당권자 농협은행 가압류권자 이순신")   # 사람만 마스킹


def test_qa0726_long_corp_names_preserved():
    """(QA 2026-07-26) 5자+ 법인명 — 종전 정규식이 앞 4자만 잘라 '[성명]공사'로 훼손하던 실측
    오염 클래스(저장분 22행). 전체 단어 접미사로 법인을 판정해 보존한다."""
    cases = [
        "등기사항전부증명서상 구분지상권(지상권자 : 한국전력공사)이 설정되어 있음",
        "지상권자인 신청채권자 충주산림조합이 2025.12.12.자 말소동의서를 제출함",
        "신청채권자 제천농업협동조합으로부터 지상권말소동의서가 제출되어 있으니",
        "신청채권자 서울보증보험주식회사가 우선변제권자로서 배당금으로 전액 변제 받지",
        "채권자 서울보증보험 주식회사의 매수인에 대한 인도명령",
        "유치권신고인 성진영농조합법인으로부터 금 180,000,000원의 유치권 신고",
    ]
    for t in cases:
        assert mask_personal_names(t) == t, t


def test_kepco_short_form_preserved():
    """(2026-07-31) 약칭 '한국전력' — 커밋 게이트가 실명 의심으로 잡아 커밋을 막던 오탐.

    '한국전력공사'는 접미사 '공사'로 이미 보존됐지만 약칭에는 접미사가 없어 구멍이었다.
    한전은 법인이고 송전선로 구분지상권은 매수인이 알아야 할 **공시정보**라, [성명]으로
    지우면 위험 신호 자체가 사라진다(실측: 부산 2024타경11154 '지상권자 한국전력').
    """
    assert mask_personal_names("지상권자 한국전력") == "지상권자 한국전력"
    assert mask_personal_names("지상권자 한국전력공사") == "지상권자 한국전력공사"
    # 회귀가드 — 법인 보존을 넓혀도 자연인은 그대로 마스킹된다.
    assert mask_personal_names("지상권자 홍길동") == "지상권자 [성명]"


def test_qa0726_document_words_not_masked():
    """(QA 2026-07-26) '채권자 제출 보정서'·'채권자 확약서 제출' — 서류·행위어는 성명이 아니다."""
    assert mask_personal_names("2025.03.18.자 채권자 제출 보정서에 첨부되어 있음") \
        == "2025.03.18.자 채권자 제출 보정서에 첨부되어 있음"
    assert mask_personal_names("대항력 포기한다는 26. 4. 30. 채권자 확약서 제출") \
        == "대항력 포기한다는 26. 4. 30. 채권자 확약서 제출"


def test_qa0726_person_after_role_still_masked():
    """(QA 2026-07-26 회귀가드) 법인 보존을 넣어도 자연인 마스킹은 그대로다."""
    assert mask_personal_names("채무자 홍길동에게 통지") == "채무자 [성명]에게 통지"
    assert mask_personal_names("임차인 전원철, 박화란") == "임차인 [성명], [성명]"
    out = mask_personal_names("채권자 김철수, 한국전력공사")
    assert "[성명]" in out and "한국전력공사" in out   # 나열 꼬리의 법인도 보존


def test_c1_creditor_role_person_masked():
    """(C1) '채권자 박보라'처럼 채권자가 자연인이면 마스킹(법인은 위 가드로 보존)."""
    assert mask_personal_names("채권자 박보라") == "채권자 [성명]"


def test_c1_owner_adverb_not_masked():
    """(C1 오탐가드) '현장조사 당시 소유자'의 '당시'는 성명이 아니다 — 소유자/소유권은 name-first 제외."""
    assert mask_personal_names("현장조사 당시 소유자가 점유") == "현장조사 당시 소유자가 점유"
    assert mask_personal_names("본건의 소유권은 채무자에게") == "본건의 소유권은 채무자에게"


def test_c1_appraisal_notes_masked():
    """(C1) appraisal_notes(감정 요항)도 마스킹 경로를 타야 한다."""
    from src.courtauction_detail import normalize
    cr = normalize({
        "dspslGdsDxdyInfo": {"tprtyRnkHypthcStngDts": "2020.1.1.근저당권"},
        "aeeWevlMnpntLst": [{"aeeWevlMnpntCtt": "임차인 김철수가 점유중임", "aeeWevlMnpntItmCd": "01"}],
    })
    assert cr.appraisal_notes and "김철수" not in cr.appraisal_notes[0]["text"]
    assert "[성명]" in cr.appraisal_notes[0]["text"]


def test_c1_convaddr_in_free_text_fields():
    """(C1) convAddr(정제 소재지)도 마스킹 대상 — 지분물건 주소에 채무자 실명이 섞인다."""
    from src.courtauction_fields import _FREE_TEXT_FIELDS
    assert "convAddr" in _FREE_TEXT_FIELDS
    out = sanitize_row({"convAddr": "부산 사하구 다대동 120-10 채무자 홍길동 지분"})
    assert "홍길동" not in out["convAddr"] and "[성명]" in out["convAddr"]


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
