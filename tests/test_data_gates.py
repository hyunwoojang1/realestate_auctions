"""데이터 품질 게이트 회귀 테스트 — 게이트가 실사고 패턴을 실제로 잡는지 고정.

게이트가 조용히 무력화되면(쿼리 오타·스키마 변경) 다시 사람이 잡아야 하므로,
'심어진 위반을 정확히 검출'과 '깨끗한 DB 는 PASS' 양방향을 모두 검증한다.
"""
from __future__ import annotations

import json

import pytest

from src import data_gates, store


def _mk_db(tmp_path):
    conn = store.connect(str(tmp_path / "g.db"))
    return conn


def _seed(conn, *, case_no="2025타경1", court="법원", item_no="1",
          ptype="아파트", est=None, appraisal=100_000_000, min_bid=80_000_000,
          sale_date="2099-01-01", band=(None, None), profits=(None, None),
          raw_extra=None):
    conn.execute(
        """insert or replace into scored_listings
           (case_no, apt_name, address, property_type, area_m2, appraisal_price,
            min_bid_price, fail_count, sale_date, est_market_price, matched_trades,
            confidence, real_acquisition_cost, expected_profit, gap_rate, gap_score,
            rights_score, liquidity_score, arb_score, grade, court, item_no, doc_id,
            market_scope, market_band_low, market_band_high, profit_low, profit_high,
            market_sample_basis)
           values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (case_no, "T", "주소", ptype, 84.0, appraisal, min_bid, 0, sale_date, est, 5,
         0.9, min_bid, None, None, 0.0, 0.0, 0.0, None, "관심", court, item_no, "",
         "", band[0], band[1], profits[0], profits[1], None))
    raw = {"buldNm": "T동", "buldList": "101동", "pjbBuldList": "", "jimokList": ""}
    raw.update(raw_extra or {})
    conn.execute(
        "insert or replace into raw_listings (uid, doc_id, court, case_no, item_no, raw_json, fetched_at) "
        "values (?,?,?,?,?,?,?)",
        (f"{court}|{case_no}|{item_no}", "", court, case_no, item_no,
         json.dumps(raw, ensure_ascii=False), "2026-07-10"))
    conn.commit()


def test_clean_db_passes_all(tmp_path):
    conn = _mk_db(tmp_path)
    _seed(conn)
    results = data_gates.run_gates(conn)
    assert data_gates.all_pass(results), data_gates.report(results)


def test_gate_type_physical_catches_land_disguise(tmp_path):
    conn = _mk_db(tmp_path)
    _seed(conn, raw_extra={"buldNm": "", "buldList": "", "jimokList": "대"})
    r = data_gates.gate_type_physical(conn)
    assert not r.ok and r.count == 1


def test_gate_extreme_est_catches_mismatch(tmp_path):
    conn = _mk_db(tmp_path)
    _seed(conn, est=600_000_000, appraisal=140_000_000)   # 4.3배 — 금오 사고 패턴
    r = data_gates.gate_extreme_est(conn)
    assert not r.ok and r.count == 1


def test_gate_share_sale_catches_bigo(tmp_path):
    conn = _mk_db(tmp_path)
    _seed(conn, est=500_000_000, raw_extra={"mulBigo": "지분매각(공유자 우선매수)"})
    r = data_gates.gate_share_sale(conn)
    assert not r.ok and r.count == 1


def test_gate_stale_sale_catches_expired(tmp_path):
    conn = _mk_db(tmp_path)
    _seed(conn, sale_date="2020-01-01")
    r = data_gates.gate_stale_sale(conn)
    assert not r.ok and r.count == 1


def test_gate_band_order_catches_inverted(tmp_path):
    conn = _mk_db(tmp_path)
    _seed(conn, band=(500_000_000, 400_000_000))   # low > high
    r = data_gates.gate_band_order(conn)
    assert not r.ok and r.count == 1


def test_gate_crash_reports_fail_not_silent(tmp_path, monkeypatch):
    """게이트 함수가 예외로 죽어도 조용히 통과되지 않고 FAIL 로 드러나야 한다."""
    conn = _mk_db(tmp_path)

    def boom(_):
        raise RuntimeError("gate bug")

    results = data_gates.run_gates(conn, gates=[boom])
    assert len(results) == 1 and not results[0].ok and "gate bug" in results[0].error


@pytest.mark.parametrize("scope", ["share_sale", "appraisal_mismatch"])
def test_new_scopes_have_ui_labels(scope):
    """새 scope 가 상세 페이지 라벨 사전에 등록돼 있어야(원문 코드 노출 방지)."""
    html = open("templates/detail.html", encoding="utf-8").read()
    assert f"'{scope}'" in html
