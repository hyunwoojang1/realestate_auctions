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
}
# 지분 표기 "2분의 1"·"3분의2" — 숫자 사이의 '분의'만 지분 신호(‘부분의’ 오탐 차단).
_SHARE_RE = re.compile(r"\d+\s*분의\s*\d+")


def detect_special_rights(*texts: str) -> list[str]:
    """문서 텍스트들에서 특수권리 표준 라벨을 검출(중복 제거, 정의 순서 유지).

    '해당사항 없음' 같은 부정 문맥은 라벨별로 판정하지 않는다(오탐보다 미탐이 위험하므로
    키워드 출현 자체를 위험 신호로 본다 — 보수적).
    """
    blob = "\n".join(t for t in texts if t)
    found: list[str] = []
    for label, variants in SPECIAL_RIGHT_KEYWORDS.items():
        if any(v in blob for v in variants):
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
    "미배당 잔액은 매수인 인수",
    "배당받지 못한 잔액이 매수인에게 인수",
    # 실측 보강(2026-07-10, 대구 2025타경669 매각물건명세서 원문): 조사('이')·표현 변형.
    # "매수인에게 대항할 수 있는 … 임차권등기 … 잔액을 매수인이 인수함" 이 기존 목록에 미탐.
    "매수인에게 대항할 수 있는",
    "매수인이 인수",
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


def _strip_negated_clauses(text: str) -> str:
    """인수-해소 표현이 포함된 '절'(문장 조각)을 제거한 텍스트 반환.

    절 단위(마침표·개행·세미콜론 구분)로 잘라 해소 표현이 있는 절만 버린다 — 같은 명세서에
    '5번 임차권은 말소 동의, 7번 임차권은 인수' 처럼 혼재할 때 인수 절은 살아남아야 하므로
    문서 전체를 버리면 안 된다(미탐 방지).
    """
    out = []
    for clause in re.split(r"[.\n;·]", text or ""):
        if any(neg in clause for neg in _OPPOSABLE_NEGATIONS):
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
    has_release = any(neg in blob for neg in _OPPOSABLE_NEGATIONS)
    return (not has_release) and any(p in stripped for p in _WEAK_PHRASES)


# ---------------------------------------------------------------------------
# 4. 인수금액(assumed_amount) — 매수인이 추가로 떠안는 금액(원)
# ---------------------------------------------------------------------------
# "매수인이 인수하는 금액 ... 금80,000,000원" / "인수 ... 150,000,000원" 등.
_AMOUNT_RE = re.compile(r"(?:금)?\s*([\d,]{4,})\s*원")
# 한글 단위 금액(재검증 감사 idx8 확정 — "4억5,000만원"·"금1억2천만원"·"6,500만원" 이 전혀
# 안 읽혀 실보증금 5건이 0원): 억/천만/만 단위를 원으로 환산.
_KOREAN_AMOUNT_RE = re.compile(
    r"(?:금)?\s*(?:(\d[\d,]*)\s*억)?\s*(?:(\d[\d,]*)\s*천만)?\s*(?:(\d[\d,]*)\s*만)?\s*원")
_ASSUME_CONTEXT = ("인수", "미배당", "떠안", "부담")
# 부정/소멸 문맥 — 같은 줄(절)에 있으면 그 금액은 '인수액'이 아니다.
# ⚠ 이중부정(재검증 감사 idx7 확정): "말소되지 않고 … 매수인이 인수함"은 '말소' 문자가
# 있어도 **인수**다 — 부정의 부정 패턴을 먼저 판정해 negation 체크를 건너뛴다.
_ASSUME_NEGATION = ("없", "말소", "소멸")
_DOUBLE_NEGATION = ("말소되지 않", "말소되지 아니", "소멸되지 않", "소멸되지 아니",
                    "변제되지 아니", "변제되지 않")


def _amounts_in(line: str) -> list[int]:
    """한 줄에서 금액(원) 전부 — 숫자 표기 + 한글 단위 표기."""
    out = [to_won(m.group(1)) for m in _AMOUNT_RE.finditer(line)]
    for m in _KOREAN_AMOUNT_RE.finditer(line):
        eok, cheonman, man = m.groups()
        if not (eok or cheonman or man):
            continue
        won = 0
        if eok:
            won += to_won(eok) * 100_000_000
        if cheonman:
            won += to_won(cheonman) * 10_000_000
        if man:
            won += to_won(man) * 10_000
        if won >= 1_000_000:   # 소액 잡음(수수료 등) 제외
            out.append(won)
    return [a for a in out if a > 0]


def detect_assumed_amount(*texts: str) -> int:
    """인수 문맥 줄들의 금액으로 인수 총액 추정.

    - 이중부정("말소되지 않고 … 인수") 줄은 negation 이 있어도 인수로 판정(idx7).
    - 한글 단위 금액(억/천만/만원)도 읽는다(idx8).
    - 서로 다른 금액이 여러 줄이면 **distinct 합산**(idx12 — 다건 임차권 과소추정 방지.
      같은 보증금이 여러 절에 반복 인용되는 경우는 중복 제거로 이중계산 방지).
    """
    picked: set[int] = set()
    for text in texts:
        if not text:
            continue
        for line in text.splitlines():
            if not any(k in line for k in _ASSUME_CONTEXT):
                continue
            double_neg = any(dn in line for dn in _DOUBLE_NEGATION)
            if not double_neg and any(neg in line for neg in _ASSUME_NEGATION):
                continue
            amts = _amounts_in(line)
            if amts:
                # 한 줄 안에서는 최대 1건(같은 보증금의 표기 중복 방지), 줄 간에는 distinct 합산
                picked.add(max(amts))
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
