"""국토부 실거래 영구 캐시 — (kind, lawd_cd, ymd) 단위로 한 번만 받아 재사용.

배경: load_live_trades 가 매 새로고침마다 최근 N개월치 실거래를 전 지역·전 유형으로 새로
받으면, 창을 12개월로 넓히는 순간 호출이 N배가 되어 국토부 무료키 일일쿼터(429)를 넘긴다.

해결: '닫힌 달'(이미 지난 달)의 실거래는 거의 변하지 않으므로 한 번 받으면 SQLite 캐시에
영구 보관하고, 새로고침 때는 '열린 달'(이번 달+직전 달, 지연등록 반영)만 다시 받는다.
→ 12개월 깊이를 확보하면서도 매 새로고침 호출은 지역×유형×2개월로 안정.
쿼터가 중간에 소진되어 일부 달을 못 받아도, 받은 만큼 캐시되고 다음 실행이 이어서 채운다
(courtauction 크롤과 동일한 '이어받기' 성질).

캐시는 auction.db 와 분리된 data/molit_trades.db(gitignore: *.db)에 둔다 — 서빙 DB 오염 방지.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Callable

from .models import Trade

logger = logging.getLogger(__name__)

_DEFAULT_CACHE = Path(__file__).resolve().parent.parent / "data" / "molit_trades.db"


def cache_path() -> Path:
    """캐시 DB 경로. 환경변수 AUCTION_MOLIT_CACHE 로 재정의 가능(테스트/격리용)."""
    p = os.environ.get("AUCTION_MOLIT_CACHE", "").strip()
    return Path(p) if p else _DEFAULT_CACHE


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """캐시 DB 연결(+ 스키마 보장). path 미지정 시 기본 경로."""
    p = Path(path) if path is not None else cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30.0)
    conn.row_factory = sqlite3.Row
    # 병렬 워머 대비: WAL(동시 읽기+단일 쓰기 허용)·busy_timeout(락 대기)·NORMAL(성능).
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS molit_trades (
            kind        TEXT NOT NULL,
            lawd_cd     TEXT NOT NULL,
            ymd         TEXT NOT NULL,
            trades_json TEXT NOT NULL,
            n           INTEGER NOT NULL,
            fetched_at  TEXT NOT NULL,
            PRIMARY KEY (kind, lawd_cd, ymd)
        )
        """
    )
    return conn


def _throttle_seconds() -> float:
    """라이브 호출 사이 지연(초). 429 완화용. 캐시 적중 호출엔 적용 안 함."""
    try:
        return max(0.0, float(os.environ.get("AUCTION_MOLIT_THROTTLE", "0.25")))
    except ValueError:
        return 0.25


def _load(conn: sqlite3.Connection, kind: str, lawd_cd: str, ymd: str) -> list[Trade] | None:
    """캐시 조회. 미캐시=None, 캐시된 빈 달=[](유효한 '그 달 거래 0건')."""
    row = conn.execute(
        "SELECT trades_json FROM molit_trades WHERE kind=? AND lawd_cd=? AND ymd=?",
        (kind, lawd_cd, ymd),
    ).fetchone()
    if row is None:
        return None
    try:
        raw = json.loads(row["trades_json"])
        return [Trade(**d) for d in raw]
    except (json.JSONDecodeError, TypeError):
        # (감사 2026-07-15) Trade(**d) 도 try 안으로 — 캐시 스키마 드리프트/손상 시 TypeError 가
        # 미포착 전파돼 해당 지역 캐시 읽기가 통째로 크래시하던 것 방지(미스로 강등 → 재수집).
        return None


def _save(conn: sqlite3.Connection, kind: str, lawd_cd: str, ymd: str,
          trades: list[Trade], now: str) -> None:
    payload = json.dumps([dataclasses.asdict(t) for t in trades], ensure_ascii=False)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO molit_trades "
            "(kind, lawd_cd, ymd, trades_json, n, fetched_at) VALUES (?,?,?,?,?,?)",
            (kind, lawd_cd, ymd, payload, len(trades), now),
        )


def get_or_fetch(conn: sqlite3.Connection, kind: str, lawd_cd: str, ymd: str,
                 fetch_fn: Callable[[], list[Trade]], *, cacheable: bool,
                 now: str, throttle_s: float | None = None) -> tuple[list[Trade], bool]:
    """(trades, from_cache) 반환.

    cacheable=True(닫힌 달)면 캐시 우선 조회 → 미스 시 fetch_fn()로 받아 캐시 저장.
    cacheable=False(열린 달)면 항상 fetch_fn()(지연등록 반영). fetch 뒤에만 throttle.
    fetch_fn 예외는 호출자가 처리한다(부분 실패가 전체를 막지 않도록 상위에서 try).
    """
    if cacheable:
        cached = _load(conn, kind, lawd_cd, ymd)
        if cached is not None:
            return cached, True
    trades = fetch_fn()
    if throttle_s is None:
        throttle_s = _throttle_seconds()
    if throttle_s > 0:
        time.sleep(throttle_s)
    if cacheable:
        # 빈 달([])도 의도적으로 캐시한다 — 거래 0건인 닫힌 달을 미캐시(None)와 구분해
        # 재fetch 를 막는다(요청 절감). 스푸리어스 '0건' 오염 위험은 알려진 트레이드오프.
        _save(conn, kind, lawd_cd, ymd, trades, now)
    return trades, False


def stats(conn: sqlite3.Connection) -> dict:
    """캐시 현황(디버그/모니터용)."""
    row = conn.execute(
        "SELECT COUNT(*) AS entries, COALESCE(SUM(n),0) AS trades, "
        "COUNT(DISTINCT ymd) AS months, COUNT(DISTINCT lawd_cd) AS regions FROM molit_trades"
    ).fetchone()
    return dict(row) if row else {"entries": 0, "trades": 0, "months": 0, "regions": 0}
