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


def test_listings_default_sorted_by_profit_desc():
    data = _client().get("/api/listings").get_json()
    # 정렬키 = 보수차익(profit_low, 인수 보증금 차감) 우선, 없으면 표면차익(2026-07-22 빈틈1).
    def eff(d):
        return d["profit_low"] if d.get("profit_low") is not None else d["expected_profit"]
    keys = [eff(d) for d in data if eff(d) is not None]
    assert keys == sorted(keys, reverse=True)


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
    assert "아파트 경매 1차 필터" in body   # T7 포지셔닝
    assert "상계주공" in body        # 실데이터 렌더
    assert "보수 기준 차익" in body   # T7 보수 차익 중심 UI
    # (2026-07-23) 종전의 'gapmeter in body' 검사 삭제 — 갭미터는 화면에서 렌더되지 않으며
    # (템플릿 meter 호출 0건), base.html 인라인 CSS 의 클래스 정의에 매칭돼온 허상 검사였다.
    assert "scoreno" not in body     # 점수 UI 제거(사용자 결정 #7)


def test_index_budget_filter():
    # 검색 우선 홈(2026-07): 최소차익 대신 예산(최저입찰가 상한)으로 필터.
    body = _client().get("/?budget=5").get_data(as_text=True)   # 최저입찰가 5억 이하
    assert "상계주공" in body                # 최저 3.97억 → 통과
    assert "해운대마린시티자이" not in body    # 최저 5.76억 → 필터됨


def test_property_detail_found():
    r = _client().get("/property/2024타경51234")  # 상계주공
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "상계주공" in body and "가격·수익 계산서" in body and "매수 적정성" in body


def test_property_detail_404():
    assert _client().get("/property/없는사건").status_code == 404


def test_property_detail_shows_hard_gate_reason():
    # 화곡동 다세대(유치권) → 인수 위험 사유 노출
    r = _client().get("/property/2024타경44102")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "인수 위험" in body and "유치권" in body


def _seed_possible_opposable_db(tmp_path):
    """빈요지(말소기준 2002만)+관계미상 전입세대(1996, possession=None)를 DB에 시드 → 삼환형."""
    from src.courtauction_detail import CaseRights, parse_curst_survey
    db = str(tmp_path / "opp.db")
    conn = store.connect(db)
    court, case_no, item_no = "부산서부지원", "2022타경3289", "1"
    s = ScoredListing(
        case_no=case_no, apt_name="삼환아파트", address="부산 사하구 다대동", property_type="아파트",
        area_m2=84.9,
        appraisal_price=370_000_000, min_bid_price=181_300_000, fail_count=2, sale_date="2026-07-28",
        est_market_price=None, matched_trades=0, confidence=0.6, real_acquisition_cost=190_000_000,
        expected_profit=None, gap_rate=None, gap_score=0.0, rights_score=85.0, liquidity_score=50.0,
        arb_score=None, grade="권리미확인", court=court, item_no=item_no,
    )
    store.replace_all(conn, [s])
    cr = CaseRights(court=court, case_no=case_no, item_no=item_no,
                    senior_lien="2002. 4. 23. 근저당권", spec_write_ymd="2026-04-06")
    store.save_rights(conn, [cr.to_row()])
    recs = parse_curst_survey({"ipcheck": True, "dlt_ordTsLserLtn": [
        {"mvinDtlCtt": "1996.10.14", "gdsPossCtt": None, "lesDposDts": None,
         "lesUsgDts": None, "lesPartCtt": None, "rgstryCrtcpCfmtnCtt": None}]})
    store.save_tenants(conn, court, case_no, item_no, recs, fetched_at="2026-07-23")
    conn.close()
    return db, case_no


def test_property_detail_possible_opposable_banner(tmp_path, monkeypatch):
    """리뷰 #5/#8 회귀가드: web.py 배선(amoveins)+템플릿 end-to-end. 빈요지+관계미상 전입세대(1996<
    말소기준2002) 물건의 상세페이지가 '대항력 여지' 경고를 띄우고 '치명적 인수권리 미발견' 초록은 안 띄운다."""
    db, case_no = _seed_possible_opposable_db(tmp_path)
    monkeypatch.setenv("AUCTION_DB", db)
    r = create_app().test_client().get(f"/property/{case_no}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "대항력 여지" in body                       # 여지 경고 렌더(상단칩+판정블록+매수적정성)
    assert "치명적 인수권리 미발견" not in body          # 거짓 clean(초록) 아님 — 이번 수정의 핵심 불변식
    assert "1996-10-14" in body and "2002-04-23" in body  # 전입 vs 말소기준 날짜 근거 노출


def test_index_filter_form_and_selection():
    body = _client().get("/?type=오피스텔").get_data(as_text=True)
    assert 'value="오피스텔" selected' in body  # 선택값 유지
    assert "강남역삼푸르지오시티" in body          # 오피스텔만 노출
    assert "상계주공" not in body                # 아파트는 빠짐


def test_index_has_filter_form():
    body = _client().get("/").get_data(as_text=True)
    assert 'name="region"' in body and 'name="budget"' in body and 'name="type"' in body
    assert 'name="area"' in body and 'name="fails"' in body   # 신규: 면적·유찰
    assert "빠른 진입" in body  # 빠른진입 칩


def test_index_area_filter():
    # 면적 브래킷 '~20'(전용 66㎡ 미만) → 소형만.
    body = _client().get("/?area=~20").get_data(as_text=True)
    assert "강남역삼푸르지오시티" in body     # 30㎡
    assert "광교호반베르디움" in body         # 59.8㎡
    assert "상계주공" not in body            # 84.9㎡(20평대) → 제외
    assert 'value="~20" selected' in body    # 선택값 유지


def test_index_fails_filter():
    # 유찰 2회+ → 가격 저감된 물건만.
    body = _client().get("/?fails=2").get_data(as_text=True)
    assert "상계주공" in body                 # 2회
    assert "반석마을아이파크" not in body       # 1회 → 제외
    assert "광교호반베르디움" not in body       # 0회 → 제외


def test_index_all_mode_shows_full_table():
    # 전체 탐색 = 미지원·시세추정불가 포함 + 기존 밀집 테이블(랭킹 헤더).
    body = _client().get("/?all=1").get_data(as_text=True)
    assert "전체 탐색" in body and "예상 투입" in body


# ---- 지도(③): 차익후보 기본 + 지역별 카운트 ----

def test_geojson_has_region_counts():
    gj = _client().get("/api/listings.geojson").get_json()
    assert gj["type"] == "FeatureCollection"
    assert isinstance(gj["by_sido"], list)                      # 지역별 카운트 제공
    assert all("sido" in e and "count" in e for e in gj["by_sido"])
    counts = [e["count"] for e in gj["by_sido"]]
    assert counts == sorted(counts, reverse=True)               # 최다 지역 먼저


def test_geojson_scope_tiers_are_nested():
    # 3단 스코프: 차익양수만(기본) ⊆ 평가가능(scope=evaluable) ⊆ 전체(all=1).
    def total(qs):
        return sum(e["count"] for e in _client().get("/api/listings.geojson" + qs).get_json()["by_sido"])
    profit_n = total("")                       # 기본 = 효과 차익 > 0
    eval_n = total("?scope=evaluable")         # 시세 추정된 것 전부(양수·음수)
    all_n = total("?all=1")                     # 미지원까지 전부
    assert profit_n <= eval_n <= all_n
    assert all_n > profit_n                      # 노이즈가 실제로 걸러짐(동률 아님)


def test_geojson_features_carry_sido_and_uncertain():
    feats = _client().get("/api/listings.geojson?all=1").get_json()["features"]
    # 각 핀은 클라 지역필터용 sido + 인수금액 미상 표시용 uncertain 플래그를 가진다.
    assert all("sido" in f["properties"] and "uncertain" in f["properties"] for f in feats)


def test_map_page_renders_region_panel():
    body = _client().get("/map").get_data(as_text=True)
    assert "지역별" in body                              # 지역 카운트 패널
    # 3단 스코프 토글 라벨
    assert "차익 양수만" in body and "평가가능" in body and "전체 보기" in body
    assert "인수금액" in body                            # 효과 차익 근거 안내


def test_methodology_page():
    r = _client().get("/methodology")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "산정 방법론" in body
    assert "예상 차익" in body and "취득세" in body       # 산식·세금
    assert "권리미확인" in body                           # 안전 게이트 공개
    assert "적중률" in body and "precision" in body       # 백테스트 캘리브레이션


def test_methodology_shows_tax_assumption():
    body = _client().get("/methodology").get_data(as_text=True)
    assert "1주택·비조정" in body      # 매수인 가정 명시
    assert "4.6%" in body              # 비주택 세율표


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
        arb_score=92.0, grade="차익 유력")])
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
    # 데이터 백엔드가 하나도 설정되지 않은 상태 → 샘플 폴백. 백엔드는 둘(SQLite/Supabase)이므로
    # 둘 다 비운다(주변 환경/셸 프로필에 SUPABASE_URL 이 있어도 이 케이스는 '미설정'을 의미).
    monkeypatch.delenv("AUCTION_DB", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
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
