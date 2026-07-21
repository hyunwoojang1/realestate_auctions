"""사건번호 검색 — 결정론적 정규화 + 로컬 매칭 + 라이브 단건 조회.

## 왜 이 모듈이 따로 있나 (무한수정 방지)
친구가 "2025-101763", "2025타경101763", "101763" 등 제각각으로 사건번호를 던진다.
표기를 그때그때 손보면 끝없이 수정하게 되므로, **모든 입력을 단 하나의 표준형으로 환원하는
규칙을 한 곳에 못 박는다.** 이 규칙과 매칭 논리는 test_casesearch.py로 고정된다.

## 3층 계약
1. 정규화 `parse_case_query`: 어떤 표기든 (year:int|None, serial:int)로 환원.
   표준형 canonical = f"{year}타경{serial}". (DB 저장 포맷과 동일 — 실측 확인.)
2. 로컬 매칭 `match_local`: 표준형 완전일치. year가 없으면 serial만으로 전 연도 후보를 돌려주고
   사용자가 고르게 한다(임의 한 건을 조용히 고르지 않는다).
3. 라이브 `live_lookup`: 법원명 → boCd(court_codes.json, 실크롤 자동추출) + 표준형 →
   CourtAuctionClient.case_detail. 상세 응답에서 **확실히 파싱되는 것만** 쓴다
   (최저입찰가·감정가·유찰수·청구금액·권리요지·기일). 주소·면적·단지명은 상세 응답에 없어
   차익 채점은 불가 — 로컬 히트 물건만 차익을 제공한다(과장 금지).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
COURT_CODES_PATH = ROOT / "data" / "court_codes.json"

# 사건 구분 문자열 — 실측상 부동산 경매는 전부 "타경". DB 저장 구분자도 "타경" 단일(실측).
CASE_MARK = "타경"

# 법원명 힌트: "…지방법원" / "…지원" / "…고등법원" / 그 밖의 "…법원".
_COURT_RE = re.compile(r"[가-힣]{2,}(?:지방법원|고등법원|지원|법원)")
_DIGITS_RE = re.compile(r"\d+")


class CaseSearchError(RuntimeError):
    """사건번호 검색 관련 실패(법원코드 미상·입력 파싱 불가 등)."""


@dataclass(frozen=True)
class CaseQuery:
    """정규화된 사건번호 질의. 불변."""
    serial: int                 # 사건 일련번호(예: 101763)
    year: int | None = None     # 접수연도(예: 2025). 미상이면 None.
    court: str | None = None    # 법원명 힌트(입력에 섞여 온 경우)
    raw: str = ""               # 원본 입력(디버그·에코용)

    @property
    def canonical(self) -> str | None:
        """DB 저장 포맷과 동일한 표준형. year가 있어야 확정된다."""
        return f"{self.year}{CASE_MARK}{self.serial}" if self.year else None


def _plausible_year(n: int) -> bool:
    # 경매 사건 접수연도 현실 범위. 밖이면 연도가 아니라 일련번호로 본다.
    return 1990 <= n <= 2099


def parse_case_query(text: str) -> CaseQuery | None:
    """자유 입력 → CaseQuery. 파싱 불가면 None(호출부가 안내 메시지로 처리).

    허용 표기(전부 동일하게 환원):
      "2025-101763", "2025타경101763", "2025 101763", "2025.101763",
      "2025타경 101763", "101763"(연도 미상), "광주지방법원 2025-101763"(법원 힌트 포함),
      "2025타경101763 광주지방법원".
    규칙:
      - 법원명 패턴이 있으면 떼어내 court로 보관.
      - "타경"이 있으면 그 앞 4자리=연도, 뒤 숫자=일련번호.
      - 없으면 숫자 그룹으로 판단: 첫 그룹이 그럴듯한 연도(4자리)면 연도+마지막 그룹=일련번호,
        아니면 연도 미상 + (가장 긴/마지막) 숫자=일련번호.
    """
    if not text or not text.strip():
        return None
    raw = text.strip()
    s = raw

    court = None
    m = _COURT_RE.search(s)
    if m:
        court = m.group(0)
        s = (s[: m.start()] + " " + s[m.end():]).strip()

    year: int | None = None
    serial: int | None = None

    if CASE_MARK in s:
        left, right = s.split(CASE_MARK, 1)
        ld = _DIGITS_RE.findall(left)
        rd = _DIGITS_RE.findall(right)
        if ld and _plausible_year(int(ld[-1])):
            year = int(ld[-1])
        if rd:
            serial = int(rd[0])
    else:
        groups = [int(g) for g in _DIGITS_RE.findall(s)]
        if not groups:
            return None
        if len(groups) >= 2 and len(str(groups[0])) == 4 and _plausible_year(groups[0]):
            year = groups[0]
            serial = groups[-1]
        elif len(groups) == 1:
            g = groups[0]
            # 단일 4자리이면서 연도로도 보이면 모호 → 일련번호로 취급하되 연도 미상(사용자가 좁힘).
            serial = g
        else:
            # 여러 그룹인데 첫 그룹이 연도가 아님 → 가장 큰 그룹을 일련번호로.
            serial = max(groups)

    if not serial or serial <= 0:
        return None
    return CaseQuery(serial=serial, year=year, court=court, raw=raw)


# --- 홈 검색창 라우팅 판별 ---
# 단지명 검색("e편한세상2차")을 사건번호로 오인해 가로채지 않도록 **엄격하게** 판별한다.
_CASE_SHAPE_RES = (
    re.compile(r".*" + CASE_MARK + r".*\d"),          # "…타경…숫자"
    re.compile(r"^\s*\d{4}\s*[-.\s]\s*\d{2,}\s*$"),   # "2025-101763" / "2025 101763"
    re.compile(r"^\s*\d{4,}\s*$"),                     # 순수 4자리+ 숫자
)


def looks_like_case_no(text: str) -> bool:
    """홈 검색어가 사건번호 표기인지(엄격). 이름 검색을 가로채지 않기 위한 보수적 판별."""
    if not text:
        return False
    s = text.strip()
    return any(rgx.match(s) for rgx in _CASE_SHAPE_RES)


# --- 저장된 case_no 파싱(로컬 매칭용) ---
_STORED_RE = re.compile(r"^(\d{4})" + CASE_MARK + r"(\d+)$")


def split_stored(case_no: str) -> tuple[int, int] | None:
    """저장 포맷 'YYYY타경NNNNN' → (year, serial). 형식 밖이면 None."""
    m = _STORED_RE.match((case_no or "").strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def serial_of(case_no: str) -> int | None:
    parts = split_stored(case_no)
    return parts[1] if parts else None


def match_local(listings, q: CaseQuery) -> list:
    """로컬 스코어 목록에서 질의에 맞는 물건 전부.

    - canonical(연도+일련번호) 있으면 표준형 완전일치.
    - 연도 미상이면 일련번호만 일치(전 연도 후보) → 사용자 선택 유도.
    - court 힌트가 있으면 법원명으로 추가 필터(부분일치).
    같은 사건 다물건(복합PK)은 전부 반환 — 임의 1건을 고르지 않는다.
    """
    if q.canonical:
        out = [s for s in listings if getattr(s, "case_no", "") == q.canonical]
    else:
        out = [s for s in listings if serial_of(getattr(s, "case_no", "")) == q.serial]
    if q.court:
        cn = _norm_court(q.court)
        out = [s for s in out if cn in _norm_court(getattr(s, "court", ""))]
    return out


# --- 법원코드(cortOfcCd=boCd) ---
def _norm_court(name: str) -> str:
    return re.sub(r"\s+", "", (name or "").strip())


@lru_cache(maxsize=1)
def load_court_codes() -> dict[str, str]:
    """court_codes.json → {법원명: boCd}. 파일 없거나 깨지면 로그 후 빈 dict(라이브만 비활성)."""
    try:
        blob = json.loads(COURT_CODES_PATH.read_text(encoding="utf-8"))
        return dict(blob.get("codes") or {})
    except (OSError, json.JSONDecodeError, AttributeError) as e:
        logger.warning("court_codes.json 로드 실패 — 라이브 조회 비활성: %s", e)
        return {}


def list_courts() -> list[str]:
    """법원명 목록(정렬) — 라이브 조회 시 사용자 선택 UI용."""
    return sorted(load_court_codes())


def court_office_code(court_name: str) -> str | None:
    """법원명 → boCd. 완전일치 우선, 없으면 공백무시 부분일치(예: '광주' → '광주지방법원')."""
    if not court_name:
        return None
    codes = load_court_codes()
    if court_name in codes:
        return codes[court_name]
    target = _norm_court(court_name)
    if not target:
        return None
    # 부분일치 후보 — 유일할 때만 확정(모호하면 None → 호출부가 목록 제시).
    cands = [(k, v) for k, v in codes.items() if target in _norm_court(k) or _norm_court(k) in target]
    if len(cands) == 1:
        return cands[0][1]
    return None


# --- 라이브 단건 조회 ---
def build_case_view(case_rights, dma_result: dict) -> dict:
    """case_detail 응답(+정규화된 CaseRights)에서 화면용 사실만 추린다(과장 금지).

    상세 응답으로 확실한 것: 청구금액·권리요지·기일내역. 기일 가격이력에서 감정가·최저가·유찰수 유도.
    상세 응답에 없는 것: 주소·전용면적·단지명 → None으로 두고 '차익 채점 불가'로 표시한다.
    """
    sched = list(getattr(case_rights, "schedule", []) or [])
    priced = sorted((e for e in sched if (e.get("price") or 0) > 0), key=lambda e: e.get("ymd") or "")
    appraisal = priced[0]["price"] if priced else None      # 최초 회차 최저가 ≈ 감정가(근사)
    min_bid = priced[-1]["price"] if priced else None       # 가장 최근 회차 최저가 = 현 최저입찰가
    fail_count = sum(1 for e in sched if e.get("result") == "유찰")
    return {
        "court": getattr(case_rights, "court", ""),
        "case_no": getattr(case_rights, "case_no", ""),
        "court_dept": getattr(case_rights, "court_dept", ""),
        "appraisal_price": appraisal,
        "min_bid_price": min_bid,
        "fail_count": fail_count,
        "claim_amt": getattr(case_rights, "claim_amt", 0),
        "demand_end": getattr(case_rights, "demand_end", ""),
        "surviving_rights": getattr(case_rights, "surviving_rights", ""),
        "senior_lien": getattr(case_rights, "senior_lien", ""),
        "lien_note": getattr(case_rights, "lien_note", ""),
        "remark": getattr(case_rights, "remark", ""),
        "appraisal_notes": getattr(case_rights, "appraisal_notes", []),
        "schedule": sched,
        "scored": False,   # 라이브 단건은 시세매칭·차익채점 대상 아님(로컬 히트만 True)
    }


def live_lookup(q: CaseQuery, court_name: str | None = None, client=None,
                gds_seq: str = "1") -> dict:
    """대법원에서 사건을 직접 조회 → build_case_view dict.

    전제: 표준형(연도+일련번호)과 법원명이 필요(사건번호만으론 어느 법원인지 서버가 특정 못 함).
    안전장치는 CourtAuctionClient가 담당(지터·일일상한·kill-switch·차단감지).
    """
    court = court_name or q.court
    if not court:
        raise CaseSearchError("법원명이 필요합니다(사건번호만으로는 관할 법원을 특정할 수 없음).")
    if not q.canonical:
        raise CaseSearchError("접수연도가 필요합니다(예: '2025타경101763' 또는 '2025-101763').")
    code = court_office_code(court)
    if not code:
        raise CaseSearchError(f"법원코드를 찾을 수 없음: {court!r}. list_courts()에서 정확한 법원명 선택 필요.")

    from .courtauction_client import CourtAuctionClient  # noqa: PLC0415
    from .courtauction_detail import normalize  # noqa: PLC0415

    c = client or CourtAuctionClient()
    dma = c.case_detail(code, q.canonical, gds_seq=gds_seq)
    cr = normalize(dma, court=court, case_no=q.canonical, item_no=gds_seq)
    return build_case_view(cr, dma)
