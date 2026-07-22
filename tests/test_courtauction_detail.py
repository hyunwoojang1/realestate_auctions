"""물건상세(pgj15B) 정규화 + 권리 문구 판정 회귀 테스트.

fixture 는 2026-07-10 실측 응답(대구 2025타경669 — 유찰 10회·대항력 임차권등기 물건)의
축약본. 이 물건이 이 기능의 존재 이유("왜 유찰 10회인지 딱 보이게")라 회귀 기준으로 고정한다.
"""
from __future__ import annotations

from src.courtauction_detail import CaseRights, is_substantive, normalize, summarize
from src.courtauction_rights import detect_tenant_opposable

# 실측 축약 fixture — 개인정보(채무자 성명)는 가명으로 치환
DMA = {
    "csBaseInfo": {"clmAmt": 707479451, "cortAuctnJdbnNm": "경매9계", "userCsNo": "2025타경669"},
    "dstrtDemnInfo": [{"dstrtDemnLstprdYmd": "20250527"}],
    "dspslGdsDxdyInfo": {
        "ndstrcRghCtt": "- 매수인에게 대항할 수 있는 을구 순위번호 5번 임차권등기(2023.08.04.등기) 있음. "
                        "배당에서 보증금 전액이 변제되지 아니하면 잔액을 매수인이 인수함",
        "tprtyRnkHypthcStngDts": "홍지분(2분의1)\n2024.02.02.압류",
        "sprfcExstcDts": "해당사항없음",
        "gdsSpcfcRmk": None, "dspslGdsRmk": None, "gdsSpcfcWrtYmd": "20260617",
    },
    "gdsDspslDxdyLst": [
        {"dxdyYmd": "20250819", "auctnDxdyKndCd": "01", "auctnDxdyRsltCd": "002",
         "tsLwsDspslPrc": "830000000"},
        {"dxdyYmd": "20260715", "auctnDxdyKndCd": "01", "auctnDxdyRsltCd": "000",
         "tsLwsDspslPrc": "33492000"},
        {"dxdyYmd": "20260722", "auctnDxdyKndCd": "02", "auctnDxdyRsltCd": "000",
         "tsLwsDspslPrc": None},
    ],
}


def test_normalize_extracts_core_fields():
    r = normalize(DMA, court="대구지방법원", case_no="2025타경669", item_no="1",
                  fetched_at="2026-07-10 12:00:00")
    assert "임차권등기(2023.08.04" in r.surviving_rights
    assert "2024.02.02.압류" in r.senior_lien
    assert r.lien_note == "해당사항없음"
    assert r.claim_amt == 707479451
    assert r.demand_end == "2025-05-27"
    assert r.spec_write_ymd == "2026-06-17"
    assert r.court_dept == "경매9계"


def test_normalize_schedule_mapped_and_sorted_desc():
    r = normalize(DMA)
    assert [e["ymd"] for e in r.schedule] == ["2026-07-22", "2026-07-15", "2025-08-19"]
    ev = r.schedule[-1]
    assert ev["kind"] == "매각기일" and ev["result"] == "유찰" and ev["price"] == 830_000_000
    assert r.schedule[0]["kind"] == "매각결정기일" and r.schedule[0]["price"] is None


def test_has_risk_text_and_substantive():
    r = normalize(DMA)
    assert r.has_risk_text is True            # 인수 문구 있음 → 위험 강조
    assert is_substantive("해당사항없음") is False
    assert is_substantive("") is False
    assert is_substantive("유치권 신고 있음") is True


def test_row_roundtrip():
    r = normalize(DMA, court="대구지방법원", case_no="2025타경669", item_no="1")
    row = r.to_row()
    back = CaseRights.from_row(row)
    assert back.schedule == r.schedule and back.surviving_rights == r.surviving_rights


def test_summarize_burden_on_opposable_without_amount():
    """대항력 임차권 물건(실측 1위) — 금액 미상이어도 burden + amount_unknown."""
    from src.courtauction_detail import summarize
    b = summarize(normalize(DMA))
    assert b.status == "burden" and b.opposable is True
    assert b.amount_unknown is True and not b.is_clean


def test_summarize_clean_when_no_signals():
    from src.courtauction_detail import summarize
    clean_dma = {**DMA, "dspslGdsDxdyInfo": {**DMA["dspslGdsDxdyInfo"],
                 "ndstrcRghCtt": "해당사항없음", "sprfcExstcDts": "해당사항없음"}}
    b = summarize(normalize(clean_dma))
    assert b.is_clean and not b.opposable and b.assumed == 0


def test_summarize_burden_with_amount():
    from src.courtauction_detail import summarize
    amt_dma = {**DMA, "dspslGdsDxdyInfo": {**DMA["dspslGdsDxdyInfo"],
               "ndstrcRghCtt": "임차보증금 금80,000,000원을 매수인이 인수함"}}
    b = summarize(normalize(amt_dma))
    assert b.status == "burden" and b.assumed == 80_000_000 and not b.amount_unknown


def test_blank_freetext_not_opposability_assessable():
    """대항력 false-negative 회귀(2026-07-22, 부산 2022타경3289 삼환아파트):
    자유기술란(인수권리·유치권·비고)이 전부 비고 말소기준만 있으면 대항력 판정 근거가
    없다 → opposability_assessable=False 여야 '대항력 임차인 발견 안 됨' 초록 오표시를 막는다.
    종전엔 spec_write_ymd/senior_lien 만으로 is_empty=False → verified→초록으로 새던 버그."""
    cr = CaseRights(
        court="부산서부지원", case_no="2022타경3289", item_no="1",
        surviving_rights="", lien_note="", remark="",
        senior_lien="2002. 4. 23. 근저당권", spec_write_ymd="2026-04-06",
    )
    assert cr.is_empty is False                    # 말소기준·작성일 있어 '완전 빈'은 아님
    assert cr.opposability_assessable is False      # 그러나 대항력 판정 근거는 없음 → '모름'


def test_remark_only_is_opposability_assessable():
    """비고(remark)에만 실체 텍스트가 있어도 대항력 판정 근거는 있는 것 — assessable=True."""
    cr = CaseRights(
        court="X", case_no="1", item_no="1",
        surviving_rights="", lien_note="",
        remark="대항력 있는 임차인 있음. 배당 부족분은 매수인 인수.",
        senior_lien="", spec_write_ymd="",
    )
    assert cr.opposability_assessable is True


def test_opposable_detected_on_real_phrase():
    """실측 미탐 회귀(2026-07-10): '매수인이 인수함'(조사)·'대항할 수 있는' 변형이
    기존 phrase 목록에 없어 False 로 판정되던 버그 — 반드시 True."""
    r = normalize(DMA)
    assert detect_tenant_opposable(r.surviving_rights) is True


def test_store_load_rights_fallback_keeps_court(tmp_path):
    """SQLite 폴백도 court 유지 — 타법원 동명 사건의 권리가 새어 나오면 안 된다."""
    from src import store
    conn = store.connect(str(tmp_path / "r.db"))
    store.save_rights(conn, [{
        "court": "다른법원", "case_no": "2025타경1", "item_no": "1",
        "surviving_rights": "유치권", "senior_lien": "", "lien_note": "", "remark": "",
        "claim_amt": None, "demand_end": "", "spec_write_ymd": "", "court_dept": "",
        "schedule": "[]", "fetched_at": "",
    }])
    # 같은 사건번호, 다른 법원 → None (타법원 오표시 금지)
    assert store.load_rights(conn, "대구지방법원", "2025타경1", "2") is None
    # (재검증 감사 idx16) 같은 법원이어도 다른 물건번호는 None — 형제 물건 명세서 과신 금지
    assert store.load_rights(conn, "다른법원", "2025타경1", "2") is None
    # 정확 매칭만 반환
    assert store.load_rights(conn, "다른법원", "2025타경1", "1") is not None


# ---- 2026-07-11 재검증 감사 확정 — 권리 문구 파서 회귀 ----

def test_double_negation_is_assumption():
    """idx7 CRITICAL: '말소되지 않고 … 인수함'은 negation('말소')이 있어도 인수다."""
    from src.courtauction_rights import detect_assumed_amount
    txt = "을구 5번 임차권등기(임대차보증금 450,000,000원)는 말소되지 않고 매수인이 인수함"
    assert detect_assumed_amount(txt) == 450_000_000


def test_korean_unit_amounts_parsed():
    """idx8 CRITICAL: 억/천만/만원 한글 단위 금액도 읽는다."""
    from src.courtauction_rights import detect_assumed_amount
    assert detect_assumed_amount("임차보증금 4억5,000만원을 매수인이 인수함") == 450_000_000
    assert detect_assumed_amount("보증금 금1억 원 매수인이 인수") == 100_000_000
    assert detect_assumed_amount("6,500만원 인수 부담") == 65_000_000


def test_no_assumption_phrase_not_opposable():
    """idx9 HIGH: '매수인이 인수하지 아니함'(인수 0원 확정)은 opposable 아님."""
    from src.courtauction_rights import detect_tenant_opposable
    txt = "특별매각조건: 임차보증금은 매수인이 인수하지 아니함"
    assert detect_tenant_opposable(txt) is False


def test_release_consent_not_opposable():
    """idx10 HIGH: 임차권등기라도 말소 동의·대항력 포기 문맥이면 opposable 아님."""
    from src.courtauction_rights import detect_tenant_opposable
    assert detect_tenant_opposable("을구 5번 임차권등기 있음. 임차인은 말소 동의 확약서 제출") is False
    assert detect_tenant_opposable("임차인 대항력 포기. 임차권등기의 말소를 조건으로 매각") is False
    # 해소 문구 없는 진짜 인수 절은 여전히 True (혼재 문서에서 미탐 금지)
    assert detect_tenant_opposable(
        "5번 임차권등기 말소 동의. 7번 임차권등기는 매수인이 인수함") is True


def test_share_keyword_not_matched_in_bubun():
    """idx11 MEDIUM: '부분의'가 지분으로 오탐되면 안 된다. 'N분의 M'은 지분."""
    from src.courtauction_rights import detect_special_rights
    assert "지분" not in detect_special_rights("건물 일부분의 하자 있음")
    assert "지분" in detect_special_rights("소유권 2분의 1 매각")


def test_share_sale_hard_gated():
    """지분매각(법원 분류)은 하드게이트 — 통물건 시세로 과대평가되므로 차익 추천 제외(감사 2026-07-15)."""
    from src.courtauction_detail import CaseRights, summarize
    from src.models import AuctionListing
    from src.score import is_hard_gated
    cr = CaseRights(court="서울중앙지방법원", case_no="2024타경1", item_no="1",
                    remark="-지분매각, 공유자우선매수신고는 1회에 한함",
                    spec_write_ymd="2024-01-01", senior_lien="2015.07.28.근저당")
    badge = summarize(cr)
    assert "지분매각" in badge.special
    lst = AuctionListing(case_no="2024타경1", court="서울중앙지방법원", address="서울 강남구",
                         lawd_cd="11680", dong="대치동", apt_name="샘플", property_type="아파트",
                         area_m2=59.9, appraisal_price=100_000_000, min_bid_price=70_000_000,
                         fail_count=1, sale_date="2026-08-01", special_rights=badge.special)
    assert is_hard_gated(lst) is True


def test_plain_share_mention_not_share_sale():
    """'대지권 지분' 등 단순 언급(매각/경매 분류어 없음)은 지분매각 게이트를 트리거하지 않는다."""
    from src.courtauction_detail import CaseRights, summarize
    cr = CaseRights(court="c", case_no="2024타경2", item_no="1",
                    senior_lien="대지권 지분 근저당 2020.01.01", spec_write_ymd="2024-01-01")
    badge = summarize(cr)
    assert "지분매각" not in badge.special


def test_normalize_sanitizes_free_text():
    """상세 자유텍스트의 크롤 아티팩트(제어문자·제로폭·BOM·NBSP·연속공백)를 정제한다(2026-07-16).

    정제는 마스킹 전에 적용되므로 제로폭이 이름 앞에 끼어도 마스킹이 정상 동작해야 한다.
    """
    from src.courtauction_detail import normalize
    dma = {
        "csBaseInfo": {"userCsNo": "2024타경1", "clmAmt": "1000", "cortAuctnJdbnNm": "경매1계"},
        "dspslGdsDxdyInfo": {
            "ndstrcRghCtt": "유치권신고인​ 김철수\x07 로부터 공사대금",
            "tprtyRnkHypthcStngDts": "근저당　　2020.01.01",
            "sprfcExstcDts": "해당﻿사항없음",
            "gdsSpcfcRmk": "비고   문구\x1f 끝",
            "gdsSpcfcWrtYmd": "20240101",
        },
        "aeeWevlMnpntLst": [{"aeeWevlMnpntItmCd": "00083006", "aeeWevlMnpntCtt": "이용​상태  양호\x0c"}],
    }
    cr = normalize(dma, court="c", case_no="2024타경1", item_no="1")
    junk = (0x200b, 0x200c, 0x200d, 0x07, 0xfeff, 0x1f, 0x0c, 0x3000, 0x00a0)  # 제로폭·제어·BOM·전각·NBSP
    for field in (cr.surviving_rights, cr.senior_lien, cr.lien_note, cr.remark):
        assert not any(ord(ch) in junk for ch in field)
        assert "  " not in field
    assert cr.appraisal_notes and not any(ord(ch) in junk for ch in cr.appraisal_notes[0]["text"])
    assert "김철수" not in cr.surviving_rights          # 정제 후에도 실명 마스킹 정상 동작
    assert "[성명]" in cr.surviving_rights


def test_sanitize_unescapes_html_entities():
    """법원 자유텍스트의 HTML 엔티티(단일·이중 인코딩)를 실제 문자로 복원한다(2026-07-19).

    자동이스케이프 템플릿에서 &amp;quot; 처럼 깨져 보이던 감정 요항 버그를 소스에서 교정.
    """
    from src.courtauction_detail import _sanitize
    assert _sanitize("&amp;quot;대구진천초등학교&amp;quot;") == '"대구진천초등학교"'
    assert _sanitize("&lt;가축분뇨의 관리 및 이용에 관한 법률&gt;") == "<가축분뇨의 관리 및 이용에 관한 법률>"
    assert _sanitize("&amp;apos;천안백석중학교&amp;apos;") == "'천안백석중학교'"
    assert _sanitize("A &amp; B") == "A & B"
    assert _sanitize("정상 텍스트") == "정상 텍스트"      # 엔티티 없으면 그대로(멱등)


def test_multiple_deposits_summed_distinct():
    """idx12 MEDIUM: 서로 다른 보증금 여러 건은 합산, 같은 금액 반복(재고지)은 1회."""
    from src.courtauction_rights import detect_assumed_amount
    txt = ("갑 임차인 보증금 100,000,000원 매수인이 인수함\n"
           "을 임차인 보증금 50,000,000원 매수인이 인수함\n"
           "위 보증금 100,000,000원 인수 관련 재안내")
    assert detect_assumed_amount(txt) == 150_000_000


def test_h1_same_deposit_two_tenants_summed():
    """H1(2026-07-22): 같은 보증금 임차인 2명은 합산(종전 set-dedup은 1명치로 과소산정=위험)."""
    from src.courtauction_rights import detect_assumed_amount
    two = ("갑 임차인 임차보증금 50,000,000원 매수인이 인수함\n"
           "을 임차인 임차보증금 50,000,000원 매수인이 인수함")
    assert detect_assumed_amount(two) == 100_000_000


def test_h1_backref_restatement_not_double_counted():
    """H1: '상기/위 보증금 …재안내' 재고지 줄은 앞 임차인 금액 반복이라 합산 제외."""
    from src.courtauction_rights import detect_assumed_amount
    txt = ("임차보증금 80,000,000원 매수인이 인수함\n"
           "상기 임차보증금 80,000,000원은 배당 후 잔액 인수 재고지")
    assert detect_assumed_amount(txt) == 80_000_000


def test_h1_identical_line_copy_counted_once():
    """H1: 완전히 동일한 줄(요지↔비고 복붙)은 1회만(과대 방지)."""
    from src.courtauction_rights import detect_assumed_amount
    dupe = ("임차보증금 70,000,000원 매수인이 인수함\n"
            "임차보증금 70,000,000원 매수인이 인수함")
    assert detect_assumed_amount(dupe) == 70_000_000


def test_h4_schema_drift_canary():
    """H4(2026-07-22): 값이 null(정상)인 것과 필드명 자체가 사라진 것(드리프트)을 구분."""
    from src.courtauction_detail import detail_schema_drift
    ok = {"csBaseInfo": {}, "dspslGdsDxdyInfo": {"ndstrcRghCtt": None,
                                                 "tprtyRnkHypthcStngDts": "2002. 4. 23. 근저당권"}}
    assert detail_schema_drift(ok) == ""                           # 키 존재(값 null이어도) = 정상
    assert "섹션 없음" in detail_schema_drift({"csBaseInfo": {}})   # 요지 섹션 자체 없음
    assert detail_schema_drift({"dspslGdsDxdyInfo": {"someRenamedField": "x"}})   # 핵심 필드명 전무
    assert detail_schema_drift("not a dict")                       # dma_result 아님


def test_empty_case_rights_is_empty():
    """idx17 HIGH: 실체 신호 전무한 요지는 is_empty — clean 배지로 오판 금지."""
    empty = CaseRights(court="법원", case_no="2025타경1", item_no="1")
    assert empty.is_empty is True
    filled = normalize(DMA)
    assert filled.is_empty is False


# ---- 2026-07-12 서빙 결과물 감사 확정 회귀 ----

def test_korean_hundred_thousand_unit_amounts():
    """#0: 백/천 혼합 한글 단위 금액 파싱('1억9천5백만원')."""
    from src.courtauction_rights import _korean_won, detect_assumed_amount
    assert _korean_won("1억9천5백만") == 195_000_000
    assert _korean_won("2억5천만") == 250_000_000
    assert _korean_won("6,500만") == 65_000_000
    assert detect_assumed_amount("임차보증금 1억9천5백만원, 잔액을 매수인이 인수함") == 195_000_000


def test_direct_negation_zero_assumed():
    """#15: '매수인이 인수하지 아니함'은 이중부정('변제되지 않')보다 우선해 인수 0원."""
    from src.courtauction_rights import detect_assumed_amount
    txt = "배당에서 전액 변제되지 않더라도 잔액을 매수인이 인수하지 아니함(특별매각조건). 보증금 200,000,000원"
    assert detect_assumed_amount(txt) == 0


def test_multiple_deposits_one_line_summed():
    """#16: 한 줄 다건 임차권 보증금은 distinct 합산, '중 미반환 Y'는 Y 채택."""
    from src.courtauction_rights import detect_assumed_amount
    multi = "을구 임차권등기 보증금 75,000,000원, 임차권등기 보증금 120,000,000원 매수인이 인수함"
    assert detect_assumed_amount(multi) == 195_000_000
    residual = "임차보증금 금150,000,000원 중 미반환 금액 120,000,000원을 매수인이 인수함"
    assert detect_assumed_amount(residual) == 120_000_000


def test_senior_jeonse_is_burden():
    """#14: 최선순위 설정=전세권이면 clean 아님(배당요구 미상 특수사례)."""
    r = CaseRights(surviving_rights="", senior_lien="2021.07.16. 전세권", lien_note="해당사항없음")
    b = summarize(r)
    assert b.status == "burden" and "선순위전세권" in b.special


def test_deposit_fallback_when_amount_unknown():
    """#9: 인수 부담인데 인수금 미상이면 명세서 보증금액을 보수 추정으로 채택."""
    r = CaseRights(surviving_rights="을구 5번 임차권등기(임차보증금 195,000,000원)는 매각으로 소멸하지 않음",
                   senior_lien="2020.1.1.근저당")
    assert summarize(r).assumed == 195_000_000


def test_substantive_not_startswith_trap():
    """#22: '해당사항없음' 뒤에 인수권리가 붙으면 실질 내용(startswith 함정 제거)."""
    from src.courtauction_detail import is_substantive
    assert is_substantive("해당사항없음. 다만 을구 5번 임차권 매수인 인수") is True
    assert is_substantive("해당사항없음") is False
    assert is_substantive("해당사항없음.") is False


def test_empty_rights_no_badge():
    """#13: 빈/부분 명세서(is_empty)는 배지 판정 대상 아님(미확인 폴백)."""
    assert CaseRights(court="법", case_no="2025타경1", item_no="1").is_empty is True


# ---- 2026-07-13 대항력 판정 근거 (전입일 vs 말소기준일) ----

def test_priority_confirmed_opposable():
    """전입 < 말소기준 → 대항력 있음(인수 근거 확인)."""
    from src.courtauction_detail import analyze_priority
    r = CaseRights(surviving_rights="을구 3번 주택임차권등기(임대차보증금 330,000,000원, 전입일자 2022.3.28., 확정일자 2022.2.28.) 매수인이 인수함",
                   senior_lien="2023.8.18. 압류")
    a = analyze_priority(r)
    assert a.verdict == "confirmed_opposable"
    assert a.movein_date == "2022-03-28" and a.senior_date == "2023-08-18"
    assert a.senior_type == "압류"


def test_priority_contradiction():
    """전입 > 말소기준인데 명세서 인수 → 확인 필요 플래그."""
    from src.courtauction_detail import analyze_priority
    r = CaseRights(surviving_rights="임차권등기(전입일자 2020.9.25.) 매수인이 인수함",
                   senior_lien="2020.9.7. 근저당권")
    a = analyze_priority(r)
    assert a.verdict == "contradiction"


def test_priority_dates_incomplete():
    """말소기준은 있으나 전입일 미기재 → 법원 판정만 + 확인 권고."""
    from src.courtauction_detail import analyze_priority
    r = CaseRights(surviving_rights="을구 5번 임차권등기 있음. 배당 부족 시 매수인 인수",
                   senior_lien="2024.2.2. 압류")
    a = analyze_priority(r)
    assert a.verdict == "dates_incomplete" and a.movein_date == ""


def test_priority_no_basis():
    """날짜가 전혀 없으면 no_basis."""
    from src.courtauction_detail import analyze_priority
    a = analyze_priority(CaseRights(surviving_rights="임차권등기 있음", senior_lien=""))
    assert a.verdict == "no_basis"


def test_priority_movein_label_with_singo():
    """C3: 법원 표준 라벨 '전입신고일자'도 전입일로 인식(종전 미매치로 근거분석 무력화되던 것 수정)."""
    from src.courtauction_detail import _MOVEIN_RE, analyze_priority
    assert _MOVEIN_RE.search("전입신고일자 2020.05.02") is not None
    assert _MOVEIN_RE.search("전입일자 2020.05.02") is not None  # 무회귀
    r = CaseRights(surviving_rights="임차권(전입신고일자 2019.1.1.) 매수인이 인수함",
                   senior_lien="2020.1.1. 근저당권")
    a = analyze_priority(r)
    assert a.movein_date == "2019-01-01" and a.verdict == "confirmed_opposable"
