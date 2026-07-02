"""주간 차익 TOP N 다이제스트 테스트 (V3)."""
from src import digest, pipeline


def _scored():
    return pipeline.run()


def test_top_limits_to_n():
    items = digest.top_listings(_scored(), n=3)
    assert len(items) == 3


def test_top_sorted_by_profit_desc():
    items = digest.top_listings(_scored(), n=10)
    profits = [s.expected_profit for s in items]
    assert profits == sorted(profits, reverse=True)
    # 표면차익 1위여도 경고(위험 등)는 그대로 노출된다 — 숫자와 경고는 독립 채널
    assert items[0].expected_profit == max(profits)


def test_top_min_profit_filter():
    items = digest.top_listings(_scored(), n=10, min_profit=100_000_000)
    assert items and all(s.expected_profit >= 100_000_000 for s in items)


def test_markdown_contains_top_and_title():
    md = digest.to_markdown(digest.top_listings(_scored(), n=5))
    assert "이번 주 차익 매물 TOP" in md
    assert "상계주공" in md
    assert md.count("\n|") >= 5  # 헤더+구분+행들


def test_digest_web_route():
    from src.web import create_app
    r = create_app().test_client().get("/digest?n=2")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "상계주공" in body  # TOP 2에 포함
