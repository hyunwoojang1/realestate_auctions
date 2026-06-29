"""국토부 실거래가 API 클라이언트 (아파트 / 연립다세대 / 오피스텔).

공공데이터포털 — 국토교통부 실거래가:
  아파트   : RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev
  연립다세대: RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade
  오피스텔 : RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade
params: serviceKey, LAWD_CD(법정동코드 5자리), DEAL_YMD(YYYYMM), pageNo, numOfRows
응답: XML. 거래금액은 '만원' 단위 + 콤마 → 원으로 환산.

라이브 호출은 운영자 API 키(MOLIT_API_KEY)가 있어야 한다(F10). 파서는 fixture로 단위테스트 가능.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from .models import Trade

ENDPOINTS = {
    "apt": "http://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev",
    "rh": "http://apis.data.go.kr/1613000/RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade",
    "officetel": "http://apis.data.go.kr/1613000/RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade",
}
ENDPOINT = ENDPOINTS["apt"]  # 하위호환

# 건물명 태그는 물건유형별로 다르다. 나머지(금액·면적·날짜·법정동·층)는 공통.
_NAME_TAGS = {
    "apt": ("아파트", "aptNm"),
    "rh": ("연립다세대", "mhouseNm"),
    "officetel": ("단지", "offiNm"),
}
_COMMON_TAGS = {
    "area": ("전용면적", "excluUseAr"),
    "amount": ("거래금액", "dealAmount"),
    "year": ("년", "dealYear"),
    "month": ("월", "dealMonth"),
    "dong": ("법정동", "umdNm"),
    "floor": ("층", "floor"),
}


def _find(item: ET.Element, keys: tuple[str, ...]) -> str:
    for k in keys:
        el = item.find(k)
        if el is not None and el.text is not None:
            return el.text.strip()
    return ""


def _to_won(amount_manwon: str) -> int:
    """'34,500' (만원) → 345000000 (원)."""
    digits = amount_manwon.replace(",", "").replace(" ", "")
    if not digits:
        return 0
    try:
        return int(digits) * 10_000
    except ValueError:
        return 0


def _parse(xml_text: str, kind: str) -> list[Trade]:
    name_keys = _NAME_TAGS.get(kind, _NAME_TAGS["apt"])
    root = ET.fromstring(xml_text)
    trades: list[Trade] = []
    for item in root.iter("item"):
        amount = _to_won(_find(item, _COMMON_TAGS["amount"]))
        area_raw = _find(item, _COMMON_TAGS["area"])
        try:
            area = float(area_raw) if area_raw else 0.0
        except ValueError:
            area = 0.0
        if amount <= 0 or area <= 0:
            continue
        year = _find(item, _COMMON_TAGS["year"])
        month = _find(item, _COMMON_TAGS["month"]).zfill(2)
        floor_raw = _find(item, _COMMON_TAGS["floor"])
        try:
            floor = int(floor_raw) if floor_raw else 0
        except ValueError:
            floor = 0
        trades.append(Trade(
            apt_name=_find(item, name_keys),
            area_m2=area,
            price=amount,
            deal_ym=f"{year}{month}" if year else "",
            dong=_find(item, _COMMON_TAGS["dong"]),
            floor=floor,
        ))
    return trades


def parse_apt_trades_xml(xml_text: str) -> list[Trade]:
    """아파트 매매 실거래 XML → Trade 리스트 (순수 함수)."""
    return _parse(xml_text, "apt")


def parse_rh_trades_xml(xml_text: str) -> list[Trade]:
    """연립다세대(빌라) 매매 실거래 XML → Trade 리스트."""
    return _parse(xml_text, "rh")


def parse_offi_trades_xml(xml_text: str) -> list[Trade]:
    """오피스텔 매매 실거래 XML → Trade 리스트."""
    return _parse(xml_text, "officetel")


def fetch_trades(kind: str, lawd_cd: str, deal_ymd: str, api_key: str,
                 num_rows: int = 1000, timeout: int = 15) -> list[Trade]:
    """라이브 호출 (F10 — 운영자 키 필요). kind: apt|rh|officetel."""
    import requests  # noqa: PLC0415

    endpoint = ENDPOINTS.get(kind, ENDPOINTS["apt"])
    params = {
        "serviceKey": api_key,
        "LAWD_CD": lawd_cd,
        "DEAL_YMD": deal_ymd,
        "pageNo": "1",
        "numOfRows": str(num_rows),
    }
    resp = requests.get(endpoint, params=params, timeout=timeout)
    resp.raise_for_status()
    return _parse(resp.text, kind)


def fetch_apt_trades(lawd_cd: str, deal_ymd: str, api_key: str,
                     num_rows: int = 1000, timeout: int = 15) -> list[Trade]:
    """하위호환 — 아파트 라이브 호출."""
    return fetch_trades("apt", lawd_cd, deal_ymd, api_key, num_rows, timeout)
