"""데이터 신선도 노출 테스트 — 2026-08-24 침묵실패 감사 CRITICAL 대응 검증.

계약:
  - /health 에 data_asof / data_age_hours / data_stale 노출.
  - 백엔드(AUCTION_DB/store_rest)가 구성돼 있는데 샘플 폴백이면 status=degraded + 503
    (배포 검증·워치독이 '가짜 데이터 서빙'을 잡는 유일한 지점).
  - 백엔드가 아예 없는 순수 로컬 데모는 종전대로 200 ok (기존 계약 유지).
  - 데이터가 STALE_HOURS(36h) 초과로 낡으면 모든 페이지에 경고 배너.
"""
import datetime as dt
import sqlite3

import pytest

from src import store
from src.web import STALE_HOURS, _parse_ts, create_app, data_freshness


def _seed_db(path, fetched_at: str) -> None:
    """raw_listings + scored_listings 최소 스키마 시드 — store.connect 마이그레이션 재사용."""
    conn = store.connect(str(path))
    conn.execute(
        "INSERT INTO raw_listings (uid, fetched_at, raw_json) VALUES (?, ?, ?)",
        ("t1", fetched_at, "{}"))
    conn.commit()
    conn.close()


# ---- _parse_ts: 두 저장계층의 시각 포맷을 모두 aware 로 ----

def test_parse_ts_sqlite_naive_and_kst_iso():
    a = _parse_ts("2026-08-24 12:52:35")           # SQLite (naive 로컬)
    b = _parse_ts("2026-08-24T12:52:35+09:00")     # store_rest (KST aware)
    assert a.tzinfo is not None and b.tzinfo is not None
    # 이 머신(KST)에선 같은 순간이어야 한다
    assert abs((a - b).total_seconds()) < 1


# ---- data_freshness ----

def test_freshness_from_sqlite(tmp_path, monkeypatch):
    db = tmp_path / "a.db"
    recent = (dt.datetime.now() - dt.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    _seed_db(db, recent)
    monkeypatch.setenv("AUCTION_DB", str(db))
    asof, age = data_freshness()
    assert asof == recent
    assert age is not None and 1.9 < age < 2.5


def test_freshness_unknown_when_no_backend(monkeypatch):
    monkeypatch.delenv("AUCTION_DB", raising=False)
    assert data_freshness() == (None, None)


# ---- /health ----

def test_health_ok_with_fresh_db(tmp_path, monkeypatch):
    db = tmp_path / "a.db"
    recent = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _seed_db(db, recent)
    # scored 행도 있어야 db 서빙으로 판정됨 — 시드에 scored 가 없으므로 상태만 확인
    monkeypatch.setenv("AUCTION_DB", str(db))
    r = create_app().test_client().get("/health")
    body = r.get_json()
    assert body["data_asof"] == recent
    assert body["data_stale"] is False


def test_health_degraded_503_when_backend_expected_but_sample(tmp_path, monkeypatch):
    # AUCTION_DB 가 가리키는 파일이 비어 있으면(scored 0건) 샘플 폴백 → degraded 여야 한다
    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()
    monkeypatch.setenv("AUCTION_DB", str(db))
    r = create_app().test_client().get("/health")
    assert r.status_code == 503
    assert r.get_json()["status"] == "degraded"


def test_health_pure_local_demo_stays_ok(monkeypatch):
    monkeypatch.delenv("AUCTION_DB", raising=False)
    r = create_app().test_client().get("/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_health_stale_flag_after_36h(tmp_path, monkeypatch):
    db = tmp_path / "a.db"
    old = (dt.datetime.now() - dt.timedelta(hours=STALE_HOURS + 12)).strftime("%Y-%m-%d %H:%M:%S")
    _seed_db(db, old)
    monkeypatch.setenv("AUCTION_DB", str(db))
    body = create_app().test_client().get("/health").get_json()
    assert body["data_stale"] is True


# ---- 화면 배너 ----

@pytest.mark.parametrize("hours,expect", [(2, False), (STALE_HOURS + 24, True)])
def test_stale_banner_on_pages(tmp_path, monkeypatch, hours, expect):
    db = tmp_path / "a.db"
    ts = (dt.datetime.now() - dt.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    _seed_db(db, ts)
    monkeypatch.setenv("AUCTION_DB", str(db))
    html = create_app().test_client().get("/guide").get_data(as_text=True)
    assert ("data-stale-banner" in html) is expect
