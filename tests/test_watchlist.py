"""워치리스트 + 차익 변동 알림 테스트 (V2)."""
from src import watchlist


def test_threshold_breach_detected():
    prev = {"A": {"arb_score": 70, "min_bid_price": 400000000, "apt_name": "A"}}
    cur = {"A": {"arb_score": 85, "min_bid_price": 400000000, "apt_name": "A"}}
    ev = watchlist.detect_changes(prev, cur, threshold=80)
    assert any(e["type"] == "차익 임계 돌파" for e in ev)


def test_score_up_and_min_bid_drop():
    prev = {"B": {"arb_score": 50, "min_bid_price": 400000000, "apt_name": "B"}}
    cur = {"B": {"arb_score": 55, "min_bid_price": 350000000, "apt_name": "B"}}
    types = {e["type"] for e in watchlist.detect_changes(prev, cur)}
    assert "스코어 상승" in types and "최저가 하락(유찰)" in types


def test_no_change_no_events():
    snap = {"C": {"arb_score": 60, "min_bid_price": 300000000, "apt_name": "C"}}
    assert watchlist.detect_changes(snap, snap) == []


def test_watchlist_scope_filter():
    prev = {"A": {"arb_score": 50, "min_bid_price": 1, "apt_name": "A"},
            "B": {"arb_score": 50, "min_bid_price": 1, "apt_name": "B"}}
    cur = {"A": {"arb_score": 90, "min_bid_price": 1, "apt_name": "A"},
           "B": {"arb_score": 90, "min_bid_price": 1, "apt_name": "B"}}
    ev = watchlist.detect_changes(prev, cur, watchlist={"A"}, threshold=80)
    assert {e["case_no"] for e in ev} == {"A"}  # B는 워치리스트 밖이라 제외


def test_watchlist_add_remove(tmp_path):
    p = tmp_path / "wl.json"
    watchlist.add_watch("2024타경1", p)
    watchlist.add_watch("2024타경2", p)
    assert watchlist.load_watchlist(p) == {"2024타경1", "2024타경2"}
    watchlist.remove_watch("2024타경1", p)
    assert watchlist.load_watchlist(p) == {"2024타경2"}


def test_snapshot_from_scored_shape():
    from src import pipeline
    snap = watchlist.snapshot_from_scored(pipeline.run())
    assert len(snap) == 6
    any_case = next(iter(snap.values()))
    assert "arb_score" in any_case and "min_bid_price" in any_case
