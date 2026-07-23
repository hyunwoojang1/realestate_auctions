"""재매각 이력 판정 테스트 — 낙찰됐다가 미납·불허가로 되돌아온 물건.

계약:
  · 판정 단위는 **물건**(court, case_no, item_no) — listing_rights.schedule 이 이미 물건 단위다.
  · '매각' 뒤에 더 늦은 기일이 있어야 '되돌아온 것'으로 센다(방금 낙찰된 건 재매각이 아니다).
  · 낙찰가(dspslAmt)는 대법원이 안 주므로 **그 회차 최저입찰가**만 보관한다(라벨 혼동 금지).
"""
from src.courtauction_detail import resale_history

# 실측 패턴(창원 2024타경36-1) — 저장 schedule 은 최신순(내림차순)이다.
REAL_DESC = [
    {"ymd": "2026-07-30", "kind": "매각결정기일", "result": "", "price": 0},
    {"ymd": "2026-07-23", "kind": "매각기일", "result": "", "price": 111230000},
    {"ymd": "2026-06-17", "kind": "대금지급기한", "result": "미납", "price": 0},
    {"ymd": "2026-05-21", "kind": "매각결정기일", "result": "최고가매각허가결정", "price": 0},
    {"ymd": "2026-05-14", "kind": "매각기일", "result": "매각", "price": 111230000},
    {"ymd": "2026-01-27", "kind": "매각기일", "result": "유찰", "price": 158900000},
    {"ymd": "2025-04-15", "kind": "매각기일", "result": "유찰", "price": 227000000},
]


def test_none_when_no_sale_in_history():
    """유찰만 있는 평범한 물건은 재매각이 아니다 — None(배지 미표시)."""
    sched = [
        {"ymd": "2026-07-23", "kind": "매각기일", "result": "", "price": 116130000},
        {"ymd": "2026-06-17", "kind": "매각기일", "result": "유찰", "price": 165900000},
        {"ymd": "2026-05-14", "kind": "매각기일", "result": "유찰", "price": 237000000},
    ]
    assert resale_history(sched) is None


def test_none_on_empty_or_missing_schedule():
    assert resale_history([]) is None
    assert resale_history(None) is None


def test_detects_unpaid_resale():
    r = resale_history(REAL_DESC)
    assert r is not None
    assert r.sold_count == 1
    assert r.last_sold_ymd == "2026-05-14"
    assert r.last_sold_floor == 111_230_000
    assert r.reason == "대금 미납"


def test_input_order_does_not_matter():
    """저장은 최신순이지만 오름차순으로 줘도 같은 결과여야 한다(정렬을 내부에서 보장)."""
    asc = list(reversed(REAL_DESC))
    assert resale_history(asc) == resale_history(REAL_DESC)


def test_detects_disallowed_sale():
    """매각 후 '최고가매각불허가결정' — 법원이 매각을 불허한 경우."""
    sched = [
        {"ymd": "2026-08-05", "kind": "매각기일", "result": "", "price": 98700000},
        {"ymd": "2025-11-05", "kind": "매각결정기일", "result": "최고가매각불허가결정", "price": 0},
        {"ymd": "2025-10-29", "kind": "매각기일", "result": "매각", "price": 98700000},
        {"ymd": "2025-09-24", "kind": "매각기일", "result": "유찰", "price": 141000000},
    ]
    r = resale_history(sched)
    assert r.reason == "매각 불허가"
    assert r.sold_count == 1


def test_detects_permission_cancelled():
    sched = [
        {"ymd": "2026-08-05", "kind": "매각기일", "result": "", "price": 251300000},
        {"ymd": "2025-07-22", "kind": "매각결정기일", "result": "최고가매각허가취소결정", "price": 0},
        {"ymd": "2025-07-09", "kind": "매각결정기일", "result": "최고가매각허가결정", "price": 0},
        {"ymd": "2025-07-02", "kind": "매각기일", "result": "매각", "price": 251300000},
    ]
    assert resale_history(sched).reason == "매각허가 취소"


def test_counts_repeated_sales_and_uses_latest():
    """3번 낙찰 → 3번 미납(실측: 우신미가뷰아파트). 횟수를 세고 '마지막' 매각을 기준으로 삼는다."""
    sched = [
        {"ymd": "2026-08-01", "kind": "매각기일", "result": "", "price": 2072000},
        {"ymd": "2026-03-10", "kind": "대금지급기한", "result": "미납", "price": 0},
        {"ymd": "2026-02-01", "kind": "매각결정기일", "result": "최고가매각허가결정", "price": 0},
        {"ymd": "2026-01-20", "kind": "매각기일", "result": "매각", "price": 5000000},
        {"ymd": "2025-09-10", "kind": "대금지급기한", "result": "미납", "price": 0},
        {"ymd": "2025-08-01", "kind": "매각결정기일", "result": "최고가매각허가결정", "price": 0},
        {"ymd": "2025-07-15", "kind": "매각기일", "result": "매각", "price": 12000000},
        {"ymd": "2025-04-20", "kind": "대금지급기한", "result": "미납", "price": 0},
        {"ymd": "2025-03-20", "kind": "매각기일", "result": "매각", "price": 25177000},
    ]
    r = resale_history(sched)
    assert r.sold_count == 3
    assert r.last_sold_ymd == "2026-01-20"       # 가장 최근 매각
    assert r.last_sold_floor == 5_000_000
    assert r.reason == "대금 미납"


def test_sale_with_nothing_after_is_not_resale():
    """방금 낙찰돼 뒤에 아무 기일도 없으면 '되돌아온 것'이 아니다 — 배지 미표시."""
    sched = [
        {"ymd": "2026-07-23", "kind": "매각기일", "result": "매각", "price": 111230000},
        {"ymd": "2026-06-17", "kind": "매각기일", "result": "유찰", "price": 158900000},
    ]
    assert resale_history(sched) is None


def test_reason_unknown_when_no_terminal_event():
    """매각 뒤에 허가결정만 있고 종결 사유가 없으면 사유는 '미상' — 사실(재매각)은 그대로 표시."""
    sched = [
        {"ymd": "2026-08-01", "kind": "매각기일", "result": "", "price": 80000000},
        {"ymd": "2026-05-21", "kind": "매각결정기일", "result": "최고가매각허가결정", "price": 0},
        {"ymd": "2026-05-14", "kind": "매각기일", "result": "매각", "price": 100000000},
    ]
    r = resale_history(sched)
    assert r is not None
    assert r.reason == "사유 미상"


def test_malformed_rows_do_not_crash():
    """크롤 드리프트 방어 — 키 누락·타입 이상에도 예외 없이 None/정상 판정."""
    assert resale_history([{"nope": 1}, {}]) is None
    weird = [
        {"ymd": None, "result": "매각", "price": None},
        {"ymd": "2026-07-23", "result": "", "price": "abc"},
    ]
    r = resale_history(weird)          # ymd 없는 매각은 정렬 하위 → 뒤에 기일 존재
    assert r is None or r.last_sold_floor is None


def test_floor_is_not_a_winning_bid():
    """last_sold_floor 는 '그 회차 최저입찰가'다 — 낙찰가가 아니다(대법원이 안 준다).

    이 계약이 깨지면 화면이 최저가를 낙찰가로 오표시한다.
    """
    r = resale_history(REAL_DESC)
    sold_row = next(x for x in REAL_DESC if x["result"] == "매각")
    assert r.last_sold_floor == sold_row["price"]


# ────────────────────────── 웹 배선 ──────────────────────────

def _app_client(tmp_db):
    import os
    os.environ["AUCTION_DB"] = str(tmp_db)
    from src.web import create_app
    return create_app().test_client()


def _seed(tmp_path, schedule):
    """샘플 물건 1건 + 주어진 기일 이력을 가진 rights 행 하나를 담은 임시 DB."""
    import json

    from src import store
    from src.models import ScoredListing
    db = tmp_path / "t.db"
    conn = store.connect(str(db))     # connect 가 스키마 생성·마이그레이션까지 담당
    s = ScoredListing(
        case_no="2024타경777", apt_name="테스트단지", address="서울 강남구", property_type="아파트",
        area_m2=84.0, appraisal_price=500_000_000, min_bid_price=200_000_000, fail_count=3,
        sale_date="2026-08-01", est_market_price=400_000_000, matched_trades=5, confidence=1.0,
        real_acquisition_cost=202_200_000, expected_profit=197_800_000, gap_rate=0.49,
        gap_score=90.0, rights_score=100.0, liquidity_score=80.0, arb_score=88.0,
        grade="차익 유력", court="서울중앙지방법원", item_no="1", rights_verified=True,
        market_band_low=380_000_000, profit_low=177_800_000,
    )
    store.replace_all(conn, [s])
    store.save_rights(conn, [{
        "court": "서울중앙지방법원", "case_no": "2024타경777", "item_no": "1",
        "surviving_rights": "", "senior_lien": "2020. 1. 1. 근저당권", "lien_note": "",
        "remark": "", "claim_amt": None, "demand_end": "", "spec_write_ymd": "2026-06-01",
        "court_dept": "", "schedule": json.dumps(schedule, ensure_ascii=False),
        "appraisal_notes": "[]", "fetched_at": "2026-07-23",
    }])
    conn.close()
    return db


RESALE_SCHED = [
    {"ymd": "2026-08-01", "kind": "매각기일", "result": "", "price": 200000000},
    {"ymd": "2026-05-30", "kind": "대금지급기한", "result": "미납", "price": 0},
    {"ymd": "2026-05-10", "kind": "매각결정기일", "result": "최고가매각허가결정", "price": 0},
    {"ymd": "2026-05-03", "kind": "매각기일", "result": "매각", "price": 320000000},
]
PLAIN_SCHED = [
    {"ymd": "2026-08-01", "kind": "매각기일", "result": "", "price": 200000000},
    {"ymd": "2026-06-01", "kind": "매각기일", "result": "유찰", "price": 285000000},
]


def test_detail_shows_resale_banner(tmp_path):
    body = _app_client(_seed(tmp_path, RESALE_SCHED)).get(
        "/property/2024타경777").get_data(as_text=True)
    assert "재매각 물건" in body
    assert "대금 미납" in body
    assert "2026-05-03" in body
    # 낙찰가로 오표시하지 않는다 — 반드시 '최저입찰가'로 라벨링
    assert "최저입찰가" in body


def test_detail_hides_banner_without_history(tmp_path):
    """유찰만 있는 평범한 물건엔 배너가 아예 안 나온다(사용자 요구: 없으면 보여주지 말 것)."""
    body = _app_client(_seed(tmp_path, PLAIN_SCHED)).get(
        "/property/2024타경777").get_data(as_text=True)
    assert "재매각 물건" not in body
    assert "gatebar resale" not in body


def test_list_shows_resale_chip(tmp_path):
    body = _app_client(_seed(tmp_path, RESALE_SCHED)).get("/").get_data(as_text=True)
    assert "↩ 재매각" in body


def test_list_has_no_chip_without_history(tmp_path):
    body = _app_client(_seed(tmp_path, PLAIN_SCHED)).get("/").get_data(as_text=True)
    assert "↩ 재매각" not in body
    assert "테스트단지" in body     # 물건 자체는 정상 노출(칩만 안 붙는 것)
