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
    "지분": ("지분매각", "지분 매각", "지분경매", "일부 지분", "분의", "지분"),
    "분묘기지권": ("분묘기지권", "분묘 기지권", "분묘"),
    "대지권미등기": ("대지권미등기", "대지권 미등기", "대지권 없음", "대지권미등기임"),
    "위반건축물": ("위반건축물", "위반 건축물", "무허가", "제시외 건물"),
}


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
)
# 명세서 표준 경고문(항상 인쇄되는 안내)은 대항력 '존재' 신호가 아니다 → 제외.
_BOILERPLATE = "임차보증금은 매수인에게 인수되는 경우가 발생할 수 있고"


def detect_tenant_opposable(myeongsaeseo: str, *others: str) -> bool:
    """대항력 있는(배당 못 받는) 임차인 존재 여부.

    항상 인쇄되는 표준 경고문(boilerplate)만으로는 True로 보지 않는다. 그 문장을
    제거한 뒤 구체적 인수 문구가 남아 있을 때만 대항력 위험으로 판정.
    """
    blob = "\n".join([myeongsaeseo, *others])
    stripped = blob.replace(_BOILERPLATE, "")
    return any(p in stripped for p in _OPPOSABLE_PHRASES)


# ---------------------------------------------------------------------------
# 4. 인수금액(assumed_amount) — 매수인이 추가로 떠안는 금액(원)
# ---------------------------------------------------------------------------
# "매수인이 인수하는 금액 ... 금80,000,000원" / "인수 ... 150,000,000원" 등.
_AMOUNT_RE = re.compile(r"(?:금)?\s*([\d,]{4,})\s*원")
_ASSUME_CONTEXT = ("인수", "미배당", "떠안", "부담")
# 부정/소멸 문맥 — 같은 줄에 있으면 그 금액은 '인수액'이 아니다.
# 예: "인수할 권리 없음", "근저당 … 전액 말소 예정"(=소멸). 오탐(안전물건→위험 오판) 방지.
# 주의: 스캐폴딩 단계 휴리스틱 — 실제 매각물건명세서 HTML 확보 후 정규식/문맥 튜닝 필요.
_ASSUME_NEGATION = ("없", "말소", "소멸")


def detect_assumed_amount(*texts: str) -> int:
    """인수 문맥이 있는 줄에서 가장 큰 금액(원)을 인수금액으로 추정.

    보수적으로 '최댓값'을 택한다(과소추정이 스코어 과대평가로 이어지는 침묵실패 방지).
    단 같은 줄에 부정/소멸 표현(없음·말소·소멸)이 있으면 그 줄은 인수액이 아니므로 제외한다.
    인수 문맥 줄이 없으면 0.
    """
    best = 0
    for text in texts:
        if not text:
            continue
        for line in text.splitlines():
            if not any(k in line for k in _ASSUME_CONTEXT):
                continue
            if any(neg in line for neg in _ASSUME_NEGATION):
                continue
            for m in _AMOUNT_RE.finditer(line):
                amt = to_won(m.group(1))
                if amt > best:
                    best = amt
    return best


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
