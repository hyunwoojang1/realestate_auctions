"""listing_detail_raw 원본 보존 (감사체계 2026-07-23) — 저장/복원 왕복 + 마스킹 + 교체."""
from src import store


def _conn(tmp_path):
    return store.connect(str(tmp_path / "raw.db"))


def test_detail_raw_roundtrip(tmp_path):
    conn = _conn(tmp_path)
    payload = {"csBaseInfo": {"userCsNo": "2022타경3289"},
               "dspslGdsDxdyInfo": {"str": "값", "num": 3}}
    store.save_detail_raw(conn, "부산서부지원", "2022타경3289", "1", "pgj15B",
                          payload, fetched_at="2026-07-23")
    got = store.load_detail_raw(conn, "부산서부지원", "2022타경3289", "1", "pgj15B")
    assert got == payload


def test_detail_raw_masks_personal_names(tmp_path):
    conn = _conn(tmp_path)
    payload = {"note": "근저당권자 김민정 지분"}
    store.save_detail_raw(conn, "법원", "2025타경1", "1", "curst", payload)
    got = store.load_detail_raw(conn, "법원", "2025타경1", "1", "curst")
    assert "김민정" not in got["note"]          # 실명이 그대로 저장되면 안 됨(마스킹)
    assert "근저당권자" in got["note"]          # 역할 라벨은 보존


def test_detail_raw_replace_latest_and_missing(tmp_path):
    conn = _conn(tmp_path)
    store.save_detail_raw(conn, "법원", "2025타경1", "1", "pgj15B", {"v": 1}, "d1")
    store.save_detail_raw(conn, "법원", "2025타경1", "1", "pgj15B", {"v": 2}, "d2")
    assert store.load_detail_raw(conn, "법원", "2025타경1", "1", "pgj15B") == {"v": 2}
    assert store.load_detail_raw(conn, "법원", "2025타경1", "1", "curst") is None
    assert store.load_detail_raw(conn, "법원", "없음", "1", "pgj15B") is None
