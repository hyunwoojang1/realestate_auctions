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
    """last_sold_floor 는 '그 회차 최저입찰가'다 — 낙찰가가 아니다.

    두 값은 별도 필드로만 존재해야 한다. 이 계약이 깨지면 화면이 최저가를 낙찰가로 오표시한다.
    """
    r = resale_history(REAL_DESC)
    sold_row = next(x for x in REAL_DESC if x["result"] == "매각")
    assert r.last_sold_floor == sold_row["price"]
    assert r.last_sold_price is None      # schedule 에 sold 키가 없으면 낙찰가는 '미상'


# ── 실제 낙찰가(maeAmt → schedule.sold) ──

def test_reads_actual_sold_price_when_injected():
    """백필이 넣은 sold 키를 낙찰가로 읽는다 — 최저입찰가와 별개 필드."""
    sched = [dict(r) for r in REAL_DESC]
    for r in sched:
        if r["result"] == "매각":
            r["sold"] = 128_000_000        # 최저 111,230,000 위로 써낸 실제 낙찰가
    r = resale_history(sched)
    assert r.last_sold_price == 128_000_000
    assert r.last_sold_floor == 111_230_000     # 최저가는 그대로 보존


def test_sold_price_uses_latest_sale_round():
    """여러 번 낙찰된 물건은 **가장 최근** 매각 회차의 낙찰가를 쓴다."""
    sched = [
        {"ymd": "2026-08-01", "kind": "매각기일", "result": "", "price": 2072000},
        {"ymd": "2026-03-10", "kind": "대금지급기한", "result": "미납", "price": 0},
        {"ymd": "2026-01-20", "kind": "매각기일", "result": "매각", "price": 5000000,
         "sold": 6_100_000},
        {"ymd": "2025-09-10", "kind": "대금지급기한", "result": "미납", "price": 0},
        {"ymd": "2025-03-20", "kind": "매각기일", "result": "매각", "price": 25177000,
         "sold": 31_000_000},
    ]
    r = resale_history(sched)
    assert r.sold_count == 2
    assert r.last_sold_ymd == "2026-01-20"
    assert r.last_sold_price == 6_100_000


def test_bad_sold_values_are_ignored():
    """0·음수·문자열 같은 쓰레기 값은 낙찰가로 인정하지 않는다(미상 폴백)."""
    for bad in (0, -1, "", "abc", None, 3.5):
        sched = [dict(r) for r in REAL_DESC]
        for r in sched:
            if r["result"] == "매각":
                r["sold"] = bad
        assert resale_history(sched).last_sold_price is None, bad


# ── 백필 스크립트 ──

def test_backfill_injects_into_latest_sale_round():
    from deploy.backfill_sold_amount import inject
    sched = [dict(r) for r in REAL_DESC]
    new, changed = inject(sched, 128_000_000)
    assert changed is True
    sold_rows = [r for r in new if r["result"] == "매각"]
    assert sold_rows[0]["sold"] == 128_000_000
    # 저장 포맷(최신순) 유지
    assert [r["ymd"] for r in new] == sorted([r["ymd"] for r in new], reverse=True)


def test_backfill_is_idempotent():
    from deploy.backfill_sold_amount import inject
    sched = [dict(r) for r in REAL_DESC]
    new, changed1 = inject(sched, 128_000_000)
    _, changed2 = inject(new, 128_000_000)
    assert changed1 is True and changed2 is False


def test_backfill_skips_when_no_sale_round():
    """매각 회차가 없으면 주입하지 않는다 — 데이터 불일치를 조용히 덮지 않는다."""
    from deploy.backfill_sold_amount import inject
    plain = [{"ymd": "2026-06-01", "kind": "매각기일", "result": "유찰", "price": 285000000}]
    new, changed = inject(plain, 99_000_000)
    assert changed is False
    assert all("sold" not in r for r in new)


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
    # 낙찰가 미수집 물건은 '최저입찰가'로 라벨링하고 미수집임을 밝힌다
    assert "최저입찰가" in body
    assert "낙찰가는 미수집" in body


def test_detail_shows_actual_sold_price(tmp_path):
    """낙찰가가 백필된 물건은 '실제 낙찰가'를 보여주고, 최저가와 섞지 않는다."""
    sched = [dict(r) for r in RESALE_SCHED]
    for r in sched:
        if r["result"] == "매각":
            r["sold"] = 350_000_000        # 최저 3.2억 → 실제 3.5억 낙찰
    body = _app_client(_seed(tmp_path, sched)).get(
        "/property/2024타경777").get_data(as_text=True)
    assert "실제 낙찰가" in body
    assert "3.50억" in body
    assert "낙찰가는 미수집" not in body      # 값이 있으면 미수집 문구가 뜨면 안 된다


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
