"""규제지역 판정 — 주소 문자열 → 조정대상지역·토지거래허가구역 (GOAL_DEAL_SIM Phase 2).

단일 출처는 data/regulated_zones.json (원문 검증본, 기준일 명시). 서빙 테이블에
lawd_cd 가 없어(2026-07-24 실측) 주소 토큰 매칭으로 판정한다 — 시군구 단위라 충분하고,
유일한 부분규제 시(화성: 동탄구만)는 '동탄' 토큰이 없으면 **'check'(확인 필요)** 로
돌려 오판 대신 모름을 표출한다(모름≠아님 — 감사 원칙).

토허구역은 지정기간이 있다(예: 서울 전역 ~2026-12-31). 판정 시점이 만료를 지나면
자동으로 미적용 + '재지정 확인 필요' 노트를 남긴다 — 데이터 부패가 조용히 오답이
되지 않게 하는 가드.
"""
from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

_ZONES_PATH = Path(__file__).resolve().parent.parent / "data" / "regulated_zones.json"


@lru_cache(maxsize=1)
def _zones() -> dict:
    with open(_ZONES_PATH, encoding="utf-8") as f:
        return json.load(f)


def basis_date() -> str:
    return _zones()["_meta"]["basis_date"]


def _match_adjusted(address: str) -> tuple[bool | str, str | None]:
    """조정대상지역(=투기과열지구) 판정 → (True/False/'check', 지역명)."""
    z = _zones()["adjusted_and_overheated"]
    if address.startswith(z["seoul"]["match"]["sido_prefix"]):
        return True, z["seoul"]["scope"]
    needs_check: str | None = None
    for area in z["gyeonggi"]:
        m = area["match"]
        if any(tok in address for tok in m.get("tokens", [])):
            return True, area["name"]
        for tok in m.get("needs_check_tokens", []):
            if tok in address:
                needs_check = area["name"]
    if needs_check:
        return "check", needs_check
    return False, None


def classify(address: str, is_apartment: bool = True, as_of: date | None = None) -> dict:
    """주소 → 규제 판정.

    반환: adjusted(True/False/'check') · land_permit(bool) · zone_name · basis_date · notes[].
    토허구역은 아파트 한정 지정이라 is_apartment=False 면 land_permit 은 항상 False.
    """
    today = as_of or date.today()
    adjusted, zone_name = _match_adjusted(address or "")
    notes: list[str] = []
    if adjusted == "check":
        notes.append(f"{zone_name}: 부분 규제 시 — 규제 여부 확인 필요(비규제 가정 계산)")

    land_permit = False
    if adjusted is True and is_apartment:
        # 토허 광역 지정은 조정지역 목록과 동일 범위 + 만료일 존재. 만료 가드 필수.
        expiry = date(2027, 12, 31) if zone_name in ("구리시", "용인시 기흥구", "화성시 동탄구") \
            else date(2026, 12, 31)
        if today <= expiry:
            land_permit = True
            notes.append("토지거래허가구역(아파트) — 경매 낙찰은 허가 불요·실거주 의무 미적용, 매도 시 매수인은 허가 대상")
        else:
            notes.append(f"토허구역 지정기간({expiry.isoformat()}) 경과 — 재지정 여부 확인 필요")
    return {
        "adjusted": adjusted,
        "land_permit": land_permit,
        "zone_name": zone_name,
        "basis_date": basis_date(),
        "notes": notes,
    }
