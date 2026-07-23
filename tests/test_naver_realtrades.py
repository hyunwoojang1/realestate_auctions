"""naver_store(T1) + 국토부 직거래·등기일 파싱(T2) 테스트 — 2026-07-19 complexNo 실거래 개편."""
import sqlite3

import pytest

from src import naver_store as ns
from src.molit_client import parse_apt_trades_xml


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ns.ensure_schema(c)
    yield c
    c.close()


# ---- prices/real 평탄화·upsert ----

# 실측(2026-07-19): 네이버 취소행 deleteYn 값은 'O'(=cdealType과 동일), 'Y' 아님.
# 취소거래는 정상행 쌍둥이(같은 ymd·floor·price)와 함께 온다 → 거래 단위로 취소 판정해야 함.
SAMPLE_REAL = {
    "addedRowCount": 4,
    "totalRowCount": 201,
    "areaNo": "2",
    "realPriceOnMonthList": [
        {"tradeBaseYear": "2026", "tradeBaseMonth": 3, "realPriceList": [
            # 취소거래 — 정상행 + 취소행 쌍둥이(같은 거래). 둘 다 있으면 그 거래는 취소.
            {"tradeYear": "2026", "tradeMonth": 3, "tradeDate": "04", "dealPrice": 28000,
             "floor": 6, "exclusiveArea": "84.96", "representativeArea": "112", "tradeType": "A1"},
            {"tradeYear": "2026", "tradeMonth": 3, "tradeDate": "04", "dealPrice": 28000,
             "floor": 6, "exclusiveArea": "84.96", "deleteYn": "O", "tradeType": "A1"},
        ]},
        {"tradeBaseYear": "2025", "tradeBaseMonth": 10, "realPriceList": [
            {"tradeYear": "2025", "tradeMonth": 10, "tradeDate": "27", "dealPrice": 25000,
             "floor": 1, "exclusiveArea": "84.96", "tradeType": "A1"},
        ]},
        {"tradeBaseYear": "2025", "tradeBaseMonth": 6, "realPriceList": [
            {"tradeYear": "2025", "tradeMonth": 6, "tradeDate": "17", "dealPrice": 26850,
             "floor": 10, "exclusiveArea": "84.96", "tradeType": "A1"},
        ]},
    ],
}


def test_flatten_real_response():
    rows = ns.flatten_real_response(SAMPLE_REAL)
    assert len(rows) == 4
    assert rows[0]["dealPrice"] == 28000


def test_flatten_none_and_empty():
    assert ns.flatten_real_response(None) == []
    assert ns.flatten_real_response({}) == []


def test_upsert_real_trades_units_and_cancel_twin(conn):
    """C1 회귀 방지 — 취소거래(정상+취소 쌍둥이)가 거래 단위로 deleted=1이 되어 엔진에서 빠진다."""
    rows = ns.flatten_real_response(SAMPLE_REAL)
    n = ns.upsert_real_trades(conn, "24958", "2", rows, "2026-07-19")
    assert n == 3   # 고유 거래 3건(취소 쌍둥이는 1건으로 합쳐짐)
    # 엔진 로더는 해제거래 제외 → 2.8억(취소) 빠지고 2건만
    live = ns.load_real_trades(conn, "24958", "2")
    assert len(live) == 2
    assert {r["price"] for r in live} == {250_000_000, 268_500_000}
    assert 280_000_000 not in {r["price"] for r in live}   # ★ 취소거래가 시세에서 빠짐(핵심)
    # include_deleted면 3건, 2.8억이 deleted=1
    allr = ns.load_real_trades(conn, "24958", "2", include_deleted=True)
    assert len(allr) == 3
    cancelled = [r for r in allr if r["deleted"] == 1]
    assert len(cancelled) == 1 and cancelled[0]["price"] == 280_000_000


def test_upsert_idempotent(conn):
    rows = ns.flatten_real_response(SAMPLE_REAL)
    ns.upsert_real_trades(conn, "24958", "2", rows, "t1")
    ns.upsert_real_trades(conn, "24958", "2", rows, "t2")   # 재크롤 멱등 — 고유 거래 3건 유지
    assert len(ns.load_real_trades(conn, "24958", "2", include_deleted=True)) == 3


# ---- 단지 메타 / KB 시계열 / 호가 ----

def test_upsert_complex_and_overview(conn):
    detail = {"complexDetail": {
        "complexNo": "24958", "complexName": "진천태왕아너스1단지", "cortarNo": "2729011700",
        "totalHouseholdCount": 182, "totalDongCount": 2, "useApproveYmd": "20080222",
        "batlRatio": 297, "btlRatio": 22, "parkingPossibleCount": 200,
        "parkingCountByHousehold": "1.09", "constructionCompanyName": "태왕",
        "dealCount": 3, "leaseCount": 1, "rentCount": 0, "latitude": 35.8, "longitude": 128.5}}
    ov = {"leasePerDealRate": "82~85%", "minPrice": 25000, "maxPrice": 31000}
    cno = ns.upsert_complex(conn, detail, "2026-07-19", overview=ov)
    assert cno == "24958"
    row = conn.execute("SELECT * FROM naver_complexes WHERE complex_no='24958'").fetchone()
    assert row["household_count"] == 182
    assert row["lease_per_deal_rate"] == "82~85%"
    assert row["min_price"] == 250_000_000   # 만원→원
    assert row["use_approve_ymd"] == "20080222"


def test_upsert_complex_missing_no(conn):
    assert ns.upsert_complex(conn, {"complexDetail": {}}, "t") is None


def test_lease_rate_str_variants():
    """H1 — 전세가율 정규화: 0.0/0/None은 무데이터(''), 문자열·유효숫자는 보존."""
    assert ns._lease_rate_str(0.0) == ""       # 소스 무데이터(현재 전건) — falsy로 안 죽고 명시적 ''
    assert ns._lease_rate_str(None) == ""
    assert ns._lease_rate_str("82~85%") == "82~85%"
    assert ns._lease_rate_str(83) == "83%"
    assert ns._lease_rate_str(0.83) == "0.83"


def test_upsert_complex_preserve_on_empty(conn):
    """H1 회귀 방지 — 재파싱(overview 없음)이 기존 min/max·전세가율을 파괴하지 않는다."""
    detail = {"complexDetail": {"complexNo": "24958", "complexName": "진천태왕아너스1단지",
                                "totalHouseholdCount": 182}}
    ov = {"leasePerDealRate": "82~85%", "minPrice": 25000, "maxPrice": 31000}
    ns.upsert_complex(conn, detail, "t1", overview=ov)      # 최초: 값 채움
    ns.upsert_complex(conn, detail, "t2", overview=None)    # 재파싱: overview 없음
    row = conn.execute("SELECT * FROM naver_complexes WHERE complex_no='24958'").fetchone()
    assert row["min_price"] == 250_000_000     # ★ 파괴 안 됨(보존)
    assert row["lease_per_deal_rate"] == "82~85%"
    assert row["household_count"] == 182


def test_upsert_complex_zero_counts_update(conn):
    """MEDIUM2 회귀 방지 — 페치 성공한 0(전량 매도)은 반영되고 high-water-mark가 사라진다."""
    detail1 = {"complexDetail": {"complexNo": "24958", "complexName": "진천태왕아너스1단지",
                                 "totalHouseholdCount": 182,
                                 "dealCount": 3, "leaseCount": 2, "rentCount": 1}}
    ov1 = {"minPrice": 25000, "maxPrice": 31000}
    ns.upsert_complex(conn, detail1, "t1", overview=ov1)
    # 재크롤: 매물 전량 소진 — 키는 전달됐고 값이 0
    detail2 = {"complexDetail": {"complexNo": "24958", "complexName": "진천태왕아너스1단지",
                                 "dealCount": 0, "leaseCount": 0, "rentCount": 0}}
    ov2 = {"minPrice": 0, "maxPrice": 0}
    ns.upsert_complex(conn, detail2, "t2", overview=ov2)
    row = conn.execute("SELECT * FROM naver_complexes WHERE complex_no='24958'").fetchone()
    assert row["deal_count"] == 0              # ★ 옛값 3이 남지 않음(핵심)
    assert row["lease_count"] == 0 and row["rent_count"] == 0
    assert row["min_price"] == 0 and row["max_price"] == 0
    assert row["fetched_at"] == "t2"           # 전값이 0이어도 신선도는 갱신
    assert row["household_count"] == 182       # 정적 메타는 결측 보존 유지


def test_upsert_complex_missing_keys_preserve_counts(conn):
    """MEDIUM2 짝 — 키 자체가 없으면(파싱실패·부분응답) 동적 컬럼도 보존된다."""
    detail1 = {"complexDetail": {"complexNo": "24958", "dealCount": 3}}
    ns.upsert_complex(conn, detail1, "t1", overview={"minPrice": 25000})
    detail2 = {"complexDetail": {"complexNo": "24958", "complexName": "진천태왕아너스1단지"}}
    ns.upsert_complex(conn, detail2, "t2", overview=None)   # dealCount·minPrice 키 부재
    row = conn.execute("SELECT * FROM naver_complexes WHERE complex_no='24958'").fetchone()
    assert row["deal_count"] == 3              # 결측 → 보존
    assert row["min_price"] == 250_000_000


def test_upsert_kb_history(conn):
    prices = [
        {"baseYearMonthDay": "20260713", "dealAveragePrice": 28500,
         "dealLowPriceLimit": 27500, "dealUpperPriceLimit": 31000,
         "leaseAveragePrice": 23000, "leasePerDealRate": "80%"},
        {"baseYearMonthDay": "20260706", "dealAveragePrice": 28400},
    ]
    n = ns.upsert_kb_history(conn, "24958", "2", prices, "t")
    assert n == 2
    row = conn.execute("SELECT * FROM naver_kb_history WHERE base_ymd='20260713'").fetchone()
    assert row["deal_avg"] == 285_000_000
    assert row["deal_high"] == 310_000_000


def test_upsert_articles_price_parse(conn):
    arts = [
        {"articleNo": "a1", "dealOrWarrantPrc": "2억 8,000", "areaName": "112",
         "floorInfo": "6/15", "direction": "남향", "tagList": ["역세권", "올수리"],
         "sameAddrCnt": 3, "sameAddrMinPrc": "27,500", "sameAddrMaxPrc": "3억"},
        {"articleNo": "a2", "dealOrWarrantPrc": "31,000"},
    ]
    n = ns.upsert_articles(conn, "24958", arts, "t")
    assert n == 2
    r1 = conn.execute("SELECT * FROM naver_articles WHERE article_no='a1'").fetchone()
    assert r1["price"] == 280_000_000
    assert r1["same_addr_min"] == 275_000_000
    assert r1["same_addr_max"] == 300_000_000
    assert r1["tags"] == "역세권|올수리"
    r2 = conn.execute("SELECT * FROM naver_articles WHERE article_no='a2'").fetchone()
    assert r2["price"] == 310_000_000


# ---- 물건 → 실거래 조회(naver_prices 매핑 경유) ----

def test_real_trades_for_case(conn):
    conn.execute("""CREATE TABLE naver_prices (
        court TEXT, case_no TEXT, item_no TEXT, status TEXT, complex_no TEXT, area_no TEXT,
        match_conf TEXT)""")
    conn.execute("INSERT INTO naver_prices VALUES ('대구서부지원','2025타경32139','1','matched_kb','24958','5','고신뢰')")
    # 저신뢰 매칭은 게이트로 제외됨(하드닝)
    conn.execute("INSERT INTO naver_prices VALUES ('X','저신뢰건','1','matched_kb','24958','5','저신뢰')")
    ns.upsert_real_trades(conn, "24958", "5",
                          ns.flatten_real_response(SAMPLE_REAL), "t")
    rows, cno, ano = ns.real_trades_for_case(conn, "대구서부지원", "2025타경32139", "1")
    assert cno == "24958" and ano == "5"
    assert len(rows) == 2   # 해제 제외
    # 저신뢰 매칭은 주입 제외
    r_low, cno_low, _ = ns.real_trades_for_case(conn, "X", "저신뢰건", "1")
    assert r_low == [] and cno_low == ""
    # 매핑 없는 물건
    rows2, cno2, _ = ns.real_trades_for_case(conn, "없는법원", "없는사건", "")
    assert rows2 == [] and cno2 == ""


# ---- T2: 국토부 직거래·등기일 파싱 ----

MOLIT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response><header><resultCode>000</resultCode></header><body><items>
<item><aptNm>테스트단지</aptNm><excluUseAr>84.96</excluUseAr><dealAmount>28,000</dealAmount>
<dealYear>2026</dealYear><dealMonth>3</dealMonth><umdNm>진천동</umdNm><floor>6</floor>
<dealingGbn>직거래</dealingGbn><rgstDate>26.05.04</rgstDate></item>
<item><aptNm>테스트단지</aptNm><excluUseAr>84.96</excluUseAr><dealAmount>25,000</dealAmount>
<dealYear>2025</dealYear><dealMonth>10</dealMonth><umdNm>진천동</umdNm><floor>1</floor>
<dealingGbn>중개거래</dealingGbn></item>
<item><aptNm>테스트단지</aptNm><excluUseAr>84.96</excluUseAr><dealAmount>29,000</dealAmount>
<dealYear>2025</dealYear><dealMonth>3</dealMonth><umdNm>진천동</umdNm><floor>9</floor></item>
</items></body></response>"""


def test_molit_dealing_gbn_and_rgst_date():
    trades = parse_apt_trades_xml(MOLIT_XML)
    assert len(trades) == 3
    direct, broker, legacy = trades
    assert direct.is_direct is True
    assert direct.dealing_gbn == "직거래"
    assert direct.rgst_date == "26.05.04"
    assert broker.is_direct is False
    assert broker.dealing_gbn == "중개거래"
    # 구년도(필드 없음) — 미상은 False로 단정하지 않음… 아니, is_direct는 False(단정 안 함)
    assert legacy.is_direct is False
    assert legacy.dealing_gbn == ""


# ---- 3c: 단지식별자(N단지/N차) 오매칭 가드 (2026-07-20 야간) ----

def test_naver_match_numeric_suffix_guard():
    from src.naver_match import best_complex
    # 다른 번호는 이름 유사도 높아도 제외
    b, _ = best_complex("노빌리안1", [{"complexName": "노빌리안2", "complexNo": "A"},
                                       {"complexName": "노빌리안1", "complexNo": "B"}])
    assert b["complexNo"] == "B"
    # 다른 번호만 있으면 매칭 없음
    b2, _ = best_complex("영등3차제일", [{"complexName": "영등제일4차", "complexNo": "C"}])
    assert b2 is None
    # 식별자 없는 물건은 종전대로 매칭
    b3, _ = best_complex("래미안", [{"complexName": "래미안1차", "complexNo": "D"}])
    assert b3 is not None
    # 같은 번호(2단지↔2차)는 매칭
    b4, _ = best_complex("성서2단지주공", [{"complexName": "성서주공2차", "complexNo": "E"}])
    assert b4 is not None
