"""지도 좌표 변환(KATEC→WGS84)·캐시·geojson 라우트 테스트 (아실 개편 M1).

CRS 판별 근거: 실크롤 3,239건 주소-시도 대조에서 KATEC 98.4% 일치(evidence/map_coords.txt).
고정 앵커: 실크롤 레코드 '서울 중구 동호로33길 15' → (127.00141, 37.56523) 검증됨.
"""
import json

from src import coords
from src.coords import build_coord_cache, load_coord_cache, lookup, sido_of, to_wgs84

# 실크롤 실측 앵커(서울 중구 동호로) — KATEC 원본 좌표는 판별 스크립트에서 역산·고정
ANCHOR_LAT, ANCHOR_LON = 37.56523, 127.00141


def test_to_wgs84_seoul_anchor():
    """서울시청 근방 KATEC 좌표가 서울 도심으로 풀린다(왕복 검증)."""
    from pyproj import Transformer
    fwd = Transformer.from_crs("EPSG:4326", coords.KATEC_PROJ, always_xy=True)
    x, y = fwd.transform(ANCHOR_LON, ANCHOR_LAT)
    lat, lon = to_wgs84(x, y)
    assert abs(lat - ANCHOR_LAT) < 1e-4
    assert abs(lon - ANCHOR_LON) < 1e-4
    # 서울 bbox 안
    assert 126.76 <= lon <= 127.19 and 37.41 <= lat <= 37.72


def test_sido_of():
    assert sido_of("서울특별시 중구 동호로") == "서울"
    assert sido_of("부산광역시 해운대구") == "부산"
    assert sido_of("이상한 주소") is None
    assert sido_of("") is None


class _Rec:
    def __init__(self, x, y, addr="서울특별시 중구", doc_id="", court="법원",
                 case_no="2025타경1", item_no="1"):
        self.x_proj, self.y_proj, self.address = x, y, addr
        self.doc_id, self.court, self.case_no, self.item_no = doc_id, court, case_no, item_no


def _katec_of(lon, lat):
    from pyproj import Transformer
    fwd = Transformer.from_crs("EPSG:4326", coords.KATEC_PROJ, always_xy=True)
    return fwd.transform(lon, lat)


def test_build_cache_validates_sido_bbox(tmp_path):
    """주소 시도와 안 맞는 좌표(오좌표)는 캐시에서 제외 — 엉뚱한 핀 방지."""
    x_seoul, y_seoul = _katec_of(127.00, 37.56)
    p = tmp_path / "coords.json"
    stats = build_coord_cache([
        _Rec(x_seoul, y_seoul, "서울특별시 중구", case_no="A"),           # 정상
        _Rec(x_seoul, y_seoul, "부산광역시 해운대구", case_no="B"),        # 시도 불일치 → 제외
        _Rec("", "", "서울특별시", case_no="C"),                          # 좌표 없음
        _Rec("abc", "def", "서울특별시", case_no="D"),                    # 파싱 불가
    ], p)
    assert stats["ok"] == 1
    assert stats["bbox_reject"] == 1
    assert stats["no_coord"] == 2
    cache = load_coord_cache(p)
    assert lookup(cache, "법원|A|1", "A") is not None
    assert lookup(cache, "법원|B|1", "B") is None


def test_cache_case_fallback_for_legacy_rows(tmp_path):
    """레거시 DB 행(uid에 item 없음)은 case: 폴백 키로 조인."""
    x, y = _katec_of(127.00, 37.56)
    p = tmp_path / "coords.json"
    build_coord_cache([_Rec(x, y, "서울특별시", doc_id="DOC9", case_no="2024타경5")], p)
    cache = load_coord_cache(p)
    assert lookup(cache, "DOC9", "2024타경5") is not None          # uid 히트
    assert lookup(cache, "||2024타경5|", "2024타경5") is not None  # 레거시 → case 폴백


def test_load_missing_or_corrupt_cache(tmp_path):
    assert load_coord_cache(tmp_path / "없음.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{{{", encoding="utf-8")
    assert load_coord_cache(bad) == {}


def test_geojson_route(tmp_path, monkeypatch):
    """geojson 라우트 — 좌표 있는 물건만 Feature, 없는 건 no_coord_count."""
    from src import web
    from src.matcher import SCOPE_SAME_COMPLEX_SAME_AREA
    from src.models import AuctionListing
    from src.score import score_listing

    lst = AuctionListing(
        case_no="M1", court="법원", address="서울 중구", lawd_cd="11140", dong="명동",
        apt_name="지도테스트", property_type="아파트", area_m2=84.0,
        appraisal_price=900_000_000, min_bid_price=500_000_000, fail_count=1,
        sale_date="2026-08-01", rights_verified=True, occupant_type="공실")
    s1 = score_listing(lst, 800_000_000, 7, market_scope=SCOPE_SAME_COMPLEX_SAME_AREA,
                       band_low=760_000_000, band_high=800_000_000, band_basis=5)
    from src.models import ScoredListing
    s1 = ScoredListing(**{**s1.to_row(), "item_no": "1"})
    s2 = ScoredListing(**{**s1.to_row(), "case_no": "M2", "apt_name": "좌표없음"})

    x, y = _katec_of(127.00, 37.56)
    p = tmp_path / "coords.json"
    build_coord_cache([_Rec(x, y, "서울특별시", court="법원", case_no="M1")], p)
    cache = load_coord_cache(p)

    monkeypatch.setattr(web, "_scored", lambda: [s1, s2])
    monkeypatch.setattr(coords, "load_coord_cache", lambda path=None: cache)
    c = web.create_app().test_client()
    gj = c.get("/api/listings.geojson").get_json()
    assert len(gj["features"]) == 1
    assert gj["no_coord_count"] == 1
    f = gj["features"][0]
    assert f["properties"]["case_no"] == "M1"
    assert f["properties"]["conservative"] is True
    lon, lat = f["geometry"]["coordinates"]
    assert 126.9 < lon < 127.1 and 37.4 < lat < 37.7
    # 지도 페이지 렌더
    html = c.get("/map").get_data(as_text=True)
    assert "leaflet" in html.lower() and "maplayout" in html


def test_coord_cache_json_shape(tmp_path):
    x, y = _katec_of(127.00, 37.56)
    p = tmp_path / "coords.json"
    build_coord_cache([_Rec(x, y, "서울특별시", case_no="S1")], p)
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert "case:S1" in raw
    lat, lon = raw["case:S1"]
    assert isinstance(lat, float) and isinstance(lon, float)
