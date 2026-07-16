"""T8 최종 감사 확정 CRITICAL/HIGH 수정 회귀 테스트 (2026-07-03, docs/audit-t8-20260703.json).

묶음: ①히어로 strict 게이트 ②복합키 소비계층(선택 페이지·300) ③'콕 집은' 캡션 제거
④미지원유형 경고 정리 ⑤레거시 라벨-값 불일치 배너 ⑥calendar/watchlist 보수 전환
⑦run.py dryrun 분리 확대 ⑧라벨 분리(매칭 vs 근거 표본).
"""
from src import web
from src.digest import passes_recommend_gates
from src.matcher import (
    SCOPE_SAME_COMPLEX_SAME_AREA,
    SCOPE_SAME_DONG_FALLBACK,
)
from src.models import AuctionListing, ScoredListing
from src.score import score_listing


def _listing(case_no="2025타경1", min_bid=520_000_000, **kw):
    d = dict(
        case_no=case_no, court="서울중앙지방법원", address="서울 노원구 상계동",
        lawd_cd="11350", dong="상계동", apt_name="행복아파트", property_type="아파트",
        area_m2=84.0, appraisal_price=900_000_000, min_bid_price=min_bid,
        fail_count=1, sale_date="2026-08-01", rights_verified=True, occupant_type="공실",
    )
    d.update(kw)
    return AuctionListing(**d)


def _scored(case_no="2025타경1", scope=SCOPE_SAME_COMPLEX_SAME_AREA, basis=5,
            band_low=760_000_000, band_high=800_000_000, min_bid=520_000_000, **kw):
    return score_listing(_listing(case_no, min_bid=min_bid, **kw), band_high, basis + 2,
                         market_scope=scope, band_low=band_low, band_high=band_high,
                         band_basis=basis)


def _legacy(case_no="L1", **kw):
    """구 DB 행 — scope/basis/profit_low 없음."""
    return score_listing(_listing(case_no, **kw), 800_000_000, 5)


def _client(monkeypatch, items):
    monkeypatch.setattr(web, "_scored", lambda: items)
    return web.create_app().test_client()


# ---- ① 히어로 strict 게이트 (감사 1·4·5·10) ----

def test_gate_strict_rejects_fallback_scope():
    s = _scored(scope=SCOPE_SAME_DONG_FALLBACK)
    assert passes_recommend_gates(s) is False          # digest도 폴백 거부
    assert passes_recommend_gates(s, allow_legacy=False) is False


def test_gate_strict_rejects_low_basis_and_legacy():
    assert passes_recommend_gates(_scored(basis=3), allow_legacy=False) is False
    legacy = _legacy()
    assert passes_recommend_gates(legacy) is True                       # digest 하위호환
    assert passes_recommend_gates(legacy, allow_legacy=False) is False  # 히어로는 거부


def test_no_hero_on_search_home_legacy_banner_in_all_mode(monkeypatch):
    """(검색 우선 홈) 홈엔 히어로 없음. 레거시 DB의 '구버전 채점' 배너는 전체 탐색(all=1)에서."""
    c = _client(monkeypatch, [_legacy("L1"), _legacy("L2", apt_name="딴단지")])
    home = c.get("/").get_data(as_text=True)
    assert 'class="hero2' not in home                  # 검색 우선 홈엔 히어로 없음
    allm = c.get("/?all=1").get_data(as_text=True)
    assert "구버전 채점 데이터" in allm                 # 전체 탐색에서 레거시 배너
    assert "기준 시세 차익" in allm                     # 구 기준 라벨


def test_home_shows_all_incl_fallback(monkeypatch):
    """(사용자 2026-07-16 정책변경) 홈은 평가가능 물건 '전체'를 점수순 노출 — 폴백(same_dong_fallback)도
    숨기지 않는다(이전엔 추천서 제외). 검증 비교군·폴백 모두 포함."""
    fallback_big = _scored("F", scope=SCOPE_SAME_DONG_FALLBACK, min_bid=400_000_000)
    ok = _scored("OK")
    c = _client(monkeypatch, [fallback_big, ok])
    import re
    picks = re.search(r'class="scards">(.*)', c.get("/").get_data(as_text=True), re.S).group(1)
    assert "/property/OK" in picks                     # 검증 비교군 포함
    assert "/property/F" in picks                      # 폴백도 이제 노출(전체 정렬 정책)


# ---- ② 복합키 소비계층 (감사 2·3) ----

def _two_items(case="2025타경77"):
    apt = _scored(case)
    shop = ScoredListing(**{**score_listing(
        _listing(case, apt_name="행복상가", property_type="상가"), None, 0).to_row(),
        "item_no": "2"})
    apt = ScoredListing(**{**apt.to_row(), "item_no": "1"})
    return [apt, shop]


def test_api_multi_item_returns_choices(monkeypatch):
    c = _client(monkeypatch, _two_items())
    r = c.get("/api/listings/2025타경77")
    assert r.status_code == 300
    j = r.get_json()
    assert j["error"] == "multiple_items"
    assert {i["item_no"] for i in j["items"]} == {"1", "2"}
    # item 지정 시 단건
    r1 = c.get("/api/listings/2025타경77?item=1")
    assert r1.status_code == 200
    assert r1.get_json()["property_type"] == "아파트"


def test_property_multi_item_shows_chooser(monkeypatch):
    c = _client(monkeypatch, _two_items())
    html = c.get("/property/2025타경77").get_data(as_text=True)
    assert "물건 선택" in html and "물건이 <b>2개</b>" in html
    assert "아파트" in html and "상가" in html
    # item 지정 시 해당 물건 상세
    d = c.get("/property/2025타경77?item=2").get_data(as_text=True)
    assert "행복상가" in d and "물건 선택" not in d


def test_single_item_routes_unchanged(monkeypatch):
    c = _client(monkeypatch, [_scored("2025타경1")])
    assert c.get("/api/listings/2025타경1").status_code == 200
    assert c.get("/property/2025타경1").status_code == 200
    assert c.get("/api/listings/없는사건").status_code == 404


# ---- ③ '콕 집은' 캡션 (감사 6 CRITICAL) ----

def test_no_recommend_caption_on_detail_pages(monkeypatch):
    """상세 페이지(위험 물건 포함)에 추천 캡션이 렌더되지 않는다 — CSS ::before 제거."""
    risky = score_listing(
        _listing("RISK", special_rights=["유치권"]), 800_000_000, 7,
        market_scope=SCOPE_SAME_COMPLEX_SAME_AREA,
        band_low=780_000_000, band_high=800_000_000, band_basis=5)
    c = _client(monkeypatch, [risky])
    html = c.get("/property/RISK").get_data(as_text=True)
    assert "콕 집은" not in html
    assert '<span class="hero-caption"' not in html    # 캡션 마크업은 목록 히어로 전용
    base = c.get("/").get_data(as_text=True)
    assert "콕 집은" not in base                       # 문구 자체 폐기(단정 표현)


# ---- ④ 미지원유형 상세 정리 (감사 12) ----

def test_unsupported_detail_no_sample_warning(monkeypatch):
    unsup = score_listing(_listing("U", property_type="다세대"), None, 0)
    c = _client(monkeypatch, [unsup])
    html = c.get("/property/U").get_data(as_text=True)
    assert "표본 낮은 신뢰" not in html                 # 시세 없는데 경고 금지
    assert "시세를 추정하지 않습니다" in html            # 정책 미추정 명시
    assert "표본 부족으로" not in html                  # 데이터 부족 오표기 제거
    assert "밴드 근거 표본" not in html                 # 0건 행 숨김


# ---- ⑥ calendar/watchlist 보수 전환 (감사 11) ----

def test_watchlist_page_conservative_label(monkeypatch, tmp_path):
    from src import watchlist as wl
    monkeypatch.setattr(wl, "watchlist_path", lambda: tmp_path / "wl.json")
    monkeypatch.setattr(wl, "snapshot_path", lambda: tmp_path / "snap.json")
    s = _scored("W1")
    c = _client(monkeypatch, [s])
    wl.add_watch("W1", wl.watchlist_path())
    html = c.get("/watchlist").get_data(as_text=True)
    assert "예상차익" not in html
    assert "보수 기준" in html


def test_calendar_conservative_label(monkeypatch):
    s = _scored("C1")
    c = _client(monkeypatch, [s])
    html = c.get("/calendar").get_data(as_text=True)
    assert "예상차익" not in html
    assert "보수 기준 차익" in html


# ---- ⑦ run.py dryrun 분리 (감사 14) ----

def test_run_sample_never_writes_serving_db(tmp_path):
    """샘플(비라이브) 실행은 지정 DB가 아니라 .dryrun.db에 적재 — 서빙 DB 오염 방지."""
    import run as run_mod
    db = str(tmp_path / "serving.db")
    rc = run_mod.main(["--json", "--db", db])
    assert rc == 0
    import os
    assert not os.path.exists(db)                      # 서빙 DB 미생성
    assert os.path.exists(db + ".dryrun.db")           # dryrun DB에 저장


# ---- ⑧ 라벨 분리: 매칭 vs 근거 표본 (감사 9) ----

def test_labels_distinguish_matched_and_basis(monkeypatch):
    # 매칭(matched_trades)과 근거 표본(basis) 라벨 구분은 상세 페이지에서 노출된다.
    s = _scored("D1", basis=5)                         # matched=7, basis=5
    c = _client(monkeypatch, [s])
    html = c.get("/property/D1").get_data(as_text=True)
    assert "매칭 7건" in html                           # 신뢰 칩
    # (2026-07-13 개편) 근거 표본은 '밴드 근거 표본' 행에서 값(5건)으로 매칭(7)과 구분 노출.
    assert "근거 표본" in html and "5건" in html        # basis(5) 별도 표기
    assert "실거래 7건" not in html                     # 동일 라벨 중복 제거
