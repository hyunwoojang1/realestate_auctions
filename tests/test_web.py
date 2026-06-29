"""Flask 웹 API 테스트 (W1)."""
from src.web import create_app


def _client():
    return create_app().test_client()


def test_health():
    r = _client().get("/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_listings_returns_all():
    r = _client().get("/api/listings")
    assert r.status_code == 200
    data = r.get_json()
    assert isinstance(data, list) and len(data) == 6
    assert "arb_score" in data[0] and "grade" in data[0]


def test_listings_min_score_filter():
    r = _client().get("/api/listings?min_score=80")
    data = r.get_json()
    assert data and all(d["arb_score"] is not None and d["arb_score"] >= 80 for d in data)


def test_listings_type_filter():
    r = _client().get("/api/listings?type=오피스텔")
    data = r.get_json()
    assert data and all(d["property_type"] == "오피스텔" for d in data)


def test_listings_default_sorted_by_score_desc():
    data = _client().get("/api/listings").get_json()
    scores = [d["arb_score"] for d in data if d["arb_score"] is not None]
    assert scores == sorted(scores, reverse=True)


def test_detail_found():
    r = _client().get("/api/listings/2024타경51234")  # 상계주공
    assert r.status_code == 200
    assert r.get_json()["apt_name"] == "상계주공"


def test_detail_404():
    assert _client().get("/api/listings/없는사건번호").status_code == 404


def test_index_page_renders():
    r = _client().get("/")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "차익 큐레이션" in body
    assert "상계주공" in body        # 실데이터 렌더
    assert "scorebadge" in body      # 스코어 뱃지 마크업
    assert "gapmeter" in body        # 갭미터 마크업


def test_index_min_score_filter():
    body = _client().get("/?min_score=80").get_data(as_text=True)
    assert "상계주공" in body        # 95점 → 통과
    assert "화곡동 다세대" not in body  # 25점 → 필터됨


def test_property_detail_found():
    r = _client().get("/property/2024타경51234")  # 상계주공
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "상계주공" in body and "차익 근거" in body and "권리 안전성" in body


def test_property_detail_404():
    assert _client().get("/property/없는사건").status_code == 404


def test_property_detail_shows_hard_gate_reason():
    # 화곡동 다세대(유치권) → 하드게이트 사유 노출
    r = _client().get("/property/2024타경44102")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "하드게이트" in body and "유치권" in body
