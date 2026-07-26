"""(A3 2026-07-27) 상세 병렬 조회 — 실패 격리·결과 동등성 회귀가드.

property_detail의 독립 조회 5종(권리·사진·임차인·점유관계·건축물대장)을 순차→병렬로 바꿨다.
계약: ①개별 조회가 예외를 던져도 페이지는 200(기존 try/warning 폴백과 동일) ②정상 경로에서
권리·사진 데이터가 종전과 동일하게 렌더된다.
"""
import json

from src import store
from src.models import ScoredListing
from src.web import create_app


def _seed(tmp_path):
    db = tmp_path / "par.db"
    conn = store.connect(str(db))
    s = ScoredListing(
        case_no="2024타경777", apt_name="병렬단지", address="서울 강남구 역삼동 1-2",
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
        "remark": "특이사항 없음", "claim_amt": None, "demand_end": "",
        "spec_write_ymd": "2026-06-01", "court_dept": "",
        "schedule": json.dumps([{"ymd": "2026-08-01", "kind": "매각기일",
                                 "result": "", "price": 200000000}], ensure_ascii=False),
        "appraisal_notes": "[]", "fetched_at": "2026-07-24",
    }])
    store.save_photos(conn, s.court, s.case_no, s.item_no, ["ZmFrZQ=="], fetched_at="x")
    conn.close()
    return db


def test_parallel_detail_renders_with_all_data(tmp_path, monkeypatch):
    """정상 경로 — 병렬화 후에도 권리 요지·사진이 종전과 동일하게 페이지에 나온다."""
    monkeypatch.setenv("AUCTION_DB", str(_seed(tmp_path)))
    body = create_app().test_client().get("/property/2024타경777").get_data(as_text=True)
    assert "2020. 1. 1. 근저당권" in body          # rights 로드됨
    assert "dphoto" in body                        # 사진 존 렌더
    assert "병렬단지" in body


def test_parallel_detail_survives_single_job_failure(tmp_path, monkeypatch):
    """개별 조회(사진) 예외 → 페이지는 200 + 나머지 데이터 정상(실패 격리 계약)."""
    monkeypatch.setenv("AUCTION_DB", str(_seed(tmp_path)))

    def boom(*a, **k):
        raise RuntimeError("사진 저장소 장애 시뮬레이션")
    monkeypatch.setattr(store, "load_photos", boom)
    r = create_app().test_client().get("/property/2024타경777")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "2020. 1. 1. 근저당권" in body          # 권리는 살아 있다


def test_parallel_detail_survives_rights_failure(tmp_path, monkeypatch):
    """권리 조회 예외 → 200 + '권리미확인' 폴백(거짓 안전 표시 없음)."""
    monkeypatch.setenv("AUCTION_DB", str(_seed(tmp_path)))

    def boom(*a, **k):
        raise RuntimeError("rights 장애 시뮬레이션")
    monkeypatch.setattr(store, "load_rights", boom)
    r = create_app().test_client().get("/property/2024타경777")
    assert r.status_code == 200
    assert "치명적 인수권리 미발견" not in r.get_data(as_text=True)   # 실패를 안전으로 오표시 금지
