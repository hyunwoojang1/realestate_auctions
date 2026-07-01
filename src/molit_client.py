"""국토부 실거래가 API 클라이언트 (아파트 / 연립다세대 / 오피스텔).

공공데이터포털 — 국토교통부 실거래가:
  아파트   : RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev
  연립다세대: RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade
  오피스텔 : RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade
params: serviceKey, LAWD_CD(법정동코드 5자리), DEAL_YMD(YYYYMM), pageNo, numOfRows
응답: XML. 거래금액은 '만원' 단위 + 콤마 → 원으로 환산.

프로덕션 강화(P1): API 오류 명확한 예외(MolitApiError), 페이지네이션, 재시도/백오프, 로깅.
라이브 호출은 운영자 API 키(MOLIT_API_KEY)가 있어야 한다(F10). 파서·오류감지는 fixture로 테스트.
"""
from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET

from .models import Trade

logger = logging.getLogger(__name__)

ENDPOINTS = {
    "apt": "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev",
    "rh": "https://apis.data.go.kr/1613000/RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade",
    "officetel": "https://apis.data.go.kr/1613000/RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade",
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
_SUCCESS_CODES = {"00", "000"}


class MolitApiError(RuntimeError):
    """국토부 API가 오류를 반환했을 때(잘못된 키·트래픽 초과 등)."""


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


def check_api_error(xml_text: str) -> ET.Element:
    """응답을 파싱해 root를 돌려준다. API 오류면 MolitApiError를 던진다.

    공공데이터포털 오류 형태:
      1) OpenAPI fault: <returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>
      2) 정상이지만 resultCode != 00/000
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise MolitApiError(f"응답 XML 파싱 실패: {e}") from e

    auth_msg = root.findtext(".//returnAuthMsg")
    if auth_msg:
        reason = root.findtext(".//returnReasonCode") or "?"
        raise MolitApiError(f"국토부 API 인증/요청 오류: {auth_msg} (code={reason}). "
                            f"인증키가 'Decoding' 키인지, 활용신청이 승인됐는지 확인하세요.")

    code = root.findtext(".//resultCode")
    if code is not None and code not in _SUCCESS_CODES:
        msg = root.findtext(".//resultMsg") or ""
        raise MolitApiError(f"국토부 API 오류 resultCode={code} {msg}")
    return root


def _total_count(root: ET.Element) -> int | None:
    tc = root.findtext(".//totalCount")
    return int(tc) if tc and tc.strip().isdigit() else None


def _parse_root(root: ET.Element, kind: str) -> list[Trade]:
    name_keys = _NAME_TAGS.get(kind, _NAME_TAGS["apt"])
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
            kind=kind,
        ))
    return trades


def _parse(xml_text: str, kind: str) -> list[Trade]:
    return _parse_root(ET.fromstring(xml_text), kind)


def parse_apt_trades_xml(xml_text: str) -> list[Trade]:
    """아파트 매매 실거래 XML → Trade 리스트 (순수 함수)."""
    return _parse(xml_text, "apt")


def parse_rh_trades_xml(xml_text: str) -> list[Trade]:
    """연립다세대(빌라) 매매 실거래 XML → Trade 리스트."""
    return _parse(xml_text, "rh")


def parse_offi_trades_xml(xml_text: str) -> list[Trade]:
    """오피스텔 매매 실거래 XML → Trade 리스트."""
    return _parse(xml_text, "officetel")


def _get_with_retry(session, url: str, params: dict, timeout: int, retries: int) -> str:
    """일시적 네트워크 오류는 지수 백오프로 재시도. 마지막 실패는 그대로 올린다."""
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except Exception as e:  # noqa: BLE001 — requests 예외 전반(연결/타임아웃/HTTP)
            last_exc = e
            if attempt < retries:
                backoff = 2 ** (attempt - 1)
                logger.warning("국토부 호출 실패(%d/%d), %ds 후 재시도: %s", attempt, retries, backoff, e)
                time.sleep(backoff)
    raise MolitApiError(f"국토부 호출 {retries}회 모두 실패: {last_exc}") from last_exc


def fetch_trades(kind: str, lawd_cd: str, deal_ymd: str, api_key: str,
                 num_rows: int = 1000, timeout: int = 15,
                 max_pages: int = 10, retries: int = 3) -> list[Trade]:
    """라이브 호출 (F10 — 운영자 키 필요). kind: apt|rh|officetel. 페이지네이션·재시도 포함."""
    import requests  # noqa: PLC0415

    endpoint = ENDPOINTS.get(kind, ENDPOINTS["apt"])
    session = requests.Session()
    all_trades: list[Trade] = []
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
        root = check_api_error(text)          # 오류면 MolitApiError
        page_trades = _parse_root(root, kind)
        all_trades.extend(page_trades)
        total = _total_count(root)
        # 마지막 페이지 판정: 이번 페이지가 꽉 안 찼거나, 누적이 totalCount 도달
        if len(page_trades) < num_rows or (total is not None and len(all_trades) >= total):
            break
        page += 1
    logger.info("국토부 실거래 %d건 수집 (kind=%s lawd=%s ymd=%s, %d page)",
                len(all_trades), kind, lawd_cd, deal_ymd, page)
    return all_trades


def fetch_apt_trades(lawd_cd: str, deal_ymd: str, api_key: str,
                     num_rows: int = 1000, timeout: int = 15) -> list[Trade]:
    """하위호환 — 아파트 라이브 호출."""
    return fetch_trades("apt", lawd_cd, deal_ymd, api_key, num_rows, timeout)


def recent_ymds(latest_ymd: str, k: int) -> list[str]:
    """latest_ymd('YYYYMM')부터 직전 k개월의 YYYYMM 목록(최신순). 표본 확대용."""
    try:
        y, m = int(latest_ymd[:4]), int(latest_ymd[4:6])
    except (ValueError, IndexError):
        return [latest_ymd]
    out: list[str] = []
    for _ in range(max(1, k)):
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


def fetch_trades_months(kind: str, lawd_cd: str, ymds: list[str], api_key: str,
                        num_rows: int = 1000, timeout: int = 15) -> list[Trade]:
    """여러 연월(ymds)의 실거래를 모아 수집(표본 확대 → 시세 추정 안정화)."""
    trades: list[Trade] = []
    for ymd in ymds:
        trades.extend(fetch_trades(kind, lawd_cd, ymd, api_key, num_rows, timeout))
    return trades
