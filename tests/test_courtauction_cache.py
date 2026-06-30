"""courtauction_cache — 증분 diff(신규/변경/유지/소멸)·저장 (네트워크 없음)."""
from __future__ import annotations

from src import courtauction_cache as cc
from src.courtauction_fields import parse_row


def _rec(docid, case, minbid, fail, sale="20260701"):
    return parse_row({
        "docid": docid, "srnSaNo": case, "gamevalAmt": "100000000",
        "minmaePrice": str(minbid), "yuchalCnt": str(fail), "maeGiil": sale,
        "maemulSer": "1",
    })


def test_record_key_prefers_docid():
    r = _rec("DOC1", "2025타경1", 5000, 1)
    assert cc.record_key(r) == "DOC1"


def test_diff_classifies_new_changed_unchanged_removed():
    a = _rec("A", "2025타경1", 50_000_000, 1)
    b = _rec("B", "2025타경2", 40_000_000, 2)
    cache = {
        "A": {"min_bid_price": 50_000_000, "fail_count": 1, "sale_date": "2026-07-01",
              "appraisal_price": 100_000_000, "case_no": "2025타경1", "address": ""},
        "C": {"min_bid_price": 30_000_000, "fail_count": 3, "sale_date": "2026-07-01",
              "appraisal_price": 100_000_000, "case_no": "2025타경3", "address": ""},
    }
    # A: 동일 → 유지, B: 캐시에 없음 → 신규, C: 이번에 없음 → 소멸
    d = cc.diff_records([a, b], cache)
    assert [r.case_no for r in d.new] == ["2025타경2"]
    assert [r.case_no for r in d.unchanged] == ["2025타경1"]
    assert d.removed == ["C"]
    assert d.changed == []


def test_diff_detects_price_drop_as_changed():
    # 같은 docid인데 유찰 1회 더 → 최저가 하락 = 변경
    cache = {"A": {"min_bid_price": 50_000_000, "fail_count": 1, "sale_date": "2026-07-01",
                   "appraisal_price": 100_000_000, "case_no": "2025타경1", "address": ""}}
    dropped = _rec("A", "2025타경1", 40_000_000, 2)
    d = cc.diff_records([dropped], cache)
    assert len(d.changed) == 1
    rec, prev = d.changed[0]
    assert rec.min_bid_price == 40_000_000
    assert prev["min_bid_price"] == 50_000_000


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "cache.json"
    recs = [_rec("A", "2025타경1", 50_000_000, 1), _rec("B", "2025타경2", 40_000_000, 2)]
    cc.save_cache(recs, path)
    loaded = cc.load_cache(path)
    assert set(loaded) == {"A", "B"}
    assert loaded["A"]["min_bid_price"] == 50_000_000
    # 재실행에서 동일 → 전부 유지, 소멸 없음
    d = cc.diff_records(recs, loaded)
    assert len(d.unchanged) == 2 and not d.removed and not d.new


def test_load_missing_returns_empty(tmp_path):
    assert cc.load_cache(tmp_path / "none.json") == {}
