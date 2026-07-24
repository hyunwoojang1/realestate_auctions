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


def test_detail_raw_strips_photo_binaries(tmp_path):
    """사진 바이너리(picFile)는 보존하지 않는다 — 감사는 서류 '텍스트'를 본다.

    사고 2026-07-23: 응답 전체를 저장했더니 한 물건에 csPicLst 34MB(사진 113장)가 딸려 들어와
    auction.db 가 267MB → 4.0GB 로 부풀었다(2,283행 3.9GB → 정리 후 5.7MB).
    사진은 listing_photos 에 URL로 따로 저장되므로 원본 보존에서 뺀다. **메타는 남긴다.**
    """
    conn = _conn(tmp_path)
    payload = {
        "dspslGdsDxdyInfo": {"gdsSpcfcWrtYmd": "20260406", "ndstrcRghCtt": "인수권리 문구"},
        "csPicLst": [
            {"picFileUrl": "http://x/1.jpg", "picTitlNm": "전경", "picFile": "A" * 50_000},
            {"picFileUrl": "http://x/2.jpg", "picTitlNm": "내부", "picFile": "B" * 50_000},
        ],
    }
    store.save_detail_raw(conn, "법원", "2025타경9", "1", "pgj15B", payload)
    got = store.load_detail_raw(conn, "법원", "2025타경9", "1", "pgj15B")

    assert all("picFile" not in p for p in got["csPicLst"])          # 바이너리 제거
    assert got["csPicLst"][0]["picFileUrl"] == "http://x/1.jpg"      # 메타는 보존
    assert got["csPicLst"][1]["picTitlNm"] == "내부"
    assert got["dspslGdsDxdyInfo"]["ndstrcRghCtt"] == "인수권리 문구"  # 서류 텍스트 무손실
    stored = conn.execute("SELECT LENGTH(payload) FROM listing_detail_raw").fetchone()[0]
    assert stored < 5_000, f"사진이 섞여 들어갔다(압축 {stored}바이트)"
