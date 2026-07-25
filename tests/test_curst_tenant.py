"""현황조사서(임차인 전입일) 파서·대항력 실판정·저장 테스트 (2026-07-22).

원천: selectCurstExmndc.on 의 dlt_ordTsLserLtn. 대항력 = 전입일 vs 말소기준일 날짜비교.
핵심 안전규칙: 소유자 전입(임차 신호 없음)은 대항력 판정에서 제외(false-positive 방지),
PII(성명·주민번호·주소)는 절대 수집하지 않는다.
"""
from __future__ import annotations

from src.courtauction_detail import (
    CaseRights,
    analyze_priority,
    curst_has_context,
    opposable_deposit,
    parse_curst_survey,
    tenant_moveins,
)

# 부산서부 2022타경3289(삼환) 실측 구조: 임차 신호·점유표기 전무(gdsPossCtt=None) = 관계 미상 세대.
# 법원도 소유자로 확정 못 한 '관계 미상' — is_tenant_like=False 지만 소유자 단정 금지(대항력 여지 후보).
AMBIGUOUS_SURVEY = OWNER_SURVEY = {
    "ipcheck": True,
    "dma_curstExmnMngInf": {"cortOfcCd": "B000414", "csNo": "20220130003289", "ordTsCnt": 1},
    "dlt_ordTsLserLtn": [
        {"mvinDtlCtt": "1996.10.14", "gdsPossCtt": None, "lesUsgDts": None,
         "lesPartCtt": None, "rgstryCrtcpCfmtnCtt": None, "lesDposDts": None,
         # PII — 파서가 절대 읽으면 안 되는 필드
         "ENRRNO": "AB12CD==암호화주민번호", "basAddr": "부산광역시 사하구 다대동 …",
         "objctDtlAddr": "205동 2004호"},
    ],
}

# 법원이 '소유자/채무자 점유'로 확정한 세대 — 관계 미상이 아니므로 대항력 여지에서 제외돼야 한다.
CONFIRMED_OWNER_SURVEY = {
    "ipcheck": True,
    "dlt_ordTsLserLtn": [
        {"mvinDtlCtt": "2005.03.10", "gdsPossCtt": "채무자(소유자) 점유", "lesUsgDts": None,
         "lesPartCtt": None, "rgstryCrtcpCfmtnCtt": None, "lesDposDts": None},
    ],
}

# 대항력 있는 임차인: 보증금·확정일자·용도 있음(임차 신호), 전입이 말소기준보다 앞섬.
TENANT_SURVEY = {
    "ipcheck": True,
    "dlt_ordTsLserLtn": [
        {"mvinDtlCtt": "1996.10.14", "rgstryCrtcpCfmtnCtt": "1996.10.20",
         "lesDposDts": "금30,000,000원", "lesUsgDts": "주거", "lesPartCtt": "전부",
         "gdsPossCtt": "임차인 점유", "ENRRNO": "ZZ==", "basAddr": "부산 …"},
    ],
}

SENIOR_2002 = "2002. 4. 23. 근저당권"


def test_parse_owner_entry_is_not_tenant_like():
    recs = parse_curst_survey(OWNER_SURVEY)
    assert len(recs) == 1
    r = recs[0]
    assert r["movein_ymd"] == "1996-10-14"
    assert r["deposit"] == 0
    assert r["is_tenant_like"] is False           # 임차 신호 전무 → 소유자 추정
    # PII 미수집 — 원문 주소·주민번호가 어떤 값에도 새지 않아야 한다.
    assert "AB12CD" not in str(r) and "다대동" not in str(r) and "2004호" not in str(r)


def test_parse_tenant_entry_extracts_amount_and_dates():
    r = parse_curst_survey(TENANT_SURVEY)[0]
    assert r["movein_ymd"] == "1996-10-14"
    assert r["confirm_ymd"] == "1996-10-20"
    assert r["deposit"] == 30_000_000
    assert r["is_tenant_like"] is True
    assert "부산" not in str(r)                    # basAddr 미수집


def test_tenant_moveins_excludes_owner():
    assert tenant_moveins(parse_curst_survey(OWNER_SURVEY)) == []       # 소유자 → 제외
    assert tenant_moveins(parse_curst_survey(TENANT_SURVEY)) == ["1996-10-14"]


def test_ambiguous_survey_confirmed_path_alone_is_no_basis():
    """확정 임차인 경로(tenant_moveins)만 넘기면 관계미상 세대는 빠져 no_basis — is_tenant_like 게이트가
    소유자 오판(false-positive)을 막는다는 원래 불변식은 그대로 유지된다."""
    cr = CaseRights(court="부산서부지원", case_no="2022타경3289", item_no="1",
                    senior_lien=SENIOR_2002)
    tm = tenant_moveins(parse_curst_survey(AMBIGUOUS_SURVEY))     # []
    p = analyze_priority(cr, tenant_moveins=tm)
    assert p.verdict == "no_basis"


def test_ambiguous_survey_flags_possible_opposable():
    """삼환 실제 서빙경로(리뷰 #7): 관계미상 세대(possession=None) + ambiguous_moveins → 대항력 '여지'.
    종전 test_owner_only...가 인자 생략으로 no_basis를 단언하던 거짓 안전을 프로덕션 배선과 일치시킴."""
    from src.courtauction_detail import ambiguous_moveins
    cr = CaseRights(court="부산서부지원", case_no="2022타경3289", item_no="1",
                    senior_lien=SENIOR_2002)
    recs = parse_curst_survey(AMBIGUOUS_SURVEY)
    am = ambiguous_moveins(recs)                 # ['1996-10-14'] — possession=None이라 통과
    assert am == ["1996-10-14"]
    p = analyze_priority(cr, tenant_moveins=tenant_moveins(recs), ambiguous_moveins=am)
    assert p.verdict == "possible_opposable" and p.movein_date == "1996-10-14"


def test_confirmed_owner_occupancy_excluded_from_opposable():
    """리뷰 #1/#6: 법원이 '채무자(소유자) 점유'로 확정한 세대는 여지에서 제외 → no_basis(소유자 과잉경보 방지)."""
    from src.courtauction_detail import ambiguous_moveins
    cr = CaseRights(court="X", case_no="1", item_no="1", senior_lien="2018.6.1. 근저당권")
    recs = parse_curst_survey(CONFIRMED_OWNER_SURVEY)   # 전입 2005 < 말소기준 2018 이지만 소유자 확정
    assert ambiguous_moveins(recs) == []               # 점유 명시 소유자 → 여지 후보 제외
    p = analyze_priority(cr, tenant_moveins=tenant_moveins(recs),
                         ambiguous_moveins=ambiguous_moveins(recs))
    assert p.verdict == "no_basis"


def test_real_tenant_before_senior_is_confirmed_opposable():
    cr = CaseRights(court="X", case_no="1", item_no="1", senior_lien=SENIOR_2002)
    tm = tenant_moveins(parse_curst_survey(TENANT_SURVEY))    # ['1996-10-14']
    p = analyze_priority(cr, tenant_moveins=tm)
    assert p.verdict == "confirmed_opposable"     # 전입 1996 ≤ 말소기준 2002 → 대항력 있음
    assert p.movein_source == "현황조사서"
    assert p.movein_date == "1996-10-14"


def test_real_tenant_after_senior_is_not_opposable():
    """전입이 말소기준보다 늦고 명세서 인수문구 없음 → 대항력 없음(소멸), '확인 필요' 아님."""
    cr = CaseRights(court="X", case_no="1", item_no="1", senior_lien="2019.6.20. 근저당권")
    p = analyze_priority(cr, tenant_moveins=["2020-03-01"])
    assert p.verdict == "not_opposable"


def test_after_senior_with_burden_text_is_contradiction():
    """명세서 요지가 '인수'라는데 전입일이 말소기준보다 늦으면 모순 → 검증 대상."""
    cr = CaseRights(court="X", case_no="1", item_no="1", senior_lien="2019.6.20. 근저당권",
                    surviving_rights="임차보증금 잔액을 매수인이 인수함")
    p = analyze_priority(cr, tenant_moveins=["2020-03-01"])
    assert p.verdict == "contradiction"


def test_opposable_deposit_sums_only_senior_earlier_tenants():
    tenants = [
        {"is_tenant_like": True, "movein_ymd": "1996-10-14", "deposit": 30_000_000},  # 대항력
        {"is_tenant_like": True, "movein_ymd": "2010-01-01", "deposit": 50_000_000},  # 소멸
        {"is_tenant_like": False, "movein_ymd": "1990-01-01", "deposit": 99},         # 소유자
    ]
    assert opposable_deposit(tenants, "2002-04-23") == 30_000_000


def test_h1_structured_same_deposit_two_tenants_summed():
    """H1 구조화 경로(대항력 임차인 2명·같은 보증금)는 텍스트 dedup 함정 없이 정확히 합산 —
    임차인표는 행이 분리돼 있어 같은 금액이어도 2명치가 온전히 잡힌다(대항력 실판정의 이점)."""
    tenants = [
        {"is_tenant_like": True, "movein_ymd": "1998-03-01", "deposit": 50_000_000},
        {"is_tenant_like": True, "movein_ymd": "1999-05-01", "deposit": 50_000_000},
    ]
    assert opposable_deposit(tenants, "2002-04-23") == 100_000_000


def test_empty_or_no_ipcheck_survey_yields_no_tenants():
    assert parse_curst_survey({"ipcheck": False}) == []
    assert parse_curst_survey({}) == []
    assert parse_curst_survey({"dlt_ordTsLserLtn": []}) == []


def test_e3_curst_has_context_distinguishes_softblock_from_empty():
    """E3(2026-07-22): ipcheck=false/errors(소프트차단)와 ipcheck=true+빈(진짜 임차인없음)을 구분 —
    전자를 '없음'으로 오인해 save_tenants([]) 하면 기존 임차인을 전량 삭제한다."""
    assert curst_has_context({"ipcheck": False}) is False          # 소프트차단 → 저장금지
    assert curst_has_context({"errors": ["x"], "ipcheck": True}) is False  # 에러 → 저장금지
    assert curst_has_context({}) is False
    assert curst_has_context({"ipcheck": True, "dlt_ordTsLserLtn": []}) is True   # 진짜 임차인없음
    assert curst_has_context({"result": {"ipcheck": True}}) is True  # result 하위 ipcheck도 인정


def test_e3_softblock_does_not_delete_existing_tenants(tmp_path):
    """E3 통합: ipcheck=false면 저장·삭제를 건너뛰어 기존 임차인이 보존돼야 한다."""
    from src import store
    conn = store.connect(str(tmp_path / "t.db"))
    recs = parse_curst_survey(TENANT_SURVEY)
    store.save_tenants(conn, "X", "1", "1", recs, fetched_at="2026-07-22")
    assert len(store.load_tenants(conn, "X", "1", "1")) == 1
    # 소프트차단 응답 — curst_has_context=False라 크롤러는 save를 호출하지 않는다(기존 보존).
    softblock = {"ipcheck": False}
    if curst_has_context(softblock):                       # False → 이 블록 미실행이 정답
        store.save_tenants(conn, "X", "1", "1", parse_curst_survey(softblock), fetched_at="x")
    assert len(store.load_tenants(conn, "X", "1", "1")) == 1   # 삭제 안 됨
    conn.close()


def test_store_tenants_roundtrip(tmp_path):
    from src import store
    conn = store.connect(str(tmp_path / "t.db"))
    recs = parse_curst_survey(TENANT_SURVEY)
    store.save_tenants(conn, "부산서부지원", "2022타경3289", "1", recs, fetched_at="2026-07-22")
    got = store.load_tenants(conn, "부산서부지원", "2022타경3289", "1")
    assert len(got) == 1
    assert got[0]["movein_ymd"] == "1996-10-14"
    assert got[0]["deposit"] == 30_000_000
    assert got[0]["is_tenant_like"] == 1
    # 재저장(멱등) — 교체되어 중복 안 쌓임
    store.save_tenants(conn, "부산서부지원", "2022타경3289", "1", recs, fetched_at="2026-07-22")
    assert len(store.load_tenants(conn, "부산서부지원", "2022타경3289", "1")) == 1
    conn.close()


# ══════ curst_possession — '부동산의 점유관계' 표시 요지 (2026-07-25) ══════
# 부산 2025타경1435(수안동) 실측 구조: 폐문부재 + 전입세대확인 소유자 세대 + lesCnt=0.
POSSESSION_SURVEY = {
    "ipcheck": True,
    "dma_curstExmnMngInf": {
        "cortOfcCd": "B000410", "csNo": "20250130001435",
        "exmnDtDts": "2025년04월18일10시06분 2025년04월25일12시20분 ",
        "printRltnDts": "본건은 현칭 `영화아파트` 임.",
    },
    "dlt_ordTsRlet": [{
        "gdsPossCtt": ("① 폐문부재하여 안내문을 현관문과 우편함에 꽂아두었으나 연락이 없어 "
                       "점유 및 임대차 관계 알 수 없었음. \r<br />② 전입세대확인서에 소유자 "
                       "홍길동 세대가 전입되어 있음. \r<br />③ 외국인체류확인서에 해당사항 없음.<br />"),
        "printSt": "부산광역시 동래구 수안동 32-2  5층503호",
        "lesCnt": 0,
    }],
    "dlt_ordTsLserLtn": [],
}


def test_curst_possession_parses_lines_and_masks_pii():
    from src.courtauction_detail import curst_possession
    got = curst_possession(POSSESSION_SURVEY)
    assert got is not None
    assert got["addr"] == "부산광역시 동래구 수안동 32-2 5층503호"
    assert len(got["possession"]) == 3                      # <br /> 기준 3줄 분리
    assert "폐문부재" in got["possession"][0]
    assert "홍길동" not in " ".join(got["possession"])       # 실명 마스킹(방어적 이중화)
    assert "소유자" in got["possession"][1]                  # 역할 단어는 보존
    assert got["etc"] == "본건은 현칭 `영화아파트` 임."
    assert got["tenant_count"] == 0                          # 0 = '신고 임차인 없음'(중요 사실)
    assert "2025년04월18일" in got["exam_dates"]


def test_curst_possession_none_when_empty():
    from src.courtauction_detail import curst_possession
    assert curst_possession({}) is None
    assert curst_possession({"ipcheck": True, "dlt_ordTsRlet": [{}]}) is None
    assert curst_possession("문자열") is None


def test_survey_rows_mirror_roundtrip(tmp_path):
    """(2026-07-25) 점유관계 클라우드 미러 행 생성 — raw 저장→파싱→행 변환 왕복."""
    from src import store
    conn = store.connect(str(tmp_path / "s.db"))
    store.save_detail_raw(conn, "부산지방법원", "2025타경1435", "1", "curst",
                          POSSESSION_SURVEY, fetched_at="2026-07-25")
    rows = store.survey_rows(conn)
    assert len(rows) == 1
    r = rows[0]
    assert r["addr"].startswith("부산광역시")
    assert r["possession"].count("\n") == 2          # 3줄 → \n 2개
    assert "홍길동" not in r["possession"]            # 저장 마스킹 + 파서 마스킹 이중화
    assert r["tenant_count"] == 0
    assert r["fetched_at"] == "2026-07-25"
    conn.close()
