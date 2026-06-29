"""국토부 실거래 XML 파서 테스트."""
from pathlib import Path

from src.molit_client import parse_apt_trades_xml, _to_won

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "sample_molit_apt.xml"


def test_to_won_handles_comma_and_space():
    assert _to_won(" 63,000") == 630_000_000
    assert _to_won("45,000") == 450_000_000
    assert _to_won("") == 0


def test_parse_fixture_yields_trades():
    trades = parse_apt_trades_xml(FIXTURE.read_text(encoding="utf-8"))
    assert len(trades) >= 10
    names = {t.apt_name for t in trades}
    assert "상계주공" in names
    assert "광교호반베르디움" in names
    # 모든 거래가 원 단위로 환산됐는지
    assert all(t.price >= 100_000_000 for t in trades)
    assert all(t.area_m2 > 0 for t in trades)


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
