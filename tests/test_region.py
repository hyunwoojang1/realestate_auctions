"""법정동코드·지역명 유틸 테스트 (X2)."""
from src import pipeline, query, region


def test_name_to_code_exact_and_partial():
    assert region.name_to_code("서울 강남구") == "11680"
    assert region.name_to_code("강남구") == "11680"   # 부분 일치
    assert region.name_to_code("노원구") == "11350"
    assert region.name_to_code("없는구") is None


def test_code_to_name_roundtrip():
    assert region.code_to_name("11680") == "서울 강남구"
    assert region.code_to_name(region.name_to_code("해운대구")) == "부산 해운대구"


def test_sido_of():
    assert region.sido_of("서울 강남구 역삼동") == "서울"
    assert region.sido_of("부산 해운대구 우동") == "부산"
    assert region.sido_of("") is None


def test_matches_region_sido_and_gu():
    addr = "서울 강남구 역삼동"
    assert region.matches_region(addr, "서울")      # 시도
    assert region.matches_region(addr, "강남구")     # 시군구 부분일치
    assert not region.matches_region(addr, "노원구")
    assert region.matches_region(addr, None)         # 미지정 → 전체 통과


def test_filter_by_gu_and_sido():
    scored = pipeline.run()
    gu = query.apply_filters(scored, region="강남구")
    assert gu and all("강남구" in s.address for s in gu)
    sido = query.apply_filters(scored, region="서울")
    assert sido and all(s.address.startswith("서울") for s in sido)
