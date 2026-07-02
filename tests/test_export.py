"""CSV 내보내기(B7) 테스트 — /export.csv 라우트 + report.csv_text 재사용. 라이브 0, 순수 조회."""
import csv
import io

import pytest

from src import report
from src.models import ScoredListing
from src.web import create_app


def _sl(case_no, name="A단지", profit=1_000):
    return ScoredListing(
        case_no=case_no, apt_name=name, address="서울 노원구 상계동", property_type="아파트",
        area_m2=59.0, appraisal_price=500_000_000, min_bid_price=400_000_000, fail_count=1,
        sale_date="2026-07-15", est_market_price=500_000_000, matched_trades=5, confidence=0.9,
        real_acquisition_cost=404_000_000, expected_profit=profit, gap_rate=0.2, gap_score=40.0,
        rights_score=20.0, liquidity_score=15.0, arb_score=80.0, grade="권리미확인")


@pytest.fixture
def client():
    return create_app().test_client()


# ---- report.csv_text 순수함수 ----

def test_csv_text_header_and_rows():
    text = report.csv_text([_sl("A"), _sl("B")])
    reader = list(csv.DictReader(io.StringIO(text)))
    assert len(reader) == 2
    assert reader[0]["case_no"] == "A" and reader[1]["case_no"] == "B"


def test_csv_text_empty_is_blank():
    assert report.csv_text([]) == ""


def test_to_csv_reuses_csv_text(tmp_path):
    # to_csv가 csv_text와 동일 내용을 파일로 쓴다(중복 구현 금지 확인)
    items = [_sl("A"), _sl("B")]
    p = report.to_csv(items, tmp_path / "out.csv")
    assert p.read_text(encoding="utf-8-sig", newline="") == report.csv_text(items)


# ---- /export.csv 라우트 ----

def test_export_route_content_type_and_attachment(client):
    r = client.get("/export.csv")
    assert r.status_code == 200
    assert r.mimetype == "text/csv"
    cd = r.headers.get("Content-Disposition", "")
    assert "attachment" in cd and ".csv" in cd


def test_export_route_utf8_sig_bom(client):
    r = client.get("/export.csv")
    assert r.data.startswith(b"\xef\xbb\xbf")   # 엑셀 한글 인식용 UTF-8-SIG BOM


def test_export_route_rowcount_matches_filter(client):
    # 데이터 행수(헤더 제외) == 동일 쿼리의 /api/listings 결과 수
    api = client.get("/api/listings").get_json()
    body = r_body(client, "/export.csv")
    rows = list(csv.DictReader(io.StringIO(body)))
    assert len(rows) == len(api)


def test_export_route_filter_passthrough(client):
    # min_profit 필터가 목록과 동일하게 적용됨
    api = client.get("/api/listings?min_profit=1").get_json()
    rows = list(csv.DictReader(io.StringIO(r_body(client, "/export.csv?min_profit=1"))))
    assert len(rows) == len(api)


def test_listings_page_has_export_link(client):
    # 목록 페이지에 CSV 내보내기 링크가 노출되고 현재 필터 쿼리를 보존
    body = client.get("/?min_profit=1").get_data(as_text=True)
    assert "/export.csv?min_profit=1" in body


def r_body(client, url):
    return client.get(url).data.decode("utf-8-sig")
