"""SQLite 저장/조회 레이어.

T1(데이터 신뢰도 개편): 식별 단위는 case_no 단일이 아니라 court+case_no+item_no 복합키다.
한 사건번호 안에 물건이 여러 개 있을 수 있어(물건번호 1=아파트, 2=상가 …) case_no 단일 PK는
같은 사건의 다른 물건을 조용히 덮어쓴다(수집 물건 누락 = 신뢰의 최하층 붕괴).
구스키마 DB는 connect() 시 자동 마이그레이션(v2)된다. 원본 row는 raw_listings에 보존한다.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from .models import ScoredListing

SCHEMA_VERSION = 2

DDL = """
CREATE TABLE IF NOT EXISTS scored_listings (
    case_no TEXT NOT NULL,
    apt_name TEXT, address TEXT, property_type TEXT, area_m2 REAL,
    appraisal_price INTEGER, min_bid_price INTEGER, fail_count INTEGER, sale_date TEXT,
    est_market_price INTEGER, matched_trades INTEGER, confidence REAL,
    real_acquisition_cost INTEGER, expected_profit INTEGER, gap_rate REAL,
    gap_score REAL, rights_score REAL, liquidity_score REAL, arb_score REAL, grade TEXT,
    court TEXT NOT NULL DEFAULT '',
    item_no TEXT NOT NULL DEFAULT '',
    doc_id TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (court, case_no, item_no)
);
"""

# 원본 보존: 파싱/채점과 무관하게 수집 시점의 raw row(개인정보 제거본)를 남긴다.
# 파싱 버그·스키마 개편 시 재처리의 원천이자, "무엇을 수집했는가"의 감사 증거.
DDL_RAW = """
CREATE TABLE IF NOT EXISTS raw_listings (
    uid TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL DEFAULT '',
    court TEXT NOT NULL DEFAULT '',
    case_no TEXT NOT NULL DEFAULT '',
    item_no TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""

_COLS = [
    "case_no", "apt_name", "address", "property_type", "area_m2",
    "appraisal_price", "min_bid_price", "fail_count", "sale_date",
    "est_market_price", "matched_trades", "confidence",
    "real_acquisition_cost", "expected_profit", "gap_rate",
    "gap_score", "rights_score", "liquidity_score", "arb_score", "grade",
    "court", "item_no", "doc_id",
]

# v1(구스키마)에서 이관 대상 컬럼 — court/item_no/doc_id는 v1에 없으므로 '' 기본값.
_V1_COLS = _COLS[:20]


def connect(db_path: str = "auction.db") -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # WAL + busy_timeout: 매일 05:30 새로고침(쓰기)이 웹 서빙(읽기)과 겹쳐도 'database is locked'로
    # 조용히 샘플 폴백되지 않게 한다. 읽기-쓰기 동시성 확보 + 최대 5초 잠금 대기.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
    except sqlite3.OperationalError:
        pass  # :memory: 등 WAL 미지원 환경은 무시
    _migrate(conn)
    conn.execute(DDL)
    conn.execute(DDL_RAW)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """구스키마(case_no 단일 PK) → v2(복합 PK) 자동 이관.

    구스키마 데이터는 item_no=''로 이관된다(당시 물건번호 미수집 — 소실된 게 아니라 원래 없던 정보).
    다음 전량 새로고침이 실제 item_no로 채운다. 트랜잭션이므로 실패 시 원상 복구.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scored_listings'"
    ).fetchone()
    if row is None:
        return  # 신규 DB — connect()가 v2 DDL로 생성
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(scored_listings)")}
    if "item_no" in cols:
        return  # 이미 v2
    with conn:
        conn.execute("ALTER TABLE scored_listings RENAME TO scored_listings_v1")
        conn.execute(DDL)
        src = ",".join(_V1_COLS)
        conn.execute(
            f"INSERT INTO scored_listings ({src}) SELECT {src} FROM scored_listings_v1"
        )
        conn.execute("DROP TABLE scored_listings_v1")


def _insert_rows(conn: sqlite3.Connection, items: Iterable[ScoredListing]) -> int:
    rows = [tuple(s.to_row()[c] for c in _COLS) for s in items]
    placeholders = ",".join("?" * len(_COLS))
    conn.executemany(
        f"INSERT OR REPLACE INTO scored_listings ({','.join(_COLS)}) VALUES ({placeholders})",
        rows,
    )
    return len(rows)


def upsert(conn: sqlite3.Connection, items: Iterable[ScoredListing]) -> int:
    """병합 적재(부분/증분 크롤용). 기존 행은 유지하고 같은 (court,case_no,item_no)만 갱신."""
    n = _insert_rows(conn, items)
    conn.commit()
    return n


def replace_all(conn: sqlite3.Connection, items: Iterable[ScoredListing]) -> int:
    """전량 교체(전국 풀스냅샷용). 한 트랜잭션에서 기존 전체 삭제 후 재적재.

    팔리거나 취하돼 이번 크롤에 없는 물건을 남겨 두지 않는다(만료 매물 추천 방지).
    """
    with conn:  # 트랜잭션: 전부 성공 or 롤백(웹이 반쯤 지워진 상태를 서빙하지 않게)
        conn.execute("DELETE FROM scored_listings")
        n = _insert_rows(conn, items)
    return n


def save_raw_records(conn: sqlite3.Connection, records: Iterable, fetched_at: str | None = None) -> int:
    """CourtAuctionRecord의 원본 row(개인정보 제거본)를 raw_listings에 보존.

    같은 uid는 최신 수집분으로 갱신(REPLACE) — '마지막으로 관측된 원본'을 유지한다.
    """
    ts = fetched_at or datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for r in records:
        uid = r.doc_id or f"{r.court}|{r.case_no}|{r.item_no}"
        rows.append((uid, r.doc_id, r.court, r.case_no, r.item_no,
                     json.dumps(r.raw, ensure_ascii=False), ts))
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO raw_listings "
            "(uid, doc_id, court, case_no, item_no, raw_json, fetched_at) "
            "VALUES (?,?,?,?,?,?,?)",
            rows,
        )
    return len(rows)


def fetch_ranked(conn: sqlite3.Connection) -> list[dict]:
    """차익 스코어 내림차순 (NULL=시세추정불가는 맨 뒤)."""
    cur = conn.execute(
        "SELECT * FROM scored_listings ORDER BY arb_score IS NULL, arb_score DESC"
    )
    return [dict(r) for r in cur.fetchall()]


def load_scored(conn: sqlite3.Connection) -> list[ScoredListing]:
    """DB에 저장된 채점결과를 ScoredListing 객체로 복원(차익 스코어순).

    웹 서버가 매 요청마다 라이브 API를 호출하지 않고, 새로고침 작업이
    적재해둔 결과를 그대로 서빙하기 위한 읽기 경로.
    """
    return [ScoredListing(**{c: r[c] for c in _COLS}) for r in fetch_ranked(conn)]


def has_rows(conn: sqlite3.Connection) -> bool:
    cur = conn.execute("SELECT 1 FROM scored_listings LIMIT 1")
    return cur.fetchone() is not None
