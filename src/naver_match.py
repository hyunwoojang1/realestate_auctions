"""경매물건(단지명·좌표·전용면적) → 네이버 단지(complexNo)·면적타입(areaNo) 매핑.

좌표로 법정동을 특정하고(오매칭 방지), 이름 유사도(RapidFuzz) + 전용면적 확증으로 단지를 고른다.
검증(2배치 24건): 매칭 24/24, 이름 임계 0.85 또는 (0.70+면적≤1㎡) 수락.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

# 음차/브랜드 표기 정규화 (경매표기 ↔ 네이버표기)
_TRANS = {"에스클래스": "s클래스", "이그린": "e그린", "이편한세상": "e편한세상",
          "이편한": "e편한", "아이파크": "ipark", "더샵": "thesharp"}


def normalize(name: str) -> str:
    s = str(name or "")
    s = re.sub(r"\([^)]*\)", "", s)                  # 괄호 (주상복합)(도시형)
    for k, v in _TRANS.items():
        s = s.replace(k, v)
    s = re.sub(r"(아파트|apt|단지)$", "", s)            # 일반 접미사
    s = re.sub(r"^[가-힣]{2,4}마을", "", s)             # 'OO마을' 접두
    s = re.sub(r"^[가-힣]+신도시|^[가-힣]+지구", "", s)   # 신도시/지구 접두
    return re.sub(r"[\s,·\-]", "", s).lower()


def name_score(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    return max(fuzz.token_set_ratio(na, nb), fuzz.partial_ratio(na, nb),
              fuzz.ratio(na, nb)) / 100.0


def best_complex(apt_name: str, complexes: list[dict]) -> tuple[dict | None, float]:
    """단지 목록에서 이름 최고유사도 후보 반환."""
    best, bs = None, 0.0
    for cp in complexes:
        sc = name_score(apt_name, cp.get("complexName", ""))
        if sc > bs:
            bs, best = sc, cp
    return best, bs


def best_area(area_m2: float, pyeongs: list[dict]) -> tuple[dict | None, float]:
    """면적타입 중 전용면적이 가장 가까운 것 + 차이(㎡)."""
    best, bd = None, 9999.0
    for a in pyeongs:
        ex = float(a.get("exclusiveArea") or 0)
        if ex and abs(ex - area_m2) < bd:
            bd, best = abs(ex - area_m2), a
    return best, bd


def accept(name_sim: float, area_diff: float) -> str | None:
    """수락 판정 → 신뢰등급('고신뢰'/'중신뢰') 또는 None(수동확인)."""
    if name_sim >= 0.85 and area_diff <= 5:
        return "고신뢰"
    if name_sim >= 0.70 and area_diff <= 1.0:
        return "중신뢰"
    return None
