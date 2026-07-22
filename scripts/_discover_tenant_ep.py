"""B-2 발굴(일회용): 물건상세 UI XML에서 임차인표(전입일) 엔드포인트 찾기.

전략: WebSquare UI 정의 XML(/pgj/ui/...)은 정적 리소스 — 여기서 submission
action(.on)과 화면 라벨(임대차/전입/확정일자)을 전수 추출해 임차인 데이터를
주는 실제 엔드포인트를 특정한다. 요청은 XML GET 소수 + 검증 POST 1회로 최소화.
"""
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, __file__.rsplit("\\", 2)[0])

from src.courtauction_client import BASE, CourtAuctionClient  # noqa: E402

cli = CourtAuctionClient()
cli._warm_session()
s = cli._ensure_session()

CANDIDATES = [
    "/pgj/ui/pgj100/PGJ151F01.xml",   # 물건상세(pgmId 실측값)
    "/pgj/ui/pgj150/PGJ151F01.xml",
    "/pgj/ui/pgj100/PGJ151M01.xml",   # 검색화면(참조용 — 상세 XML 경로 힌트)
]

ACTION_RE = re.compile(r'action="([^"]+\.on)"')
REF_RE = re.compile(r'src="(/pgj/ui/[^"]+\.xml)"|popupUrl[^"]*"(/pgj/ui/[^"]+\.xml)"')
KEYWORDS = ("임대차", "임차", "전입", "확정일자", "배당요구", "점유", "현황조사", "명세서")

seen_xml: set[str] = set()
queue = list(CANDIDATES)
found_actions: dict[str, list[str]] = {}

while queue and len(seen_xml) < 12:          # 요청 상한(정적 XML 12개)
    path = queue.pop(0)
    if path in seen_xml:
        continue
    seen_xml.add(path)
    time.sleep(2.0)
    r = s.get(BASE + path, timeout=30)
    print(f"GET {path} -> {r.status_code} ({len(r.text)}b)")
    if r.status_code != 200 or "<html" in r.text[:200].lower():
        continue
    txt = r.text
    actions = sorted(set(ACTION_RE.findall(txt)))
    kw_hits = [k for k in KEYWORDS if k in txt]
    print(f"  actions={len(actions)} kw={kw_hits}")
    found_actions[path] = actions
    for a in actions:
        # 액션 주변 500자에 키워드 있으면 강조
        for m in re.finditer(re.escape(a), txt):
            ctx = txt[max(0, m.start() - 800): m.start() + 800]
            near = [k for k in KEYWORDS if k in ctx]
            if near:
                print(f"    * {a}  <-- near {near}")
                break
    # 참조된 하위 XML(탭/팝업)도 큐잉
    for m in REF_RE.finditer(txt):
        ref = m.group(1) or m.group(2)
        if ref and ref not in seen_xml:
            queue.append(ref)

print("\n=== 전체 액션 목록(중복 제거) ===")
all_actions = sorted({a for lst in found_actions.values() for a in lst})
for a in all_actions:
    print(" ", a)
