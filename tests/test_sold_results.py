"""매각결과검색 낙찰가 백필(deploy/crawl_sold_results.py) 계약 테스트 — 네트워크 0.

핵심 계약:
  · saNo 14자리 → 'YYYY타경N' 변환(타경=0130 만, 이형은 None)
  · rows_to_updates: maeAmt>0 만 · 응답 중복 행 제거(라이브 실측: 같은 물건 2행)
  · apply_updates: 빈 sold_price 만 채움 — **기존 값(재매각 maeAmt 경로) 절대 불변**
    (낙찰가 정직성 계약), 로컬 미보존 행은 missing 카운트만
"""
import sqlite3

from deploy.crawl_sold_results import (
    EVIDENCE,
    apply_updates,
    rows_to_updates,
    sa_to_case_no,
)
from src import store


def test_sa_to_case_no_converts_takyeong():
    assert sa_to_case_no("20230130002726") == "2023타경2726"
    assert sa_to_case_no("20080130025092") == "2008타경25092"


def test_sa_to_case_no_rejects_non_takyeong_and_malformed():
    assert sa_to_case_no("20230131002726") is None   # 사건구분 0131 ≠ 타경
    assert sa_to_case_no("2023013000272") is None    # 13자리
    assert sa_to_case_no("") is None
    assert sa_to_case_no("2023타경2726xx") is None


def _row(sa="20230130002726", ser="1", amt="324400000"):
    return {"saNo": sa, "maemulSer": ser, "maeAmt": amt}


def test_rows_to_updates_filters_and_dedupes():
    rows = [
        _row(),                                  # 정상
        _row(),                                  # 라이브 실측: 같은 행이 두 번 온다
        _row(ser="2", amt="0"),                  # maeAmt 0 → 제외
        _row(sa="20230131000001"),               # 타경 아님 → 제외
        _row(sa="20240130114251", ser="1", amt="3075860000"),
    ]
    ups = rows_to_updates(rows, "서울중앙지방법원")
    assert [(u["case_no"], u["item_no"], u["sold_price"]) for u in ups] == [
        ("2023타경2726", "1", 324400000),
        ("2024타경114251", "1", 3075860000),
    ]
    assert all(u["court"] == "서울중앙지방법원" for u in ups)


def _sold_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(store.DDL_SOLD)
    conn.executemany(
        "INSERT INTO sold_listings (court, case_no, item_no, sold_price, sold_evidence) "
        "VALUES (?,?,?,?,?)",
        [("서울중앙지방법원", "2023타경2726", "1", None, "disappeared"),
         ("서울중앙지방법원", "2024타경114251", "1", 999, "maeAmt")])  # 재매각 경로 기존값
    conn.commit()
    return conn


def test_apply_updates_fills_only_empty_and_counts():
    conn = _sold_conn()
    ups = [
        {"court": "서울중앙지방법원", "case_no": "2023타경2726", "item_no": "1",
         "sold_price": 324400000},
        {"court": "서울중앙지방법원", "case_no": "2024타경114251", "item_no": "1",
         "sold_price": 3075860000},   # 기존값 999 — 덮으면 안 됨
        {"court": "서울중앙지방법원", "case_no": "2025타경9999", "item_no": "1",
         "sold_price": 100},          # 로컬 미보존
    ]
    filled, existing, missing = apply_updates(conn, ups)
    assert (filled, existing, missing) == (1, 1, 1)
    r = conn.execute("SELECT sold_price, sold_evidence FROM sold_listings "
                     "WHERE case_no='2023타경2726'").fetchone()
    assert (r["sold_price"], r["sold_evidence"]) == (324400000, EVIDENCE)
    # 정직성 계약: 재매각 경로 기존값·출처는 그대로
    r2 = conn.execute("SELECT sold_price, sold_evidence FROM sold_listings "
                      "WHERE case_no='2024타경114251'").fetchone()
    assert (r2["sold_price"], r2["sold_evidence"]) == (999, "maeAmt")
