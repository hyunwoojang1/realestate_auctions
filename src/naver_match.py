"""경매물건(단지명·좌표·전용면적) → 네이버 단지(complexNo)·면적타입(areaNo) 매핑.

좌표로 법정동을 특정하고(오매칭 방지), 이름 유사도(RapidFuzz) + 전용면적 확증으로 단지를 고른다.
검증(2배치 24건): 매칭 24/24, 이름 임계 0.85 또는 (0.70+면적≤1㎡) 수락.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

# 음차/브랜드 표기 정규화 (경매표기 ↔ 네이버표기)
# ⚠ 한글 알파벳 이름을 **일반 규칙**으로 변환하면 안 된다 — 단음절 letter name 이 한국어 음절과
#   겹쳐 브랜드명을 파괴한다(실측: '푸르지오'→'푸르go'(지+오=G+O), '디오션시티'→'do션시티').
#   따라서 다음절 조합만 **명시적으로** 등재한다. 신규 항목은 실패 물건명 실측 기반(2026-07-15).
_TRANS = {"에스클래스": "s클래스", "이그린": "e그린", "이편한세상": "e편한세상",
          "이편한": "e편한", "아이파크": "ipark", "더샵": "thesharp",
          "에쓰제이": "sj", "엘지": "lg", "에스케이": "sk", "케이비": "kb",
          "에이치": "h", "제이알": "jr", "지에스": "gs",
          # (2026-07-15 감사) no_match 실측에서 관측된 미등재 음차 — 같은 단지인데 임계 0.70 미달로
          # 탈락하던 것들. 예: 에스씨그린아파트↔SC그린(0.44), 창원무동에스티엑스칸1차↔…STX칸1차(0.64).
          "에스티엑스": "stx", "에스씨": "sc", "에스아이": "si", "에스제이": "sj",
          "에스엠": "sm", "엠제이": "mj"}


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
    # ratio + 정렬문자비교(어순차이 강건: 시지2차사월↔사월시지2차) + token_set.
    scores = [fuzz.token_set_ratio(na, nb), fuzz.ratio(na, nb),
              fuzz.ratio("".join(sorted(na)), "".join(sorted(nb)))]
    # partial은 '완전 포함'(정평현대⊂정평현대타운=100)엔 옳지만 '접두 부분겹침'(청라봄↔청라로데오…=67)은
    # 과대평가 → 거의 완전포함(≥90)일 때만 신뢰.
    pr = fuzz.partial_ratio(na, nb)
    if pr >= 90:
        scores.append(pr)
    return max(scores) / 100.0


# 단지 식별자 — 'N단지'·'N차'. 다른 번호끼리 매칭(노빌리안1↔노빌리안2, 영등3차↔영등4차)을
# 막는다. (2026-07-20 야간 3c 검수: 중신뢰 매칭에서 실측된 오매칭 2건 — KB교차검증은 통과했으나
# 인접 단지의 다른 실거래를 comps로 씀). 국토부 경로 matcher._complex_ids와 동일 취지.
_ID_RE = re.compile(r"(\d+)\s*(?:단지|차)")


def _complex_ids(name: str) -> frozenset:
    return frozenset(m.group(1) for m in _ID_RE.finditer(name or ""))


def best_complex(apt_name: str, complexes: list[dict]) -> tuple[dict | None, float]:
    """단지 목록에서 이름 최고유사도 후보 반환.

    (3c 가드) 경매물건에 단지식별자(N단지/N차)가 있으면, 그와 '다른 번호만' 가진 후보는
    이름이 아무리 비슷해도 다른 단지이므로 제외한다. 물건에 식별자가 없으면 종전대로.
    """
    want_ids = _complex_ids(apt_name)
    best, bs = None, 0.0
    for cp in complexes:
        cname = cp.get("complexName", "")
        if want_ids:
            cand_ids = _complex_ids(cname)
            # 후보에 식별자가 있는데 물건 식별자와 교집합이 0이면 = 다른 단지/차수 → 제외.
            if cand_ids and not (want_ids & cand_ids):
                continue
        sc = name_score(apt_name, cname)
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
