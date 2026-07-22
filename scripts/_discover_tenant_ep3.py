"""B-2 발굴 3차(일회용): XML 그래프 탐색 — selectAuctnCsSrchRslt.on 참조 화면(=진짜 물건상세)과
임대차 라벨을 가진 화면을 찾는다. 받은 XML은 전부 로컬 캐시(_ui_xml/)에 저장해 재요청 방지."""
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


def fetch(path: str) -> str:
    out = os.path.join(SCRATCH, path.rsplit("/", 1)[1])
    if os.path.exists(out):
        return open(out, encoding="utf-8", errors="replace").read()
    time.sleep(2)
    r = s.get(BASE + path, timeout=30)
    print(f"GET {path} -> {r.status_code} ({len(r.content)}b)")
    if r.status_code != 200:
        open(out, "w", encoding="utf-8").write("")  # 404도 캐시(재요청 방지)
        return ""
    open(out, "wb").write(r.content)
    return r.content.decode("utf-8", errors="replace")


KEYWORDS = ("임대차", "임차인", "전입", "확정일자", "배당요구", "점유")
queue = ["/pgj/ui/pgj100/PGJ151M02.xml", "/pgj/ui/pgj100/PGJ111M01.xml"]
seen: set[str] = set()
budget = 10  # 신규 네트워크 요청 상한

while queue and budget > 0:
    path = queue.pop(0)
    if path in seen:
        continue
    seen.add(path)
    cached = os.path.exists(os.path.join(SCRATCH, path.rsplit("/", 1)[1]))
    if not cached:
        budget -= 1
    xml = fetch(path)
    if not xml:
        continue
    name = path.rsplit("/", 1)[1]
    screen = re.search(r'meta_screenName="([^"]*)"', xml)
    ons = sorted(set(re.findall(r"[\"']([^\"']*\.on)[\"']", xml)))
    kw = [k for k in KEYWORDS if k in xml]
    hit_detail = "selectAuctnCsSrchRslt" in xml
    print(f"\n### {name} — 화면명={screen.group(1) if screen else '?'} "
          f"{'★상세콜참조' if hit_detail else ''} kw={kw}")
    for o in ons:
        print("   .on:", o)
    # 임대차 키워드가 있으면 그 주변 300자 문맥 1회
    for k in kw[:3]:
        i = xml.find(k)
        print(f"   [{k}] …{re.sub(chr(92)+'s+', ' ', xml[max(0,i-200):i+300])}…")
    # 참조 XML 큐잉(경로형 + 파일명형)
    refs = set(re.findall(r"[\"'](/pgj/ui/[^\"']+\.xml)[\"']", xml))
    refs |= {f"/pgj/ui/pgj100/{n}" for n in re.findall(r"[\"'](PGJ\w+\.xml)[\"']", xml)}
    for ref in sorted(refs):
        if ref not in seen:
            queue.append(ref)

print(f"\n(남은 요청예산 {budget}, 탐색한 화면 {len(seen)}개)")
