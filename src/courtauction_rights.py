"""courtauction 물건상세 → 권리분석 원재료 파서(스캐폴딩).

리스트 검색(searchControllerMain)에는 권리·임차·점유 정보가 **없다**.
그 원재료는 물건상세의 세 문서에 흩어져 있다:

  - 매각물건명세서: 임차인 대항력·인수권리·특수권리(비고)·소멸되지 않는 등기 권리
  - 현황조사서: 실제 점유관계(공실/임차인/소유자점유/다수점유)
  - 감정평가서: 감정평가액(교차검증용)

이 모듈은 **저장된 문서 텍스트를 상대로만** 동작한다(오프라인). 라이브 크롤 후
텍스트 추출은 상위 계층(client)이 담당하고, 여기서는 텍스트 → 구조화 권리필드만 뽑는다.

설계 원칙(이 프로젝트의 침묵실패 회피 규범과 정합):
  - 애매하면 **보수적으로 위험 쪽**(있음/인수/대항력)으로 표시. 차익 스코어가 과대평가되어
    사용자가 위험 매물을 좋게 보는 침묵실패를 막는다.
  - 개인정보(임차인/소유자 성명 등)는 원문을 저장하지 않고 **불리언·금액·유형만** 추출.
  - 세 문서 중 일부만 있어도 동작(있는 것만 반영). 전부 없으면 '미상' 신호를 남긴다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import CONFIG
from .courtauction_fields import to_won
from .models import AuctionListing

# ---------------------------------------------------------------------------
# 1. 특수권리 사전 — 키워드 → 표준 라벨(score.CONFIG.special_penalty 키와 정합)
# ---------------------------------------------------------------------------
# 표준 라벨은 config.ScoreConfig.special_penalty 의 키를 그대로 써야 페널티가 적용된다.
# 각 라벨에 여러 표기 변형을 매핑(관측 기반, 점진 보강).
SPECIAL_RIGHT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "유치권": ("유치권",),
    "법정지상권": ("법정지상권", "관습법상 법정지상권", "관습상 법정지상권"),
    # '분의' 단독은 '부분의'에 부분문자열 오탐(재검증 감사 idx11) — 정규식(_SHARE_RE)으로 분리.
    "지분": ("지분매각", "지분 매각", "지분경매", "일부 지분", "지분"),
    "분묘기지권": ("분묘기지권", "분묘 기지권", "분묘"),
    "대지권미등기": ("대지권미등기", "대지권 미등기", "대지권 없음", "대지권미등기임"),
    "위반건축물": ("위반건축물", "위반 건축물", "무허가", "제시외 건물"),
    # (감사 2026-07-20) 소유권/인수 위험인데 사전 누락으로 배지=clean 오판하던 3종.
    # 실 DB: 가등기 24·가처분 10·토지별도등기 39건이 배지·게이트에 안 잡히고 있었음.
    "가등기": ("소유권이전청구권가등기", "소유권이전청구권 가등기", "가등기"),
    "가처분": ("가처분",),
    "토지별도등기": ("토지별도등기", "토지 별도등기", "별도등기"),
}
# 지분 표기 "2분의 1"·"3분의2" — 숫자 사이의 '분의'만 지분 신호(‘부분의’ 오탐 차단).
_SHARE_RE = re.compile(r"\d+\s*분의\s*\d+")

# (감사 2026-07-20 C2) 특수권리 '부존재' 문맥 — 키워드와 **같은 절**에 이 표현이 있으면 위험이
# 아니라 부존재 명시다. 법원 명세서는 유치권/법정지상권이 없을 때도 "신고 없음"·"성립 여지 없음"
# 으로 명시하는 경우가 흔한데, 종전엔 키워드 출현만으로 유치권(fatal)→하드게이트 '위험'에 직행해
# 정상 물건을 영구 배제했다. 절 단위로 부존재 표현이 붙은 절만 배제한다(다른 절의 진짜 신고는 보존).
_SPECIAL_NEGATIONS = (
    "신고 없", "신고된 바 없", "미신고", "설정된 바 없", "설정되어 있지 않",
    "성립 여지 없", "성립여지 없", "성립여지없", "성립되지 않", "성립하지 않",
    "존재하지 않", "존재하지않", "해당사항 없", "해당 없", "해당없",
    "없음", "없다", "없슴", "없는 것으로",
)


def _appears_unnegated(variant: str, blob: str) -> bool:
    """variant 가 부존재 문맥이 아닌 절에 한 번이라도 나오면 True(부존재 명시 절은 제외)."""
    for clause in re.split(r"[.\n;·]", blob):
        if variant in clause and not any(neg in clause for neg in _SPECIAL_NEGATIONS):
            return True
    return False


# (감사 2026-07-20 C1 보강) 명세서 표준 항목 안내문(모든 명세서에 법정 서식으로 인쇄되는 헤더)은
# 특정 권리의 '존재' 신고가 아니라 항목 설명이다. 여기 예시로 나열되는 '가처분/가등기/지상권'을
# 실제 권리로 오탐하면 전 물건이 가처분 보유로 오판된다(대항력 _BOILERPLATE와 동일한 함정).
#  - "2. 등기된 부동산에 관한 권리 또는 가처분으로서 매각으로 그 효력이 소멸되지 아니하는 것"
#  - "3. 매각에 따라 설정된 것으로 보는 지상권의 개요"
_SPECIAL_BOILERPLATE_RE = re.compile(r"등기된\s*부동산에\s*관한\s*권리.*?소멸되지\s*아니하는\s*것")
_SPECIAL_BOILERPLATE = ("매각에 따라 설정된 것으로 보는 지상권의 개요",)


def _strip_special_boilerplate(blob: str) -> str:
    """특수권리 검출 전, 표준 항목 안내문을 제거해 오탐(전 물건 가처분 오판)을 막는다."""
    out = _SPECIAL_BOILERPLATE_RE.sub("", blob or "")
    for bp in _SPECIAL_BOILERPLATE:
        out = out.replace(bp, "")
    return out


def detect_special_rights(*texts: str) -> list[str]:
    """문서 텍스트들에서 특수권리 표준 라벨을 검출(중복 제거, 정의 순서 유지).

    '해당사항 없음' 같은 부정 문맥은 라벨별로 판정하지 않는다(오탐보다 미탐이 위험하므로
    키워드 출현 자체를 위험 신호로 본다 — 보수적).
    """
    blob = _strip_special_boilerplate("\n".join(t for t in texts if t))
    found: list[str] = []
    for label, variants in SPECIAL_RIGHT_KEYWORDS.items():
        # (감사 2026-07-20 C2) 부존재 문맥 절은 제외 — '유치권 신고 없음'을 위험으로 오탐하던 것 수정.
        if any(_appears_unnegated(v, blob) for v in variants):
            found.append(label)
        elif label == "지분" and _SHARE_RE.search(blob):
            found.append(label)
    return found


# ---------------------------------------------------------------------------
# 2. 점유관계 — 공실 / 임차인 / 소유자점유 / 다수점유
# ---------------------------------------------------------------------------
# config.eviction_cost / occupant_penalty 의 키와 정합해야 한다.
_VACANT_RE = re.compile(r"공\s*실|점유자\s*없음|폐문부재.*점유자\s*없음")
_TENANT_RE = re.compile(r"임차인")
_OWNER_RE = re.compile(r"소유자(가)?\s*점유|채무자\s*겸\s*소유자\s*점유|소유자")
_MULTI_RE = re.compile(r"다수\s*점유|여러\s*세대|점유자\s*여럿|점유자\s*수인")


def detect_occupant_type(*texts: str) -> str:
    """현황조사서/명세서 텍스트에서 점유유형 판정.

    우선순위: 다수점유 > 임차인 > 소유자점유 > 공실 > (미상→'소유자점유' 보수 기본).
    임차인·소유자 표기가 함께 나오면 인수 위험이 큰 임차인 쪽을 택한다(보수적).
    """
    blob = "\n".join(t for t in texts if t)
    if not blob.strip():
        return "소유자점유"  # 정보 없음 → 비용 큰 쪽으로 보수 가정
    if _MULTI_RE.search(blob):
        return "다수점유"
    if _TENANT_RE.search(blob):
        return "임차인"
    if _OWNER_RE.search(blob):
        return "소유자점유"
    if _VACANT_RE.search(blob):
        return "공실"
    return "소유자점유"


# ---------------------------------------------------------------------------
# 3. 대항력 있는 임차인 — 매수인 인수 위험의 핵심 신호
# ---------------------------------------------------------------------------
_OPPOSABLE_PHRASES = (
    "대항력 있는 임차인",
    "대항력있는 임차인",
    "인수되는 경우가 발생",
    "매수인에게 인수",
    "매수인 인수",
    # (감사 2026-07-20 H5) '인수' 대신 '부담'으로 쓴 명세서 변형 — 대항력 -30점 페널티 누락 방지.
    "매수인이 부담",
    "매수인에게 부담",
    "매수인 부담",
    "미배당 잔액은 매수인 인수",
    "배당받지 못한 잔액이 매수인에게 인수",
    # 실측 보강(2026-07-10, 대구 2025타경669 매각물건명세서 원문): 조사('이')·표현 변형.
    # "매수인에게 대항할 수 있는 … 임차권등기 … 잔액을 매수인이 인수함" 이 기존 목록에 미탐.
    "매수인에게 대항할 수 있는",
    "매수인이 인수",
    # (2026-07-22 mulBigo 실측) 목록 비고의 변형 — '대항력 여지 있는 임대차'·'대항력 있는
    # 주택임차권 승계인'이 기존 목록에 미탐. '대항력 포기'는 _OPPOSABLE_NEGATIONS 절제거가 처리하므로
    # 이 강한 신호는 포기 아닌 진짜 대항력만 남는다(과잉경고 방향 = 보수적).
    "대항력 여지 있는",
    "대항력 있는 주택임차권",
    "대항력 있는 임차권",
    "임차권등기",   # 임차권등기명령 자체가 대항력+우선변제권 유지 신호(보수적)
)
# 명세서 표준 경고문(항상 인쇄되는 안내)은 대항력 '존재' 신호가 아니다 → 제외.
_BOILERPLATE = "임차보증금은 매수인에게 인수되는 경우가 발생할 수 있고"

# 인수 '해소' 문맥(재검증 감사 2026-07-11 확정 idx9·10 오탐 수정) — 이 표현이 붙은 절은
# 위험 신호가 아니라 반대(인수 없음 확정/말소 예정) 신호다. 절 단위로 제거 후 매칭한다.
#  - "…매수인이 인수하지 아니함"(특별매각조건으로 인수 0원 확정)
#  - "임차권등기 말소 동의(확약)" / "대항력 포기" / "말소조건 매각" — 임차권등기가 있어도 소멸 예정
_OPPOSABLE_NEGATIONS = (
    "인수하지 아니",
    "인수하지 않",
    "인수되지 아니",
    "인수되지 않",
    "인수할 권리 없",
    "말소 동의",
    "말소동의",
    "말소에 동의",
    "말소를 조건",
    "말소 조건",
    "말소조건",
    "대항력 포기",
    "대항력을 포기",
    "임차권등기 말소",
    "임차권등기의 말소",
)

# (감사 2026-07-23 P-14) 위 고정 문자열은 **조사 변형을 못 잡는다** — 실측 결과 가장 흔한 표기가
# "대항력**은** 포기"(574건)인데 목록에 없어(있는 건 "대항력 포기" 494·"대항력을 포기" 175),
# HUG·주금공이 대항력을 포기한 물건까지 대항력 있음으로 남아 추천에서 강등됐다(강등 248건 중 58건).
# 조사·부사 삽입을 흡수하는 정규식으로 보완한다. 문자열 목록은 그대로 두고 **둘 다** 검사한다.
_OPPOSABLE_NEGATION_RES = (
    re.compile(r"대항력\s*[은는을를]?\s*(?:전부\s*)?포기"),   # 대항력은/을/─ 포기, 대항력포기
)


def _has_negation(text: str) -> bool:
    """인수-해소(부정) 표현이 있는가 — 고정 문자열 + 조사 변형 정규식 양쪽."""
    return (any(neg in text for neg in _OPPOSABLE_NEGATIONS)
            or any(rx.search(text) for rx in _OPPOSABLE_NEGATION_RES))


def _strip_negated_clauses(text: str) -> str:
    """인수-해소 표현이 포함된 '절'(문장 조각)을 제거한 텍스트 반환.

    절 단위(마침표·개행·세미콜론 구분)로 잘라 해소 표현이 있는 절만 버린다 — 같은 명세서에
    '5번 임차권은 말소 동의, 7번 임차권은 인수' 처럼 혼재할 때 인수 절은 살아남아야 하므로
    문서 전체를 버리면 안 된다(미탐 방지).
    """
    out = []
    # (감사 2026-07-20 C4) 콤마도 절 구분자에 포함 — "김철수는 인수하지 아니하고, 이영희는 매수인에게
    # 인수됨"처럼 한 문장에 인수-해소와 진짜 인수가 콤마로 이어진 다중임차인 명세서에서, 콤마가 없으면
    # 부정절이 인수절까지 통째로 먹어 인수신호를 미탐하던 것 수정(주석 목표를 실제로 달성).
    for clause in re.split(r"[.\n;·,，]", text or ""):
        if _has_negation(clause):
            continue
        out.append(clause)
    return " ".join(out)


# 약한 신호 — 존재만으로 위험 추정하는 phrase(임차권등기 자체). 문서에 해소 표현이 하나라도
# 있으면 이 신호는 억제한다(말소동의·대항력 포기가 다른 절에 있는 경우가 흔함 — 실측 15건 오탐).
_WEAK_PHRASES = ("임차권등기",)
_STRONG_PHRASES = tuple(p for p in _OPPOSABLE_PHRASES if p not in _WEAK_PHRASES)


def detect_tenant_opposable(myeongsaeseo: str, *others: str) -> bool:
    """대항력 있는(배당 못 받는) 임차인 존재 여부 — 2단계 판정.

    (재검증 감사 idx9·10 오탐 수정)
    1) 강한 신호(인수 명시 문구): 인수-해소 절("인수하지 아니함"·"말소 동의" 등)을 제거한
       나머지에서 찾는다 — 같은 문서에 '5번은 말소동의, 7번은 인수' 혼재 시 인수 절은 살린다.
    2) 약한 신호(임차권등기 존재): 문서 어디에도 해소 표현이 없을 때만 위험으로 본다 —
       말소동의 확약·대항력 포기가 다른 절에 있으면 등기 존재만으로 True 를 주지 않는다.
    표준 경고문(boilerplate)은 신호가 아니다.
    """
    blob = "\n".join([myeongsaeseo, *others]).replace(_BOILERPLATE, "")
    stripped = _strip_negated_clauses(blob)
    if any(p in stripped for p in _STRONG_PHRASES):
        return True
    has_release = _has_negation(blob)
    return (not has_release) and any(p in stripped for p in _WEAK_PHRASES)


# ---------------------------------------------------------------------------
# 4. 인수금액(assumed_amount) — 매수인이 추가로 떠안는 금액(원)
# ---------------------------------------------------------------------------
_ASSUME_CONTEXT = ("인수", "미배당", "떠안", "부담")
# 부정/소멸 문맥 — 같은 줄(절)에 있으면 그 금액은 '인수액'이 아니다.
# ⚠ 이중부정(재검증 감사 idx7 확정): "말소되지 않고 … 매수인이 인수함"은 '말소' 문자가
# 있어도 **인수**다 — 부정의 부정 패턴을 먼저 판정해 negation 체크를 건너뛴다.
_ASSUME_NEGATION = ("없", "말소", "소멸")
_DOUBLE_NEGATION = ("말소되지 않", "말소되지 아니", "소멸되지 않", "소멸되지 아니",
                    "변제되지 아니", "변제되지 않")
# 인수 '직접 부정'(서빙감사 2026-07-12 #15): "…매수인이 인수하지 아니함(특별매각조건)"은
# 인수 0원 확정 신호다. 이중부정('변제되지 않')이 같은 줄에 있어 negation 을 건너뛰더라도
# 이 표현이 있으면 그 줄은 인수액에서 제외한다(직접 부정이 최우선).
_ASSUME_DIRECT_NEGATION = ("인수하지 아니", "인수하지 않", "인수되지 아니", "인수되지 않",
                           "인수하지아니", "인수하지않")

# 한글 혼합 수(만 단위 계수) — '9천5백'=9500, '천5백'=1500, '5000'=5000 (서빙감사 #0).
_MIX_RE = {
    "천": re.compile(r"(\d*)\s*천"),
    "백": re.compile(r"(\d*)\s*백"),
    "십": re.compile(r"(\d*)\s*십"),
}
# 금액 토큰 스캐너 — 순수 숫자('150,000,000원')와 한글 단위('1억9천5백만원') 모두 포착.
_MONEY_RE = re.compile(r"(?:금\s*)?(\d[\d,]*(?:\s*억)?(?:[\d,천백십\s]*만)?)\s*원")


def _mixed_man(s: str) -> int:
    """만 단위 계수 혼합표기 → 정수. '9천5백'→9500, '5,000'→5000, '천5백'→1500."""
    s = (s or "").replace(",", "").strip()
    if not s:
        return 0
    total = 0
    rest = s
    for unit, mult in (("천", 1000), ("백", 100), ("십", 10)):
        m = _MIX_RE[unit].search(rest)
        if m and m.start() == 0:
            total += (int(m.group(1)) if m.group(1) else 1) * mult
            rest = rest[m.end():]
    m = re.match(r"\s*(\d+)\s*$", rest)
    if m:                       # 남은 순수 숫자(단위 없는 '5000') — 있으면 계수 자체가 그 값
        return total + int(m.group(1)) if total else int(m.group(1))
    return total


def _korean_won(token: str) -> int:
    """금액 토큰 → 원. 순수 숫자·억/천만/백만/만 혼합 모두 처리(서빙감사 #0)."""
    s = (token or "").replace(" ", "").replace(",", "")
    if not s:
        return 0
    won = 0
    m = re.match(r"(\d+)억", s)
    if m:
        won += int(m.group(1)) * 100_000_000
        s = s[m.end():]
    m = re.match(r"([\d천백십]+)만", s)
    if m:
        won += _mixed_man(m.group(1)) * 10_000
        s = s[m.end():]
    m = re.match(r"(\d+)$", s)
    if m:                       # 단위 없는 순수 원 단위 숫자(억/만 없는 '80000000')
        won += int(m.group(1))
    return won


def _amounts_in(line: str) -> list[int]:
    """한 줄에서 금액(원) 전부 — 순수 숫자 + 한글 단위 표기."""
    return [w for w in (_korean_won(m.group(1)) for m in _MONEY_RE.finditer(line))
            if w > 0]


# '금 X원 중 (미반환|잔액) Y원' — 원계약 보증금 X 가 아니라 실제 인수액 Y 를 채택(서빙감사 #16).
_RESIDUAL_RE = re.compile(
    r"(?:금\s*)?[\d,억천백십\s만]+\s*원\s*중\s*(?:미반환|반환받지\s*못한|배당받지\s*못한|잔[액여])\S*?"
    r"\s*(?:금액\s*)?(?:금\s*)?([\d,억천백십\s만]+)\s*원")


def _line_assumed(line: str) -> int:
    """한 줄의 인수 금액 — '중 미반환 Y' 우선, 임차권/보증금 다건이면 합산, 아니면 max."""
    residuals = _RESIDUAL_RE.findall(line)
    if residuals:
        return sum(_korean_won(r) for r in residuals)
    amts = _amounts_in(line)
    if not amts:
        return 0
    # 임차권/전세권/보증금이 2건 이상 나열되면 개별 인수액 **distinct 합산**(서빙감사 #16 —
    # 다건 과소표시 방지, 단 같은 금액 반복표기는 set 로 이중계산 차단), 1건이면 max.
    n = line.count("임차권") + line.count("전세권") + line.count("보증금")
    return sum(set(amts)) if n >= 2 else max(amts)


# (H1 수정 2026-07-22) 앞 임차인 금액을 되짚는 '재고지' 줄 — 이런 줄의 금액은 새 인수액이
# 아니라 앞 줄의 반복이므로 합산에서 제외한다. 이 마커가 있어야 '같은 보증금 임차인 2명'(합산)과
# '한 임차인 금액 재안내'(1회)를 구분할 수 있다(텍스트만으론 금액이 같아 구분 불가했던 게 H1).
_ASSUME_BACKREF = ("재안내", "재고지", "재통지", "상기", "앞서", "위 보증", "위 임차",
                   "위 임대차", "참고로", "다시 안내")


def detect_assumed_amount(*texts: str) -> int:
    """인수 문맥 줄들의 금액으로 인수 총액 추정.

    - 인수 직접부정("…인수하지 아니함") 줄은 이중부정보다 우선해 제외(서빙감사 #15).
    - 이중부정("말소되지 않고 … 인수") 줄은 negation 이 있어도 인수로 판정(idx7).
    - 한글 단위(억/천만/백만/만원)·혼합표기 파싱(서빙감사 #0).
    - 줄 내 다건 임차권 합산·'중 미반환 Y' 채택(서빙감사 #16).
    - (H1 2026-07-22) **줄 간 합산** — 같은 보증금이 서로 다른 임차인 줄에서 나오면 합산한다
      (종전 set-dedup 은 '같은 금액 임차인 2명'을 1명치로 과소산정 = 위험한 미탐). 단 '위 보증금
      …재안내' 같은 재고지 줄(_ASSUME_BACKREF)과 **완전히 동일한 줄**(요지↔비고 복붙)은 이중계산
      하지 않는다. 텍스트만으론 애매하므로 임차인 전입일 실데이터가 있으면 그쪽(구조화 합산)이 우선.
    """
    total = 0
    seen_lines: set[str] = set()    # 완전 동일 줄(복붙) 이중계산 방지
    for text in texts:
        if not text:
            continue
        for line in text.splitlines():
            if not any(k in line for k in _ASSUME_CONTEXT):
                continue
            if any(dn in line for dn in _ASSUME_DIRECT_NEGATION):
                continue    # '인수하지 아니함' = 인수 0 확정 (최우선)
            double_neg = any(dn in line for dn in _DOUBLE_NEGATION)
            if not double_neg and any(neg in line for neg in _ASSUME_NEGATION):
                continue
            if any(b in line for b in _ASSUME_BACKREF):
                continue    # (H1) 앞 임차인 금액 재고지 줄 — 새 인수액 아님
            key = re.sub(r"\s+", "", line)
            if key in seen_lines:
                continue    # 동일 줄 복붙(요지↔비고)은 1회만
            amt = _line_assumed(line)
            if amt > 0:
                total += amt
                seen_lines.add(key)
    return total


# 보증금 표기 — '임대차보증금 금X원' / '보증금 X원' (서빙감사 #9: 인수 금액미상 물건의
# 보수 추정용). 인수 문맥 없이도 명세서에 적힌 보증금을 잡는다.
_DEPOSIT_RE = re.compile(r"(?:임대차|임차)?\s*보증금\s*(?:금\s*)?([\d,억천백십\s만]+)\s*원")


def detect_deposit_amount(*texts: str) -> int:
    """명세서 텍스트의 보증금액(원) 합산 — 인수 부담인데 인수금액이 미상일 때 보수 추정.

    인수-해소 절('말소 동의'·'인수하지 아니')이 있는 절의 보증금은 제외(소멸 예정).
    같은 금액 반복은 중복 제거. 못 찾으면 0.
    """
    picked: set[int] = set()
    for text in texts:
        if not text:
            continue
        cleaned = _strip_negated_clauses(text)
        for m in _DEPOSIT_RE.finditer(cleaned):
            if any(dn in m.group(0) for dn in _ASSUME_DIRECT_NEGATION):
                continue
            w = _korean_won(m.group(1))
            if w > 0:
                picked.add(w)
    return sum(picked)


# ---------------------------------------------------------------------------
# 5. 감정평가액(교차검증용) — 감정평가서에서 추출
# ---------------------------------------------------------------------------
_APPRAISAL_RE = re.compile(r"감정평가액\s*(?:금)?\s*([\d,]{4,})\s*원")


def detect_appraisal_amount(gamjeong: str) -> int:
    """감정평가서 텍스트에서 감정평가액(원). 못 찾으면 0."""
    if not gamjeong:
        return 0
    m = _APPRAISAL_RE.search(gamjeong)
    return to_won(m.group(1)) if m else 0


# ---------------------------------------------------------------------------
# 6. 통합 결과
# ---------------------------------------------------------------------------
@dataclass
class ParsedRights:
    """물건상세 세 문서에서 뽑은 권리분석 원재료.

    `parsed_sources`는 실제로 텍스트가 주어진 문서 이름 집합 — 신뢰도 판단용.
    """
    assumed_amount: int = 0
    special_rights: list[str] = field(default_factory=list)
    tenant_opposable: bool = False
    occupant_type: str = "소유자점유"
    appraisal_amount: int = 0
    parsed_sources: list[str] = field(default_factory=list)

    @property
    def is_partial(self) -> bool:
        """세 문서 중 하나라도 빠졌으면 True(권리분석 불완전 신호)."""
        return set(self.parsed_sources) != {"명세서", "현황조사서", "감정평가서"}


def parse_rights(
    myeongsaeseo: str = "",
    hyeonhwang: str = "",
    gamjeong: str = "",
) -> ParsedRights:
    """세 문서 텍스트 → ParsedRights. 빈 문자열이면 그 문서는 무시."""
    sources: list[str] = []
    if myeongsaeseo.strip():
        sources.append("명세서")
    if hyeonhwang.strip():
        sources.append("현황조사서")
    if gamjeong.strip():
        sources.append("감정평가서")

    return ParsedRights(
        assumed_amount=detect_assumed_amount(myeongsaeseo, hyeonhwang),
        special_rights=detect_special_rights(myeongsaeseo, hyeonhwang, gamjeong),
        tenant_opposable=detect_tenant_opposable(myeongsaeseo, hyeonhwang),
        occupant_type=detect_occupant_type(hyeonhwang, myeongsaeseo),
        appraisal_amount=detect_appraisal_amount(gamjeong),
        parsed_sources=sources,
    )


def apply_rights(listing: AuctionListing, rights: ParsedRights) -> AuctionListing:
    """파싱된 권리를 AuctionListing에 반영한 **새 객체**를 반환(불변 패턴).

    감정가는 리스트 검색값을 우선 신뢰하되, 리스트에 값이 없고(0) 감정평가서에서
    읽혔으면 그것으로 보정한다(침묵 0원 → affordable 필터 왜곡 방지).
    """
    appraisal = listing.appraisal_price
    if appraisal <= 0 and rights.appraisal_amount > 0:
        appraisal = rights.appraisal_amount

    return AuctionListing(
        case_no=listing.case_no,
        court=listing.court,
        address=listing.address,
        lawd_cd=listing.lawd_cd,
        dong=listing.dong,
        apt_name=listing.apt_name,
        property_type=listing.property_type,
        area_m2=listing.area_m2,
        appraisal_price=appraisal,
        min_bid_price=listing.min_bid_price,
        fail_count=listing.fail_count,
        sale_date=listing.sale_date,
        assumed_amount=rights.assumed_amount,
        special_rights=list(rights.special_rights),
        tenant_opposable=rights.tenant_opposable,
        occupant_type=rights.occupant_type,
        rights_verified=True,   # 물건상세 권리분석 반영됨 → '권리미확인' 해제, 하드게이트 실작동
    )


def gate_reasons(rights: ParsedRights, min_bid_price: int) -> list[str]:
    """이 권리 상태가 하드게이트에 걸리는 이유(사용자 표시용). 없으면 빈 리스트.

    score.py의 하드게이트 로직(치명 특수권리 / 인수비율 초과)과 동일 기준을 재현한다.
    """
    reasons: list[str] = []
    fatal = [s for s in rights.special_rights if s in CONFIG.fatal_special]
    if fatal:
        reasons.append(f"치명적 특수권리: {', '.join(fatal)}")
    if min_bid_price > 0:
        ratio = rights.assumed_amount / min_bid_price
        if ratio > CONFIG.assumed_ratio_gate:
            reasons.append(
                f"인수금액 비율 {ratio * 100:.0f}% (>{CONFIG.assumed_ratio_gate * 100:.0f}%)"
            )
    return reasons


# ---------------------------------------------------------------------------
# 6. 입찰보증금 비율 — 법원이 명세서에 명시한 값 (UX 감사 U-01, 2026-07-23)
# ---------------------------------------------------------------------------
# 보증금은 보통 최저매각가격의 10%지만, **재매각·특별매각조건 물건은 20~30%**다.
# 화면이 이를 반영하지 않고 늘 10%로 계산하면, 사용자가 절반만 준비해 법정에 가고
# **입찰이 무효 처리된다**(UX 감사에서 3개 페르소나가 독립 지적한 실제 금전 손실 경로).
# 법원은 이 비율을 명세서 비고에 문장으로 준다 — 실측 609건, 예:
#   "특별매각조건 매수신청보증금 최저매각가격의 20%"(159) · "재매각임. 매수신청보증금은 …의 20%"(34)
# 표기 변형(조사·구두점·번호 접두)이 많아 문자열 비교가 아니라 정규식으로 읽는다.
_DEPOSIT_RATE_RE = re.compile(
    r"(?:매수신청)?보증금[^\n%]{0,40}?(\d{1,2})\s*%"
)
# 상식 범위 밖 값은 오탐(다른 비율 언급이 '보증금' 근처에 있었던 경우)으로 보고 버린다.
_DEPOSIT_RATE_MIN, _DEPOSIT_RATE_MAX = 10, 30


def parse_deposit_rate(*texts: str) -> int | None:
    """명세서 비고 등에서 **입찰보증금 비율(%)** 을 읽는다. 명시가 없으면 None.

    None 은 '10%'가 아니라 **'법원이 명시하지 않았다'** 는 뜻이다 — 호출부는 이를
    '통상 10% 가정'으로 쓰되 **추정임을 화면에 밝혀야** 한다(모름을 확정으로 바꾸지 않는다).
    여러 비율이 언급되면 **가장 큰 값**을 택한다(보수적 — 준비할 현금을 과소평가하지 않는다).
    """
    best: int | None = None
    for t in texts:
        for m in _DEPOSIT_RATE_RE.finditer(t or ""):
            try:
                rate = int(m.group(1))
            except (TypeError, ValueError):
                continue
            if _DEPOSIT_RATE_MIN <= rate <= _DEPOSIT_RATE_MAX and (best is None or rate > best):
                best = rate
    return best


def bid_deposit(min_bid_price: int, *texts: str) -> tuple[int, int, bool]:
    """(보증금액, 적용비율%, 법원명시여부). 명시가 없으면 통상 10%로 계산하되 stated=False.

    화면은 stated=False 일 때 반드시 '통상 10% 가정 · 법원 공고 확인'을 함께 표기해야 한다.
    """
    rate = parse_deposit_rate(*texts)
    stated = rate is not None
    rate = rate if stated else 10
    return int(round((min_bid_price or 0) * rate / 100)), rate, stated
