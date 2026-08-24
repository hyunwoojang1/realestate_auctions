"""backup_db 백업 스크립트 테스트 — 2026-08-24 감사 C-1(자동 백업 부재) 대응 검증.

오프사이트(R2/D:)는 네트워크·볼륨 의존이라 여기서 안 때린다(오프라인 계약 유지).
스냅샷 일관성·보존정책·슬롯 순환 등 순수 로직만 검증한다.
"""
import datetime as dt
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backup_db import main, prune_keep, snapshot_sqlite, weekly_slot  # noqa: E402


def _make_db(path: Path, rows: int = 3) -> None:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO t (v) VALUES (?)", [(f"r{i}",) for i in range(rows)])
    con.commit()
    con.close()


def test_snapshot_is_consistent_copy(tmp_path):
    src = tmp_path / "src.db"
    _make_db(src, rows=5)
    dst = tmp_path / "nested" / "snap.db"  # 부모 디렉터리 자동 생성도 함께 검증

    snapshot_sqlite(src, dst)

    con = sqlite3.connect(str(dst))
    assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 5
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


def test_prune_keeps_newest_by_name(tmp_path):
    for stamp in ["20260801-0530", "20260802-0530", "20260803-0530", "20260804-0530"]:
        (tmp_path / f"auction-{stamp}.db").write_bytes(b"x")

    removed = prune_keep(tmp_path, keep=2, pattern="auction-*.db")

    survivors = sorted(p.name for p in tmp_path.glob("auction-*.db"))
    assert survivors == ["auction-20260803-0530.db", "auction-20260804-0530.db"]
    assert sorted(removed) == ["auction-20260801-0530.db", "auction-20260802-0530.db"]


def test_prune_missing_dir_is_noop(tmp_path):
    assert prune_keep(tmp_path / "없는폴더", keep=3) == []


def test_weekly_slot_cycles_over_4_weeks():
    # 4주 간격이면 같은 슬롯을 재사용(=원격 보존 4개)하고, 연속 주는 서로 다른 슬롯이어야 한다
    base = dt.date(2026, 8, 24)  # 월요일
    slots = [weekly_slot(base + dt.timedelta(weeks=w)) for w in range(5)]
    assert slots[0] == slots[4]
    assert len(set(slots[:4])) == 4


def test_main_pre_refresh_creates_and_prunes(tmp_path):
    src = tmp_path / "auction.db"
    _make_db(src)
    root = tmp_path / "backups"
    # 보존정책(3) 초과를 만들기 위해 오래된 스냅샷 4개를 미리 심는다
    tier = root / "pre-refresh"
    tier.mkdir(parents=True)
    for stamp in ["20260701-0530", "20260702-0530", "20260703-0530", "20260704-0530"]:
        (tier / f"auction-{stamp}.db").write_bytes(b"x")

    rc = main(["--db", str(src), "--pre-refresh", "--local-root", str(root)])

    assert rc == 0
    files = sorted(p.name for p in tier.glob("auction-*.db"))
    assert len(files) == 3  # 신규 1 + 최신 기존 2 (KEEP=3)
    assert files[-1].startswith(f"auction-{dt.date.today():%Y%m%d}")


def test_main_missing_db_returns_1(tmp_path):
    assert main(["--db", str(tmp_path / "없다.db"), "--daily",
                 "--local-root", str(tmp_path / "b")]) == 1
