"""SQLite 저장/조회 레이어."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from .models import ScoredListing

DDL = """
CREATE TABLE IF NOT EXISTS scored_listings (
    case_no TEXT PRIMARY KEY,
    apt_name TEXT, address TEXT, property_type TEXT, area_m2 REAL,
    appraisal_price INTEGER, min_bid_price INTEGER, fail_count INTEGER, sale_date TEXT,
    est_market_price INTEGER, matched_trades INTEGER, confidence REAL,
    real_acquisition_cost INTEGER, expected_profit INTEGER, gap_rate REAL,
    gap_score REAL, rights_score REAL, liquidity_score REAL, arb_score REAL, grade TEXT
);
"""

_COLS = [
    "case_no", "apt_name", "address", "property_type", "area_m2",
    "appraisal_price", "min_bid_price", "fail_count", "sale_date",
    "est_market_price", "matched_trades", "confidence",
    "real_acquisition_cost", "expected_profit", "gap_rate",
    "gap_score", "rights_score", "liquidity_score", "arb_score", "grade",
]


def connect(db_path: str = "auction.db") -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(DDL)
    return conn


def upsert(conn: sqlite3.Connection, items: Iterable[ScoredListing]) -> int:
    rows = [tuple(s.to_row()[c] for c in _COLS) for s in items]
    placeholders = ",".join("?" * len(_COLS))
    conn.executemany(
        f"INSERT OR REPLACE INTO scored_listings ({','.join(_COLS)}) VALUES ({placeholders})",
        rows,
    )
    conn.commit()
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
