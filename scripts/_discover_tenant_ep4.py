"""B-2 발굴 4차(일회용): 부동산 물건상세 화면(PGJ15BM01~03) 정조준."""
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SCRATCH = os.path.join(ROOT, "scripts", "_ui_xml")

from src.courtauction_client import BASE, CourtAuctionClient  # noqa: E402

cli = CourtAuctionClient()
cli._warm_session()
s = cli._ensure_session()

KEYWORDS = ("임대차", "임차인", "전입", "확정일자", "배당요구", "점유")
for name in ("PGJ15BM01.xml", "PGJ15BM02.xml", "PGJ15BM03.xml"):
    out = os.path.join(SCRATCH, name)
    if os.path.exists(out):
        xml = open(out, encoding="utf-8", errors="replace").read()
    else:
        time.sleep(2)
        r = s.get(f"{BASE}/pgj/ui/pgj100/{name}", timeout=30)
        print(f"GET {name} -> {r.status_code} ({len(r.content)}b)")
        if r.status_code != 200:
            continue
        open(out, "wb").write(r.content)
        xml = r.content.decode("utf-8", errors="replace")
    screen = re.search(r'meta_screenName="([^"]*)"', xml)
    ons = sorted(set(re.findall(r"[\"']([^\"']*\.on)[\"']", xml)))
    kw = [k for k in KEYWORDS if k in xml]
    print(f"\n### {name} — 화면명={screen.group(1) if screen else '?'} kw={kw}")
    for o in ons:
        print("   .on:", o)
    for k in kw:
        i = xml.find(k)
        ctx = re.sub(r"\s+", " ", xml[max(0, i - 250): i + 350])
        print(f"   [{k}] …{ctx}…")
