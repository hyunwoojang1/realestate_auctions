"""홈 로딩 비용 절감 — 목록 경로가 감정요항(14.1MB)을 읽지 않는지 고정.

배경(실측 2026-07-23): `listing_rights` 21.5MB 중 `appraisal_notes` 가 14.1MB(66%)인데
목록(홈·지도)은 이 컬럼을 전혀 쓰지 않는다. `SELECT *` 로 긁으면 홈 요청마다 그 14MB 를
읽고, 클라우드(Supabase REST)에서는 매번 전송해 콜드 로딩을 지배했다.

이 테스트가 깨지면 (a) 목록이 감정요항을 쓰기 시작했거나 (b) 실수로 SELECT * 로 되돌아간 것.
어느 쪽이든 성능 회귀이므로 의도를 확인하고 고쳐야 한다.
"""
import json

from src import store, store_rest
from src.courtauction_detail import CaseRights, resale_history, summarize

RIGHT = {
    "court": "서울중앙지방법원", "case_no": "2024타경900", "item_no": "1",
    "surviving_rights": "", "senior_lien": "2020. 1. 1. 근저당권", "lien_note": "",
    "remark": "비고 텍스트", "claim_amt": 100_000_000, "demand_end": "2025-01-01",
    "spec_write_ymd": "2026-06-01", "court_dept": "경매1계",
    "schedule": json.dumps([{"ymd": "2026-08-01", "kind": "매각기일", "result": "", "price": 1}],
                           ensure_ascii=False),
    "appraisal_notes": json.dumps([{"label": "감정 요항", "text": "가" * 5000}],
                                  ensure_ascii=False),
    "fetched_at": "2026-07-23",
}


def _conn(tmp_path):
    conn = store.connect(str(tmp_path / "t.db"))
    store.save_rights(conn, [RIGHT])
    return conn


def test_list_query_excludes_appraisal_notes(tmp_path):
    """목록용 전량 조회는 감정요항을 가져오지 않는다."""
    conn = _conn(tmp_path)
    try:
        rows = store.fetch_all_rights(conn)
        assert len(rows) == 1
        assert "appraisal_notes" not in rows[0], "감정요항이 목록 조회에 다시 들어왔다"
        # 판정에 필요한 컬럼은 전부 살아 있어야 한다
        for col in ("court", "case_no", "item_no", "surviving_rights", "senior_lien",
                    "lien_note", "remark", "schedule"):
            assert col in rows[0], col
    finally:
        conn.close()


def test_detail_query_still_has_appraisal_notes(tmp_path):
    """상세(단건)는 감정요항을 그대로 가져온다 — 화면에서 쓰기 때문."""
    conn = _conn(tmp_path)
    try:
        row = store.load_rights(conn, "서울중앙지방법원", "2024타경900", "1")
        assert row is not None
        assert row.get("appraisal_notes"), "상세에서 감정요항이 사라졌다"
    finally:
        conn.close()


def test_from_row_survives_missing_column(tmp_path):
    """부분 SELECT 행도 안전하게 파싱된다 — 없는 컬럼은 기본값(빈 리스트)."""
    conn = _conn(tmp_path)
    try:
        row = store.fetch_all_rights(conn)[0]
        cr = CaseRights.from_row(row)
        assert cr.appraisal_notes == [], "없는 컬럼이 None 이 되면 렌더에서 TypeError"
        assert isinstance(cr.schedule, list)
        # 목록이 실제로 쓰는 두 경로가 모두 동작해야 한다
        assert summarize(cr) is not None
        assert resale_history(cr.schedule) is None      # 매각 이력 없음
    finally:
        conn.close()


def test_full_row_still_parses(tmp_path):
    """전체 컬럼 행(상세 경로)은 종전과 동일하게 감정요항이 채워진다 — 무회귀."""
    conn = _conn(tmp_path)
    try:
        cr = CaseRights.from_row(store.load_rights(conn, "서울중앙지방법원", "2024타경900", "1"))
        assert len(cr.appraisal_notes) == 1
        assert cr.appraisal_notes[0]["label"] == "감정 요항"
    finally:
        conn.close()


def test_rest_and_sqlite_use_same_column_set():
    """클라우드(REST)와 로컬(SQLite)이 같은 컬럼 집합을 쓴다 — 두 경로가 갈리면 배지가 달라진다."""
    assert store_rest._RIGHTS_LIST_COLS is store._RIGHTS_LIST_COLS
    assert "appraisal_notes" not in store._RIGHTS_LIST_COLS
    assert "schedule" in store._RIGHTS_LIST_COLS


# ── 파생 맵 캐시의 지문 ──

def test_fingerprint_distinguishes_same_shape_different_content():
    """행 수·수집일이 같아도 **내용이 다르면** 지문이 달라야 한다.

    이 가드가 없으면 서로 다른 데이터가 같은 캐시 키를 얻어 스테일 배지를 서빙한다
    (실제로 이 충돌이 테스트에서 재현됐다 — 2026-07-23).
    """
    from src.web import _rights_fingerprint
    base = {"fetched_at": "2026-07-23", "remark": "", "surviving_rights": "",
            "lien_note": "", "senior_lien": ""}
    a = [{**base, "schedule": '[{"result":"유찰"}]'}]
    b = [{**base, "schedule": '[{"result":"매각"},{"result":"미납"}]'}]
    assert _rights_fingerprint(a) != _rights_fingerprint(b)


def test_fingerprint_is_stable_for_identical_rows():
    """같은 내용이면 지문이 같아야 캐시가 실제로 적중한다."""
    from src.web import _rights_fingerprint
    rows = [{"fetched_at": "2026-07-23", "schedule": "[]", "remark": "x",
             "surviving_rights": "", "lien_note": "", "senior_lien": "y"}]
    assert _rights_fingerprint(rows) == _rights_fingerprint(list(rows))


def test_fingerprint_catches_equal_length_edit():
    """(적대감사 F5) 등길이 수정 — '유찰'→'매각' + 같은 자릿수 금액 정정.

    기일 결과 어휘('유찰','매각','변경','미납')가 전부 2자라 등길이 정정은 계통적이다.
    길이합 지문은 이를 놓쳐 스테일 배지(특히 clean 오표시)를 서빙했다 — crc32 는 잡아야 한다.
    """
    from src.web import _rights_fingerprint
    base = {"fetched_at": "2026-07-23 10:00:00", "remark": "", "surviving_rights": "",
            "lien_note": "", "senior_lien": ""}
    a = [{**base, "schedule": '[{"result":"유찰","price":50000000}]'}]
    b = [{**base, "schedule": '[{"result":"매각","price":80000000}]'}]
    assert _rights_fingerprint(a) != _rights_fingerprint(b)


def test_fingerprint_catches_field_move():
    """(적대감사 F5) 필드 간 문구 이동 — remark→surviving_rights.

    길이 총합은 불변이지만 summarize 판정(clean↔burden)이 뒤집히는 실충돌 클래스(실행 재현됨).
    """
    from src.web import _rights_fingerprint
    base = {"fetched_at": "2026-07-23 10:00:00", "schedule": "[]", "lien_note": "",
            "senior_lien": ""}
    text = "임차권등기 잔액을 매수인이 인수함"
    a = [{**base, "remark": text, "surviving_rights": ""}]
    b = [{**base, "remark": "", "surviving_rights": text}]
    assert _rights_fingerprint(a) != _rights_fingerprint(b)


def test_fingerprint_includes_data_source():
    """다른 DB를 보면 지문도 달라야 한다(프로세스 캐시가 DB 간에 새지 않게)."""
    import os

    from src.web import DB_ENV, _rights_fingerprint
    rows = [{"fetched_at": "2026-07-23", "schedule": "[]", "remark": "",
             "surviving_rights": "", "lien_note": "", "senior_lien": ""}]
    old = os.environ.get(DB_ENV)
    try:
        os.environ[DB_ENV] = "a.db"
        fa = _rights_fingerprint(rows)
        os.environ[DB_ENV] = "b.db"
        fb = _rights_fingerprint(rows)
        assert fa != fb
    finally:
        if old is None:
            os.environ.pop(DB_ENV, None)
        else:
            os.environ[DB_ENV] = old
