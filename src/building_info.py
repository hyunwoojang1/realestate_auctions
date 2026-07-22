# -*- coding: utf-8 -*-
"""건축물대장 상세 표시 서비스 — 주소 → 법정동코드(VWorld) → 건축HUB 표제부 → 요약 dict.

상세페이지 렌더 시점에 호출되는 웹 서비스층(GOAL_DEPLOY [E] 배선):
  1) 물건 주소에서 지번(bun-ji) 파싱(순수 함수)
  2) VWorld 주소 API로 법정동코드 10자리 확보(시군구5+법정동5)
  3) building_register_client.fetch_building_titles 로 표제부 조회
  4) 여러 동(棟) → 요약 규칙: 준공연도·주용도·층수는 최대 연면적 동(주동) 기준,
     위반건축물은 **한 동이라도 위반이면 위반**(경매 리스크는 필지 단위).

실패는 전부 None(카드 미표시) — 페이지를 절대 막지 않는다. 프로세스 내 캐시로
같은 물건 재조회 0콜(실패도 캐시해 외부 API 반복 타격 방지).
"""
from __future__ import annotations

import logging
import os
import re
import threading

logger = logging.getLogger(__name__)

_VWORLD_ADDR_URL = "https://api.vworld.kr/req/address"
# 렌더 경로 블로킹 — requests 스칼라 타임아웃은 connect·read 각각에 적용돼 최악이 2배가 된다
# (감사 2026-07-20). (connect, read) 튜플로 명시해 체인 최악합 ~8.5s(Vercel 10s 안)로 고정.
_TIMEOUT_VWORLD = (1.5, 2.0)
_TIMEOUT_BLDG = (1.5, 3.5)

_cache: dict[str, dict | None] = {}
_lock = threading.Lock()

# 지번 패턴: 마지막 행정단위(동/리/가/로) 뒤의 "산?123-4" — 뒤따르는 단지명 숫자 오인 방지 위해
# 공백 경계 + 번지 뒤 비숫자. 예) "충북 진천군 진천읍 교성리 123-4 태왕아너스" → (123, 4)
_JIBUN_RE = re.compile(r"[가-힣0-9]+(?:동|리|가|로)\s+(산)?\s*(\d{1,4})(?:-(\d{1,4}))?")


def parse_jibun(address: str) -> tuple[str, str] | None:
    """주소 문자열에서 (bun, ji) 4자리 패딩 추출. 산지·미발견은 None(v1 미지원)."""
    m = None
    for m_ in _JIBUN_RE.finditer(address or ""):
        m = m_   # 마지막 매치 — 도로명·건물명 앞의 실제 지번이 보통 마지막 행정단위 뒤
    if m is None or m.group(1):   # 산지(산 번지)는 표제부 platGbCd 필요 — v1 미지원
        return None
    bun = m.group(2).zfill(4)
    ji = (m.group(3) or "0").zfill(4)
    return bun, ji


def _vworld_coord(address: str, vworld_key: str, type_: str) -> dict | None:
    """VWorld getcoord 1회 호출. status OK면 response dict, 아니면 None(경고 로그만)."""
    import requests  # noqa: PLC0415

    params = {
        "service": "address", "request": "getcoord", "version": "2.0",
        "crs": "epsg:4326", "format": "json", "type": type_,
        "refine": "true", "simple": "false",
        "address": address, "key": vworld_key,
    }
    domain = os.environ.get("VWORLD_DOMAIN", "").strip()
    if domain:
        params["domain"] = domain
    r = requests.get(_VWORLD_ADDR_URL, params=params, timeout=_TIMEOUT_VWORLD)
    r.raise_for_status()
    res = (r.json() or {}).get("response") or {}
    if res.get("status") != "OK":
        # (감사 2026-07-20) VWorld는 키 회수·쿼터 소진도 HTTP 200 + status ERROR로 온다.
        logger.warning("VWorld 주소해석 실패(type=%s status=%s error=%s): %s", type_,
                       res.get("status"), (res.get("error") or {}).get("code"), address[:40])
        return None
    return res


def _reverse_parcel(address: str, vworld_key: str) -> tuple[str, str, str] | None:
    """도로명-only 주소 폴백: road getcoord(좌표) → getAddress(PARCEL 역지오코딩) → 지번.

    (2026-07-22 실측) type=road 는 level4LC(지번 PNU)를 비워 보내지만 좌표는 정확하다.
    그 좌표를 getAddress 로 역변환하면 법정동코드10(level4LC)+지번(level5 '120-10')이 나온다
    — juso.go.kr 별도 키 없이 도로명 주소 ~23%를 구제(예: 다대로429번길 20 → 다대동 120-10).
    """
    import requests  # noqa: PLC0415

    res = _vworld_coord(address, vworld_key, "road")
    if res is None:
        return None
    pt = (res.get("result") or {}).get("point") or {}
    x, y = pt.get("x"), pt.get("y")
    if not x or not y:
        return None
    params = {
        "service": "address", "request": "getAddress", "version": "2.0",
        "crs": "epsg:4326", "format": "json", "type": "PARCEL",
        "point": f"{x},{y}", "key": vworld_key,
    }
    domain = os.environ.get("VWORLD_DOMAIN", "").strip()
    if domain:
        params["domain"] = domain
    r = requests.get(_VWORLD_ADDR_URL, params=params, timeout=_TIMEOUT_VWORLD)
    r.raise_for_status()
    rev = (r.json() or {}).get("response") or {}
    if rev.get("status") != "OK":
        logger.warning("VWorld 역지오코딩 실패(status=%s): %s",
                       rev.get("status"), address[:40])
        return None
    st = ((rev.get("result") or [{}])[0] or {}).get("structure") or {}
    lc = st.get("level4LC") or ""
    jibun = (st.get("level5") or "").strip()
    if not lc.isdigit() or len(lc) < 10 or not jibun:
        return None
    if jibun.startswith("산"):   # 산지 — 표제부 platGbCd 미지원(v1 제외)
        return None
    bun, _, ji = jibun.partition("-")
    if not bun.strip().isdigit():
        return None
    return lc[:10], bun.strip().zfill(4), (ji.strip() or "0").zfill(4)


def _resolve_parcel(address: str, vworld_key: str) -> tuple[str, str, str] | None:
    """VWorld 주소 API(getcoord, type=parcel) → (법정동코드10, bun4, ji4).

    level4LC는 실측 **19자리 PNU**(법정동10+대지구분1+본번4+부번4). 산지(구분 '2')는 표제부
    platGbCd 미지원이라 None. 10자리만 오면 parse_jibun 폴백을 쓰도록 bun/ji 빈 값.
    parcel 해석 실패(NOT_FOUND) 시 도로명 역지오코딩 폴백(_reverse_parcel)을 1회 시도한다.
    둘 다 실패면 RuntimeError(결과 캐시 방지).
    """
    res = _vworld_coord(address, vworld_key, "parcel")
    if res is None:
        rev = _reverse_parcel(address, vworld_key)
        if rev is not None:
            return rev
        raise RuntimeError("vworld status not OK")   # 실패로 승격 — 결과 캐시 방지
    lc = ((res.get("refined") or {}).get("structure") or {}).get("level4LC") or ""
    if not lc.isdigit() or len(lc) < 10:
        return None
    if len(lc) >= 19:
        if lc[10] == "2":   # 산지 — v1 미지원
            return None
        return lc[:10], lc[11:15], lc[15:19]
    return lc[:10], "", ""


def _summarize(records: list) -> dict | None:
    """표제부 여러 동 → 표시용 요약 dict. 주동(최대 연면적) 기준 + 위반은 any."""
    if not records:
        return None
    main = max(records, key=lambda r: r.total_area_m2)
    viol = [r for r in records if r.is_violation]
    from datetime import date  # noqa: PLC0415
    age = main.age_years(date.today().year)
    ymd = (main.use_approval_day or "").strip()
    approved = f"{ymd[:4]}.{ymd[4:6]}" if len(ymd) >= 6 else (ymd[:4] if len(ymd) >= 4 else "")
    return {
        "approved": approved,                 # 준공(사용승인) 연월 "YYYY.MM"
        "age_years": age,                     # 연식(년) | None
        "main_purpose": main.main_purpose,    # 주용도
        "ground_floors": main.ground_floors,
        "underground_floors": main.underground_floors,
        "total_area_m2": main.total_area_m2,
        "is_violation": bool(viol),           # 필지 내 위반동 존재 여부
        "violation_content": (viol[0].violation_content if viol else ""),
        "dong_count": len(records),
    }


def get_building_summary(address: str) -> dict | None:
    """주소 → 건축물대장 요약. 키 미설정·파싱실패·API실패 전부 None(카드 미표시).

    프로세스 캐시(실패 포함) — Vercel 서버리스 인스턴스 수명 동안 물건당 최대 1회 체인.
    """
    if not address:
        return None
    with _lock:
        if address in _cache:
            return _cache[address]
    out: dict | None = None
    failed = False
    molit_key = os.environ.get("MOLIT_API_KEY", "").strip()
    vworld_key = os.environ.get("VWORLD_API_KEY", "").strip()
    try:
        if molit_key and vworld_key:
            parcel = _resolve_parcel(address, vworld_key)
            if parcel:
                lc, bun, ji = parcel
                if not bun:   # PNU 미제공 응답 — 주소 문자열 지번 폴백
                    jibun = parse_jibun(address)
                    bun, ji = jibun if jibun else ("", "")
                if bun:
                    from .building_register_client import fetch_building_titles  # noqa: PLC0415
                    records = fetch_building_titles(lc[:5], lc[5:], molit_key,
                                                    bun=bun, ji=ji,
                                                    timeout=_TIMEOUT_BLDG, retries=1)
                    out = _summarize(records)
    except Exception as e:  # noqa: BLE001 — 표시용 부가정보: 어떤 실패도 페이지를 막지 않음
        failed = True
        msg = str(e)
        for sec in (molit_key, vworld_key):   # 예외 문자열의 요청 URL에 키가 실릴 수 있다 — 레닥션
            if sec:
                msg = msg.replace(sec, "***")
        logger.warning("건축물대장 요약 실패(%s): %s", address[:40], msg)
    # (감사 2026-07-20) 일시 실패(타임아웃·쿼터)는 캐시하지 않는다 — 다음 뷰에서 재시도.
    # 정상 완료(키부재·지번없음·표제부 0건 포함)만 캐시해 외부 API 반복 타격을 막는다.
    if not failed:
        with _lock:
            _cache[address] = out
    return out
