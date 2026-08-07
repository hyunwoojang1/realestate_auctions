"""네이버 부동산 수집 데이터 저장 계층 (2026-07-19 complexNo 실거래 개편).

naver_prices(요약 1행)만으로는 부족해진 4종을 정규 테이블로 저장한다:
  - naver_real_trades : 단지·평형별 국토부 실거래 이력(prices/real) — **시세 엔진의 새 1차 소스**
  - naver_complexes   : 단지 메타(세대수·사용승인일·용적률·주차·전세가율·매물수) — 환금성·노후도·폴백 정확도
  - naver_kb_history  : KB 시세 시계열(기존엔 prices[0]만 쓰고 버리던 것) — 추세·교차검증
  - naver_articles    : 개별 호가 매물(층·향·확인일·태그) — 호가 근거 투명화

원칙:
  - 금액은 전부 **원(won)** 단위로 통일 저장(네이버 응답 만원 → ×10,000은 upsert 시점에 1회).
  - upsert(INSERT OR REPLACE) — 재크롤이 항상 안전(멱등).
  - 해제거래(deleteYn)는 행을 지우지 않고 deleted=1로 보존(감사 가능) — 엔진 로더가 기본 제외.
  - 스키마 변경은 ensure_schema()가 CREATE TABLE IF NOT EXISTS + ALTER 컬럼 보강으로 처리(멱등).
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
MANWON = 10_000   # 네이버 금액 단위(만원) → 원


def _db_path(explicit: str | Path | None = None) -> str:
    return str(explicit or os.environ.get("AUCTION_DB") or (ROOT / "auction.db"))


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(db_path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# 스키마
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS naver_real_trades (
    complex_no          TEXT NOT NULL,
    area_no             TEXT NOT NULL,
    trade_ymd           TEXT NOT NULL,     -- YYYYMMDD (일 미상은 YYYYMM00)
    floor               INTEGER DEFAULT 0,
    price               INTEGER NOT NULL,  -- 원
    exclusive_area      REAL DEFAULT 0,
    representative_area REAL DEFAULT 0,
    deleted             INTEGER DEFAULT 0, -- 1=해제(deleteYn='O') — 보존하되 엔진은 제외
    trade_type          TEXT DEFAULT 'A1',
    fetched_at          TEXT DEFAULT '',
    -- (감사 2026-07-19 C1) deleted를 PK에서 제거: 같은 거래(ymd·floor·price)가 정상행+취소행
    -- 쌍으로 오는데(650건 중 345건 공존), deleted를 PK에 두면 정상 쌍둥이가 별도 행으로 남아
    -- 시세에 계속 혼입됐다. PK를 거래 자체로 잡고 취소가 정상을 덮어쓰게 한다(upsert가 취소 우선).
    PRIMARY KEY (complex_no, area_no, trade_ymd, floor, price)
);
CREATE INDEX IF NOT EXISTS idx_nrt_complex ON naver_real_trades (complex_no, area_no, trade_ymd);

CREATE TABLE IF NOT EXISTS naver_complexes (
    complex_no           TEXT PRIMARY KEY,
    complex_name         TEXT DEFAULT '',
    cortar_no            TEXT DEFAULT '',
    household_count      INTEGER DEFAULT 0,
    total_dong_count     INTEGER DEFAULT 0,
    use_approve_ymd      TEXT DEFAULT '',   -- 사용승인일(노후도)
    batl_ratio           REAL DEFAULT 0,    -- 용적률
    btl_ratio            REAL DEFAULT 0,    -- 건폐율
    parking_possible     INTEGER DEFAULT 0,
    parking_per_household TEXT DEFAULT '',
    construction_company TEXT DEFAULT '',
    heat_method          TEXT DEFAULT '',
    heat_fuel            TEXT DEFAULT '',
    deal_count           INTEGER DEFAULT 0, -- 매매 매물수(유동성)
    lease_count          INTEGER DEFAULT 0,
    rent_count           INTEGER DEFAULT 0,
    lease_per_deal_rate  TEXT DEFAULT '',   -- 전세가율(예 '82~85%') — 전세 역산 폴백의 실측 근거
    min_price            INTEGER DEFAULT 0, -- 원
    max_price            INTEGER DEFAULT 0, -- 원
    latitude             REAL DEFAULT 0,
    longitude            REAL DEFAULT 0,
    fetched_at           TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS naver_kb_history (
    complex_no  TEXT NOT NULL,
    area_no     TEXT NOT NULL,
    base_ymd    TEXT NOT NULL,      -- KB 기준일
    deal_low    INTEGER DEFAULT 0,  -- 원
    deal_avg    INTEGER DEFAULT 0,
    deal_high   INTEGER DEFAULT 0,
    lease_low   INTEGER DEFAULT 0,
    lease_avg   INTEGER DEFAULT 0,
    lease_high  INTEGER DEFAULT 0,
    lease_per_deal_rate TEXT DEFAULT '',
    fetched_at  TEXT DEFAULT '',
    PRIMARY KEY (complex_no, area_no, base_ymd)
);

CREATE TABLE IF NOT EXISTS naver_articles (
    article_no    TEXT PRIMARY KEY,
    complex_no    TEXT DEFAULT '',
    trade_type    TEXT DEFAULT 'A1',
    price         INTEGER DEFAULT 0,  -- 원(dealOrWarrantPrc)
    area_name     TEXT DEFAULT '',
    area1         REAL DEFAULT 0,     -- 공급면적
    area2         REAL DEFAULT 0,     -- 전용면적
    floor_info    TEXT DEFAULT '',
    direction     TEXT DEFAULT '',
    confirm_ymd   TEXT DEFAULT '',
    feature_desc  TEXT DEFAULT '',
    tags          TEXT DEFAULT '',    -- '|' join
    same_addr_cnt INTEGER DEFAULT 0,
    same_addr_min INTEGER DEFAULT 0,  -- 원
    same_addr_max INTEGER DEFAULT 0,  -- 원
    building_name TEXT DEFAULT '',
    fetched_at    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_na_complex ON naver_articles (complex_no, trade_type);

-- (2026-07-19 M3/증분) 쌍 처리상태 — 실거래 0건 쌍도 '확인함'을 기록해 매 실행 재크롤을 막고,
-- last_checked 로 증분 갱신(오래된 쌍만 재수집) 기준을 만든다. trade_count=0도 정상 완료.
CREATE TABLE IF NOT EXISTS naver_pair_status (
    complex_no   TEXT NOT NULL,
    area_no      TEXT NOT NULL,
    last_checked TEXT NOT NULL,      -- YYYY-MM-DD HH:MM:SS
    trade_count  INTEGER DEFAULT 0,  -- 이 쌍의 실거래 행수(0=빈 쌍, 확인 완료)
    latest_ymd   TEXT DEFAULT '',    -- 이 쌍의 최신 거래 YYYYMMDD(증분 판단)
    exhausted    INTEGER DEFAULT 1,  -- 0=페이지캡 잘림(옛 꼬리 미수집)
    PRIMARY KEY (complex_no, area_no)
);
"""

# naver_prices(기존 요약 테이블) 컬럼 보강 — 전세 상·하한(기존엔 lease_avg만 저장하고 버림)
_PRICE_EXTRA_COLS = (("lease_low", "INTEGER"), ("lease_high", "INTEGER"))


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    # 기존 naver_prices가 있으면 확장 컬럼 보강(멱등). 없으면 크롤러(deploy)가 만들 때 포함됨.
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(naver_prices)")}
        if cols:
            for name, typ in _PRICE_EXTRA_COLS:
                if name not in cols:
                    conn.execute(f"ALTER TABLE naver_prices ADD COLUMN {name} {typ}")
    except sqlite3.DatabaseError as e:
        logger.warning("naver_prices 컬럼 보강 실패(무시하고 계속): %s", e)
    conn.commit()


# ---------------------------------------------------------------------------
# prices/real 응답 → 정규화 행
# ---------------------------------------------------------------------------
def flatten_real_response(resp: dict | None) -> list[dict]:
    """prices/real 응답(realPriceOnMonthList 중첩)을 행 리스트로 평탄화.

    실측(2026-07-19 스모크, complexNo 24958): 행 필드 = tradeYear/tradeMonth/tradeDate/
    dealPrice(만원)/floor/exclusiveArea/representativeArea/deleteYn(취소행에만 존재)/tradeType.
    """
    out: list[dict] = []
    for month in (resp or {}).get("realPriceOnMonthList") or []:
        for r in month.get("realPriceList") or []:
            out.append(r)
    return out


def _trade_ymd(r: dict) -> str:
    """행 → YYYYMMDD. tradeDate는 '04'/'27' 문자열 실측 — 미상이면 '00'."""
    y = str(r.get("tradeYear") or "").strip()
    m = str(r.get("tradeMonth") or "").strip().zfill(2)
    d = str(r.get("tradeDate") or "").strip().zfill(2) or "00"
    if not y or len(y) != 4:
        return ""
    return f"{y}{m}{d}"


def _is_cancelled(r: dict) -> bool:
    """네이버 실거래 취소행 판정 — deleteYn 값은 실측상 'O'(감사 2026-07-19 C1: 코드가 'Y'를
    찾아 612건 취소가 전부 정상으로 새던 버그). 대소문자·공백 방어 + 혹시 모를 'Y'도 포함."""
    v = str(r.get("deleteYn") or "").strip().upper()
    return v in ("O", "Y")


def _row_price_won(r: dict) -> int:
    try:
        return int(r.get("dealPrice") or 0) * MANWON
    except (TypeError, ValueError):
        return 0


def _row_floor(r: dict) -> int:
    try:
        return int(r.get("floor") or 0)
    except (TypeError, ValueError):
        return 0


def upsert_real_trades(conn: sqlite3.Connection, complex_no: str, area_no: str,
                       rows: list[dict], fetched_at: str) -> int:
    """평탄화 행들을 naver_real_trades에 upsert. 저장 건수(고유 거래) 반환.

    (감사 2026-07-19 C1) 취소거래는 정상행 쌍둥이(같은 ymd·floor·price)까지 함께 오므로,
    **거래 단위로 먼저 취소여부를 집계**한 뒤 고유 거래 1건씩 저장한다 — 한 거래에 취소행이
    하나라도 있으면 deleted=1. deleteYn 행도 deleted=1로 보존(감사 가능), 엔진 로더가 제외.
    """
    # 1) 거래키(ymd,floor,price)별 취소여부 집계 + 대표 행 선택
    agg: dict[tuple, dict] = {}
    cancelled_keys: set[tuple] = set()
    for r in rows:
        ymd = _trade_ymd(r)
        price = _row_price_won(r)
        if not ymd or not price:
            continue
        floor = _row_floor(r)
        key = (ymd, floor, price)
        if _is_cancelled(r):
            cancelled_keys.add(key)
        agg.setdefault(key, r)   # 대표 행(면적 등 표시용) — 첫 행
    # 2) 고유 거래 1건씩 저장
    n = 0
    for (ymd, floor, price), r in agg.items():
        try:
            excl = float(r.get("exclusiveArea") or 0)
        except (TypeError, ValueError):
            excl = 0.0
        try:
            rep = float(r.get("representativeArea") or 0)
        except (TypeError, ValueError):
            rep = 0.0
        conn.execute(
            "INSERT OR REPLACE INTO naver_real_trades "
            "(complex_no, area_no, trade_ymd, floor, price, exclusive_area, "
            " representative_area, deleted, trade_type, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (str(complex_no), str(area_no), ymd, floor, price, excl, rep,
             1 if (ymd, floor, price) in cancelled_keys else 0,
             str(r.get("tradeType") or "A1"), fetched_at))
        n += 1
    conn.commit()
    return n


def record_pair_status(conn: sqlite3.Connection, complex_no: str, area_no: str,
                       trade_count: int, latest_ymd: str, exhausted: bool,
                       checked_at: str) -> None:
    """쌍 처리상태 기록 — 실거래 0건 쌍도 '확인함'으로 남겨 재크롤 방지(M3)·증분 기준."""
    conn.execute(
        "INSERT OR REPLACE INTO naver_pair_status "
        "(complex_no, area_no, last_checked, trade_count, latest_ymd, exhausted) "
        "VALUES (?,?,?,?,?,?)",
        (str(complex_no), str(area_no), checked_at, int(trade_count),
         str(latest_ymd or ""), 1 if exhausted else 0))
    conn.commit()


# last_checked 가 '날짜'인지 확인하는 GLOB — YYYY-MM-DD… 형태만 인정.
# (2026-08-07 실사고) scripts/reprocess_real_trades.py 가 이 칸에 'reprocess:2026-07-19 21:30'
# 처럼 접두어를 붙여 넣었고, 아래 비교는 **문자열 비교**라서 'r'(0x72) > '2'(0x32) 로
# 어떤 날짜보다도 크게 판정됐다 → 그 행들이 영구히 '신선함'으로 분류돼 증분 갱신에서 빠졌다
# (실측: 1,096행, 그중 갱신 대상 모집단에 남아 있던 407쌍이 2~3주치 거래를 놓치고 있었다).
_DATE_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*"


def checked_pairs(conn: sqlite3.Connection, stale_before: str | None = None) -> set:
    """이미 확인한 (complex_no, area_no) 집합. stale_before(YYYY-MM-DD HH:MM:SS) 지정 시
    그보다 이전에 확인된 쌍은 '재확인 대상'으로 보고 제외(=증분에서 다시 크롤).

    stale_before 비교는 날짜 형태(_DATE_GLOB) 행만 대상 — 날짜가 아닌 값은 '확인 시점 불명'
    이므로 신선하다고 보지 않는다(모름을 신선으로 바꾸지 않는다).
    """
    q = "SELECT complex_no, area_no FROM naver_pair_status"
    args: tuple = ()
    if stale_before:
        q += f" WHERE last_checked >= ? AND last_checked GLOB '{_DATE_GLOB}'"
        args = (stale_before,)
    try:
        return {(r[0], r[1]) for r in conn.execute(q, args)}
    except sqlite3.DatabaseError:
        return set()


def pair_status_map(conn: sqlite3.Connection) -> dict:
    """(complex_no, area_no) → {latest_ymd, exhausted, trade_count, last_checked}.

    증분 크롤이 쌍별 '보유한 최신 거래일'을 알아야 조기 종료(naver_client.real_prices의
    stop_before_ymd)를 걸 수 있다. 없는 쌍은 호출부가 None 으로 취급 → 전량 수집.
    """
    try:
        rows = conn.execute(
            "SELECT complex_no, area_no, latest_ymd, exhausted, trade_count, last_checked "
            "FROM naver_pair_status").fetchall()
    except sqlite3.DatabaseError:
        return {}
    return {(str(r[0]), str(r[1])): {"latest_ymd": str(r[2] or ""),
                                     "exhausted": bool(r[3]),
                                     "trade_count": int(r[4] or 0),
                                     "last_checked": str(r[5] or "")}
            for r in rows}


def _pair_jitter_days(complex_no: str, area_no: str, jitter_days: int) -> int:
    """쌍마다 안정적으로 재현되는 0..jitter_days-1 편차.

    ⚠ 내장 hash()는 프로세스마다 값이 달라(PYTHONHASHSEED) 실행할 때마다 만기일이 흔들린다 —
    crc32 로 고정한다.
    """
    if jitter_days <= 1:
        return 0
    import zlib  # noqa: PLC0415
    return zlib.crc32(f"{complex_no}:{area_no}".encode()) % jitter_days


def fresh_pairs(conn: sqlite3.Connection, stale_days: int, jitter_days: int = 7,
                now=None) -> set:
    """증분 갱신에서 **제외**할(=아직 신선한) 쌍 집합 — 쌍마다 만기일을 흩뿌린다.

    임계 = stale_days + (쌍 해시 % jitter_days) 일.

    왜(실사고 2026-08-04): 7/20 에 853쌍을 한 번에 확인해 두면 그 853쌍의 last_checked 가
    전부 7/20 이 되고, stale_days=14 단일 임계에서는 **14일 뒤 같은 날 한꺼번에** 만기된다.
    실측으로 하루 대상이 105건 → 716건으로 튀어 하루 예산을 넘겼고, 못 끝낸 쌍은 갱신 기록이
    안 남아 다음날 또 대상이 되면서 백로그가 스스로 유지됐다. 편차를 주면 같은 날 확인분이
    jitter_days 일에 걸쳐 나뉘어 만기된다.

    날짜 형태가 아닌 last_checked('reprocess:…' 등)는 신선으로 보지 않는다(checked_pairs 주석 참조).
    """
    from datetime import datetime, timedelta  # noqa: PLC0415
    ref = now or datetime.now()
    try:
        rows = conn.execute(
            "SELECT complex_no, area_no, last_checked FROM naver_pair_status "
            f"WHERE last_checked GLOB '{_DATE_GLOB}'").fetchall()
    except sqlite3.DatabaseError:
        return set()
    out = set()
    for cno, ano, checked in rows:
        try:
            when = datetime.strptime(str(checked)[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                when = datetime.strptime(str(checked)[:16], "%Y-%m-%d %H:%M")
            except ValueError:
                continue          # 날짜로 못 읽으면 신선하다고 보지 않는다(재크롤 대상)
        threshold = stale_days + _pair_jitter_days(str(cno), str(ano), jitter_days)
        if ref - when < timedelta(days=threshold):
            out.add((cno, ano))
    return out


def load_real_trades(conn: sqlite3.Connection, complex_no: str,
                     area_no: str | None = None,
                     include_deleted: bool = False) -> list[sqlite3.Row]:
    """단지(선택: 평형)의 실거래 행 — 최신순. 해제거래는 기본 제외."""
    q = "SELECT * FROM naver_real_trades WHERE complex_no=?"
    args: list = [str(complex_no)]
    if area_no is not None:
        q += " AND area_no=?"
        args.append(str(area_no))
    if not include_deleted:
        q += " AND deleted=0"
    q += " ORDER BY trade_ymd DESC"
    return list(conn.execute(q, args))


# ---------------------------------------------------------------------------
# 단지 메타 / KB 시계열 / 호가
# ---------------------------------------------------------------------------
def _i(v, scale: int = 1) -> int:
    try:
        return int(float(v)) * scale
    except (TypeError, ValueError):
        return 0


def _i_opt(v, scale: int = 1) -> int | None:
    """키 없음/파싱불가 → None(보존 신호). 값이 있으면 int — 0도 유효값.

    (감사 2026-07-20 MEDIUM2) _i는 결측과 진짜 0을 구분 못해 매물수·호가가
    high-water-mark로 굳는다. 동적 컬럼은 이 파서로 결측(None)만 보존한다.
    """
    if v is None:
        return None
    try:
        return int(float(v)) * scale
    except (TypeError, ValueError):
        return None


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _lease_rate_str(v) -> str:
    """전세가율 정규화 — 문자열('82~85%')·숫자(0.83/83) 모두 수용, 0/빈값은 ''(무데이터).

    (감사 2026-07-19 H1) 종전 `str(v or "")`는 숫자 0.0을 falsy로 버리고, 유효한 0 아닌
    float도 문자열 캐스팅만 했다. 값이 있을 때만 살린다.
    """
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    return "" if f == 0 else (f"{f:.2f}" if f < 1 else f"{f:.0f}%")


# 동적 컬럼 — 매물수·호가·전세가율. 크롤마다 오르내리는 값이라 0/''도 유효한 현재값이다.
_DYNAMIC_COLS = {"deal_count", "lease_count", "rent_count",
                 "lease_per_deal_rate", "min_price", "max_price"}


def upsert_complex(conn: sqlite3.Connection, detail: dict, fetched_at: str,
                   overview: dict | None = None) -> str | None:
    """complex_detail 응답(complexDetail 서브객체) + (선택) overview → naver_complexes 1행.

    (감사 2026-07-19 H1) **빈값 덮어쓰기 금지**: detail/overview가 비어(재파싱·일시실패) 들어와도
    기존 행의 채워진 값을 파괴하지 않는다.
    (감사 2026-07-20 MEDIUM2) 보존 판정은 값이 아니라 **소스 키 존재 여부** 기준 — 동적 컬럼
    (_DYNAMIC_COLS)은 페치가 값을 실제 전달했으면 0/''도 갱신한다(high-water-mark 제거).
    정적 메타(세대수·준공일 등)는 0=결측 의미라 종전 값-기반 보존을 유지한다.
    """
    cd = (detail or {}).get("complexDetail") or detail or {}
    cno = str(cd.get("complexNo") or "").strip()
    if not cno:
        return None
    ov = overview or {}
    # 기존 행 유무 — INSERT OR REPLACE 대신 존재 시 조건부 갱신으로 파괴 방지.
    exists = conn.execute("SELECT 1 FROM naver_complexes WHERE complex_no=?", (cno,)).fetchone()
    vals = {
        "complex_name": cd.get("complexName") or "",
        "cortar_no": str(cd.get("cortarNo") or ""),
        "household_count": _i(cd.get("totalHouseholdCount") or ov.get("totalHouseHoldCount")),
        "total_dong_count": _i(cd.get("totalDongCount") or ov.get("totalDongCount")),
        "use_approve_ymd": str(cd.get("useApproveYmd") or ov.get("useApproveYmd") or ""),
        "batl_ratio": _f(cd.get("batlRatio")),
        "btl_ratio": _f(cd.get("btlRatio")),
        "parking_possible": _i(cd.get("parkingPossibleCount")),
        "parking_per_household": str(cd.get("parkingCountByHousehold") or ""),
        "construction_company": cd.get("constructionCompanyName") or "",
        "heat_method": str(cd.get("heatMethodTypeCode") or ""),
        "heat_fuel": str(cd.get("heatFuelTypeCode") or ""),
        # 동적 컬럼(매물수·호가) — 결측(None)만 보존, 페치 성공한 0은 유효값으로 반영.
        "deal_count": _i_opt(cd.get("dealCount")),
        "lease_count": _i_opt(cd.get("leaseCount")),
        "rent_count": _i_opt(cd.get("rentCount")),
        # 전세가율은 0=무데이터 정의(_lease_rate_str) — ''는 결측 신호(None)로 승격해 기존값 보존.
        "lease_per_deal_rate": ((_lease_rate_str(ov["leasePerDealRate"]) or None)
                                if "leasePerDealRate" in ov else None),
        "min_price": _i_opt(ov.get("minPrice"), MANWON),
        "max_price": _i_opt(ov.get("maxPrice"), MANWON),
        "latitude": _f(cd.get("latitude")),
        "longitude": _f(cd.get("longitude")),
    }
    if not exists:
        ins = {k: v for k, v in vals.items() if v is not None}
        cols = ["complex_no", *ins.keys(), "fetched_at"]
        conn.execute(f"INSERT INTO naver_complexes ({','.join(cols)}) "
                     f"VALUES ({','.join('?' * len(cols))})",
                     (cno, *ins.values(), fetched_at))
    else:
        # 정적 메타: 유효값(0/'' 아님)만 갱신(H1 빈값 덮어쓰기 금지 유지).
        # 동적 컬럼: 소스 키가 실제 전달된 경우(None 아님)만 갱신 — 0/''도 유효(MEDIUM2).
        sets, args = [], []
        for k, v in vals.items():
            ok = (v is not None) if k in _DYNAMIC_COLS else (v not in (None, 0, 0.0, ""))
            if ok:
                sets.append(f"{k}=?")
                args.append(v)
        if sets:
            sets.append("fetched_at=?")
            args.extend([fetched_at, cno])
            conn.execute(f"UPDATE naver_complexes SET {','.join(sets)} WHERE complex_no=?", args)
    conn.commit()
    return cno


def upsert_kb_history(conn: sqlite3.Connection, complex_no: str, area_no: str,
                      market_prices: list[dict], fetched_at: str) -> int:
    """KB marketPrices 시계열 전체 upsert(기존엔 [0]만 쓰고 버리던 것). 저장 건수 반환."""
    n = 0
    for p in market_prices or []:
        base = str(p.get("baseYearMonthDay") or "").strip()
        if not base:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO naver_kb_history "
            "(complex_no, area_no, base_ymd, deal_low, deal_avg, deal_high, "
            " lease_low, lease_avg, lease_high, lease_per_deal_rate, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (str(complex_no), str(area_no), base,
             _i(p.get("dealLowPriceLimit"), MANWON),
             _i(p.get("dealAveragePrice"), MANWON),
             _i(p.get("dealUpperPriceLimit"), MANWON),
             _i(p.get("leaseLowPriceLimit"), MANWON),
             _i(p.get("leaseAveragePrice"), MANWON),
             _i(p.get("leaseUpperPriceLimit"), MANWON),
             str(p.get("leasePerDealRate") or ""),
             fetched_at))
        n += 1
    conn.commit()
    return n


def _prc_to_won(s) -> int:
    """호가 dealOrWarrantPrc: '2억 8,000' | '28,000'(만원) → 원. 실패 시 0."""
    if s is None:
        return 0
    if isinstance(s, (int, float)):
        return int(s) * MANWON
    txt = str(s).replace(",", "").replace(" ", "").strip()
    if not txt:
        return 0
    try:
        if "억" in txt:
            eok, _, rest = txt.partition("억")
            return int(float(eok)) * 100_000_000 + (int(rest) * MANWON if rest else 0)
        return int(txt) * MANWON
    except ValueError:
        return 0


def upsert_articles(conn: sqlite3.Connection, complex_no: str,
                    articles: list[dict], fetched_at: str) -> int:
    """호가 매물 목록 upsert. 저장 건수 반환."""
    n = 0
    for a in articles or []:
        ano = str(a.get("articleNo") or "").strip()
        if not ano:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO naver_articles "
            "(article_no, complex_no, trade_type, price, area_name, area1, area2, floor_info, "
            " direction, confirm_ymd, feature_desc, tags, same_addr_cnt, same_addr_min, "
            " same_addr_max, building_name, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ano, str(complex_no), str(a.get("tradeTypeCode") or "A1"),
             _prc_to_won(a.get("dealOrWarrantPrc")),
             a.get("areaName") or "", _f(a.get("area1")), _f(a.get("area2")),
             a.get("floorInfo") or "", a.get("direction") or "",
             str(a.get("articleConfirmYmd") or ""), a.get("articleFeatureDesc") or "",
             "|".join(a.get("tagList") or []),
             _i(a.get("sameAddrCnt")), _prc_to_won(a.get("sameAddrMinPrc")),
             _prc_to_won(a.get("sameAddrMaxPrc")), a.get("buildingName") or "",
             fetched_at))
        n += 1
    conn.commit()
    return n


# ---------------------------------------------------------------------------
# 채점 주입용 조회 — 물건(court, case_no, item_no) → 그 단지·평형 실거래
# ---------------------------------------------------------------------------
def real_trades_for_case(conn: sqlite3.Connection, court: str, case_no: str,
                         item_no: str) -> tuple[list[sqlite3.Row], str, str]:
    """naver_prices 매핑(complex_no·area_no)을 따라 그 물건의 실거래 행을 돌려준다.

    반환: (rows 최신순·해제 제외, complex_no, area_no). 매핑 없으면 ([], "", "").
    """
    # (감사 2026-07-20 하드닝) 신뢰게이트 — 저신뢰·NULL 매칭은 주입 대상 아님(run.py 주입경로와 동일).
    # 현재 이 함수는 미배선(dead)이지만, 향후 채점에 배선될 때 저신뢰 오매칭 주입을 선제 차단.
    row = conn.execute(
        "SELECT complex_no, area_no FROM naver_prices WHERE court=? AND case_no=? AND item_no=? "
        "AND complex_no IS NOT NULL AND complex_no != '' "
        "AND match_conf IN ('고신뢰','중신뢰')",
        (court, case_no, str(item_no or ""))).fetchone()
    if not row:
        return [], "", ""
    cno, ano = str(row["complex_no"]), str(row["area_no"] or "")
    if not cno:
        return [], "", ""
    return load_real_trades(conn, cno, ano or None), cno, ano
