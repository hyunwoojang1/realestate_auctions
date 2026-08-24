"""store(SQLite) ↔ store_rest(Supabase 미러) 스키마 드리프트 계약 테스트 (2026-08-24 감사).

store_rest 는 store._COLS 를 단일 출처로 select/upsert 하고, 로컬 SQLite 는 store.DDL 의
scored_listings 가 같은 컬럼을 영속한다. 한쪽에만 컬럼이 추가되면 —
SQLite insert 실패, 또는 미러 400(컬럼 없음) 후 조용한 로컬 보존 — 이 드리프트가
런타임(크롤 새벽 5:30 무인 실행)에야 터진다. 여기서 커밋 게이트로 앞당긴다.

과거 실사고: rights_verified 가 dataclass 에만 있고 DDL/_COLS 에 없어 DB 왕복마다
False 로 리셋됐다(audit-t8-20260703). 이 유형을 계약으로 봉쇄한다.
"""
import dataclasses
import sqlite3

from src import store, store_rest
from src.models import ScoredListing


def _sqlite_scored_cols() -> set[str]:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(store.DDL)
        return {r[1] for r in conn.execute("PRAGMA table_info(scored_listings)")}
    finally:
        conn.close()


def _sample() -> ScoredListing:
    return ScoredListing(
        case_no="2026타경1", apt_name="계약아파트", address="서울 노원구 상계동",
        property_type="아파트", area_m2=84.9, appraisal_price=500_000_000,
        min_bid_price=400_000_000, fail_count=1, sale_date="2026-09-01",
        est_market_price=520_000_000, matched_trades=7, confidence=0.9,
        real_acquisition_cost=430_000_000, expected_profit=90_000_000,
        gap_rate=0.23, gap_score=80.0, rights_score=90.0, liquidity_score=70.0,
        arb_score=82.0, grade="차익 유력",
    )


def test_cols_all_exist_in_sqlite_schema():
    """_COLS(미러·영속 공용 단일 출처)의 모든 컬럼이 SQLite DDL 에 존재해야 한다.

    빠지면 save_scored 의 INSERT 컬럼 목록이 SQLite 에서 즉시 실패한다.
    """
    missing = set(store._COLS) - _sqlite_scored_cols()
    assert not missing, f"_COLS 에는 있는데 SQLite scored_listings DDL 에 없음: {sorted(missing)}"


def test_payload_mirror_cols_exist_in_sqlite_schema():
    """_payload(Supabase 미러 행)의 컬럼이 — 클라우드 전용 파생 필드를 빼고 —
    전부 SQLite 스키마에도 존재해야 한다(양쪽 저장소 동형 계약).

    sale_time 은 의도된 예외: 클라우드는 raw_listings 가 없어 파생 불가라 미러에만
    포함한다(store_rest._payload 주석 계약). 새 예외를 추가하려면 여기 명시할 것.
    """
    cloud_only = {"sale_time"}
    payload = store_rest._payload(_sample())
    missing = set(payload) - cloud_only - _sqlite_scored_cols()
    assert not missing, f"미러 payload 에는 있는데 SQLite DDL 에 없음: {sorted(missing)}"


def test_sqlite_schema_has_no_orphan_cols():
    """역방향: SQLite DDL 컬럼 중 _COLS 도 미러 예외도 아닌 것이 있으면 안 된다.

    있으면 그 값은 저장 경로가 없어 항상 DEFAULT 로 남는 죽은 컬럼(침묵 드리프트)이다.
    """
    persisted = set(store._COLS) | {"market_comps"}  # market_comps 는 _COLS 밖 별도 직렬화 경로
    orphan = _sqlite_scored_cols() - persisted
    assert not orphan, f"SQLite DDL 에만 있고 저장 경로가 없는 컬럼: {sorted(orphan)}"


def test_dataclass_provides_every_col():
    """ScoredListing.to_row()(=asdict)가 _COLS 전 키를 제공해야 _payload 가 KeyError 없이 돈다."""
    field_names = {f.name for f in dataclasses.fields(ScoredListing)}
    missing = set(store._COLS) - field_names
    assert not missing, f"_COLS 에는 있는데 ScoredListing 필드에 없음: {sorted(missing)}"
