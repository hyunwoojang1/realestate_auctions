"""T6 호가 스텁 — 점 표시·밴드 검증 보조·과대 경고.

근거: 데이터_신뢰도_문제의식_및_개선방향.md 11~12장.
- 호가는 체결가가 아님 — 밴드의 주재료가 아니라 점으로 표시하는 검증 보조자료.
- 호가가 밴드 아래면 "실거래 밴드 과대 가능성" 경고.
- 데이터 수급은 약관 문제로 크롤 금지 — 수동 입력 파일(없으면 완전 무표시).
"""
import json

from src.asking import (
    POS_ABOVE,
    POS_BELOW,
    POS_INSIDE,
    AskingPrice,
    asking_points,
    band_overstated,
    classify_vs_band,
    load_asking_prices,
)

BAND_LOW, BAND_HIGH = 760_000_000, 800_000_000


# ---- 위치 판정 ----

def test_classify_positions():
    assert classify_vs_band(700_000_000, BAND_LOW, BAND_HIGH) == POS_BELOW
    assert classify_vs_band(780_000_000, BAND_LOW, BAND_HIGH) == POS_INSIDE
    assert classify_vs_band(850_000_000, BAND_LOW, BAND_HIGH) == POS_ABOVE
    # 경계값은 밴드 안
    assert classify_vs_band(BAND_LOW, BAND_LOW, BAND_HIGH) == POS_INSIDE
    assert classify_vs_band(BAND_HIGH, BAND_LOW, BAND_HIGH) == POS_INSIDE


def test_asking_points_sorted_desc_with_position():
    pts = asking_points([AskingPrice(700_000_000), AskingPrice(850_000_000)],
                        BAND_LOW, BAND_HIGH)
    assert [p["price"] for p in pts] == [850_000_000, 700_000_000]
    assert [p["position"] for p in pts] == [POS_ABOVE, POS_BELOW]


def test_asking_points_no_band_no_position():
    """밴드가 없으면(시세근거 부족 등) 위치 판정 없이 값만 — 단정하지 않는다."""
    pts = asking_points([AskingPrice(700_000_000)], None, None)
    assert pts[0]["position"] == ""


# ---- 밴드 과대 경고 ----

def test_overstated_when_min_asking_below_band_low():
    """최저 호가 < 검증 하한가 → 실거래 밴드 과대 가능성 True(문서 11장 3항)."""
    asks = [AskingPrice(850_000_000), AskingPrice(700_000_000)]
    assert band_overstated(asks, BAND_LOW) is True


def test_not_overstated_when_askings_at_or_above_band():
    asks = [AskingPrice(BAND_LOW), AskingPrice(850_000_000)]
    assert band_overstated(asks, BAND_LOW) is False


def test_not_overstated_without_band_or_askings():
    assert band_overstated([], BAND_LOW) is False
    assert band_overstated([AskingPrice(700_000_000)], None) is False


# ---- 로드: 없으면 무표시, 손상은 로그 후 빈 상태 ----

def test_load_missing_file_returns_empty(tmp_path):
    assert load_asking_prices(tmp_path / "없는파일.json") == {}


def test_load_valid_file(tmp_path):
    p = tmp_path / "asking.json"
    p.write_text(json.dumps({
        "2025타경1": [{"price": 850_000_000, "label": "3층", "observed_at": "2026-07-01"}],
    }, ensure_ascii=False), encoding="utf-8")
    got = load_asking_prices(p)
    assert got["2025타경1"][0].price == 850_000_000
    assert got["2025타경1"][0].label == "3층"


def test_load_corrupt_file_returns_empty(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{{{{", encoding="utf-8")
    assert load_asking_prices(p) == {}


def test_load_skips_invalid_rows(tmp_path):
    p = tmp_path / "mixed.json"
    p.write_text(json.dumps({
        "A": [{"price": 0}, {"price": -1}, {"no_price": 1}, {"price": 500_000_000}],
        "B": "리스트아님",
    }), encoding="utf-8")
    got = load_asking_prices(p)
    assert len(got["A"]) == 1 and got["A"][0].price == 500_000_000
    assert "B" not in got


def test_load_accepts_float_rejects_bool(tmp_path):
    """수동 입력 친화 — float 호가 허용, bool(true)은 배제."""
    p = tmp_path / "float.json"
    p.write_text(json.dumps({"A": [{"price": 850000000.0}, {"price": True}]}), encoding="utf-8")
    got = load_asking_prices(p)
    assert len(got["A"]) == 1 and got["A"][0].price == 850_000_000


# ---- 웹: 호가 없으면 완전 무표시, 있으면 점·경고 렌더 ----

def _first_case(client):
    return client.get("/api/listings").get_json()[0]["case_no"]


def test_detail_without_asking_shows_nothing(monkeypatch):
    from src import asking as asking_mod
    from src.web import create_app
    # 환경(실파일 존재 여부)에 의존하지 않게 빈 상태를 명시 주입
    monkeypatch.setattr(asking_mod, "load_asking_prices", lambda path=None: {})
    app = create_app()
    c = app.test_client()
    html = c.get(f"/property/{_first_case(c)}").get_data(as_text=True)
    assert "현재 호가" not in html
    assert "밴드 과대 가능성" not in html


def test_detail_with_asking_renders_points_and_warning(tmp_path, monkeypatch):
    from src import asking as asking_mod
    from src.web import create_app
    app = create_app()
    c = app.test_client()
    case = _first_case(c)
    # 밴드 하한보다 낮은 호가 1건 + 높은 호가 1건 주입(수동 입력 파일 대체)
    monkeypatch.setattr(asking_mod, "load_asking_prices", lambda path=None: {
        case: [asking_mod.AskingPrice(100_000_000, label="저가"),
               asking_mod.AskingPrice(2_000_000_000, label="고가")],
    })
    html = c.get(f"/property/{case}").get_data(as_text=True)
    assert "현재 호가" in html
    assert "밴드 아래" in html and "밴드 위" in html
    assert "실거래 밴드 과대 가능성" in html
