"""국토부 확장 시세유형(단독/다가구·상업·토지) + 건축물대장 파서 테스트.

전부 오프라인 fixture 기반. 라이브 호출(fetch_*)은 절대 실행하지 않는다.
"""
from pathlib import Path

import pytest

from src.building_register_client import (
    building_age_years,
    parse_building_titles_xml,
)
from src.molit_extra_client import (
    ENDPOINTS_EXTRA,
    ExtraTrade,
    MolitApiError,
    parse_land_trades_xml,
    parse_nrg_trades_xml,
    parse_sh_trades_xml,
)
from src.molit_extra_client import (
    check_api_error as extra_check_api_error,
)

DATA = Path(__file__).resolve().parent.parent / "data"
SH_FIXTURE = DATA / "sample_sh_trades.xml"
NRG_FIXTURE = DATA / "sample_nrg_trades.xml"
LAND_FIXTURE = DATA / "sample_land_trades.xml"
BLD_FIXTURE = DATA / "sample_bld_title.xml"


# ---------- 단독/다가구 (sh) ----------

def test_parse_sh_fixture_yields_house_trades():
    trades = parse_sh_trades_xml(SH_FIXTURE.read_text(encoding="utf-8"))
    assert len(trades) == 3
    assert all(t.kind == "sh" for t in trades)
    assert all(t.price > 0 and t.area_m2 > 0 for t in trades)
    assert any(t.dong == "수유동" for t in trades)
    # 대표면적=연면적 우선, 대지면적은 별도 보존
    first = trades[0]
    assert first.area_m2 == 165.0
    assert first.plot_area_m2 == 210.5
    assert first.subtype in {"단독", "다가구"}
    # 거래금액 만원→원 환산
    assert first.price == 850_000_000


def test_sh_price_per_m2():
    trades = parse_sh_trades_xml(SH_FIXTURE.read_text(encoding="utf-8"))
    t = trades[0]
    assert t.price_per_m2() == pytest.approx(t.price / t.area_m2)


# ---------- 상업업무용 (nrg) ----------

def test_parse_nrg_fixture_yields_commercial_trades():
    trades = parse_nrg_trades_xml(NRG_FIXTURE.read_text(encoding="utf-8"))
    assert len(trades) == 3
    assert all(t.kind == "nrg" for t in trades)
    assert all(t.price > 0 and t.area_m2 > 0 for t in trades)
    # 대표면적=건물면적
    assert trades[0].area_m2 == 180.0
    assert trades[0].price == 3_500_000_000
    assert "근린생활시설" in trades[0].name


def test_parse_nrg_handles_english_tags():
    """국문/영문 태그 혼용 — 3번째 item은 dealAmount 등 영문 태그."""
    trades = parse_nrg_trades_xml(NRG_FIXTURE.read_text(encoding="utf-8"))
    english = trades[2]
    assert english.price == 2_100_000_000
    assert english.area_m2 == 95.0
    assert english.dong == "역삼동"
    assert english.deal_ym == "202605"
    assert english.floor == 2


# ---------- 토지 (land) ----------

def test_parse_land_fixture_yields_land_trades():
    trades = parse_land_trades_xml(LAND_FIXTURE.read_text(encoding="utf-8"))
    assert len(trades) == 3
    assert all(t.kind == "land" for t in trades)
    assert all(t.price > 0 and t.area_m2 > 0 for t in trades)
    # 토지는 거래면적이 대표면적, 건물면적 없음
    assert trades[0].area_m2 == 150.0
    assert trades[0].name == "대"           # 지목
    assert trades[0].zoning == "제2종일반주거지역"
    # 지분 거래 구분 보존
    assert any(t.subtype == "지분" for t in trades)


# ---------- 공통 규약 ----------

def test_extra_endpoints_present():
    assert set(ENDPOINTS_EXTRA) == {"sh", "nrg", "land"}
    # TLS 필수(API 키 평문 전송 방지) — https 로만.
    assert all(u.startswith("https://apis.data.go.kr/") for u in ENDPOINTS_EXTRA.values())


def test_extra_unknown_kind_raises():
    import xml.etree.ElementTree as ET  # noqa: PLC0415

    from src.molit_extra_client import _parse_root  # noqa: PLC0415

    root = ET.fromstring("<response><body><items></items></body></response>")
    with pytest.raises(ValueError, match="확장 시세유형"):
        _parse_root(root, "bogus")


def test_empty_items_yield_empty_list():
    """정상 응답이지만 item 이 없으면 빈 리스트(예외 아님)."""
    empty = "<response><body><items></items></body></response>"
    assert parse_sh_trades_xml(empty) == []
    assert parse_nrg_trades_xml(empty) == []
    assert parse_land_trades_xml(empty) == []


def test_extra_skips_zero_price_or_area():
    xml = """<response><body><items>
      <item><거래금액>0</거래금액><거래면적>100</거래면적><년>2026</년><월>3</월><법정동>x</법정동></item>
      <item><거래금액>5,000</거래금액><거래면적>0</거래면적><년>2026</년><월>3</월><법정동>x</법정동></item>
      <item><거래금액>5,000</거래금액><거래면적>50</거래면적><년>2026</년><월>3</월><법정동>x</법정동></item>
    </items></body></response>"""
    trades = parse_land_trades_xml(xml)
    assert len(trades) == 1
    assert trades[0].price == 50_000_000


def test_extra_reuses_molit_error_detection():
    """확장 클라이언트도 molit_client.check_api_error 를 그대로 재사용해 인증오류를 잡는다."""
    fault = """<OpenAPI_ServiceResponse><cmmMsgHeader>
      <returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>
      <returnReasonCode>30</returnReasonCode>
    </cmmMsgHeader></OpenAPI_ServiceResponse>"""
    with pytest.raises(MolitApiError):
        extra_check_api_error(fault)


def test_extratrade_is_trade_compatible_shape():
    """ExtraTrade 는 아파트류 Trade 와 같은 매칭 인터페이스(area_m2/price/deal_ym/dong/kind)를 갖는다."""
    t = ExtraTrade(kind="land", price=100, area_m2=10.0, deal_ym="202601", dong="x")
    for attr in ("area_m2", "price", "deal_ym", "dong", "kind"):
        assert hasattr(t, attr)


# ---------- 건축물대장 노후도·위반 ----------

def test_parse_building_titles_extracts_records():
    records = parse_building_titles_xml(BLD_FIXTURE.read_text(encoding="utf-8"))
    assert len(records) == 2
    a, b = records
    assert a.use_approval_day == "19980815"
    assert a.is_violation is False
    assert b.use_approval_day == "20150320"
    assert b.is_violation is True
    assert b.violation_content == "옥탑 무단 증축"
    assert b.ground_floors == 5
    assert b.underground_floors == 1


def test_building_age_years_computed_from_approval():
    records = parse_building_titles_xml(BLD_FIXTURE.read_text(encoding="utf-8"))
    a, b = records
    assert a.age_years(2026) == 28   # 2026 - 1998
    assert b.age_years(2026) == 11   # 2026 - 2015


def test_building_age_years_handles_bad_input():
    assert building_age_years("", 2026) is None
    assert building_age_years("abcd", 2026) is None
    assert building_age_years("0000", 2026) is None
    # 미래 승인일은 0으로 클램프
    assert building_age_years("30250101", 2026) is None  # 3025 > 3000 이상치
    assert building_age_years("2030", 2026) == 0


def test_building_violation_flag_variants():
    xml = """<response><header><resultCode>00</resultCode></header><body><items>
      <item><bldNm>A</bldNm><violYn>Y</violYn><useAprDay>20000101</useAprDay></item>
      <item><bldNm>B</bldNm><violYn>0</violYn><useAprDay>20000101</useAprDay></item>
      <item><bldNm>C</bldNm><위반건축물>위반</위반건축물><useAprDay>20000101</useAprDay></item>
    </items></body></response>"""
    records = parse_building_titles_xml(xml)
    assert [r.is_violation for r in records] == [True, False, True]
