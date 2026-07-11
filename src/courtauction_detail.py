"""courtauction 물건상세(pgj15B) 응답 → 권리·기일 요지 정규화.

사용자 요구("왜 유찰 10회인지 권리 내역이 딱 보이게")의 원천 데이터 계층.
클라이언트(case_detail)가 받아온 dma_result dict에서 상세 페이지가 그릴 것만 뽑는다:

  - 매각물건명세서 요지: 인수되는 권리(ndstrcRghCtt) / 최선순위 설정 = 말소기준
    (tprtyRnkHypthcStngDts) / 유치권·법정지상권 등(sprfcExstcDts) / 비고
  - 사건: 청구금액(clmAmt) / 배당요구종기(dstrtDemnLstprdYmd) / 경매계
  - 기일 역사(gdsDspslDxdyLst): 회차별 날짜·최저가·결과(유찰/매각/변경…)

주의: 명세서 문구는 법원이 대국민 공개한 원문 요지를 그대로 담는다(성명이 포함될 수 있음
— 법원 공개 범위 내). 파생 판정(대항력 등)은 기존 courtauction_rights 파서를 재사용한다.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

# 공식 코드표 (실측: /pgj/scframe/lib/sccd/list.on — LJH-AUCTN_DXDY_RSLT_CD / PGJ-AUCTN_DXDY_KND_CD)
DXDY_RESULT: dict[str, str] = {
    "000": "매각준비", "001": "매각", "002": "유찰", "003": "최고가매각허가결정",
    "004": "차순위매각허가결정", "005": "최고가매각불허가결정", "006": "차순위매각불허가결정",
    "007": "기한변경", "008": "추후지정", "009": "납부", "010": "미납",
    "011": "기한후납부", "012": "상계허가", "013": "진행", "014": "변경",
    "015": "배당종결", "016": "배당불가", "017": "최고가매각허가취소결정",
    "018": "차순위매각허가취소결정",
}
DXDY_KIND: dict[str, str] = {
    "01": "매각기일", "02": "매각결정기일", "03": "대금지급기한", "04": "대금지급및배당기일",
    "05": "배당기일", "06": "일부배당", "07": "일부배당및상계", "08": "심문기일",
    "09": "추가배당기일", "11": "개찰기일",
}

# 명세서 '해당 없음' 계열 표기 — 위험 아님으로 표시 정리용(원문은 그대로 보존).
_NONE_MARKS = ("해당사항없음", "해당사항 없음", "해당없음", "없음")


def _ymd(v: str | None) -> str:
    """'20260715' → '2026-07-15' (비정형은 원문 유지)."""
    s = (v or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _int(v) -> int | None:
    try:
        n = int(str(v).replace(",", "").strip())
        return n
    except (TypeError, ValueError):
        return None


def is_substantive(text: str | None) -> bool:
    """명세서 항목이 '실질 내용 있음'인지 — 해당없음/공백은 False."""
    t = (text or "").strip()
    if not t:
        return False
    return not any(t.replace(" ", "").startswith(m.replace(" ", "")) for m in _NONE_MARKS)


@dataclass
class CaseRights:
    """물건상세에서 뽑은 권리·기일 요지 — 저장(listing_rights)·렌더 단위."""
    court: str = ""
    case_no: str = ""            # 사용자 포맷(2025타경669)
    item_no: str = ""            # dspslGdsSeq
    surviving_rights: str = ""   # 인수되는 권리(ndstrcRghCtt) — ⛔ 핵심 위험
    senior_lien: str = ""        # 최선순위 설정 내역(tprtyRnkHypthcStngDts) — 말소기준
    lien_note: str = ""          # 유치권·법정지상권 등(sprfcExstcDts)
    remark: str = ""             # 명세서 비고(gdsSpcfcRmk + dspslGdsRmk)
    claim_amt: int | None = None         # 청구금액(원)
    demand_end: str = ""                 # 배당요구종기(YYYY-MM-DD)
    spec_write_ymd: str = ""             # 명세서 작성일
    court_dept: str = ""                 # 담당 경매계
    schedule: list[dict] = field(default_factory=list)  # [{ymd,kind,result,price}] 최신순
    fetched_at: str = ""

    @property
    def has_risk_text(self) -> bool:
        """인수 권리 또는 유치권류에 실질 문구가 있으면 True(상세 배너 강조용)."""
        return is_substantive(self.surviving_rights) or is_substantive(self.lien_note)

    @property
    def is_empty(self) -> bool:
        """명세서 실체 신호가 전무한(빈/부분 응답) 요지 — clean 으로 오판하면 안 된다.

        (재검증 감사 2026-07-11 idx17) 빈 pgj15B 응답이 badge=clean('낙찰 후 추가 인수
        없음')으로 표시되는 것은 '애매하면 burden' 보수 원칙과 모순 — 작성일도 최선순위도
        없는 요지는 판정 근거가 없으므로 저장·판정 대상에서 제외한다.
        """
        return not (self.spec_write_ymd or self.senior_lien.strip()
                    or self.surviving_rights.strip() or self.lien_note.strip())

    def to_row(self) -> dict:
        d = asdict(self)
        d["schedule"] = json.dumps(self.schedule, ensure_ascii=False)
        return d

    @classmethod
    def from_row(cls, row: dict) -> "CaseRights":
        d = dict(row)
        sched = d.get("schedule")
        if isinstance(sched, str):
            try:
                d["schedule"] = json.loads(sched) if sched else []
            except json.JSONDecodeError:
                d["schedule"] = []
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})  # type: ignore[arg-type]


@dataclass
class RightsBadge:
    """목록/상세 공용 '인수 부담' 판정 — 낙찰가 외 추가로 낼 돈이 있는가.

    status:
      clean   = 명세서 확인 결과 인수 권리·대항력 신호 없음("낙찰만 하면 추가 인수 없음")
      burden  = 인수 신호 있음. assumed>0 이면 금액 반영, 0이면 금액 미상(+α)
      (미크롤 물건은 badge 자체가 None — '미확인'으로 렌더)
    """
    status: str                     # clean | burden
    opposable: bool = False         # 대항력 임차인/임차권등기 신호
    assumed: int = 0                # 파싱된 인수금액(원). 0=미상 또는 없음
    special: list[str] = field(default_factory=list)   # 유치권 등 특수권리 라벨

    @property
    def is_clean(self) -> bool:
        return self.status == "clean"

    @property
    def amount_unknown(self) -> bool:
        """부담은 있는데 금액을 명세서 요지에서 못 읽은 경우(+α 표기)."""
        return self.status == "burden" and self.assumed <= 0


def summarize(rights: CaseRights) -> RightsBadge:
    """CaseRights → 인수 부담 판정. 판정 규칙은 기존 명세서 파서(courtauction_rights)를 재사용.

    보수 원칙: 애매하면 burden 쪽(사용자가 함정 물건을 '깨끗'으로 오독하는 침묵실패 방지).
    """
    from .courtauction_rights import (  # noqa: PLC0415 — 순환 import 회피(지연)
        detect_assumed_amount,
        detect_special_rights,
        detect_tenant_opposable,
    )
    opposable = detect_tenant_opposable(rights.surviving_rights, rights.remark)
    assumed = detect_assumed_amount(rights.surviving_rights, rights.remark)
    special = detect_special_rights(rights.surviving_rights, rights.lien_note, rights.remark)
    burden = opposable or assumed > 0 or bool(special) or rights.has_risk_text
    return RightsBadge(status="burden" if burden else "clean",
                       opposable=opposable, assumed=assumed, special=special)


def normalize(dma_result: dict, court: str = "", case_no: str = "",
              item_no: str = "", fetched_at: str = "") -> CaseRights:
    """pgj15B dma_result → CaseRights. 누락 섹션은 빈 값(부분 응답도 수용)."""
    base = dma_result.get("csBaseInfo") or {}
    gds = dma_result.get("dspslGdsDxdyInfo") or {}
    demn = dma_result.get("dstrtDemnInfo") or []

    remark_parts = [t for t in (gds.get("gdsSpcfcRmk"), gds.get("dspslGdsRmk")) if t]
    schedule = []
    for r in (dma_result.get("gdsDspslDxdyLst") or []):
        schedule.append({
            "ymd": _ymd(r.get("dxdyYmd")),
            "kind": DXDY_KIND.get(str(r.get("auctnDxdyKndCd") or ""), str(r.get("auctnDxdyKndCd") or "")),
            "result": DXDY_RESULT.get(str(r.get("auctnDxdyRsltCd") or ""), str(r.get("auctnDxdyRsltCd") or "")),
            "price": _int(r.get("tsLwsDspslPrc")),
        })
    # 최신이 위로 오게(내림차순) — 상세 테이블 렌더 순서
    schedule.sort(key=lambda x: x["ymd"], reverse=True)

    return CaseRights(
        court=court, case_no=case_no or (base.get("userCsNo") or ""), item_no=str(item_no or ""),
        surviving_rights=(gds.get("ndstrcRghCtt") or "").strip(),
        senior_lien=(gds.get("tprtyRnkHypthcStngDts") or "").strip(),
        lien_note=(gds.get("sprfcExstcDts") or "").strip(),
        remark="\n".join(remark_parts).strip(),
        claim_amt=_int(base.get("clmAmt")),
        demand_end=_ymd((demn[0] or {}).get("dstrtDemnLstprdYmd") if demn else ""),
        spec_write_ymd=_ymd(gds.get("gdsSpcfcWrtYmd")),
        court_dept=(base.get("cortAuctnJdbnNm") or "").strip(),
        schedule=schedule,
        fetched_at=fetched_at,
    )
