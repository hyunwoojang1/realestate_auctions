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

SCHEMA_VERSION = 7

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
    rights_verified INTEGER NOT NULL DEFAULT 0,
    assumed_amount INTEGER NOT NULL DEFAULT 0,
    burden_amount_unknown INTEGER NOT NULL DEFAULT 0,
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

# 임차인 현황(현황조사서 crawl) — 대항력 '실판정'(전입일 vs 말소기준일)의 원천. 물건당 0..N행.
# ⚠️ PII 미저장: 성명·주민번호·상세주소 없음. 전입일·확정일자·보증금(금액)·점유유형만.
DDL_TENANTS = """
CREATE TABLE IF NOT EXISTS listing_tenants (
    court TEXT NOT NULL DEFAULT '',
    case_no TEXT NOT NULL,
    item_no TEXT NOT NULL DEFAULT '',
    seq INTEGER NOT NULL DEFAULT 0,
    movein_ymd TEXT NOT NULL DEFAULT '',
    confirm_ymd TEXT NOT NULL DEFAULT '',
    deposit INTEGER NOT NULL DEFAULT 0,
    possession TEXT NOT NULL DEFAULT '',
    usage TEXT NOT NULL DEFAULT '',
    part TEXT NOT NULL DEFAULT '',
    is_tenant_like INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (court, case_no, item_no, seq)
);
"""

_TENANT_COLS = [
    "court", "case_no", "item_no", "seq", "movein_ymd", "confirm_ymd", "deposit",
    "possession", "usage", "part", "is_tenant_like", "fetched_at",
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
    thumb_b64 TEXT NOT NULL DEFAULT '',
    photo_url TEXT NOT NULL DEFAULT '',
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
    lease_low INTEGER,       -- 전세 하한(원) — 2026-07-19 실거래 개편
    lease_high INTEGER,      -- 전세 상한(원)
    base_ymd TEXT DEFAULT '',
    fetched_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (court, case_no, item_no)
);
"""

_NAVER_COLS = ["court", "case_no", "item_no", "status", "complex_no", "complex_name",
               "area_no", "match_conf", "kb_low", "kb_avg", "kb_high", "lease_avg",
               "ask_min", "ask_max", "ask_count", "lease_low", "lease_high",
               "base_ymd", "fetched_at"]

# 건축물대장 요약(물건별) — 정부 OpenAPI(BldRgstHubService) 기반. courtauction 상세 크롤과
# 무관한 '빠른 병렬 enrichment'(deploy/enrich_building)가 채운다. status: ok | no_addr | no_bld | error
DDL_BUILDING = """
CREATE TABLE IF NOT EXISTS listing_building (
    court TEXT NOT NULL DEFAULT '',
    case_no TEXT NOT NULL,
    item_no TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    approved TEXT DEFAULT '',          -- 사용승인 YYYY.MM
    age_years INTEGER,                 -- 연식(년)
    main_purpose TEXT DEFAULT '',      -- 주용도(공동주택 등)
    ground_floors INTEGER,             -- 지상 층수
    underground_floors INTEGER,        -- 지하 층수
    total_area_m2 REAL,                -- 연면적(㎡)
    is_violation INTEGER DEFAULT 0,    -- 위반건축물 여부(0/1)
    violation_content TEXT DEFAULT '',
    dong_count INTEGER,                -- 동수
    jibun_addr TEXT DEFAULT '',        -- 조회에 쓴 지번주소
    fetched_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (court, case_no, item_no)
);
"""

# (감사체계 2026-07-23) 물건상세(pgj15B)·현황조사서(curst) 원본 보존 — 블라인드 감사·사후 재파싱 재료.
# 종전엔 normalize 후 원본을 버려 "언제 어떤 필드가 왜 깨졌는지" 사후 재구성이 불가능했다(QA F1).
# 실명 마스킹 후 zlib 압축 BLOB로 저장(용량 ~1/10). doc_type: 'pgj15B' | 'curst'.
DDL_DETAIL_RAW = """
CREATE TABLE IF NOT EXISTS listing_detail_raw (
    court TEXT NOT NULL DEFAULT '',
    case_no TEXT NOT NULL,
    item_no TEXT NOT NULL DEFAULT '',
    doc_type TEXT NOT NULL,
    payload BLOB NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (court, case_no, item_no, doc_type)
);
"""

_BUILDING_COLS = ["court", "case_no", "item_no", "status", "approved", "age_years",
                  "main_purpose", "ground_floors", "underground_floors", "total_area_m2",
                  "is_violation", "violation_content", "dong_count", "jibun_addr", "fetched_at"]

_COLS = [
    "case_no", "apt_name", "address", "property_type", "area_m2",
    "appraisal_price", "min_bid_price", "fail_count", "sale_date",
    "est_market_price", "matched_trades", "confidence",
    "real_acquisition_cost", "expected_profit", "gap_rate",
    "gap_score", "rights_score", "liquidity_score", "arb_score", "grade",
    "court", "item_no", "doc_id", "market_scope",
    "market_band_low", "market_band_high", "profit_low", "profit_high",
    "market_sample_basis",
    # (감사 2026-07-15 / T8 기존지적 audit-t8-20260703) 권리분석 수행 여부. DDL에 없어서 DB
    # 왕복 시 항상 False 로 복원됐다 — 권리 배선(apply_rights_from_rows) 도입으로 이 값이
    # 실제 의미를 갖게 되므로 영속화한다. sqlite는 bool을 0/1 정수로 저장.
    "rights_verified",
    # (2026-07-22) 인수금액 — 보수차익(profit_low)에 차감 반영됨. 서빙 폴백(_apply_market_price)이
    # 재계산할 때도 차감하려면 영속 필요(프로덕션은 Supabase에서 ScoredListing 복원).
    "assumed_amount",
    # (감사 2026-07-23 P-01) 인수 명시인데 금액 미상 — 추천계열 진입 금지 판정에 쓰인다.
    # 서빙 폴백도 같은 규칙을 적용해야 하므로 영속.
    "burden_amount_unknown",
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
    conn.execute(DDL_BUILDING)
    conn.execute(DDL_TENANTS)
    conn.execute(DDL_DETAIL_RAW)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    return conn


def save_naver_price(conn: sqlite3.Connection, row: dict) -> None:
    """네이버 매핑 결과 1건 upsert(물건당 1행)."""
    vals = [row.get(c) for c in _NAVER_COLS]
    ph = ",".join("?" * len(_NAVER_COLS))
    with conn:
        conn.execute(
            f"INSERT OR REPLACE INTO naver_prices ({','.join(_NAVER_COLS)}) VALUES ({ph})", vals)


# 매칭·수집이 성사되지 못한 status — 코드 수정 후 재시도 가치가 있는 것들.
# no_kb(단지는 찾았으나 KB·호가 모두 없음)도 포함: 단지가 오매칭이었을 수 있다.
NAVER_FAILED_STATUS = ("no_match", "no_kb", "no_coord", "")


def naver_done_keys(conn: sqlite3.Connection, include_failed: bool = True) -> set:
    """이미 처리한 (court,case_no,item_no) — 이어받기용.

    include_failed=False 면 **실패로 저장된 행을 '미처리'로 취급**해 재시도 대상이 되게 한다.
    (감사 2026-07-15) 기본 동작은 status 무관하게 전부 '처리됨'이라, 옛 버그 시절에 실패로
    굳은 행이 코드를 고쳐도 영영 재수집되지 않았다 — 실측 861건이 영구 스킵 상태였고 그중
    150건은 오피스텔 realEstateType 버그(13:10 수정)·이름매칭 개선(14:05) **이전** 수집분이다.
    운영자가 --retry-failed 로 명시할 때만 재시도한다(매 실행 재시도는 진짜 미등재 물건에
    불필요한 요청을 반복하므로 기본값은 보수적으로 유지).
    """
    sql = "SELECT court, case_no, item_no FROM naver_prices"
    params: tuple = ()
    if not include_failed:
        ph = ",".join("?" * len(NAVER_FAILED_STATUS))
        sql += f" WHERE status NOT IN ({ph})"
        params = NAVER_FAILED_STATUS
    return {(r["court"], r["case_no"], r["item_no"]) for r in conn.execute(sql, params)}


def load_naver_price(conn: sqlite3.Connection, court: str, case_no: str, item_no: str) -> dict | None:
    r = conn.execute("SELECT * FROM naver_prices WHERE court=? AND case_no=? AND item_no=?",
                     (court, case_no, item_no)).fetchone()
    return dict(r) if r else None


def load_all_naver(conn: sqlite3.Connection) -> list[dict]:
    """naver_prices 전량(서빙 조인용). (court,case_no,item_no) 복합키로 맵 구성해 쓴다."""
    return [dict(r) for r in conn.execute("SELECT * FROM naver_prices")]


# ── 건축물대장 요약(정부 OpenAPI 기반 빠른 enrichment) ──────────────────────────
# 실패로 저장된 status — --retry-failed 시 재조회 대상.
BUILDING_FAILED_STATUS = ("no_addr", "no_bld", "error", "")


def save_building(conn: sqlite3.Connection, row: dict) -> None:
    """건축물대장 요약 1건 upsert(물건당 1행)."""
    vals = [row.get(c) for c in _BUILDING_COLS]
    ph = ",".join("?" * len(_BUILDING_COLS))
    with conn:
        conn.execute(
            f"INSERT OR REPLACE INTO listing_building ({','.join(_BUILDING_COLS)}) "
            f"VALUES ({ph})", vals)


def building_done_keys(conn: sqlite3.Connection, include_failed: bool = True) -> set:
    """이미 처리한 (court,case_no,item_no) — 이어받기용. include_failed=False 면
    실패 status 행을 '미처리'로 취급해 재시도 대상이 되게 한다(naver_done_keys와 동일 규약)."""
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='listing_building'"
    ).fetchone()
    if not has:
        return set()
    sql = "SELECT court, case_no, item_no FROM listing_building"
    params: tuple = ()
    if not include_failed:
        ph = ",".join("?" * len(BUILDING_FAILED_STATUS))
        sql += f" WHERE status NOT IN ({ph})"
        params = BUILDING_FAILED_STATUS
    return {(r["court"], r["case_no"], r["item_no"]) for r in conn.execute(sql, params)}


def load_building(conn: sqlite3.Connection, court: str, case_no: str,
                  item_no: str) -> dict | None:
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='listing_building'"
    ).fetchone()
    if not has:
        return None
    r = conn.execute(
        "SELECT * FROM listing_building WHERE court=? AND case_no=? AND item_no=?",
        (court, case_no, item_no)).fetchone()
    return dict(r) if r else None


def load_all_building(conn: sqlite3.Connection) -> list[dict]:
    """listing_building 전량(서빙 조인용). 테이블 없으면 빈 리스트."""
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='listing_building'"
    ).fetchone()
    if not has:
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM listing_building")]


def load_all_rights(conn: sqlite3.Connection) -> list[dict]:
    """listing_rights 전량(채점 전 권리 배선용 — pipeline.apply_rights_from_rows).

    단건 조회(load_rights)를 물건마다 부르면 N번 왕복하므로 배치는 전량 1회 로드 후
    (court,case_no,item_no) 맵으로 조인한다. 테이블이 없으면 빈 리스트(신규 DB 안전).
    """
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='listing_rights'"
    ).fetchone()
    if not has:
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM listing_rights")]


def save_tenants(conn: sqlite3.Connection, court: str, case_no: str, item_no: str,
                 tenants: list[dict], fetched_at: str = "") -> None:
    """물건별 임차인 현황을 교체 저장(기존 행 삭제 후 재삽입 — 재크롤 멱등).

    tenants = parse_curst_survey 결과. 빈 리스트면 기존 행만 지운다(임차인 없음 확정).
    """
    item_no = str(item_no or "")
    with conn:
        conn.execute("DELETE FROM listing_tenants WHERE court=? AND case_no=? AND item_no=?",
                     (court, case_no, item_no))
        ph = ",".join("?" * len(_TENANT_COLS))
        for seq, t in enumerate(tenants or []):
            row = {
                "court": court, "case_no": case_no, "item_no": item_no, "seq": seq,
                "movein_ymd": t.get("movein_ymd", ""), "confirm_ymd": t.get("confirm_ymd", ""),
                "deposit": int(t.get("deposit") or 0),
                "possession": t.get("possession", ""), "usage": t.get("usage", ""),
                "part": t.get("part", ""),
                "is_tenant_like": 1 if t.get("is_tenant_like") else 0,
                "fetched_at": fetched_at,
            }
            conn.execute(
                f"INSERT INTO listing_tenants ({','.join(_TENANT_COLS)}) VALUES ({ph})",
                [row[c] for c in _TENANT_COLS])


def load_tenants(conn: sqlite3.Connection, court: str, case_no: str,
                 item_no: str) -> list[dict]:
    """물건별 임차인 현황(서빙 상세용). 테이블 없거나 미크롤이면 빈 리스트."""
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='listing_tenants'"
    ).fetchone()
    if not has:
        return []
    rows = conn.execute(
        "SELECT * FROM listing_tenants WHERE court=? AND case_no=? AND item_no=? ORDER BY seq",
        (court, case_no, str(item_no or ""))).fetchall()
    return [dict(r) for r in rows]


def save_detail_raw(conn: sqlite3.Connection, court: str, case_no: str, item_no: str,
                    doc_type: str, payload: dict, fetched_at: str = "") -> None:
    """물건상세/현황조사서 응답 원본 보존 (감사체계 2026-07-23).

    실명 마스킹(mask_personal_names) → zlib 압축 저장. 같은 (물건, doc_type)은 최신으로 교체.
    파서(normalize)를 거치지 않은 원문이므로, 파서 버그의 사후 감사·재파싱 재료가 된다.
    """
    import zlib  # noqa: PLC0415

    from .courtauction_fields import mask_personal_names  # noqa: PLC0415 — 순환 import 회피
    text = json.dumps(payload, ensure_ascii=False, default=str)
    blob = zlib.compress(mask_personal_names(text).encode("utf-8"))
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO listing_detail_raw "
            "(court, case_no, item_no, doc_type, payload, fetched_at) VALUES (?,?,?,?,?,?)",
            (court, case_no, str(item_no or ""), doc_type, blob, fetched_at))


def load_detail_raw(conn: sqlite3.Connection, court: str, case_no: str, item_no: str,
                    doc_type: str) -> dict | None:
    """보존된 원본을 복원(압축 해제 → JSON). 없으면 None."""
    import zlib  # noqa: PLC0415
    row = conn.execute(
        "SELECT payload FROM listing_detail_raw "
        "WHERE court=? AND case_no=? AND item_no=? AND doc_type=?",
        (court, case_no, str(item_no or ""), doc_type)).fetchone()
    if row is None:
        return None
    return json.loads(zlib.decompress(row["payload"]).decode("utf-8"))


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
    if "rights_verified" not in cols:
        # v6 → v7(권리 배선, 감사 2026-07-15): 권리분석 수행 여부. 레거시 행은 0(권리미확인) —
        # 실제로도 그 시점 채점은 권리를 안 봤으므로 0이 사실이다. 다음 새로고침이 실값을 채운다.
        with conn:
            conn.execute(
                "ALTER TABLE scored_listings ADD COLUMN rights_verified INTEGER NOT NULL DEFAULT 0"
            )
    if "assumed_amount" not in cols:
        # v7 → v8(2026-07-22 빈틈1): 인수금액. 보수차익(profit_low)에 차감 반영. 레거시 행은 0
        # (인수액 미반영 상태) — 다음 새로고침이 실값·재계산된 profit_low를 채운다.
        with conn:
            conn.execute(
                "ALTER TABLE scored_listings ADD COLUMN assumed_amount INTEGER NOT NULL DEFAULT 0"
            )
    if "burden_amount_unknown" not in cols:
        # v8 → v9(감사 2026-07-23 P-01): '인수 명시인데 금액 미상'. 레거시 행은 0 —
        # 실제로도 그 시점엔 이 구분이 없었으므로 0이 사실이다. 다음 재채점이 실값을 채운다.
        with conn:
            conn.execute(
                "ALTER TABLE scored_listings "
                "ADD COLUMN burden_amount_unknown INTEGER NOT NULL DEFAULT 0"
            )

    # listing_rights: 감정평가 요항점 컬럼 추가. 테이블이 이미 있고 컬럼만 없을 때 ALTER.
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

    # listing_photos: base64(thumb_b64)→Storage URL 이전용 photo_url 컬럼 추가.
    pt = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='listing_photos'"
    ).fetchone()
    if pt is not None:
        pcols = {r["name"] for r in conn.execute("PRAGMA table_info(listing_photos)")}
        if "photo_url" not in pcols:
            with conn:
                conn.execute(
                    "ALTER TABLE listing_photos ADD COLUMN photo_url TEXT NOT NULL DEFAULT ''")


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
    items = list(items)
    if not items:
        # 방어선: 수집 0건에 전량 교체를 돌리면 서빙 DB가 통째로 비워진다(크롤 실패=만료 아님).
        # 빈 스냅샷은 교체하지 않고 기존 데이터를 보존한다(호출부가 실수해도 데이터 소실 방지).
        return 0
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
    return _prune_orphans(conn, "listing_rights")


def _prune_orphans(conn: sqlite3.Connection, table: str) -> int:
    """scored_listings 에 대응 물건이 없는 자식테이블 행(고아) 삭제. 반환=삭제 건수.

    (E2 2026-07-22 QA HIGH) rights 만 정리기가 있어 listing_photos(6578)·naver_prices(1700)
    고아가 무한 누적됐다(고아 사진행은 Supabase Storage 죽은 JPEG를 가리킴). 풀스냅샷 후 공통 배선.
    테이블 없으면 0(신규 DB 안전).
    """
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if not has:
        return 0
    with conn:
        cur = conn.execute(
            f"DELETE FROM {table} WHERE NOT EXISTS ("  # noqa: S608 — table은 내부 상수만 전달
            "  SELECT 1 FROM scored_listings s"
            f"  WHERE s.court={table}.court AND s.case_no={table}.case_no"
            f"    AND s.item_no={table}.item_no)"
        )
    return cur.rowcount


def prune_orphan_photos(conn: sqlite3.Connection) -> int:
    """scored 에 없는 listing_photos 고아 삭제(풀스냅샷 후). ⚠ Supabase Storage JPEG GC는 별도."""
    return _prune_orphans(conn, "listing_photos")


def prune_orphan_naver(conn: sqlite3.Connection) -> int:
    """scored 에 없는 naver_prices 고아 삭제(풀스냅샷 후)."""
    return _prune_orphans(conn, "naver_prices")


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


def save_photo_urls(conn: sqlite3.Connection, court: str, case_no: str, item_no: str,
                    urls: list[str], fetched_at: str = "") -> int:
    """Storage 업로드 후 공개 URL 저장 — 물건별 전량 교체(stale 방지). 반환=저장 장수."""
    key = (court, case_no, str(item_no or ""))
    with conn:
        conn.execute("DELETE FROM listing_photos WHERE court=? AND case_no=? AND item_no=?", key)
        conn.executemany(
            # thumb_b64='' 명시 — 기존 DB가 옛 스키마(thumb_b64 NOT NULL·기본값 없음)로 생성됐으면
            # Storage 모드 insert가 NOT NULL 위반으로 깨진다(2026-07-16 백필 실패 재현). 열 순서 명시로 회피.
            "INSERT INTO listing_photos (court,case_no,item_no,seq,photo_url,thumb_b64,fetched_at) "
            "VALUES (?,?,?,?,?,'',?)",
            [(*key, i, u, fetched_at) for i, u in enumerate(urls) if u])
    return sum(1 for u in urls if u)


def load_photos(conn: sqlite3.Connection, court: str, case_no: str,
                item_no: str = "") -> list[str]:
    """단건 물건 사진의 렌더용 src seq 순 — 상세 히어로용.

    듀얼모드: photo_url(Storage) 있으면 그 URL을, 없으면 base64를 data URI로 감싸 반환.
    → 마이그레이션 중에도 기존 base64 사진이 그대로 렌더된다(무중단 이전).
    """
    cur = conn.execute(
        "SELECT photo_url, thumb_b64 FROM listing_photos WHERE court=? AND case_no=? AND item_no=? "
        "ORDER BY seq", (court, case_no, str(item_no or "")))
    out = []
    for r in cur.fetchall():
        if r["photo_url"]:
            out.append(r["photo_url"])
        elif r["thumb_b64"]:
            out.append(f"data:image/jpeg;base64,{r['thumb_b64']}")
    return out


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
        # sqlite는 bool을 0/1 정수로 돌려준다 — dataclass 계약(bool)에 맞춰 복원.
        # (truthy 비교는 통과하지만 `is True` 류 검사와 직렬화에서 어긋난다.)
        kw["rights_verified"] = bool(kw.get("rights_verified"))
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
