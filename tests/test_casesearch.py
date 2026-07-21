"""casesearch — 사건번호 정규화·로컬매칭·법원코드·라이브뷰 계약 고정.

이 테스트가 '명확한 논리'를 고정한다. 표기 변형이 늘어도 여기 케이스를 추가하는 방식으로만
확장하고, 임의로 정규화 규칙을 바꾸지 않는다(무한수정 방지의 핵심).
"""
from dataclasses import dataclass

import pytest

from src import casesearch as cs


# --- 정규화 ---
@pytest.mark.parametrize("text,year,serial", [
    ("2025-101763", 2025, 101763),      # 친구가 보내는 대표 포맷
    ("2025타경101763", 2025, 101763),   # 표준형
    ("2025타경 101763", 2025, 101763),  # 타경 뒤 공백
    ("2025 101763", 2025, 101763),      # 공백 구분
    ("2025.101763", 2025, 101763),      # 점 구분
    ("2024타경1865", 2024, 1865),       # 짧은 일련번호
    ("101763", None, 101763),           # 연도 미상 — 일련번호만
])
def test_parse_variants(text, year, serial):
    q = cs.parse_case_query(text)
    assert q is not None
    assert q.year == year
    assert q.serial == serial


def test_parse_canonical_matches_db_format():
    assert cs.parse_case_query("2025-101763").canonical == "2025타경101763"
    assert cs.parse_case_query("101763").canonical is None  # 연도 없으면 확정 불가


def test_parse_with_court_hint():
    for text in ("광주지방법원 2025-101763", "2025타경101763 광주지방법원"):
        q = cs.parse_case_query(text)
        assert q.year == 2025 and q.serial == 101763
        assert q.court == "광주지방법원"


@pytest.mark.parametrize("bad", ["", "   ", "안녕하세요", "타경", "법원"])
def test_parse_garbage_returns_none(bad):
    assert cs.parse_case_query(bad) is None


# --- 홈 검색창 판별(이름검색 오탈취 방지) ---
@pytest.mark.parametrize("text,expected", [
    ("2025-101763", True),
    ("2025타경101763", True),
    ("2025 101763", True),
    ("101763", True),           # 순수 6자리
    ("2025", True),             # 4자리(연도로도 보이나 사건검색으로 라우팅)
    ("e편한세상2차", False),     # 단지명 — 가로채면 안 됨
    ("래미안", False),
    ("서울 아파트", False),
    ("힐스테이트 3단지", False),
])
def test_looks_like_case_no(text, expected):
    assert cs.looks_like_case_no(text) is expected


# --- 저장 포맷 파싱 ---
def test_split_stored():
    assert cs.split_stored("2025타경101763") == (2025, 101763)
    assert cs.serial_of("2024타경1865") == 1865
    assert cs.split_stored("2025-101763") is None  # 저장 포맷 아님


# --- 로컬 매칭 ---
@dataclass
class FakeListing:
    case_no: str
    court: str = ""


def _pool():
    return [
        FakeListing("2025타경101763", "광주지방법원"),
        FakeListing("2024타경101763", "부산지방법원"),   # 같은 일련번호, 다른 연도
        FakeListing("2025타경135", "수원지방법원"),
    ]


def test_match_local_canonical_exact():
    q = cs.parse_case_query("2025-101763")
    got = cs.match_local(_pool(), q)
    assert [s.case_no for s in got] == ["2025타경101763"]


def test_match_local_serial_only_returns_all_years():
    q = cs.parse_case_query("101763")   # 연도 미상 → 전 연도 후보
    got = cs.match_local(_pool(), q)
    assert {s.case_no for s in got} == {"2025타경101763", "2024타경101763"}


def test_match_local_court_hint_filters():
    # 정식 법원명이 있을 때만 법원 필터 적용(지역명 단독은 모호 → 필터 안 함, 전 후보 반환).
    q = cs.parse_case_query("부산지방법원 101763")
    got = cs.match_local(_pool(), q)
    assert [s.case_no for s in got] == ["2024타경101763"]

    q2 = cs.parse_case_query("부산 101763")   # 지역명 단독 = 법원 미특정
    assert q2.court is None
    assert {s.case_no for s in cs.match_local(_pool(), q2)} == {"2025타경101763", "2024타경101763"}


# --- 법원코드 ---
def test_court_office_code_exact_and_partial():
    assert cs.court_office_code("광주지방법원") == "B000510"
    assert cs.court_office_code("창원지방법원") == "B000420"
    # 부분일치가 유일하면 확정
    assert cs.court_office_code("제주") == "B000530"
    # 없는 법원
    assert cs.court_office_code("화성지방법원") is None


def test_list_courts_nonempty():
    courts = cs.list_courts()
    assert "서울중앙지방법원" in courts
    assert len(courts) >= 50


# --- 라이브 뷰 빌드(네트워크 없음) ---
class FakeRights:
    court = "광주지방법원"
    case_no = "2025타경101763"
    court_dept = "경매9계"
    claim_amt = 707479451
    demand_end = "2025-05-27"
    surviving_rights = "대항력 있는 임차권 있음"
    senior_lien = "근저당 2020.01.01"
    lien_note = ""
    remark = ""
    appraisal_notes = [{"label": "이용상태", "text": "양호"}]
    schedule = [
        {"ymd": "2025-08-19", "kind": "매각", "result": "유찰", "price": 830000000},
        {"ymd": "2026-07-15", "kind": "매각", "result": "유찰", "price": 33492000},
        {"ymd": "2026-07-22", "kind": "매각", "result": "", "price": 0},
    ]


def test_build_case_view_derives_prices_and_faults():
    view = cs.build_case_view(FakeRights(), {})
    assert view["appraisal_price"] == 830000000   # 최초 회차 최저가 ≈ 감정가
    assert view["min_bid_price"] == 33492000       # 가장 최근 가격 회차 = 현 최저가
    assert view["fail_count"] == 2                 # 유찰 2회
    assert view["claim_amt"] == 707479451
    assert view["scored"] is False                 # 라이브 단건은 차익채점 대상 아님


def test_live_lookup_requires_court_and_year():
    with pytest.raises(cs.CaseSearchError):
        cs.live_lookup(cs.parse_case_query("101763"), court_name="광주지방법원")  # 연도 없음
    with pytest.raises(cs.CaseSearchError):
        cs.live_lookup(cs.parse_case_query("2025-101763"), court_name=None)     # 법원 없음


def test_live_lookup_uses_client_case_detail():
    """client.case_detail을 올바른 (법원코드, 표준형)으로 부르는지 — 네트워크 목."""
    calls = {}

    class FakeClient:
        def case_detail(self, cort_ofc_cd, cs_no, gds_seq="1", warm=True):
            calls["code"] = cort_ofc_cd
            calls["cs_no"] = cs_no
            # normalize가 먹을 최소 dma_result
            return {"csBaseInfo": {"userCsNo": cs_no, "clmAmt": 100, "cortAuctnJdbnNm": "경매1계"},
                    "dspslGdsDxdyInfo": {}, "gdsDspslDxdyLst": []}

    q = cs.parse_case_query("광주지방법원 2025-101763")
    view = cs.live_lookup(q, client=FakeClient())
    assert calls["code"] == "B000510"
    assert calls["cs_no"] == "2025타경101763"
    assert view["case_no"] == "2025타경101763"
