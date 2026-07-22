"""B-2 발굴 2차(일회용): 상세 XML 저장 후 오프라인 분석 — .on 전수 + 임대차 문맥."""
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SCRATCH = os.path.join(ROOT, "scripts", "_ui_xml")
os.makedirs(SCRATCH, exist_ok=True)

from src.courtauction_client import BASE, CourtAuctionClient  # noqa: E402

cli = CourtAuctionClient()
cli._warm_session()
s = cli._ensure_session()

targets = ["/pgj/ui/pgj100/PGJ151F01.xml"]
for path in targets:
    out = os.path.join(SCRATCH, path.rsplit("/", 1)[1])
    if not os.path.exists(out):
        time.sleep(2)
        r = s.get(BASE + path, timeout=30)
        print(f"GET {path} -> {r.status_code} ({len(r.content)}b)")
        with open(out, "wb") as f:
            f.write(r.content)

# ---- 오프라인 분석 ----
xml = open(os.path.join(SCRATCH, "PGJ151F01.xml"), encoding="utf-8", errors="replace").read()
print("head 300:", xml[:300].replace("\n", " ")[:300])
print()

ons = sorted(set(re.findall(r"[\"']([^\"']*\.on)[\"']", xml)))
print(f"=== .on 엔드포인트 {len(ons)}개 ===")
for o in ons:
    print(" ", o)

print("\n=== 임대차/임차/전입 키워드 문맥(각 첫 2회) ===")
for kw in ("임대차", "임차인", "전입", "확정일자", "배당요구", "점유", "현황조사서", "매각물건명세서"):
    idxs = [m.start() for m in re.finditer(kw, xml)][:2]
    print(f"[{kw}] {len(idxs)}+회")
    for i in idxs:
        ctx = xml[max(0, i - 250): i + 250].replace("\n", " ")
        print("   …", re.sub(r"\s+", " ", ctx), "…")

print("\n=== 참조 xml/팝업 ===")
refs = sorted(set(re.findall(r"[\"'](/pgj/ui/[^\"']+\.xml)[\"']", xml)))
for r_ in refs:
    print(" ", r_)
