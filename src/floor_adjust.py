"""저층(1~2층) 시세 보정 — 층을 무시한 중앙값이 저층 물건 시세를 부풀리는 것을 잡는다.

## 왜 (2026-07-24 실측, naver_real_trades 2021~ 77,433건 · 710개 단지·평형)
같은 단지·같은 평형 안에서 저층(1~2층) 실거래 중앙값은 상층(3층↑) 대비
  1층 −7.6% · 2층 −5.5% (단지·평형별 중앙값의 중앙값, 최대 −34%).
경매 물건의 45건/358건(clean 타겟)이 저층인데, 층 무시 중앙값으로 시세를 잡으면
저층 물건의 차익이 체계적으로 과대평가된다(감정가는 층을 반영하므로 갭이 벌어진다).

## 어떻게
1) 물건의 층 = 소재지 주소에서 파싱(파싱률 실측 99.5%). 못 읽으면 보정하지 않는다(모름≠할인).
2) 비교군(같은 단지·평형 comps)에 저층·상층이 각각 MIN_SIDE_BASIS건 이상 있으면
   **그 단지의 실측 비율**(저층 중앙값/상층 중앙값)을 쓰고, 부족하면 전국 실측 기본계수를 쓴다.
3) 상향 보정은 하지 않는다(저층이 더 비싼 단지 18% 실측 — 소음일 수 있어 1.0 클램프).
   하한 클램프 0.65(실측 최대 할인 34% 근방) — 소표본 극단 비율 방어.

적용 지점: matcher.estimate_market / estimate_from_complex_trades (채점) +
score._apply_market_price (서빙 KB/호가/전세 폴백 — comps 가 없어 기본계수만).
"""

from __future__ import annotations

import re
import statistics

LOW_FLOOR_MAX = 2          # 이 층 이하가 '저층'(1~2층). 0=지하.
MIN_SIDE_BASIS = 3         # 단지 실측 비율에 필요한 저층·상층 각측 최소 표본
MULT_MIN = 0.65            # 비율 하한 클램프(실측 최대 할인 근방 — 소표본 극단 방어)
# 전국 실측 기본계수(단지·평형별 저층/상층 중앙값 비율의 중앙값, 2026-07-24):
# 1층 0.9244(n=436) · 2층 0.9450(n=516). 지하는 표본 없음 — 1층 계수 준용(보수 방향 아님을
# 명세서에 기록: 실제 지하 할인은 더 클 수 있으나 데이터 없이 더 깎지 않는다).
DEFAULT_MULT = {0: 0.92, 1: 0.92, 2: 0.945}

# 주소의 층 표기: "제5층", "4층504호", "18층1805호" 등. 1~2자리(아파트 초고층 상한).
_FLOOR_RE = re.compile(r"제?\s*(\d{1,2})\s*층")
_BASEMENT_NEAR = "지하"


def subject_floor(address: str) -> int | None:
    """소재지 주소 → 물건의 층. 지하는 0, 표기 없으면 None(보정 안 함).

    복수 층 표기(복층 등)는 최저층을 쓴다(보수). '지하1층'의 '1층' 오인은
    직전 문자 창의 '지하'로 걸러 0으로 판정한다.
    """
    floors: list[int] = []
    for m in _FLOOR_RE.finditer(address or ""):
        prefix = (address or "")[max(0, m.start() - 3):m.start()]
        if _BASEMENT_NEAR in prefix:
            return 0
        floors.append(int(m.group(1)))
    if not floors:
        return None
    return min(floors)


def is_low_floor(floor: int | None) -> bool:
    return floor is not None and floor <= LOW_FLOOR_MAX


def default_multiplier(floor: int | None) -> float:
    """전국 실측 기본계수 — comps 없는 경로(서빙 KB/호가/전세 폴백)용."""
    if not is_low_floor(floor):
        return 1.0
    return DEFAULT_MULT.get(floor, DEFAULT_MULT[1])


def floor_multiplier(floor: int | None,
                     comps: list[tuple[int, float]]) -> tuple[float, str]:
    """(보정배율, 근거) — 근거: 'complex'(단지 실측) | 'default'(전국 계수) | 'none'(무보정).

    comps: (층, 값) 쌍 — 값은 경로에 맞는 단위(평단가든 가격이든 저층/상층 **동일 단위**면 된다.
    비율만 쓰므로 절대 단위 무관). 층 0/미상 행은 호출부가 이미 제외했다고 가정하지 않고
    여기서도 걸러낸다(floor<=0 제외 — 실거래의 floor=0 은 '미상'이다).
    """
    if not is_low_floor(floor):
        return 1.0, "none"
    low = [v for f, v in comps if 0 < f <= LOW_FLOOR_MAX and v > 0]
    upper = [v for f, v in comps if f > LOW_FLOOR_MAX and v > 0]
    if len(low) >= MIN_SIDE_BASIS and len(upper) >= MIN_SIDE_BASIS:
        ratio = statistics.median(low) / statistics.median(upper)
        # 상향 보정 금지(1.0 상한) + 소표본 극단 방어(0.65 하한)
        return min(1.0, max(MULT_MIN, ratio)), "complex"
    return default_multiplier(floor), "default"
