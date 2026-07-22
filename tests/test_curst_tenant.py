"""현황조사서(임차인 전입일) 파서·대항력 실판정·저장 테스트 (2026-07-22).

원천: selectCurstExmndc.on 의 dlt_ordTsLserLtn. 대항력 = 전입일 vs 말소기준일 날짜비교.
핵심 안전규칙: 소유자 전입(임차 신호 없음)은 대항력 판정에서 제외(false-positive 방지),
PII(성명·주민번호·주소)는 절대 수집하지 않는다.
"""
from __future__ import annotations

from src.courtauction_detail import (
    CaseRights,
    analyze_priority,
    opposable_deposit,
    parse_curst_survey,
    tenant_moveins,
)

# 부산서부 2022타경3289(삼환) 실측 구조: 임차 신호 없는 단독 전입 = 소유자로 추정.
OWNER_SURVEY = {
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


def test_owner_only_survey_does_not_flag_opposable():
    """삼환 시나리오: 자유란 빈 CaseRights + 소유자 전입만 → 대항력으로 오판하면 안 됨."""
    cr = CaseRights(court="부산서부지원", case_no="2022타경3289", item_no="1",
                    senior_lien=SENIOR_2002)
    tm = tenant_moveins(parse_curst_survey(OWNER_SURVEY))     # []
    p = analyze_priority(cr, tenant_moveins=tm)
    assert p.verdict == "no_basis"                # 임차인 전입일 없음(소유자만) → 판정근거 없음


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


def test_empty_or_no_ipcheck_survey_yields_no_tenants():
    assert parse_curst_survey({"ipcheck": False}) == []
    assert parse_curst_survey({}) == []
    assert parse_curst_survey({"dlt_ordTsLserLtn": []}) == []


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
