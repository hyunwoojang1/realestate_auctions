"""B4 /stats 스모크 — 실 DB(AUCTION_DB) 대상 응답 확인 + evidence 저장."""
import json

from src.web import create_app

c = create_app().test_client()
r1 = c.get("/health")
r2 = c.get("/api/stats")
r3 = c.get("/stats")
d = r2.get_json()
lines = [
    f"health: {r1.status_code} {r1.get_json()}",
    f"/api/stats: {r2.status_code}",
    f"/stats(SSR): {r3.status_code}, title: {'매각·차익 통계' in r3.get_data(as_text=True)}",
    f"overview: {json.dumps(d['overview'], ensure_ascii=False)}",
    f"by_property_type top3: {json.dumps(d['by_property_type'][:3], ensure_ascii=False)}",
    f"by_sido top3: {json.dumps(d['by_sido'][:3], ensure_ascii=False)}",
    f"score buckets: {json.dumps(d['score_distribution'], ensure_ascii=False)}",
    f"fail dist: {json.dumps(d['fail_count_distribution'], ensure_ascii=False)}",
]
with open("evidence/stats_smoke.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("\n".join(lines))
