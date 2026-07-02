"""집계 통계(B4) 테스트 — 순수 함수 + 웹 라우트.

레퍼런스(지지옥션·탱크옥션) '매각통계' 모방 기능. 라이브 호출 0 — 고정 fixture만 사용.
"""
from src import stats
from src.models import ScoredListing
from src.web import create_app


def _sl(case_no="2026타경1", ptype="아파트", addr="서울 노원구 상계동", score=80.0,
        gap=0.30, profit=100_000_000, grade="차익 유력", fail=1, conf=0.9,
        est=500_000_000):
    return ScoredListing(
        case_no=case_no, apt_name="A단지", address=addr, property_type=ptype,
        area_m2=59.0, appraisal_price=500_000_000, min_bid_price=400_000_000,
        fail_count=fail, sale_date="2026-07-15",
        est_market_price=est, matched_trades=5, confidence=conf,
        real_acquisition_cost=404_000_000, expected_profit=profit,
        gap_rate=gap, gap_score=40.0, rights_score=20.0, liquidity_score=15.0,
        arb_score=score, grade=grade)


FIX = [
    _sl("1", "아파트", "서울 노원구", score=95.0, gap=0.40, profit=200_000_000, fail=0),
    _sl("2", "아파트", "서울 강남구", score=55.0, gap=0.10, profit=50_000_000, fail=1),
    _sl("3", "오피스텔", "부산 해운대구", score=72.0, gap=0.25, profit=80_000_000, fail=2),
    _sl("4", "다세대", "부산 사하구", score=None, gap=None, profit=None,
        grade="시세추정불가", fail=5, est=None),
]


# ---- overview ----

def test_overview_counts_and_rates():
    o = stats.overview(FIX)
    assert o["count"] == 4
    assert o["est_success_rate"] == 0.75          # 4건 중 시세추정 3건
    assert o["positive_profit_count"] == 3
    assert abs(o["avg_gap_rate"] - 0.25) < 1e-9   # (0.40+0.10+0.25)/3, None 제외


def test_overview_empty_is_safe():
    o = stats.overview([])
    assert o["count"] == 0
    assert o["est_success_rate"] is None
    assert o["avg_gap_rate"] is None


# ---- 그룹 집계 ----

def test_by_property_type_groups_and_sorts_by_count():
    rows = stats.by_property_type(FIX)
    assert rows[0]["name"] == "아파트" and rows[0]["count"] == 2
    assert {r["name"] for r in rows} == {"아파트", "오피스텔", "다세대"}
    apt = rows[0]
    assert apt["top_score"] == 95.0
    assert abs(apt["avg_gap_rate"] - 0.25) < 1e-9  # (0.40+0.10)/2


def test_by_sido_extracts_sido_from_address():
    rows = stats.by_sido(FIX)
    assert {r["name"]: r["count"] for r in rows} == {"서울": 2, "부산": 2}


def test_by_sido_unknown_address_goes_to_etc():
    rows = stats.by_sido([_sl(addr="알수없는 주소 123")])
    assert rows[0]["name"] == "기타"


# ---- 분포 ----

def test_score_distribution_buckets_and_none():
    d = stats.score_distribution(FIX)
    labels = {b["label"]: b["count"] for b in d["buckets"]}
    assert labels["90~100"] == 1      # 95점
    assert labels["50~59"] == 1       # 55점
    assert labels["70~79"] == 1       # 72점
    assert d["none_count"] == 1       # 시세추정불가


def test_score_distribution_boundary_100_goes_to_top_bucket():
    d = stats.score_distribution([_sl(score=100.0)])
    labels = {b["label"]: b["count"] for b in d["buckets"]}
    assert labels["90~100"] == 1


def test_fail_count_distribution_caps_at_3plus():
    rows = stats.fail_count_distribution(FIX)
    by = {r["label"]: r["count"] for r in rows}
    assert by["0회"] == 1 and by["1회"] == 1 and by["2회"] == 1 and by["3회+"] == 1


def test_grade_distribution_most_common_first():
    rows = stats.grade_distribution(FIX)
    assert rows[0]["name"] == "차익 유력" and rows[0]["count"] == 3


# ---- summarize (라우트가 쓰는 묶음) ----

def test_summarize_has_all_sections():
    d = stats.summarize(FIX)
    assert set(d) == {"overview", "by_property_type", "by_sido",
                      "score_distribution", "fail_count_distribution",
                      "grade_distribution"}


# ---- 웹 라우트 ----

def test_api_stats_returns_summary():
    r = create_app().test_client().get("/api/stats")
    assert r.status_code == 200
    d = r.get_json()
    assert d["overview"]["count"] > 0
    assert d["by_property_type"] and d["score_distribution"]["buckets"]


def test_stats_page_renders():
    r = create_app().test_client().get("/stats")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "매각·차익 통계" in body
    assert "용도별" in body and "지역별" in body
