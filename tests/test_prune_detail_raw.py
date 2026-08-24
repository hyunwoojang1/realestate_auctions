"""고아 정리 신규 2종 테스트 — 2026-08-24 DB감사 HIGH(정리 목록 누락) 대응 검증."""
import sqlite3

from src import store


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO scored_listings (court, case_no, item_no) VALUES ('법원','살아있음','1')")
    for t, extra in [("listing_detail_raw", ", 'pgj15B', X'00', ''"), ("tenant_checks", ", ''")]:
        cols = ("court, case_no, item_no, doc_type, payload, fetched_at"
                if t == "listing_detail_raw" else "court, case_no, item_no, checked_at")
        conn.execute(f"INSERT INTO {t} ({cols}) VALUES ('법원','살아있음','1'{extra})")
        conn.execute(f"INSERT INTO {t} ({cols}) VALUES ('법원','고아','1'{extra})")
    conn.commit()


def test_prune_detail_raw_and_tenant_checks_remove_only_orphans():
    conn = store.connect(":memory:")
    # tenant_checks 는 crawl_rights 가 만드는 테이블 — 여기선 직접 생성
    conn.execute("""CREATE TABLE tenant_checks (
        court TEXT NOT NULL DEFAULT '', case_no TEXT NOT NULL,
        item_no TEXT NOT NULL DEFAULT '', checked_at TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (court, case_no, item_no))""")
    _seed(conn)

    assert store.prune_orphan_detail_raw(conn) == 1
    assert store.prune_orphan_tenant_checks(conn) == 1

    for t in ("listing_detail_raw", "tenant_checks"):
        rows = conn.execute(f"SELECT case_no FROM {t}").fetchall()
        assert [r[0] for r in rows] == ["살아있음"]


def test_prune_tenant_checks_missing_table_is_zero():
    conn = store.connect(":memory:")   # tenant_checks 미생성 DB(신규 환경)
    assert store.prune_orphan_tenant_checks(conn) == 0
