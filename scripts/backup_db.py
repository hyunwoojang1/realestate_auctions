"""auction-arbitrage 백업 — 2026-08-24 감사 CRITICAL C-1(자동 백업 부재) 대응.

3계층 백업 (전부 프로젝트 폴더 **바깥** — 폴더 이동/삭제 사고와 격리):
  --pre-refresh : 크롤 직전 스냅샷. 나쁜 크롤·마이그레이션 사고의 되돌림점. 3개 보존.
  --daily       : 일일 스냅샷. 7개 보존.
  --weekly      : 오프사이트 —
                  (1) 전체 zip(auction.db 스냅샷 + molit_trades.db + 핵심 캐시) → 별도 볼륨 D:. 4개 보존.
                  (2) auction.db 단독 zip → R2 `backups/` 고정 슬롯 4개 순환.
                      슬롯 순환(ISO주차 % 4)을 쓰는 이유: photo_store 에 list/delete API 가 없어
                      원격 보존정책을 이름 재사용으로 대신한다(구현 30줄 절약, 실패 모드 단순).
                      R2 에는 auction.db 만 올린다 — raw_listings/낙찰이력은 재수집 불가지만
                      molit/naver 캐시는 (비싸도) 재수집 가능하므로 오프사이트 대역폭을 아낀다.

스냅샷은 sqlite3 backup API — 크롤/서빙과 파일 잠금 충돌 없이 일관된 사본을 뜬다.
(파일 복사(cp)는 WAL 중간 상태를 뜰 수 있어 쓰지 않는다.)

exit: 0 정상 / 1 로컬 스냅샷 실패 / 2 오프사이트만 실패(로컬 성공 — 크롤은 계속해도 됨)
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sqlite3
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backup_db")

# 백업 루트가 프로젝트 밖인 이유: 2026-08-24 폴더 이동 중 스케줄러 경로가 깨진 사고처럼
# 프로젝트 폴더는 옮겨질 수 있다. 백업까지 같이 옮겨지면 복구 시점에 못 찾는다.
DEFAULT_LOCAL_ROOT = Path.home() / "backups" / "auction-arbitrage"
DEFAULT_OFFSITE_ROOT = Path("D:/backups/auction-arbitrage")

KEEP = {"pre-refresh": 3, "daily": 7, "weekly": 4}
R2_SLOTS = 4  # backups/auction-weekly-slot{0..3}.zip 순환

# 전체 zip에 넣는 재수집-비싼 파일들(auction.db 스냅샷은 별도로 항상 포함)
WEEKLY_EXTRA = [
    "data/molit_trades.db",
    "data/naver_cache.json",
    "data/courtauction_full_cache.json",
    "data/courtauction_cache.json",
    "data/coords_cache.json",
]


def _load_dotenv(path: Path) -> None:
    """run.py 와 동일한 최소 .env 로더(외부 의존 없이). 이미 설정된 env 는 존중."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def snapshot_sqlite(src: Path, dst: Path) -> None:
    """sqlite backup API 로 일관 스냅샷. dst 는 덮어쓴다."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(src))
    try:
        out = sqlite3.connect(str(dst))
        try:
            con.backup(out)
        finally:
            out.close()
    finally:
        con.close()


def prune_keep(dir_: Path, keep: int, pattern: str = "*") -> list[str]:
    """이름 내림차순(=타임스탬프 최신순)으로 keep 개만 남기고 삭제. 삭제한 이름 반환."""
    if not dir_.exists():
        return []
    files = sorted((p for p in dir_.glob(pattern) if p.is_file()),
                   key=lambda p: p.name, reverse=True)
    removed = []
    for p in files[keep:]:
        p.unlink()
        removed.append(p.name)
    return removed


def weekly_slot(day: dt.date | None = None) -> int:
    """ISO 주차 기반 R2 고정 슬롯(0..R2_SLOTS-1). 같은 슬롯이 4주 주기로 재사용된다."""
    d = day or dt.date.today()
    return d.isocalendar().week % R2_SLOTS


def _local_snapshot(db: Path, tier: str, root: Path, stamp: str) -> Path:
    dst = root / tier / f"auction-{stamp}.db"
    snapshot_sqlite(db, dst)
    removed = prune_keep(root / tier, KEEP[tier], "auction-*.db")
    logger.info("%s 스냅샷 OK → %s (%.0fMB, 정리 %d개)",
                tier, dst, dst.stat().st_size / 1e6, len(removed))
    return dst


def _weekly_offsite(snapshot: Path, offsite_root: Path, stamp: str) -> bool:
    """전체 zip → D: + auction.db zip → R2. 하나라도 실패하면 False (둘 다 시도는 한다)."""
    ok = True

    # (1) 별도 볼륨 전체 zip
    try:
        offsite_root.mkdir(parents=True, exist_ok=True)
        zpath = offsite_root / f"auction-full-{stamp}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.write(snapshot, "auction.db")
            for rel in WEEKLY_EXTRA:
                p = ROOT / rel
                if p.exists():
                    z.write(p, rel)
                else:
                    logger.warning("weekly zip: %s 없음 — 건너뜀", rel)
        prune_keep(offsite_root, KEEP["weekly"], "auction-full-*.zip")
        logger.info("weekly 전체 zip OK → %s (%.0fMB)", zpath, zpath.stat().st_size / 1e6)
    except OSError as e:
        logger.error("weekly D: 백업 실패: %s %s", type(e).__name__, e)
        ok = False

    # (2) R2 오프사이트 (auction.db 단독 zip, 고정 슬롯 순환)
    try:
        import io

        from src import photo_store  # .env 로드 후에 임포트해야 R2 설정을 읽는다

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.write(snapshot, "auction.db")
            # 어느 날짜의 사본인지 슬롯 이름만으로는 모르므로 zip 안에 스탬프를 남긴다
            z.writestr("BACKUP_STAMP.txt", f"{stamp}\n")
        data = buf.getvalue()
        path = f"backups/auction-weekly-slot{weekly_slot()}.zip"
        url = photo_store.upload_blob(data, path, content_type="application/zip")
        if url:
            logger.info("weekly R2 OK → %s (%.0fMB)", path, len(data) / 1e6)
        else:
            logger.error("weekly R2 업로드 실패(설정/네트워크) — 로그 위 경고 참조")
            ok = False
    except Exception as e:  # noqa: BLE001 — 오프사이트 실패가 로컬 백업 성과를 지우면 안 됨
        logger.error("weekly R2 백업 실패: %s %s", type(e).__name__, e)
        ok = False

    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(ROOT / "auction.db"))
    ap.add_argument("--pre-refresh", action="store_true")
    ap.add_argument("--daily", action="store_true")
    ap.add_argument("--weekly", action="store_true")
    ap.add_argument("--local-root", default=os.environ.get("AUCTION_BACKUP_DIR", str(DEFAULT_LOCAL_ROOT)))
    ap.add_argument("--offsite-root", default=str(DEFAULT_OFFSITE_ROOT))
    args = ap.parse_args(argv)

    _load_dotenv(ROOT / ".env")
    db = Path(args.db)
    if not db.exists():
        logger.error("DB 없음: %s", db)
        return 1
    root = Path(args.local_root)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")

    try:
        if args.pre_refresh:
            _local_snapshot(db, "pre-refresh", root, stamp)
        if args.daily:
            _local_snapshot(db, "daily", root, stamp)
        if args.weekly:
            snap = _local_snapshot(db, "weekly", root, stamp)
            if not _weekly_offsite(snap, Path(args.offsite_root), stamp):
                return 2
        if not (args.pre_refresh or args.daily or args.weekly):
            ap.error("--pre-refresh / --daily / --weekly 중 하나 이상 지정")
    except (sqlite3.Error, OSError) as e:
        logger.error("로컬 스냅샷 실패: %s %s", type(e).__name__, e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
