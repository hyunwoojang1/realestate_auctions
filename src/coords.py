"""courtauction 투영좌표 → WGS84 변환 + 좌표 캐시 (지도 뷰 — 아실 개편 M1).

좌표계 판별(2026-07-03, evidence/map_coords.txt): 실크롤 표본 3,239건의 주소 시도와
후보 좌표계 변환 결과를 전수 대조 — **KATEC(TM128, 옛 다음지도 좌표계) 98.4% 일치**,
EPSG 표준 후보(5174/5179/5185/5186 등)는 전부 16% 이하. courtauction의
xCordi/yCordi는 KATEC으로 확정한다(QUESTIONS Q1 해소).

안전장치: 변환 결과가 물건 주소의 시도 bbox를 벗어나면 캐시에서 제외한다 —
오좌표 핀(엉뚱한 지역에 찍힘)을 구조적으로 차단(약 1.6% 탈락).
외부 API 호출 0 — 순수 로컬 변환.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_COORD_CACHE = Path(__file__).resolve().parent.parent / "data" / "coords_cache.json"

# KATEC(TM128): 원점 38N/128E, k=0.9999, FE 400000/FN 600000, Bessel + 표준 towgs84
KATEC_PROJ = ("+proj=tmerc +lat_0=38 +lon_0=128 +k=0.9999 +x_0=400000 +y_0=600000 "
              "+ellps=bessel +units=m "
              "+towgs84=-115.80,474.99,674.11,1.16,-2.31,-1.63,6.43")

# 시도별 대략 bbox (lon_min, lat_min, lon_max, lat_max) — 오좌표 검증용
SIDO_BBOX = {
    "서울": (126.76, 37.41, 127.19, 37.72),
    "부산": (128.75, 34.98, 129.31, 35.40),
    "대구": (128.35, 35.60, 128.77, 36.02),
    "인천": (126.36, 37.33, 126.80, 37.62),
    "광주": (126.64, 35.05, 127.02, 35.26),
    "대전": (127.24, 36.18, 127.56, 36.50),
    "울산": (129.03, 35.31, 129.47, 35.72),
    "경기": (126.39, 36.89, 127.86, 38.30),
    "강원": (127.06, 37.02, 129.37, 38.62),
    "충북": (127.26, 36.01, 128.22, 37.22),
    "충남": (125.90, 35.97, 127.39, 37.05),
    "전북": (125.95, 35.30, 127.90, 36.15),
    "전남": (125.06, 33.89, 127.90, 35.50),
    "경북": (127.79, 35.56, 129.70, 37.55),
    "경남": (127.46, 34.55, 129.29, 35.91),
    "제주": (126.08, 33.10, 126.98, 33.60),
    "세종": (127.10, 36.40, 127.42, 36.73),
}
# 한반도 남한 전체 bbox — 시도 식별 실패 시 최소 검증
KOREA_BBOX = (124.5, 33.0, 131.0, 38.7)

_transformer = None


def _get_transformer():
    global _transformer
    if _transformer is None:
        from pyproj import Transformer  # noqa: PLC0415 — 무거운 import 지연
        _transformer = Transformer.from_crs(KATEC_PROJ, "EPSG:4326", always_xy=True)
    return _transformer


def to_wgs84(x: float, y: float) -> tuple[float, float]:
    """KATEC (x, y) → (lat, lon)."""
    lon, lat = _get_transformer().transform(x, y)
    return lat, lon


def sido_of(address: str) -> str | None:
    """주소 문자열 앞부분에서 시도 키 추출(서울특별시→서울)."""
    head = (address or "")[:8]
    return next((k for k in SIDO_BBOX if k in head), None)


def _valid(lon: float, lat: float, sido: str | None) -> bool:
    b = SIDO_BBOX.get(sido) if sido else None
    if b is None:
        b = KOREA_BBOX
    return b[0] <= lon <= b[2] and b[1] <= lat <= b[3]


def build_coord_cache(records, path: str | Path | None = None) -> dict:
    """CourtAuctionRecord 리스트 → 좌표 캐시 저장. 반환: 통계 dict.

    키: uid(doc_id 또는 court|case_no|item_no) + 폴백용 "case:<case_no>"(첫 물건 우선).
    레거시 DB 행(item_no 없음)은 case: 키로 조인된다.
    """
    p = Path(path) if path else DEFAULT_COORD_CACHE
    out: dict[str, list[float]] = {}
    total = ok = bad_coord = bad_bbox = 0
    for r in records:
        total += 1
        try:
            x, y = float(r.x_proj), float(r.y_proj)
        except (TypeError, ValueError):
            bad_coord += 1
            continue
        if not x or not y:
            bad_coord += 1
            continue
        lat, lon = to_wgs84(x, y)
        if not _valid(lon, lat, sido_of(r.address)):
            bad_bbox += 1
            continue
        ok += 1
        uid = r.doc_id or f"{r.court}|{r.case_no}|{r.item_no}"
        out[uid] = [round(lat, 6), round(lon, 6)]
        out.setdefault(f"case:{r.case_no}", [round(lat, 6), round(lon, 6)])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    stats = {"total": total, "ok": ok, "no_coord": bad_coord, "bbox_reject": bad_bbox}
    logger.info("좌표 캐시 저장 %s — %s", p, stats)
    return stats


def load_coord_cache(path: str | Path | None = None) -> dict[str, list[float]]:
    """좌표 캐시 로드. 없거나 손상이면 빈 dict(지도는 핀 없이 뜸 — 침묵실패 아님, 로그)."""
    p = Path(path) if path else DEFAULT_COORD_CACHE
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("좌표 캐시 손상/읽기 실패(%s): %s", p, e)
        return {}
    return raw if isinstance(raw, dict) else {}


def lookup(cache: dict, uid: str, case_no: str) -> list[float] | None:
    """uid 우선, 없으면 case_no 폴백(레거시 행)."""
    return cache.get(uid) or cache.get(f"case:{case_no}")
