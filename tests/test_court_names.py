"""법원 정식 명칭 변환 — 축약 court 필드를 경매사건검색 드롭다운 명칭으로.

배경(2026-07-26 사용자 QA): 상세페이지가 "고양지원"만 보여줘서, 법원 원문 버튼으로
경매사건검색 화면에 가도 드롭다운(정식 명칭 "의정부지방법원 고양지원")에서 어느
지방법원을 골라야 할지 알 수 없었다. 지원→본원 소속은 법원조직법상 정적 사실.
"""
import json

import pytest

from src.court_names import full_court_name

# 크롤 DB(scored_listings) 실측 distinct court 53종 전수(2026-07-26) — 새 법원이
# 크롤에 등장해 매핑이 빠지면 이 목록을 갱신할 것. 값은 (입력, 기대 정식 명칭).
DB_COURTS = [
    ("강릉지원", "춘천지방법원 강릉지원"), ("거창지원", "창원지방법원 거창지원"),
    ("경주지원", "대구지방법원 경주지원"), ("고양지원", "의정부지방법원 고양지원"),
    ("공주지원", "대전지방법원 공주지원"), ("광주지방법원", "광주지방법원"),
    ("군산지원", "전주지방법원 군산지원"), ("김천지원", "대구지방법원 김천지원"),
    ("남양주지원", "의정부지방법원 남양주지원"), ("남원지원", "전주지방법원 남원지원"),
    ("논산지원", "대전지방법원 논산지원"), ("대구서부지원", "대구지방법원 서부지원"),
    ("대구지방법원", "대구지방법원"), ("대전지방법원", "대전지방법원"),
    ("동부지원", "부산지방법원 동부지원"),   # 무접두 축약 — 실물건 주소 전수(부산 남구)로 확정
    ("마산지원", "창원지방법원 마산지원"), ("목포지원", "광주지방법원 목포지원"),
    ("밀양지원", "창원지방법원 밀양지원"), ("부산서부지원", "부산지방법원 서부지원"),
    ("부산지방법원", "부산지방법원"), ("부천지원", "인천지방법원 부천지원"),
    ("서산지원", "대전지방법원 서산지원"), ("서울남부지방법원", "서울남부지방법원"),
    ("서울동부지방법원", "서울동부지방법원"), ("서울북부지방법원", "서울북부지방법원"),
    ("서울서부지방법원", "서울서부지방법원"), ("서울중앙지방법원", "서울중앙지방법원"),
    ("성남지원", "수원지방법원 성남지원"), ("수원지방법원", "수원지방법원"),
    ("순천지원", "광주지방법원 순천지원"), ("안동지원", "대구지방법원 안동지원"),
    ("영덕지원", "대구지방법원 영덕지원"), ("영동지원", "청주지방법원 영동지원"),
    ("영월지원", "춘천지방법원 영월지원"), ("울산지방법원", "울산지방법원"),
    ("원주지원", "춘천지방법원 원주지원"), ("의정부지방법원", "의정부지방법원"),
    ("인천지방법원", "인천지방법원"), ("장흥지원", "광주지방법원 장흥지원"),
    ("전주지방법원", "전주지방법원"), ("정읍지원", "전주지방법원 정읍지원"),
    ("제주지방법원", "제주지방법원"), ("제천지원", "청주지방법원 제천지원"),
    ("진주지원", "창원지방법원 진주지원"), ("창원지방법원", "창원지방법원"),
    ("천안지원", "대전지방법원 천안지원"), ("청주지방법원", "청주지방법원"),
    ("춘천지방법원", "춘천지방법원"), ("충주지원", "청주지방법원 충주지원"),
    ("통영지원", "창원지방법원 통영지원"), ("평택지원", "수원지방법원 평택지원"),
    ("포항지원", "대구지방법원 포항지원"), ("홍성지원", "대전지방법원 홍성지원"),
]


@pytest.mark.parametrize(("court", "expected"), DB_COURTS)
def test_db_courts_all_mapped(court, expected):
    """DB 실측 53종 전수 — 지원은 본원이 붙고, 본원은 그대로."""
    assert full_court_name(court) == expected


def test_unknown_court_passes_through():
    """모르는 값을 지어내지 않는다 — 그대로 반환."""
    assert full_court_name("미지의지원") == "미지의지원"


def test_empty_and_none_safe():
    assert full_court_name(None) == ""
    assert full_court_name("") == ""
    assert full_court_name("  고양지원  ") == "의정부지방법원 고양지원"


def test_detail_page_shows_full_court_name(tmp_path, monkeypatch):
    """상세 헤더·토스트가 정식 명칭을 노출한다 — 축약형 단독 노출 금지."""
    from src import store
    from src.models import ScoredListing
    from src.web import create_app

    db = tmp_path / "t_court.db"
    conn = store.connect(str(db))
    s = ScoredListing(
        case_no="2024타경777", apt_name="테스트단지", address="경기 고양시 일산동구 1-2",
        property_type="아파트", area_m2=84.0, appraisal_price=500_000_000,
        min_bid_price=200_000_000, fail_count=1, sale_date="2026-08-01",
        est_market_price=400_000_000, matched_trades=5, confidence=1.0,
        real_acquisition_cost=202_200_000, expected_profit=197_800_000, gap_rate=0.49,
        gap_score=90.0, rights_score=100.0, liquidity_score=80.0, arb_score=88.0,
        grade="차익 유력", court="고양지원", item_no="1", rights_verified=True,
        market_band_low=380_000_000, profit_low=177_800_000,
    )
    store.replace_all(conn, [s])
    store.save_rights(conn, [{
        "court": s.court, "case_no": s.case_no, "item_no": s.item_no,
        "surviving_rights": "", "senior_lien": "2020. 1. 1. 근저당권", "lien_note": "",
        "remark": "특이사항 없음", "claim_amt": None, "demand_end": "",
        "spec_write_ymd": "2026-06-01", "court_dept": "",
        "schedule": json.dumps([{"ymd": "2026-08-01", "kind": "매각기일",
                                 "result": "", "price": 200000000}], ensure_ascii=False),
        "appraisal_notes": "[]", "fetched_at": "2026-07-24",
    }])
    conn.close()

    monkeypatch.setenv("AUCTION_DB", str(db))
    body = create_app().test_client().get("/property/2024타경777").get_data(as_text=True)
    assert "의정부지방법원 고양지원 2024타경777" in body        # 헤더 사건번호 옆
    assert "의정부지방법원 고양지원 선택 후 붙여넣" in body      # 법원 원문 토스트 안내
