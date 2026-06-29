"""주간 차익 TOP N 다이제스트 테스트 (V3)."""
from src import digest, pipeline


def _scored():
    return pipeline.run()


def test_top_limits_to_n():
    items = digest.top_listings(_scored(), n=3)
    assert len(items) == 3


def test_top_sorted_by_score_desc():
    items = digest.top_listings(_scored(), n=10)
    scores = [s.arb_score for s in items]
    assert scores == sorted(scores, reverse=True)
    assert items[0].apt_name == "상계주공"  # 95점 최상위


def test_top_min_score_filter():
    items = digest.top_listings(_scored(), n=10, min_score=80)
    assert items and all(s.arb_score >= 80 for s in items)


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
