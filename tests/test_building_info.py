"""건축물대장 표시 서비스(building_info) 테스트 — 지번 파싱·요약 규칙(순수 부분만)."""
from src.building_info import _summarize, parse_jibun
from src.building_register_client import BuildingRecord


def test_parse_jibun_basic():
    assert parse_jibun("충청북도 진천군 진천읍 교성리 123-4 태왕아너스") == ("0123", "0004")


def test_parse_jibun_no_ji():
    assert parse_jibun("서울특별시 노원구 상계동 666 주공아파트") == ("0666", "0000")


def test_parse_jibun_last_admin_unit_wins():
    # 도로명·리 중첩 — 마지막 행정단위 뒤 지번을 취한다
    assert parse_jibun("경기도 성남시 분당구 정자동 178-1 정자아이파크 101동") == ("0178", "0001")


def test_parse_jibun_san_unsupported():
    assert parse_jibun("강원도 홍천군 북방면 성동리 산 12-3") is None


def test_parse_jibun_none_and_empty():
    assert parse_jibun("") is None
    assert parse_jibun(None) is None
    assert parse_jibun("주소에 지번 없음") is None


def _rec(**kw) -> BuildingRecord:
    d = dict(name="101동", use_approval_day="20080222", main_purpose="공동주택",
             total_area_m2=12000.0, ground_floors=15, underground_floors=1)
    d.update(kw)
    return BuildingRecord(**d)


def test_summarize_main_dong_and_violation_any():
    records = [
        _rec(name="101동", total_area_m2=12000.0),
        _rec(name="관리동", total_area_m2=300.0, is_violation=True,
             violation_content="무단 증축", use_approval_day="20100101"),
    ]
    out = _summarize(records)
    assert out["approved"] == "2008.02"          # 주동(최대 연면적) 기준
    assert out["is_violation"] is True           # 한 동이라도 위반이면 위반
    assert out["violation_content"] == "무단 증축"
    assert out["dong_count"] == 2
    assert out["ground_floors"] == 15


def test_summarize_empty():
    assert _summarize([]) is None
