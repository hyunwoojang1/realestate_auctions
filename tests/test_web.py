"""Flask 웹 API 테스트 (W1)."""
from src import store
from src.models import ScoredListing
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


def test_index_filter_form_and_selection():
    body = _client().get("/?type=오피스텔").get_data(as_text=True)
    assert 'value="오피스텔" selected' in body  # 선택값 유지
    assert "강남역삼푸르지오시티" in body          # 오피스텔만 노출
    assert "상계주공" not in body                # 아파트는 빠짐


def test_index_has_filter_form():
    body = _client().get("/").get_data(as_text=True)
    assert 'name="min_score"' in body and 'name="type"' in body and 'name="sort"' in body


def test_methodology_page():
    r = _client().get("/methodology")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "차익 스코어 방법론" in body
    assert "가격갭" in body and "하드게이트" in body   # 공식·게이트
    assert "적중률" in body and "precision" in body      # 백테스트 캘리브레이션


def test_methodology_shows_calibration_values():
    body = _client().get("/methodology").get_data(as_text=True)
    # 백테스트 구간 라벨이 렌더됨
    assert "확실한차익" in body or "확실한 차익" in body


# ---- 라이브 DB 서빙(AUCTION_DB) ----

def _seed_db(path: str) -> None:
    conn = store.connect(path)
    store.upsert(conn, [ScoredListing(
        case_no="LIVE-1", apt_name="라이브단지", address="서울 강남구 역삼동",
        property_type="아파트", area_m2=84.0, appraisal_price=900_000_000,
        min_bid_price=500_000_000, fail_count=1, sale_date="2026-08-01",
        est_market_price=950_000_000, matched_trades=7, confidence=1.0,
        real_acquisition_cost=560_000_000, expected_profit=390_000_000,
        gap_rate=0.47, gap_score=47.0, rights_score=30.0, liquidity_score=20.0,
        arb_score=92.0, grade="확실한 차익")])
    conn.close()


def test_serves_from_db_when_env_set(tmp_path, monkeypatch):
    dbp = str(tmp_path / "live.db")
    _seed_db(dbp)
    monkeypatch.setenv("AUCTION_DB", dbp)
    data = create_app().test_client().get("/api/listings").get_json()
    assert len(data) == 1
    assert data[0]["apt_name"] == "라이브단지"   # 샘플이 아니라 DB가 서빙됨


def test_falls_back_to_sample_when_no_env(monkeypatch):
    monkeypatch.delenv("AUCTION_DB", raising=False)
    data = create_app().test_client().get("/api/listings").get_json()
    assert len(data) == 6   # 샘플 6건


# ---- 데이터 출처 표시(샘플/라이브 오인 방지) ----

def test_data_source_header_sample_when_no_env(monkeypatch):
    monkeypatch.delenv("AUCTION_DB", raising=False)
    r = create_app().test_client().get("/api/listings")
    assert r.headers["X-Data-Source"] == "sample(no-db)"


def test_data_source_header_db_when_seeded(tmp_path, monkeypatch):
    dbp = str(tmp_path / "live.db")
    _seed_db(dbp)
    monkeypatch.setenv("AUCTION_DB", dbp)
    r = create_app().test_client().get("/api/listings")
    assert r.headers["X-Data-Source"] == "db"


def test_health_reports_data_source_db(tmp_path, monkeypatch):
    dbp = str(tmp_path / "live.db")
    _seed_db(dbp)
    monkeypatch.setenv("AUCTION_DB", dbp)
    body = create_app().test_client().get("/health").get_json()
    assert body["data_source"] == "db"


def test_empty_db_warns_and_marks_sample(tmp_path, monkeypatch, caplog):
    """AUCTION_DB가 설정됐지만 0건이면: 샘플 폴백 + 경고 로그 + 출처=sample(db-empty)."""
    dbp = str(tmp_path / "empty.db")
    store.connect(dbp).close()          # 스키마만 생성(0 rows)
    monkeypatch.setenv("AUCTION_DB", dbp)
    import logging
    with caplog.at_level(logging.WARNING, logger="src.web"):
        r = create_app().test_client().get("/api/listings")
    assert len(r.get_json()) == 6                        # 샘플 폴백
    assert r.headers["X-Data-Source"] == "sample(db-empty)"
    assert any("적재 결과 0건" in rec.message for rec in caplog.records)


def test_property_detail_renders_for_db_listing_not_in_samples(tmp_path, monkeypatch):
    # courtauction 등 DB 서빙 매물(샘플에 없음)도 상세페이지가 404 아니라 렌더돼야 함
    dbp = str(tmp_path / "live.db")
    _seed_db(dbp)
    monkeypatch.setenv("AUCTION_DB", dbp)
    r = create_app().test_client().get("/property/LIVE-1")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "라이브단지" in body
