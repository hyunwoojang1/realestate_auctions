"""B2 /watchlist 스모크 — 실 DB(AUCTION_DB) 대상 추가→조회→해제 왕복 + evidence 저장.

주의: 워치리스트는 tmp로 격리(운영 data/watchlist.json 미오염).
"""
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

with tempfile.TemporaryDirectory() as td:
    os.environ["AUCTION_WATCHLIST"] = str(Path(td) / "wl.json")
    from src.web import create_app

    c = create_app().test_client()
    first = c.get("/api/listings").get_json()[0]["case_no"]
    r_add = c.post(f"/api/watchlist/{first}")
    r_list = c.get("/api/watchlist")
    r_page = c.get("/watchlist")
    body = r_page.get_data(as_text=True)
    r_del = c.delete(f"/api/watchlist/{first}")
    lines = [
        f"source: {r_page.headers.get('X-Data-Source')}",
        f"add {first}: {r_add.status_code} {r_add.get_json()}",
        f"list: {r_list.status_code} {r_list.get_json()}",
        f"/watchlist page: {r_page.status_code}, title: {'관심물건' in body}, "
        f"item link: {f'/property/{first}' in body}",
        f"remove: {r_del.status_code} {r_del.get_json()}",
        f"after remove: {c.get('/api/watchlist').get_json()}",
    ]
    (ROOT / "evidence" / "watchlist_web_smoke.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
