"""(B1/B2 2026-07-27) 목록→상세 체감 즉시화 배선 계약.

①목록에 프리페치+진행바 스크립트 ②상세 응답 private max-age=45(프리페치 재사용의 전제)
③관심 토글 복귀 리다이렉트에 캐시버스터 _r(45초 캐시가 옛 별 상태를 되살리지 않게 — 짝 계약).
"""
import json

from src import store
from src.models import ScoredListing
from src.web import create_app


def _seed(tmp_path):
    db = tmp_path / "pf.db"
    conn = store.connect(str(db))
    s = ScoredListing(
        case_no="2024타경777", apt_name="프리페치단지", address="서울 강남구 1-2",
        property_type="아파트", area_m2=84.0, appraisal_price=500_000_000,
        min_bid_price=200_000_000, fail_count=1, sale_date="2026-08-01",
        est_market_price=400_000_000, matched_trades=5, confidence=1.0,
        real_acquisition_cost=202_200_000, expected_profit=197_800_000, gap_rate=0.49,
        gap_score=90.0, rights_score=100.0, liquidity_score=80.0, arb_score=88.0,
        grade="차익 유력", court="서울중앙지방법원", item_no="1", rights_verified=True,
        market_band_low=380_000_000, profit_low=177_800_000,
    )
    store.replace_all(conn, [s])
    store.save_rights(conn, [{
        "court": s.court, "case_no": s.case_no, "item_no": s.item_no,
        "surviving_rights": "", "senior_lien": "2020. 1. 1. 근저당권", "lien_note": "",
        "remark": "", "claim_amt": None, "demand_end": "",
        "spec_write_ymd": "2026-06-01", "court_dept": "",
        "schedule": json.dumps([], ensure_ascii=False),
        "appraisal_notes": "[]", "fetched_at": "2026-07-24",
    }])
    conn.close()
    return db


def test_listing_page_has_prefetch_and_navprog(tmp_path, monkeypatch):
    monkeypatch.setenv("AUCTION_DB", str(_seed(tmp_path)))
    body = create_app().test_client().get("/").get_data(as_text=True)
    assert "prefetch" in body and "navprog" in body
    assert "touchstart" in body                      # 모바일 터치 시작 프리페치
    assert "saveData" in body                        # 데이터 절약 존중


def test_detail_response_has_short_private_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("AUCTION_DB", str(_seed(tmp_path)))
    r = create_app().test_client().get("/property/2024타경777")
    assert r.status_code == 200
    assert r.headers.get("Cache-Control") == "private, max-age=45"


def test_detail_star_reconcile_wired(tmp_path, monkeypatch):
    """(D3 리뷰 #1) 목록토글→45초 내 상세 진입의 옛 별 상태 — 상세가 /api/watchlist(no-store)로
    별 라벨을 재동기화하는 스크립트를 배선하고 있어야 한다."""
    monkeypatch.setenv("AUCTION_DB", str(_seed(tmp_path)))
    c = create_app().test_client()
    body = c.get("/property/2024타경777").get_data(as_text=True)
    assert "/api/watchlist" in body and "관심 별 상태 재동기화" in body
    api = c.get("/api/watchlist")
    assert api.headers.get("Cache-Control") == "no-store"     # 진실 원천은 무캐시


def test_watchlist_toggle_redirect_busts_detail_cache(tmp_path, monkeypatch):
    """토글 복귀 URL에 _r 캐시버스터 — 45초 캐시가 옛 별 상태를 보여주지 않게."""
    monkeypatch.setenv("AUCTION_DB", str(_seed(tmp_path)))
    monkeypatch.setenv("AUCTION_WATCHLIST", str(tmp_path / "wl.json"))
    c = create_app().test_client()
    r = c.post("/watchlist/toggle/2024타경777",
               data={"court": "서울중앙지방법원", "item": "1"},
               headers={"Referer": "http://localhost/property/2024타경777?item=1"})
    assert r.status_code in (301, 302, 303)
    assert "_r=" in r.headers.get("Location", "")
