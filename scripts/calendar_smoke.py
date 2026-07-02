"""B1 /calendar 스모크 — 실 DB(AUCTION_DB) 대상 응답 확인 + evidence 저장."""
from pathlib import Path

from src.web import create_app

ROOT = Path(__file__).resolve().parent.parent

c = create_app().test_client()
r1 = c.get("/calendar")
r2 = c.get("/calendar?all=1")
b1 = r1.get_data(as_text=True)
b2 = r2.get_data(as_text=True)
lines = [
    f"/calendar: {r1.status_code}, title: {'경매 일정' in b1}, source: {r1.headers.get('X-Data-Source')}",
    f"/calendar?all=1: {r2.status_code}, detail links: {'/property/' in b2}",
    f"upcoming chip present: {'예정' in b1}",
    f"month sections(all): {b2.count('년 ')}",
]
(ROOT / "evidence" / "calendar_smoke.txt").write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
