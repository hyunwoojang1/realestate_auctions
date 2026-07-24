"""규제지역 판정 — 주소 문자열 → 조정대상지역·토지거래허가구역 (GOAL_DEAL_SIM Phase 2).

단일 출처는 data/regulated_zones.json (원문 검증본, 기준일 명시). 서빙 테이블에
lawd_cd 가 없어(2026-07-24 실측) 주소 토큰 매칭으로 판정한다 — 시군구 단위라 충분하고,
유일한 부분규제 시(화성: 동탄구만)는 '동탄' 토큰이 없으면 **'check'(확인 필요)** 로
돌려 오판 대신 모름을 표출한다(모름≠아님 — 감사 원칙).

토허 만료일도 **JSON 의 `land_permit_expiry` 필드에서 읽는다**(감사 확정: 종전 하드코딩은
"이 파일만 갱신" 규칙과 모순 — 재지정 고시를 JSON 에 반영해도 판정이 안 바뀌는 드리프트).
판정 시점이 만료를 지나면 자동 미적용 + '재지정 확인 필요' 노트 — 데이터 부패가 조용히
오답이 되지 않게 하는 가드. 시각은 KST 기준(서버리스는 UTC — date.today() 금지).
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_ZONES_PATH = Path(__file__).resolve().parent.parent / "data" / "regulated_zones.json"
_KST = timezone(timedelta(hours=9))


@lru_cache(maxsize=1)
def _zones() -> dict | None:
    """데이터 로드 — 실패해도 앱을 죽이지 않는다(판정만 '확인 필요'로 강등)."""
    try:
        with open(_ZONES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.error("regulated_zones.json 로드 실패 — 규제 판정 비활성(배포 번들 확인)")
        return None


def _today_kst() -> date:
    return datetime.now(_KST).date()


def basis_date() -> str:
    z = _zones()
    return z["_meta"]["basis_date"] if z else "?"


def _match_adjusted(address: str) -> tuple[bool | str, str | None, str | None]:
    """조정대상지역(=투기과열지구) 판정 → (True/False/'check', 지역명, 토허만료일)."""
    z = _zones()
    if z is None:
        return "check", None, None
    ao = z["adjusted_and_overheated"]
    if address.startswith(ao["seoul"]["match"]["sido_prefix"]):
        return True, ao["seoul"]["scope"], ao["seoul"].get("land_permit_expiry")
    needs_check: str | None = None
    for area in ao["gyeonggi"]:
        m = area["match"]
        if any(tok in address for tok in m.get("tokens", [])):
            return True, area["name"], area.get("land_permit_expiry")
        for tok in m.get("needs_check_tokens", []):
            if tok in address:
                needs_check = area["name"]
    if needs_check:
        return "check", needs_check, None
    return False, None, None


def classify(address: str, is_apartment: bool = True, as_of: date | None = None) -> dict:
    """주소 → 규제 판정.

    반환: adjusted(True/False/'check') · land_permit(bool) · zone_name · basis_date · notes[].
    토허구역은 아파트 한정 지정이라 is_apartment=False 면 land_permit 은 항상 False.
    """
    today = as_of or _today_kst()
    adjusted, zone_name, expiry_s = _match_adjusted(address or "")
    notes: list[str] = []
    if adjusted == "check":
        notes.append(f"{zone_name or '규제 데이터'}: 판정 불가 — 규제 여부 확인 필요(비규제 가정 계산)")

    land_permit = False
    if adjusted is True and is_apartment and expiry_s:
        try:
            expiry = date.fromisoformat(expiry_s)
        except ValueError:
            expiry = None
            notes.append("토허 만료일 형식 오류 — regulated_zones.json 확인 필요")
        if expiry and today <= expiry:
            land_permit = True
            notes.append("토지거래허가구역(아파트) — 경매 낙찰은 허가 불요·실거주 의무 미적용, 매도 시 매수인은 허가 대상")
        elif expiry:
            notes.append(f"토허구역 지정기간({expiry.isoformat()}) 경과 — 재지정 여부 확인 필요")
    return {
        "adjusted": adjusted,
        "land_permit": land_permit,
        "zone_name": zone_name,
        "basis_date": basis_date(),
        "notes": notes,
    }
