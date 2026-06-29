"""국토부 아파트 매매 실거래가 API 클라이언트.

공공데이터포털: 국토교통부_아파트 매매 실거래가 자료
endpoint(dev): http://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev
params: serviceKey, LAWD_CD(법정동코드 5자리), DEAL_YMD(YYYYMM), pageNo, numOfRows
응답: XML. 거래금액은 '만원' 단위 + 콤마. → 원으로 환산.

라이브 호출은 운영자 API 키(MOLIT_API_KEY)가 있어야 한다(F10). 파서는 fixture로 단위테스트 가능.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Optional

from .models import Trade

ENDPOINT = "http://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"

# 한글/영문 태그 모두 방어적으로 처리
_TAG = {
    "apt": ("아파트", "aptNm"),
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
    return int(digits) * 10_000


def parse_apt_trades_xml(xml_text: str) -> list[Trade]:
    """API 응답 XML → Trade 리스트. (순수 함수 — 테스트 대상)"""
    root = ET.fromstring(xml_text)
    trades: list[Trade] = []
    for item in root.iter("item"):
        amount = _to_won(_find(item, _TAG["amount"]))
        area_raw = _find(item, _TAG["area"])
        try:
            area = float(area_raw) if area_raw else 0.0
        except ValueError:
            area = 0.0
        year = _find(item, _TAG["year"])
        month = _find(item, _TAG["month"]).zfill(2)
        floor_raw = _find(item, _TAG["floor"])
        try:
            floor = int(floor_raw) if floor_raw else 0
        except ValueError:
            floor = 0
        if amount <= 0 or area <= 0:
            continue
        trades.append(Trade(
            apt_name=_find(item, _TAG["apt"]),
            area_m2=area,
            price=amount,
            deal_ym=f"{year}{month}" if year else "",
            dong=_find(item, _TAG["dong"]),
            floor=floor,
        ))
    return trades


def fetch_apt_trades(lawd_cd: str, deal_ymd: str, api_key: str,
                     num_rows: int = 1000, timeout: int = 15) -> list[Trade]:
    """라이브 호출 (F10 — 운영자 키 필요). requests 지연 import로 테스트 의존 제거."""
    import requests  # noqa: PLC0415

    params = {
        "serviceKey": api_key,
        "LAWD_CD": lawd_cd,
        "DEAL_YMD": deal_ymd,
        "pageNo": "1",
        "numOfRows": str(num_rows),
    }
    resp = requests.get(ENDPOINT, params=params, timeout=timeout)
    resp.raise_for_status()
    return parse_apt_trades_xml(resp.text)
