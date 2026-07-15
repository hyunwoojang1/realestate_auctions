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

SCHEMA_VERSION = 6

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
    market_scope TEXT NOT NULL DEFAULT '',
    market_band_low INTEGER,
    market_band_high INTEGER,
    profit_low INTEGER,
    profit_high INTEGER,
    market_sample_basis INTEGER,
    market_comps TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (court, case_no, item_no)
);
"""

# 권리·기일 요지(물건상세 크롤) — 상세 페이지 '권리 내역' 렌더의 원천.
# 매각물건명세서 요지(인수권리/최선순위/유치권)·청구금액·배당요구종기·기일역사(JSON).
DDL_RIGHTS = """
CREATE TABLE IF NOT EXISTS listing_rights (
    court TEXT NOT NULL DEFAULT '',
    case_no TEXT NOT NULL,
    item_no TEXT NOT NULL DEFAULT '',
    surviving_rights TEXT NOT NULL DEFAULT '',
    senior_lien TEXT NOT NULL DEFAULT '',
    lien_note TEXT NOT NULL DEFAULT '',
    remark TEXT NOT NULL DEFAULT '',
    claim_amt INTEGER,
    demand_end TEXT NOT NULL DEFAULT '',
    spec_write_ymd TEXT NOT NULL DEFAULT '',
    court_dept TEXT NOT NULL DEFAULT '',
    schedule TEXT NOT NULL DEFAULT '[]',
    appraisal_notes TEXT NOT NULL DEFAULT '[]',
    fetched_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (court, case_no, item_no)
);
"""

_RIGHTS_COLS = [
    "court", "case_no", "item_no", "surviving_rights", "senior_lien", "lien_note",
    "remark", "claim_amt", "demand_end", "spec_write_ymd", "court_dept",
    "schedule", "appraisal_notes", "fetched_at",
]

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

# 물건 사진 썸네일(base64 JPEG) — 상세 히어로용. 물건당 소수(seq 0..)만. 용량 억제 위해
# 수집부(crawl_rights)가 '시세추정 가능' 물건에만 저장한다(전물건 저장 시 수 GB).
DDL_PHOTOS = """
CREATE TABLE IF NOT EXISTS listing_photos (
    court TEXT NOT NULL DEFAULT '',
    case_no TEXT NOT NULL,
    item_no TEXT NOT NULL DEFAULT '',
    seq INTEGER NOT NULL DEFAULT 0,
    thumb_b64 TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (court, case_no, item_no, seq)
);
"""

# 네이버 KB시세·호가 매핑 결과(물건별). status: matched_kb | matched_ask | no_kb | no_match | no_coord
DDL_NAVER = """
CREATE TABLE IF NOT EXISTS naver_prices (
    court TEXT NOT NULL DEFAULT '',
    case_no TEXT NOT NULL,
    item_no TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    complex_no TEXT DEFAULT '',
    complex_name TEXT DEFAULT '',
    area_no TEXT DEFAULT '',
    match_conf TEXT DEFAULT '',
    kb_low INTEGER,          -- 하한가(원)
    kb_avg INTEGER,          -- 일반가(원) = KB '시세'
    kb_high INTEGER,         -- 상한가(원)
    lease_avg INTEGER,       -- 전세 일반가(원)
    ask_min INTEGER,         -- 호가 최저(원)
    ask_max INTEGER,         -- 호가 최고(원)
    ask_count INTEGER DEFAULT 0,
    base_ymd TEXT DEFAULT '',
    fetched_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (court, case_no, item_no)
);
"""

_NAVER_COLS = ["court", "case_no", "item_no", "status", "complex_no", "complex_name",
               "area_no", "match_conf", "kb_low", "kb_avg", "kb_high", "lease_avg",
               "ask_min", "ask_max", "ask_count", "base_ymd", "fetched_at"]

_COLS = [
    "case_no", "apt_name", "address", "property_type", "area_m2",
    "appraisal_price", "min_bid_price", "fail_count", "sale_date",
    "est_market_price", "matched_trades", "confidence",
    "real_acquisition_cost", "expected_profit", "gap_rate",
    "gap_score", "rights_score", "liquidity_score", "arb_score", "grade",
    "court", "item_no", "doc_id", "market_scope",
    "market_band_low", "market_band_high", "profit_low", "profit_high",
    "market_sample_basis",
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
    conn.execute(DDL_RIGHTS)
    conn.execute(DDL_PHOTOS)
    conn.execute(DDL_NAVER)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    return conn


def save_naver_price(conn: sqlite3.Connection, row: dict) -> None:
    """네이버 매핑 결과 1건 upsert(물건당 1행)."""
    vals = [row.get(c) for c in _NAVER_COLS]
    ph = ",".join("?" * len(_NAVER_COLS))
    with conn:
        conn.execute(
            f"INSERT OR REPLACE INTO naver_prices ({','.join(_NAVER_COLS)}) VALUES ({ph})", vals)


def naver_done_keys(conn: sqlite3.Connection) -> set:
    """이미 처리한 (court,case_no,item_no) — 이어받기용."""
    return {(r["court"], r["case_no"], r["item_no"])
            for r in conn.execute("SELECT court, case_no, item_no FROM naver_prices")}


def load_naver_price(conn: sqlite3.Connection, court: str, case_no: str, item_no: str) -> dict | None:
    r = conn.execute("SELECT * FROM naver_prices WHERE court=? AND case_no=? AND item_no=?",
                     (court, case_no, item_no)).fetchone()
    return dict(r) if r else None


def load_all_naver(conn: sqlite3.Connection) -> list[dict]:
    """naver_prices 전량(서빙 조인용). (court,case_no,item_no) 복합키로 맵 구성해 쓴다."""
    return [dict(r) for r in conn.execute("SELECT * FROM naver_prices")]


def _migrate(conn: sqlite3.Connection) -> None:
    """구스키마 자동 이관 — v1(case_no 단일 PK) → v2(복합 PK) → v3(market_scope).

    v1 데이터는 item_no=''로 이관된다(당시 물건번호 미수집 — 소실된 게 아니라 원래 없던 정보).
    market_scope도 ''(레거시)로 시작해 다음 전량 새로고침이 실제 값으로 채운다.
    트랜잭션이므로 실패 시 원상 복구.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scored_listings'"
    ).fetchone()
    if row is None:
        return  # 신규 DB — connect()가 최신 DDL로 생성
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(scored_listings)")}
    if "item_no" not in cols:
        # v1 → 최신: PK 변경은 ALTER 불가 → 재생성 이관(최신 DDL이라 v3 컬럼 포함)
        with conn:
            conn.execute("ALTER TABLE scored_listings RENAME TO scored_listings_v1")
            conn.execute(DDL)
            src = ",".join(_V1_COLS)
            conn.execute(
                f"INSERT INTO scored_listings ({src}) SELECT {src} FROM scored_listings_v1"
            )
            conn.execute("DROP TABLE scored_listings_v1")
        return
    if "market_scope" not in cols:
        # v2 → v3: 컬럼 추가만 — ALTER로 충분(데이터 이동 없음)
        with conn:
            conn.execute(
                "ALTER TABLE scored_listings ADD COLUMN market_scope TEXT NOT NULL DEFAULT ''"
            )
    if "market_band_low" not in cols:
        # v3 → v4(T4 가격 밴드): NULL 허용 컬럼 4개 추가 — 레거시 행은 밴드 없음(None)
        with conn:
            for col in ("market_band_low", "market_band_high", "profit_low", "profit_high"):
                conn.execute(f"ALTER TABLE scored_listings ADD COLUMN {col} INTEGER")
    if "market_sample_basis" not in cols:
        # v4 → v5(T5 표본 게이트): 밴드 실기반 표본수 — 레거시 행은 None(게이트 미적용)
        with conn:
            conn.execute("ALTER TABLE scored_listings ADD COLUMN market_sample_basis INTEGER")
    if "market_comps" not in cols:
        # v5 → v6(시간축 차트): 개별 실거래 점 JSON. 레거시 행은 '[]'(차트 점 없음 — 다음
        # 전량 새로고침이 실제 comps로 채운다). NOT NULL + DEFAULT라 ALTER 한 번으로 충분.
        with conn:
            conn.execute(
                "ALTER TABLE scored_listings ADD COLUMN market_comps TEXT NOT NULL DEFAULT '[]'"
            )

    # listing_rights: 감정평가 요항점 컬럼 추가(v6 → v7). 테이블이 이미 있고 컬럼만 없을 때 ALTER.
    rt = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='listing_rights'"
    ).fetchone()
    if rt is not None:
        rcols = {r["name"] for r in conn.execute("PRAGMA table_info(listing_rights)")}
        if "appraisal_notes" not in rcols:
            with conn:
                conn.execute(
                    "ALTER TABLE listing_rights ADD COLUMN appraisal_notes TEXT NOT NULL DEFAULT '[]'"
                )


# 저장 컬럼 = 스칼라 _COLS + market_comps(JSON 텍스트). market_comps는 리스트라 스칼라
# 경로(_COLS)에 넣지 않고 직렬화해 별도 취급한다(load_scored에서 역직렬화).
_STORE_COLS = [*_COLS, "market_comps"]


def _insert_rows(conn: sqlite3.Connection, items: Iterable[ScoredListing]) -> int:
    rows = []
    for s in items:
        d = s.to_row()
        base = [d[c] for c in _COLS]
        base.append(json.dumps(d.get("market_comps") or [], ensure_ascii=False))
        rows.append(tuple(base))
    placeholders = ",".join("?" * len(_STORE_COLS))
    conn.executemany(
        f"INSERT OR REPLACE INTO scored_listings ({','.join(_STORE_COLS)}) VALUES ({placeholders})",
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


def save_rights(conn: sqlite3.Connection, rights_rows: Iterable[dict]) -> int:
    """권리·기일 요지 upsert — 같은 (court,case_no,item_no)는 최신 크롤로 갱신."""
    rows = [tuple(r.get(c) for c in _RIGHTS_COLS) for r in rights_rows]
    placeholders = ",".join("?" * len(_RIGHTS_COLS))
    with conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO listing_rights ({','.join(_RIGHTS_COLS)}) "
            f"VALUES ({placeholders})",
            rows,
        )
    return len(rows)


def prune_orphan_rights(conn: sqlite3.Connection) -> int:
    """현재 scored_listings 에 대응 물건이 없는 listing_rights(고아) 삭제.

    scored 는 새로고침마다 전량교체(replace_all)로 만료매물을 지우지만, listing_rights 는
    INSERT OR REPLACE 라 한 번 쌓이면 안 지워져 죽은 권리가 무한 누적된다. 풀스냅샷 새로고침
    직후 호출해 rights 를 scored 와 동기화(중복/부풀림 방지). 반환=삭제 건수.
    """
    with conn:
        cur = conn.execute(
            "DELETE FROM listing_rights WHERE NOT EXISTS ("
            "  SELECT 1 FROM scored_listings s"
            "  WHERE s.court=listing_rights.court AND s.case_no=listing_rights.case_no"
            "    AND s.item_no=listing_rights.item_no)"
        )
    return cur.rowcount


def load_rights(conn: sqlite3.Connection, court: str, case_no: str,
                item_no: str = "") -> dict | None:
    """단건 권리 요지 조회 — (court, case_no, item_no) **정확 매칭만**.

    (재검증 감사 2026-07-11 idx16) 과거의 '같은 사건 아무 물건' 폴백은 다물건 사건에서
    형제 물건의 명세서를 이 물건 것처럼 표시하고 rights_verified=True 로 하드게이트까지
    발동시키는 과신이었다(실측 15건). 매각물건명세서는 물건번호별로 인수권리가 다를 수 있다
    — 크롤이 물건번호 단위(dspslGdsSeq)로 수집하므로 정확 매칭 실패 = 미크롤로 취급한다.
    """
    cur = conn.execute(
        "SELECT * FROM listing_rights WHERE court=? AND case_no=? AND item_no=?",
        (court, case_no, str(item_no or "")),
    )
    row = cur.fetchone()
    return dict(row) if row is not None else None


def fetch_all_rights(conn: sqlite3.Connection) -> list[dict]:
    """권리 요지 전량(목록 배지 조인용 — 수백 행 수준의 작은 테이블)."""
    cur = conn.execute("SELECT * FROM listing_rights")
    return [dict(r) for r in cur.fetchall()]


def estimable_keys(conn: sqlite3.Connection) -> set[tuple[str, str, str]]:
    """시세추정 가능(est_market_price 존재) 물건의 (court,case_no,item_no) 집합.

    사진은 용량 때문에 이 집합에만 저장한다(사용자가 실제로 여는 물건 ≈ 평가 가능한 것).
    """
    cur = conn.execute(
        "SELECT court, case_no, item_no FROM scored_listings WHERE est_market_price IS NOT NULL"
    )
    return {(r["court"], r["case_no"], str(r["item_no"] or "")) for r in cur.fetchall()}


def save_photos(conn: sqlite3.Connection, court: str, case_no: str, item_no: str,
                thumbs: list[str], fetched_at: str = "") -> int:
    """물건 사진 썸네일 저장 — 해당 물건 기존 사진 전량 교체(stale 방지). 반환=저장 장수."""
    key = (court, case_no, str(item_no or ""))
    with conn:
        conn.execute(
            "DELETE FROM listing_photos WHERE court=? AND case_no=? AND item_no=?", key)
        conn.executemany(
            "INSERT INTO listing_photos (court,case_no,item_no,seq,thumb_b64,fetched_at) "
            "VALUES (?,?,?,?,?,?)",
            [(*key, i, t, fetched_at) for i, t in enumerate(thumbs) if t],
        )
    return len([t for t in thumbs if t])


def load_photos(conn: sqlite3.Connection, court: str, case_no: str,
                item_no: str = "") -> list[str]:
    """단건 물건 사진 썸네일(base64) seq 순 — 상세 히어로용."""
    cur = conn.execute(
        "SELECT thumb_b64 FROM listing_photos WHERE court=? AND case_no=? AND item_no=? "
        "ORDER BY seq", (court, case_no, str(item_no or "")))
    return [r["thumb_b64"] for r in cur.fetchall()]


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
    out = []
    for r in fetch_ranked(conn):
        kw = {c: r[c] for c in _COLS}
        kw["market_comps"] = _parse_comps(r["market_comps"] if "market_comps" in r.keys() else None)
        out.append(ScoredListing(**kw))
    return out


def _parse_comps(raw: str | None) -> list[list]:
    """저장된 comps JSON('[[ym, price], ...]') → 리스트. 손상/누락 시 빈 리스트(무점)."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return data if isinstance(data, list) else []


def has_rows(conn: sqlite3.Connection) -> bool:
    cur = conn.execute("SELECT 1 FROM scored_listings LIMIT 1")
    return cur.fetchone() is not None
