"""법정동코드(LAWD_CD)·지역명 유틸 (X2).

국토부 실거래 API·크롤러의 지역 키(시군구→코드)이자, 사이트의 지역 검색(시도/시군구) 근거.
data/lawd_codes.json 의 시군구명↔LAWD_CD 매핑을 로드한다.
"""
from __future__ import annotations

import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

SIDO = ("서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
        "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주")


def _load(path: str | Path | None = None) -> dict:
    p = Path(path) if path else (DATA / "lawd_codes.json")
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


LAWD = _load()
_CODE_TO_NAME = {v: k for k, v in LAWD.items()}


def name_to_code(name: str) -> str | None:
    """시군구명 → LAWD_CD 5자리. 정확/부분 일치('강남구' → 11680)."""
    if name in LAWD:
        return LAWD[name]
    n = name.replace(" ", "")
    if not n:
        return None
    # (감사 2026-07-15) 부분일치는 유일할 때만 채택. '중구'처럼 여러 시도에 있는 이름은 먼저
    # 걸린 임의의 구를 반환하면 엉뚱한 지역을 수집한다 — 모호하면 None(호출부가 처리).
    matches = [v for k, v in LAWD.items() if n in k.replace(" ", "")]
    return matches[0] if len(matches) == 1 else None


def code_to_name(code: str) -> str | None:
    return _CODE_TO_NAME.get(code)


def sido_of(address: str) -> str | None:
    """주소 prefix에서 시도 추출('서울 강남구 ...' → '서울')."""
    a = (address or "").strip()
    for s in SIDO:
        if a.startswith(s):
            return s
    return None


def matches_region(address: str, query: str | None) -> bool:
    """지역 쿼리(시도명 또는 시군구명)가 주소에 해당하는지. 둘 다 지원."""
    if not query:
        return True
    q = query.strip()
    return address.startswith(q) or q in address
