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

import html
import json
import re
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

# 감정평가 요항점 항목코드(aeeWevlMnpntItmCd) → 라벨. 실측 pgj15B 응답에서 확인한 코드만
# 단정하고, 미상 코드는 '감정 요항'으로 폴백(원문 텍스트 자체가 자기설명적이라 라벨은 보조).
# courtauction 코드표 엔드포인트가 비공개(HTTP 500)라 하드코딩 — 필요 시 확장.
AEE_ITEM_LABELS: dict[str, str] = {
    "00083006": "이용상태",
    "00083015": "건물 구조",
    "00083017": "설비 내역",
    "00083018": "제시외 물건",
    "00083026": "기타 참고사항",
}


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


# 크롤 아티팩트 정제(2026-07-16) — 명세서/감정 요항 자유텍스트에 섞여 들어오는 제어문자·제로폭·
# BOM·NBSP/전각공백·연속 공백을 정리한다(개행은 보존). 마스킹 전에 적용해 마스커 정규식이
# 깨끗한 텍스트를 보게 한다. 상세 페이지에 '이상한 특수문자'로 노출되던 것을 없앤다.
_JUNK_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁠﻿]")


def _sanitize(text: str) -> str:
    """자유텍스트 정제: 제어문자·제로폭 제거, NBSP/전각공백→일반, 연속 수평공백 축약. 개행 보존."""
    if not text:
        return ""
    # 법원 API 자유텍스트는 HTML 엔티티가 섞여 온다(&quot; &lt; &gt; &amp; …). 자동이스케이프
    # 템플릿에 그대로 넣으면 &amp;quot; 처럼 깨져 보이므로 저장 전 실제 문자로 되돌린다.
    # 간혹 이중 인코딩(&amp;quot;)도 오므로 변화가 없을 때까지(최대 3회) 반복 해제한다.
    for _ in range(3):
        u = html.unescape(text)
        if u == text:
            break
        text = u
    t = _JUNK_RE.sub("", text).replace(" ", " ").replace("　", " ")
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def is_substantive(text: str | None) -> bool:
    """명세서 항목이 '실질 내용 있음'인지 — 해당없음/공백은 False.

    (서빙감사 2026-07-12 #22) startswith 판정은 '해당사항없음. 다만 을구5번 임차권 인수'처럼
    부정표기로 시작해도 뒤에 진짜 인수권리가 붙는 명세서를 통째로 무해(clean)로 삼키는 구조였다.
    → '전체 텍스트가 부정표기와 같을 때만' 무해. 부정표기 뒤에 내용이 이어지면 실질 내용으로 본다.
    """
    t = (text or "").replace(" ", "").strip()
    if not t:
        return False
    for m in _NONE_MARKS:
        mm = m.replace(" ", "")
        if t == mm or t.rstrip(".·,") == mm:   # 전체가 '해당사항없음'(+마침표)뿐이면 무해
            return False
    return True


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
    appraisal_notes: list[dict] = field(default_factory=list)  # 감정 요항 [{label,text}]
    fetched_at: str = ""

    @property
    def has_risk_text(self) -> bool:
        """인수 권리 또는 유치권류에 실질 문구가 있으면 True(상세 배너 강조용)."""
        return is_substantive(self.surviving_rights) or is_substantive(self.lien_note)

    @property
    def surviving_is_real(self) -> bool:
        """인수되는 권리란에 실질 문구가 있는지 — 템플릿 ⛔ 배너 분기와 배지 판정 통일(#22)."""
        return is_substantive(self.surviving_rights)

    @property
    def senior_jeonse(self) -> bool:
        """최선순위 설정이 전세권인지(서빙감사 #14) — 배당요구 여부에 따라 인수 갈리는 특수사례."""
        return "전세권" in (self.senior_lien or "")

    @property
    def is_empty(self) -> bool:
        """명세서 실체 신호가 전무한(빈/부분 응답) 요지 — clean 으로 오판하면 안 된다.

        (재검증 감사 2026-07-11 idx17) 빈 pgj15B 응답이 badge=clean('낙찰 후 추가 인수
        없음')으로 표시되는 것은 '애매하면 burden' 보수 원칙과 모순 — 작성일도 최선순위도
        없는 요지는 판정 근거가 없으므로 저장·판정 대상에서 제외한다.
        """
        return not (self.spec_write_ymd or self.senior_lien.strip()
                    or self.surviving_rights.strip() or self.lien_note.strip())

    @property
    def opposability_assessable(self) -> bool:
        """대항력/인수 임차인을 판정할 **근거 텍스트**가 하나라도 있는지.

        (2026-07-22 대항력 false-negative 수정) 우리가 쓰는 물건상세 엔드포인트(PGJ151F01)는
        임차인 전입일/보증금 표를 담지 않는다 — 대항력 신호는 오직 자유기술란
        (surviving_rights=인수되는 권리 / lien_note=유치권 등 / remark=비고)에만 들어온다.
        이 세 칸이 전부 비면(실 DB의 19%) 대항력 여부를 **판정할 근거가 아예 없다**.
        그런데 spec_write_ymd·senior_lien(말소기준)만 있으면 is_empty=False 라서 종전엔
        rights_verified=True → '대항력 임차인 발견 안 됨' 초록으로 오표시됐다(부산 2022타경3289
        삼환아파트: 세 칸 전부 null인데 초록 추천 → 실제론 대항력 임차인 존재).
        근거 텍스트가 없으면 '없음'이 아니라 '모름'으로 남겨 오판을 막는다(보수 원칙과 정합).
        """
        return bool((self.surviving_rights or "").strip()
                    or (self.lien_note or "").strip()
                    or (self.remark or "").strip())

    def to_row(self) -> dict:
        d = asdict(self)
        d["schedule"] = json.dumps(self.schedule, ensure_ascii=False)
        d["appraisal_notes"] = json.dumps(self.appraisal_notes, ensure_ascii=False)
        return d

    @classmethod
    def from_row(cls, row: dict) -> CaseRights:
        d = dict(row)
        for jkey in ("schedule", "appraisal_notes"):
            v = d.get(jkey)
            if isinstance(v, str):
                try:
                    d[jkey] = json.loads(v) if v else []
                except json.JSONDecodeError:
                    d[jkey] = []
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 대항력 분석 (2026-07-13) — 임차인 전입일 vs 말소기준일 비교로 '왜 인수인가' 근거 명시.
#   전입일 ≤ 말소기준일 → 대항력 있음(매수인 인수) · 전입일 > 말소기준일 → 대항력 없음(소멸).
#   법원의 '인수되는 권리(surviving_rights)' 필드가 이미 대항력 판정 결과이므로, 여기서는
#   그 판정의 근거(날짜 비교)를 투명하게 보여주고, 날짜가 판정과 어긋나는 모순건을 검출한다.
# ---------------------------------------------------------------------------
_DATE_RE = re.compile(r"(\d{4})\s*[.\-년]\s*(\d{1,2})\s*[.\-월]\s*(\d{1,2})")
# (감사 2026-07-20 C3) 법원 표준 라벨은 "전입신고일자" — 종전 정규식은 '전입' 뒤 '일/자'만 허용해
# '신고'가 끼면 미매치 → 대항력 근거분석이 실데이터에서 사실상 무력화됐다. '신고'·'세대' 변형 허용.
_MOVEIN_RE = re.compile(r"전입\s*(?:신고)?\s*(?:세대|일)?\s*자?\s*[:\-]?\s*"
                        r"(\d{4}\s*[.\-년]\s*\d{1,2}\s*[.\-월]\s*\d{1,2})")
# 말소기준권리 유형 — 앞선 것이 말소기준(담보물권·압류류·경매개시).
_SENIOR_TYPES = ("근저당권", "근저당", "저당권", "전세권", "담보가등기",
                 "가압류", "압류", "경매개시결정", "경매개시", "임차권등기")


def _to_ymd(m) -> tuple[int, int, int] | None:
    try:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except (TypeError, ValueError, IndexError):
        return None


def _first_date(text: str) -> tuple[tuple[int, int, int], str] | None:
    """텍스트의 첫 날짜(YYYY,M,D) 튜플 + 'YYYY-MM-DD' 표기. 없으면 None."""
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    ymd = _to_ymd(m)
    return (ymd, f"{ymd[0]}-{ymd[1]:02d}-{ymd[2]:02d}") if ymd else None


@dataclass
class PriorityAnalysis:
    """대항력 판정 근거 — 상세 페이지에 '왜 인수/소멸인가'를 날짜로 보여준다.

    verdict:
      confirmed_opposable   — 전입 ≤ 말소기준 → 대항력 있음(법원 인수 판정과 일치, 근거 명확)
      not_opposable         — 전입 > 말소기준 → 대항력 없음(낙찰로 소멸, 명세서 인수 문구 없음)
      contradiction         — 전입 > 말소기준인데 명세서는 인수(임차권등기명령 등 특수·파싱오류 검증대상)
      dates_incomplete      — 말소기준은 있으나 전입일 미기재(법원 판정만 신뢰 + 등기부 확인 권고)
      no_basis              — 판정할 날짜가 없음

    movein_source: '현황조사서'(임차인 전입일 실데이터) | '명세서'(요지 자유텍스트 스캔) | ''.
    """
    senior_type: str = ""       # 말소기준 유형(근저당/전세권/압류 …)
    senior_date: str = ""       # 말소기준일 YYYY-MM-DD
    movein_date: str = ""       # 임차인 전입일 YYYY-MM-DD
    movein_source: str = ""     # 전입일 출처(현황조사서=실판정 / 명세서=요지스캔)
    verdict: str = "no_basis"
    note: str = ""


def analyze_priority(r: CaseRights,
                     tenant_moveins: list[str] | None = None) -> PriorityAnalysis:
    """CaseRights → 대항력 판정 근거.

    말소기준일 = senior_lien 의 첫 날짜. 임차인 전입일 우선순위:
      1) tenant_moveins(현황조사서 selectCurstExmndc 의 **임차인** 전입일 실데이터) — 있으면 최우선.
         ★ 호출측이 반드시 '임차인(is_tenant_like)'만 걸러서 넘길 것 — 소유자 전입일을 넘기면
           대항력 없는 물건을 있음으로 오판(false-positive)한다. 대항력은 임차인에게만 성립.
      2) 없으면 명세서 요지 자유텍스트에서 '전입' 라벨 날짜 스캔(구경로, 사실상 빈 요지엔 무력).
    실데이터(1)가 있으면 명세서 요지가 비어 있어도 날짜비교로 직접 대항력을 판정한다.
    """
    # 말소기준: senior_lien 의 첫 날짜 + 그 근처 유형 키워드
    senior = _first_date(r.senior_lien or "")
    senior_type = ""
    if senior:
        for t in _SENIOR_TYPES:
            if t in (r.senior_lien or ""):
                senior_type = t
                break

    movein = None
    movein_source = ""
    if tenant_moveins:
        parsed = [d for d in (_first_date(str(x)) for x in tenant_moveins) if d]
        if parsed:
            movein = min(parsed, key=lambda x: x[0])   # 가장 이른 전입 = 대항력 최강
            movein_source = "현황조사서"
    if movein is None:
        # 구경로: 명세서 요지 자유텍스트에서 '전입' 라벨 날짜 스캔
        text = f"{r.surviving_rights}\n{r.remark}"
        moveins = [d for m in _MOVEIN_RE.finditer(text)
                   if (d := _first_date(m.group(1)))]
        if moveins:
            movein = min(moveins, key=lambda x: x[0])
            movein_source = "명세서"

    a = PriorityAnalysis(
        senior_type=senior_type,
        senior_date=senior[1] if senior else "",
        movein_date=movein[1] if movein else "",
        movein_source=movein_source,
    )
    has_burden_text = is_substantive(r.surviving_rights)
    src_tag = "현황조사서 전입일" if movein_source == "현황조사서" else "명세서 판정"
    if movein and senior:
        if movein[0] <= senior[0]:
            a.verdict = "confirmed_opposable"
            a.note = (f"임차인 전입({a.movein_date})이 말소기준"
                      f"({a.senior_type or '최선순위'} {a.senior_date})보다 앞서 대항력 있음 → "
                      f"배당 부족분은 매수인 인수({src_tag} 기준). 확정일자·보증금은 최종 확인 요망.")
        elif has_burden_text:
            # 명세서 요지가 '인수'라는데 날짜는 대항력 없음 → 임차권등기명령 등 특수사유·파싱오류 검증대상.
            a.verdict = "contradiction"
            a.note = (f"임차인 전입({a.movein_date})이 말소기준({a.senior_date})보다 늦어 "
                      f"통상 대항력 없으나, 명세서 요지는 인수로 기재됨 — 임차권등기명령 등 특수사유·"
                      f"표기차 가능. 등기부·명세서 전문으로 반드시 확인하세요.")
        else:
            # 실전입일(현황조사서 등)이 말소기준보다 늦고 명세서 인수 문구도 없음 → 대항력 없음(소멸).
            a.verdict = "not_opposable"
            a.note = (f"임차인 전입({a.movein_date})이 말소기준({a.senior_type or '최선순위'} "
                      f"{a.senior_date})보다 늦어 대항력 없음 — 낙찰로 소멸({src_tag} 기준). "
                      f"확정일자·배당요구는 최종 확인 요망.")
    elif senior and has_burden_text:
        a.verdict = "dates_incomplete"
        a.note = (f"말소기준은 {a.senior_type or '최선순위'} {a.senior_date}이나 명세서 요지에 "
                  f"임차인 전입일이 없습니다. 법원은 '인수'로 판정 — 전입일·확정일은 등기부·"
                  f"현황조사서로 확인하세요.")
    else:
        a.verdict = "no_basis"
    return a


# ---------------------------------------------------------------------------
# 현황조사서(selectCurstExmndc) 파서 — 임차인 전입일·점유의 유일한 구조화 원천 (2026-07-22)
# ---------------------------------------------------------------------------
# 매각물건명세서 요지엔 임차인 전입일이 없다. 이 파서로 대항력 '실판정'의 재료를 확보한다.
# ⚠️ PII 절대 미수집: 성명·주민번호(ENRRNO)·기본주소(basAddr)·상세주소(objctDtlAddr) 는 읽지 않는다.
#   전입일·확정일자·보증금(금액)·점유유형만 뽑는다. 대항력은 임차인에게만 성립하므로 소유자 전입과
#   구분하기 위해 is_tenant_like(보증금>0·확정일자·임차용도·임차부분 중 하나라도 있음) 를 남긴다.
_CURST_PII_KEYS = ("ENRRNO", "ZPCD", "basAddr", "objctDtlAddr")  # 명시적 배제 목록(읽지 않음)


def _curst_deposit_won(s: str) -> int:
    """임차보증금 텍스트('금50,000,000원'·'5,000만원'·'50000000') → 원. 실패 0."""
    from .courtauction_rights import _korean_won  # noqa: PLC0415 — 순환 import 회피
    if not s:
        return 0
    t = re.sub(r"[금원\s]", "", str(s))     # 라벨·단위 문자 제거 후 억/만/숫자 파싱
    return _korean_won(t)


def parse_curst_survey(data: dict) -> list[dict]:
    """현황조사서 응답 dict → 임차인 레코드 리스트(PII 제외).

    각 레코드: {movein_ymd, confirm_ymd, deposit, possession, usage, part, is_tenant_like}.
    ipcheck=false / 리스트 없음 / 빈 물건이면 [](예외 아님 — '임차인 없음/미상').
    """
    if not isinstance(data, dict):
        return []
    res = data.get("result") if isinstance(data.get("result"), dict) else data
    rows = res.get("dlt_ordTsLserLtn") if isinstance(res, dict) else None
    out: list[dict] = []
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        mv = _first_date(str(r.get("mvinDtlCtt") or ""))
        cf = _first_date(str(r.get("rgstryCrtcpCfmtnCtt") or ""))
        deposit = _curst_deposit_won(str(r.get("lesDposDts") or ""))
        # 용도·임차부분·점유내용은 구조 필드(주거/전부/임차인 점유 등)라 성명이 없다 — 마스킹하면
        # '점유' 같은 단어를 이름으로 오탐해 훼손한다. 성명·주민번호는 애초에 다른 필드라 안 읽는다.
        usage = (r.get("lesUsgDts") or "").strip()
        part = (r.get("lesPartCtt") or "").strip()
        possession = (r.get("gdsPossCtt") or "").strip()
        is_tenant_like = bool(deposit > 0 or cf or usage or part)
        out.append({
            "movein_ymd": mv[1] if mv else "",
            "confirm_ymd": cf[1] if cf else "",
            "deposit": deposit,
            "possession": possession,
            "usage": usage,
            "part": part,
            "is_tenant_like": is_tenant_like,
        })
    return out


def tenant_moveins(tenants: list[dict]) -> list[str]:
    """임차인 레코드 중 **is_tenant_like** 이고 전입일이 있는 것들의 전입일 리스트.

    analyze_priority(tenant_moveins=...) 에 넘길 값 — 소유자 전입(is_tenant_like=False)은 제외해
    대항력 false-positive 를 막는다."""
    return [t["movein_ymd"] for t in (tenants or [])
            if t.get("is_tenant_like") and t.get("movein_ymd")]


def opposable_deposit(tenants: list[dict], senior_ymd: str) -> int:
    """말소기준일 **이전(≤)** 전입한 대항력 임차인들의 보증금 합(원) — 매수인 인수 상한 추정.

    말소기준일 파싱 실패거나 대항력 임차인 없으면 0. is_tenant_like 만 집계(소유자 제외)."""
    sd = _first_date(senior_ymd or "")
    if not sd:
        return 0
    total = 0
    for t in (tenants or []):
        if not t.get("is_tenant_like") or not t.get("movein_ymd"):
            continue
        mv = _first_date(str(t["movein_ymd"]))
        if mv and mv[0] <= sd[0]:
            total += int(t.get("deposit") or 0)
    return total


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


# 지분매각 — 법원이 '지분매각'/'지분경매'로 분류한 물건(비고·명세서). 통물건 시세로 과대평가되는
# 지분물건을 차익 추천에서 제외하기 위한 신호. 'N분의 M' 단순 언급이나 '지분(2분의1) 매각'의
# 괄호삽입형은 배제하고 법원 분류어(연속된 지분매각/지분경매)만 잡아 통물건 오배제를 막는다.
_SHARE_SALE_RE = re.compile(r"지분\s*(?:매각|경매)")


def summarize(rights: CaseRights) -> RightsBadge:
    """CaseRights → 인수 부담 판정. 판정 규칙은 기존 명세서 파서(courtauction_rights)를 재사용.

    보수 원칙: 애매하면 burden 쪽(사용자가 함정 물건을 '깨끗'으로 오독하는 침묵실패 방지).
    (서빙감사 2026-07-12) #14 최선순위 전세권도 burden, #9 금액미상이면 surviving_rights 의
    보증금액을 파싱해 assumed 로 반영(랭킹 차감 가능하게).
    """
    from .courtauction_rights import (  # noqa: PLC0415 — 순환 import 회피(지연)
        _strip_negated_clauses,
        detect_assumed_amount,
        detect_deposit_amount,
        detect_special_rights,
        detect_tenant_opposable,
    )
    opposable = detect_tenant_opposable(rights.surviving_rights, rights.remark)
    assumed = detect_assumed_amount(rights.surviving_rights, rights.remark)
    special = list(detect_special_rights(rights.surviving_rights, rights.lien_note, rights.remark))
    jeonse = rights.senior_jeonse
    if jeonse and "선순위전세권" not in special:
        special.append("선순위전세권")
    # (감사 2026-07-15) 지분매각 하드게이트 라벨 — 통물건 시세로 과대평가되는 지분물건(실측 376건)을
    # 차익 추천에서 제외한다. 법원 분류어(지분매각/지분경매)만 잡아 'N분의M' 단순 언급 오배제를 막는다.
    if (_SHARE_SALE_RE.search(f"{rights.remark}\n{rights.surviving_rights}\n{rights.lien_note}")
            and "지분매각" not in special):
        special.append("지분매각")
    # (감사 2026-07-20 M1) has_risk_text(원문 실질텍스트 존재)를 그대로 burden 근거로 쓰면
    # '임차권등기(다만 말소동의 확약서 제출됨)'처럼 **소멸 예정** 권리도 burden으로 오판돼 실제로는
    # clean인 물건이 차익추천서 빠졌다(실측 86건). 부정절(말소동의·인수하지 아니 등)을 제거한 뒤에도
    # 실질 텍스트가 남을 때만 위험으로 본다 — opposable/assumed/special은 이미 부정절 인식하므로 정합.
    unresolved_text = (is_substantive(_strip_negated_clauses(rights.surviving_rights))
                       or is_substantive(_strip_negated_clauses(rights.lien_note)))
    burden = (opposable or assumed > 0 or bool(special) or unresolved_text or jeonse)
    # (#9) 인수 부담인데 금액 미상(+α)이면 명세서 원문의 보증금액을 보수 추정으로 채택 —
    # 임차권 미소멸(보증금 잔액 인수) 물건이 무차감으로 랭킹 상위를 점하지 않게 한다.
    if burden and assumed <= 0:
        dep = detect_deposit_amount(rights.surviving_rights, rights.remark)
        if dep > 0:
            assumed = dep
    return RightsBadge(status="burden" if burden else "clean",
                       opposable=opposable, assumed=assumed, special=special)


def extract_photos(dma_result: dict, cap: int = 3) -> list[str]:
    """pgj15B csPicLst 에서 현장 물건사진 base64(picFile) 상위 cap장 추출(리사이즈 전 원본).

    picFile 이 있는 항목만(구분코드 무관 — 실측상 물건사진), cortAuctnPicSeq 순서 유지.
    저장부(crawl)가 photo.thumbnail_jpeg 로 축소한다. 서빙엔 쓰지 않는다(용량).
    """
    out: list[str] = []
    for p in (dma_result.get("csPicLst") or []):
        b64 = p.get("picFile")
        if b64:
            out.append(b64)
        if len(out) >= cap:
            break
    return out


# (H4 필드 카나리 2026-07-22) 법원이 응답 필드명을 바꾸거나 섹션을 빼면, normalize 는 조용히
# 빈 값을 채우고 그 물건은 '치명적 인수권리 미발견'으로 오표시된다(침묵실패). 값이 비어있는 것
# (정상)과 **필드명 자체가 사라진 것**(스키마 드리프트)을 구분해 후자만 경보한다. 값 유무가 아니라
# '키 이름의 존재'를 본다 — 삼환처럼 ndstrcRghCtt=null 은 정상(키는 있음)이라 드리프트 아님.
_DETAIL_CORE_KEYS = ("ndstrcRghCtt", "tprtyRnkHypthcStngDts", "sprfcExstcDts")


def detail_schema_drift(dma_result: dict) -> str:
    """물건상세 응답의 스키마 드리프트 사유(있으면). 정상이면 ''.

    - dspslGdsDxdyInfo(명세서 요지) 섹션 자체가 없음 → 드리프트.
    - 섹션은 있으나 인수권리·최선순위·유치권 **핵심 필드명이 모두 사라짐** → 드리프트.
    크롤러가 이 사유를 집계해 드리프트율이 높으면 '스키마 변경/차단'으로 비정상 종료(exit code).
    """
    if not isinstance(dma_result, dict):
        return "dma_result 아님"
    gds = dma_result.get("dspslGdsDxdyInfo")
    if not isinstance(gds, dict):
        return "dspslGdsDxdyInfo(명세서 요지) 섹션 없음"
    if not any(k in gds for k in _DETAIL_CORE_KEYS):
        return "명세서 요지 핵심 필드명 전무(ndstrcRghCtt/tprtyRnkHypthcStngDts/sprfcExstcDts)"
    return ""


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

    # (감사 2026-07-15) 개인정보 마스킹 — 명세서 자유텍스트에는 유치권신고인·임차인 **실명**이
    # 그대로 들어온다("유치권신고인 홍길동로부터 공사대금채권…"). listing_rights 는 Supabase 로
    # 미러돼 상세 페이지로 서빙되므로 리스트(mulBigo)와 동일 기준으로 저장 전에 마스킹한다.
    # (2026-07-22 C1) senior_lien(최선순위=근저당권자/가압류권자/공유자 어순)·감정요항에도 실명이
    # 무마스킹으로 새어 서빙되던 것 수정 — 형제 필드와 동일하게 마스킹한다. 법인명(신한은행 등)은
    # 마스커의 법인 접미사 가드가 보존한다. 날짜·금액·라벨은 마스커가 안 건드린다.
    from .courtauction_fields import mask_personal_names  # noqa: PLC0415 — 순환 import 회피

    # 감정평가 요항점 — 감정사 원문(이용상태·구조·설비 등)에 소유자·임차인 실명이 섞여 온다.
    appraisal_notes = []
    for a in (dma_result.get("aeeWevlMnpntLst") or []):
        text = mask_personal_names(_sanitize(a.get("aeeWevlMnpntCtt") or ""))
        if not text or text in ("-", "–"):
            continue
        code = str(a.get("aeeWevlMnpntItmCd") or "")
        appraisal_notes.append({"label": AEE_ITEM_LABELS.get(code, "감정 요항"), "text": text})

    return CaseRights(
        court=court, case_no=case_no or (base.get("userCsNo") or ""), item_no=str(item_no or ""),
        surviving_rights=mask_personal_names(_sanitize(gds.get("ndstrcRghCtt") or "")),
        senior_lien=mask_personal_names(_sanitize(gds.get("tprtyRnkHypthcStngDts") or "")),
        lien_note=mask_personal_names(_sanitize(gds.get("sprfcExstcDts") or "")),
        remark=mask_personal_names(_sanitize("\n".join(remark_parts))),
        claim_amt=_int(base.get("clmAmt")),
        demand_end=_ymd((demn[0] or {}).get("dstrtDemnLstprdYmd") if demn else ""),
        spec_write_ymd=_ymd(gds.get("gdsSpcfcWrtYmd")),
        court_dept=(base.get("cortAuctnJdbnNm") or "").strip(),
        schedule=schedule,
        appraisal_notes=appraisal_notes,
        fetched_at=fetched_at,
    )
