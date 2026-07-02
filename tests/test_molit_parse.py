"""국토부 실거래 XML 파서 테스트."""
from pathlib import Path

import pytest

from src.molit_client import (
    MolitApiError,
    _redact,
    _to_won,
    check_api_error,
    parse_apt_trades_xml,
    parse_offi_trades_xml,
    parse_rh_trades_xml,
)

DATA = Path(__file__).resolve().parent.parent / "data"
FIXTURE = DATA / "sample_molit_apt.xml"
RH_FIXTURE = DATA / "sample_rh_trades.xml"
OFFI_FIXTURE = DATA / "sample_offi_trades.xml"


def test_to_won_handles_comma_and_space():
    assert _to_won(" 63,000") == 630_000_000
    assert _to_won("45,000") == 450_000_000
    assert _to_won("") == 0


def test_redact_masks_service_key():
    # 에러/로그에 API 키가 새지 않도록 serviceKey 값을 마스킹.
    url = "https://apis.data.go.kr/x/getY?serviceKey=abcd1234SECRET&sigunguCd=11680"
    red = _redact(f"500 Server Error for url: {url}")
    assert "abcd1234SECRET" not in red
    assert "serviceKey=***" in red
    assert "sigunguCd=11680" in red   # 다른 파라미터는 보존


def test_parse_fixture_yields_trades():
    trades = parse_apt_trades_xml(FIXTURE.read_text(encoding="utf-8"))
    assert len(trades) >= 10
    names = {t.apt_name for t in trades}
    assert "상계주공" in names
    assert "광교호반베르디움" in names
    # 모든 거래가 원 단위로 환산됐는지
    assert all(t.price >= 100_000_000 for t in trades)
    assert all(t.area_m2 > 0 for t in trades)


def test_parse_rh_fixture_yields_villa_trades():
    """연립다세대(빌라) 실거래 파서 — 건물명 태그가 <연립다세대>여도 처리."""
    trades = parse_rh_trades_xml(RH_FIXTURE.read_text(encoding="utf-8"))
    assert len(trades) >= 3
    assert any(t.dong == "화곡동" for t in trades)
    assert all(t.price > 0 and t.area_m2 > 0 for t in trades)
    # 건물명이 비어있지 않게 추출됐는지(연립다세대 태그)
    assert any(t.apt_name for t in trades)


def test_parse_offi_fixture_yields_officetel_trades():
    """오피스텔 실거래 파서 — 건물명 태그가 <단지>여도 처리."""
    trades = parse_offi_trades_xml(OFFI_FIXTURE.read_text(encoding="utf-8"))
    assert len(trades) >= 3
    assert any(t.dong == "역삼동" for t in trades)
    assert any("강남역삼푸르지오시티" in t.apt_name for t in trades)
    assert all(t.price > 0 and t.area_m2 > 0 for t in trades)


def test_check_api_error_raises_on_auth_fault():
    """잘못된 키 등 OpenAPI fault → MolitApiError(키 안내 포함)."""
    fault = """<OpenAPI_ServiceResponse><cmmMsgHeader>
      <errMsg>SERVICE ERROR</errMsg>
      <returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>
      <returnReasonCode>30</returnReasonCode>
    </cmmMsgHeader></OpenAPI_ServiceResponse>"""
    with pytest.raises(MolitApiError) as ei:
        check_api_error(fault)
    assert "SERVICE_KEY_IS_NOT_REGISTERED_ERROR" in str(ei.value)


def test_check_api_error_raises_on_bad_resultcode():
    bad = "<response><header><resultCode>99</resultCode><resultMsg>오류</resultMsg></header></response>"
    with pytest.raises(MolitApiError):
        check_api_error(bad)


def test_check_api_error_passes_success():
    """정상 응답(resultCode 000)은 통과하고 root를 돌려준다."""
    ok = FIXTURE.read_text(encoding="utf-8")
    root = check_api_error(ok)
    assert root is not None
    # 통과한 root로 파싱도 정상
    assert len(parse_apt_trades_xml(ok)) >= 10


def test_parsers_tag_kind():
    """파서가 물건유형(kind)을 태깅해야 매칭 단계에서 유형 분리가 가능하다."""
    apt = parse_apt_trades_xml(FIXTURE.read_text(encoding="utf-8"))
    rh = parse_rh_trades_xml(RH_FIXTURE.read_text(encoding="utf-8"))
    offi = parse_offi_trades_xml(OFFI_FIXTURE.read_text(encoding="utf-8"))
    assert apt and all(t.kind == "apt" for t in apt)
    assert rh and all(t.kind == "rh" for t in rh)
    assert offi and all(t.kind == "officetel" for t in offi)


def test_parse_english_tags():
    xml = """<response><body><items>
      <item><dealAmount>34,500</dealAmount><aptNm>테스트아파트</aptNm>
      <excluUseAr>59.9</excluUseAr><dealYear>2026</dealYear><dealMonth>3</dealMonth>
      <umdNm>역삼동</umdNm><floor>10</floor></item>
    </items></body></response>"""
    trades = parse_apt_trades_xml(xml)
    assert len(trades) == 1
    assert trades[0].apt_name == "테스트아파트"
    assert trades[0].price == 345_000_000
    assert trades[0].deal_ym == "202603"
