"""국토부 실거래가 확장 클라이언트 — 단독/다가구·상업업무용·토지.

시세유형 확대(GOAL_DEPLOY [E])의 뼈대. 기존 아파트/연립다세대/오피스텔은 src/molit_client.py가
담당한다. 이 모듈은 필드 구조가 다른 3종을 추가한다:

  단독/다가구 (sh)  : RTMSDataSvcSHTrade/getRTMSDataSvcSHTrade
    - 건물명 없음. 대지면적(plottageAr)·연면적(totalFloorAr)·주택유형(houseType).
  상업업무용 (nrg)  : RTMSDataSvcNrgTrade/getRTMSDataSvcNrgTrade
    - 건물주용도(buildingUse)·유형(buildingType)·건물면적(buildingAr)·대지면적(plottageAr).
  토지 (land)       : RTMSDataSvcLandTrade/getRTMSDataSvcLandTrade
    - 건물 없음. 지목(jimok)·용도지역(landUse)·거래면적(dealArea)·지분구분(shareType).

공통 규약(molit_client와 동일):
  - 거래금액은 '만원' 단위 + 콤마 → 원으로 환산.
  - 국문/영문 태그 혼용을 모두 지원(_find 다중 키).
  - 라이브 호출은 운영자 API 키(MOLIT_API_KEY) 필요. 파서·오류감지는 fixture로만 테스트.
  - API 오류감지·재시도·페이지네이션은 molit_client 재사용(DRY).

`ExtraTrade`는 매칭 단계가 쓰는 대표 면적/금액을 area_m2/price로 정규화해 담고,
유형별 부가정보(대지면적·용도 등)는 별도 필드로 보존한다. 아파트류의 Trade와 인터페이스가
유사(area_m2/price/deal_ym/dong/kind)하여 하류 매칭에 그대로 흘려보낼 수 있다.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .molit_client import (
    MolitApiError,  # re-export 편의
    _find,
    _get_with_retry,
    _to_won,
    _total_count,
    check_api_error,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ENDPOINTS_EXTRA",
    "ExtraTrade",
    "MolitApiError",
    "fetch_extra_trades",
    "parse_land_trades_xml",
    "parse_nrg_trades_xml",
    "parse_sh_trades_xml",
]

ENDPOINTS_EXTRA = {
    "sh": "https://apis.data.go.kr/1613000/RTMSDataSvcSHTrade/getRTMSDataSvcSHTrade",
    "nrg": "https://apis.data.go.kr/1613000/RTMSDataSvcNrgTrade/getRTMSDataSvcNrgTrade",
    "land": "https://apis.data.go.kr/1613000/RTMSDataSvcLandTrade/getRTMSDataSvcLandTrade",
}

# 공통 태그(금액·날짜·법정동) — molit_client와 동일 규약.
_AMOUNT = ("거래금액", "dealAmount")
_YEAR = ("년", "dealYear")
_MONTH = ("월", "dealMonth")
_DONG = ("법정동", "umdNm")

# 유형별 태그 매핑. area_key = 매칭에 쓸 '대표 면적' 태그(우선순위 리스트).
_SH_TAGS = {
    "house_type": ("주택유형", "houseType"),
    "plot_area": ("대지면적", "plottageAr"),
    "floor_area": ("연면적", "totalFloorAr"),
}
_NRG_TAGS = {
    "building_use": ("건물주용도", "buildingUse"),
    "building_type": ("유형", "buildingType"),
    "building_area": ("건물면적", "buildingAr"),
    "plot_area": ("대지면적", "plottageAr"),
}
_LAND_TAGS = {
    "jimok": ("지목", "jimok"),
    "land_use": ("용도지역", "landUse"),
    "deal_area": ("거래면적", "dealArea"),
    "share_type": ("지분구분", "shareDealingType", "shareType"),
}


@dataclass
class ExtraTrade:
    """확장 시세유형(단독/다가구·상업·토지) 실거래 1건.

    area_m2/price/deal_ym/dong/kind 는 아파트류 Trade와 같은 의미(하류 매칭 호환).
    나머지는 유형별 부가정보(없으면 기본값). 불변 사용 권장(새 객체 생성).
    """
    kind: str            # sh | nrg | land
    price: int           # 거래금액(원)
    area_m2: float       # 대표 면적(㎡) — sh/nrg=건물/연면적, land=거래면적
    deal_ym: str = ""    # YYYYMM
    dong: str = ""
    # ---- 유형별 부가 ----
    name: str = ""                 # 표시용 라벨(주택유형/건물용도/지목 등)
    plot_area_m2: float = 0.0      # 대지면적(sh/nrg)
    subtype: str = ""              # nrg 유형(상업/업무), land 지분구분, sh 주택유형
    zoning: str = ""               # land 용도지역
    floor: int = 0
    extra: dict = field(default_factory=dict)  # 원시 부가필드 보존

    def price_per_m2(self) -> float:
        return self.price / self.area_m2 if self.area_m2 else 0.0


def _to_float(raw: str) -> float:
    try:
        return float(raw) if raw else 0.0
    except ValueError:
        return 0.0


def _to_int(raw: str) -> int:
    try:
        return int(raw) if raw else 0
    except ValueError:
        return 0


def _ym(item: ET.Element) -> str:
    year = _find(item, _YEAR)
    month = _find(item, _MONTH).zfill(2)
    return f"{year}{month}" if year else ""


def _parse_sh(item: ET.Element) -> ExtraTrade | None:
    price = _to_won(_find(item, _AMOUNT))
    floor_area = _to_float(_find(item, _SH_TAGS["floor_area"]))
    plot_area = _to_float(_find(item, _SH_TAGS["plot_area"]))
    area = floor_area or plot_area  # 대표 면적: 연면적 우선, 없으면 대지면적
    if price <= 0 or area <= 0:
        return None
    house_type = _find(item, _SH_TAGS["house_type"])
    return ExtraTrade(
        kind="sh", price=price, area_m2=area, deal_ym=_ym(item),
        dong=_find(item, _DONG), name=house_type or "단독/다가구",
        plot_area_m2=plot_area, subtype=house_type,
        extra={"floor_area_m2": floor_area},
    )


def _parse_nrg(item: ET.Element) -> ExtraTrade | None:
    price = _to_won(_find(item, _AMOUNT))
    building_area = _to_float(_find(item, _NRG_TAGS["building_area"]))
    plot_area = _to_float(_find(item, _NRG_TAGS["plot_area"]))
    area = building_area or plot_area
    if price <= 0 or area <= 0:
        return None
    use = _find(item, _NRG_TAGS["building_use"])
    btype = _find(item, _NRG_TAGS["building_type"])
    return ExtraTrade(
        kind="nrg", price=price, area_m2=area, deal_ym=_ym(item),
        dong=_find(item, _DONG), name=use or "상업업무용",
        plot_area_m2=plot_area, subtype=btype,
        floor=_to_int(_find(item, ("층", "floor"))),
        extra={"building_use": use},
    )


def _parse_land(item: ET.Element) -> ExtraTrade | None:
    price = _to_won(_find(item, _AMOUNT))
    area = _to_float(_find(item, _LAND_TAGS["deal_area"]))
    if price <= 0 or area <= 0:
        return None
    jimok = _find(item, _LAND_TAGS["jimok"])
    return ExtraTrade(
        kind="land", price=price, area_m2=area, deal_ym=_ym(item),
        dong=_find(item, _DONG), name=jimok or "토지",
        subtype=_find(item, _LAND_TAGS["share_type"]),
        zoning=_find(item, _LAND_TAGS["land_use"]),
        extra={"jimok": jimok},
    )


_PARSERS = {"sh": _parse_sh, "nrg": _parse_nrg, "land": _parse_land}


def _parse_root(root: ET.Element, kind: str) -> list[ExtraTrade]:
    parser = _PARSERS.get(kind)
    if parser is None:
        raise ValueError(f"알 수 없는 확장 시세유형: {kind!r} (sh|nrg|land)")
    out: list[ExtraTrade] = []
    for item in root.iter("item"):
        trade = parser(item)
        if trade is not None:
            out.append(trade)
    return out


def _parse(xml_text: str, kind: str) -> list[ExtraTrade]:
    return _parse_root(ET.fromstring(xml_text), kind)


def parse_sh_trades_xml(xml_text: str) -> list[ExtraTrade]:
    """단독/다가구 매매 실거래 XML → ExtraTrade 리스트 (순수 함수)."""
    return _parse(xml_text, "sh")


def parse_nrg_trades_xml(xml_text: str) -> list[ExtraTrade]:
    """상업업무용 부동산 매매 실거래 XML → ExtraTrade 리스트."""
    return _parse(xml_text, "nrg")


def parse_land_trades_xml(xml_text: str) -> list[ExtraTrade]:
    """토지 매매 실거래 XML → ExtraTrade 리스트."""
    return _parse(xml_text, "land")


def fetch_extra_trades(kind: str, lawd_cd: str, deal_ymd: str, api_key: str,
                       num_rows: int = 1000, timeout: int = 15,
                       max_pages: int = 10, retries: int = 3) -> list[ExtraTrade]:
    """라이브 호출 (운영자 키 필요). kind: sh|nrg|land. 페이지네이션·재시도·오류감지 포함.

    파서와 달리 이 함수는 외부 서버를 호출하므로 테스트/개발에서는 절대 부르지 않는다
    (아침 라이브 검증 전용). molit_client 의 재시도/오류감지 로직을 재사용한다.
    """
    import requests  # noqa: PLC0415

    endpoint = ENDPOINTS_EXTRA.get(kind)
    if endpoint is None:
        raise ValueError(f"알 수 없는 확장 시세유형: {kind!r} (sh|nrg|land)")
    session = requests.Session()
    all_trades: list[ExtraTrade] = []
    page = 1
    while page <= max_pages:
        params = {
            "serviceKey": api_key,
            "LAWD_CD": lawd_cd,
            "DEAL_YMD": deal_ymd,
            "pageNo": str(page),
            "numOfRows": str(num_rows),
        }
        text = _get_with_retry(session, endpoint, params, timeout, retries)
        root = check_api_error(text)
        page_trades = _parse_root(root, kind)
        all_trades.extend(page_trades)
        total = _total_count(root)
        if len(page_trades) < num_rows or (total is not None and len(all_trades) >= total):
            break
        page += 1
    logger.info("국토부 확장 실거래 %d건 수집 (kind=%s lawd=%s ymd=%s)",
                len(all_trades), kind, lawd_cd, deal_ymd)
    return all_trades
