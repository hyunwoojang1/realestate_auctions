"""규제지역 판정(src/regulation.py) — 2026-07 검증본 기준 계약 고정."""
from datetime import date

from src.regulation import basis_date, classify

AS_OF = date(2026, 7, 24)


def test_seoul_is_regulated_with_land_permit():
    r = classify("서울특별시 노원구 상계동 111", is_apartment=True, as_of=AS_OF)
    assert r["adjusted"] is True and r["land_permit"] is True
    assert any("허가 불요" in n for n in r["notes"])  # 경매 특례가 반드시 안내됨


def test_gyeonggi_listed_areas():
    assert classify("경기도 과천시 중앙동 1", as_of=AS_OF)["adjusted"] is True
    assert classify("경기도 성남시 분당구 정자동 1", as_of=AS_OF)["adjusted"] is True
    assert classify("경기도 구리시 인창동 1", as_of=AS_OF)["adjusted"] is True  # 2026-07-01 신규


def test_hwaseong_dongtan_vs_check():
    """화성시는 동탄구만 규제 — '동탄' 토큰 없으면 오판 대신 'check'."""
    assert classify("경기도 화성시 동탄대로 100", as_of=AS_OF)["adjusted"] is True
    r = classify("경기도 화성시 봉담읍 상리 1", as_of=AS_OF)
    assert r["adjusted"] == "check" and r["land_permit"] is False


def test_unregulated_region():
    r = classify("대구광역시 수성구 범어동 1", as_of=AS_OF)
    assert r["adjusted"] is False and r["land_permit"] is False and r["zone_name"] is None


def test_land_permit_expiry_guard():
    """토허 지정기간 경과 시 자동 미적용 + 재확인 노트 — 데이터 부패가 오답이 되지 않게."""
    r = classify("서울특별시 강남구 대치동 1", as_of=date(2027, 1, 15))  # 서울 광역 토허 만료 후
    assert r["adjusted"] is True and r["land_permit"] is False
    assert any("경과" in n for n in r["notes"])
    r2 = classify("경기도 구리시 인창동 1", as_of=date(2027, 1, 15))  # 신규 3곳은 2027-12-31까지
    assert r2["land_permit"] is True


def test_non_apartment_no_land_permit():
    r = classify("서울특별시 마포구 합정동 1", is_apartment=False, as_of=AS_OF)
    assert r["adjusted"] is True and r["land_permit"] is False


def test_basis_date_exposed():
    assert basis_date() >= "2026-07-24"  # UI 는 항상 기준일을 노출한다
