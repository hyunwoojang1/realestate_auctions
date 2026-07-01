"""건축물대장 클라이언트 — 노후도·위반건축물 뼈대 (GOAL_DEPLOY [E]).

공공데이터포털 — 건축HUB 건축물대장 표제부:
  BldRgstService_v2/getBrTitleInfo
  params: serviceKey, sigunguCd(시군구 5자리), bjdongCd(법정동 5자리), bun/ji(번지), _type
  응답: XML.

경매 물건의 두 가지 리스크를 정량화하기 위한 원재료를 뽑는다:
  - 노후도: 사용승인일(useAprDay, YYYYMMDD) 기준 경과 연수. 오래될수록 리모델링/재건축 변수.
  - 위반건축물: 위반 여부(violYn/위반건축물)와 내용(violCn). 대출·환금성·과태료 리스크.

라이브 호출은 운영자 키(MOLIT_API_KEY) 필요. 파서·리스크 계산은 fixture로만 테스트한다.
API 오류감지는 molit_client.check_api_error 재사용(DRY). 표제부 정상코드는 '00'.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .molit_client import (
    MolitApiError,  # re-export 편의
    _find,
    _get_with_retry,
    check_api_error,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BLD_TITLE_ENDPOINT",
    "BuildingRecord",
    "MolitApiError",
    "building_age_years",
    "fetch_building_titles",
    "parse_building_titles_xml",
]

BLD_TITLE_ENDPOINT = "http://apis.data.go.kr/1613000/BldRgstService_v2/getBrTitleInfo"

# 위반건축물 여부 태그(코드/텍스트 혼용 대응). 값 '1'/'Y'/'위반' 계열이면 위반.
_VIOL_YN = ("violYn", "위반건축물", "violationYn")
_VIOL_TRUE = {"1", "y", "yes", "true", "위반", "위반건축물"}

_TAGS = {
    "name": ("bldNm", "건물명"),
    "addr": ("platPlc", "대지위치", "newPlatPlc"),
    "use_apr_day": ("useAprDay", "사용승인일"),
    "main_purpose": ("mainPurpsCdNm", "주용도"),
    "tot_area": ("totArea", "연면적"),
    "grnd_flr": ("grndFlrCnt", "지상층수"),
    "ugrnd_flr": ("ugrndFlrCnt", "지하층수"),
    "viol_content": ("violCn", "위반내용"),
}


@dataclass
class BuildingRecord:
    """건축물대장 표제부 1건에서 뽑은 노후도·위반 리스크 원재료 (불변 사용 권장)."""
    name: str = ""
    address: str = ""
    use_approval_day: str = ""   # YYYYMMDD
    main_purpose: str = ""
    total_area_m2: float = 0.0
    ground_floors: int = 0
    underground_floors: int = 0
    is_violation: bool = False
    violation_content: str = ""

    def age_years(self, ref_year: int) -> int | None:
        """사용승인 연도 대비 경과 연수(ref_year 기준). 승인일 미상이면 None."""
        return building_age_years(self.use_approval_day, ref_year)


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


def _is_violation(raw: str) -> bool:
    return raw.strip().lower() in _VIOL_TRUE


def building_age_years(use_apr_day: str, ref_year: int) -> int | None:
    """사용승인일(YYYYMMDD 또는 YYYY) 기준 경과 연수. 파싱불가면 None.

    경매 물건 노후도 판단용. 음수(미래 승인일)는 0으로 클램프(데이터 오류 방어).
    """
    digits = (use_apr_day or "").strip()
    if len(digits) < 4 or not digits[:4].isdigit():
        return None
    year = int(digits[:4])
    if year < 1800 or year > 3000:  # 명백한 이상치 방어
        return None
    return max(0, ref_year - year)


def _parse_item(item: ET.Element) -> BuildingRecord:
    return BuildingRecord(
        name=_find(item, _TAGS["name"]),
        address=_find(item, _TAGS["addr"]),
        use_approval_day=_find(item, _TAGS["use_apr_day"]),
        main_purpose=_find(item, _TAGS["main_purpose"]),
        total_area_m2=_to_float(_find(item, _TAGS["tot_area"])),
        ground_floors=_to_int(_find(item, _TAGS["grnd_flr"])),
        underground_floors=_to_int(_find(item, _TAGS["ugrnd_flr"])),
        is_violation=_is_violation(_find(item, _VIOL_YN)),
        violation_content=_find(item, _TAGS["viol_content"]),
    )


def _parse_root(root: ET.Element) -> list[BuildingRecord]:
    return [_parse_item(item) for item in root.iter("item")]


def parse_building_titles_xml(xml_text: str) -> list[BuildingRecord]:
    """건축물대장 표제부 XML → BuildingRecord 리스트 (순수 함수)."""
    return _parse_root(ET.fromstring(xml_text))


def fetch_building_titles(sigungu_cd: str, bjdong_cd: str, api_key: str,
                          bun: str = "", ji: str = "",
                          num_rows: int = 100, timeout: int = 15,
                          retries: int = 3) -> list[BuildingRecord]:
    """라이브 호출 (운영자 키 필요). 표제부 조회 → BuildingRecord 리스트.

    테스트/개발에서는 절대 부르지 않는다(아침 라이브 검증 전용).
    check_api_error 로 인증/오류 응답을 MolitApiError 로 변환한다.
    """
    import requests  # noqa: PLC0415

    session = requests.Session()
    params = {
        "serviceKey": api_key,
        "sigunguCd": sigungu_cd,
        "bjdongCd": bjdong_cd,
        "bun": bun,
        "ji": ji,
        "numOfRows": str(num_rows),
        "_type": "xml",
    }
    text = _get_with_retry(session, BLD_TITLE_ENDPOINT, params, timeout, retries)
    root = check_api_error(text)
    records = _parse_root(root)
    logger.info("건축물대장 표제부 %d건 수집 (sigungu=%s bjdong=%s bun=%s ji=%s)",
                len(records), sigungu_cd, bjdong_cd, bun, ji)
    return records
