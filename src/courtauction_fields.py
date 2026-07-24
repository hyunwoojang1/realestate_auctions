"""courtauction(대법원 법원경매) 검색응답 필드 카탈로그 · 레코드 · 개인정보 가드.

물건 검색 API(`searchControllerMain.on`)의 한 행은 117개 필드를 담는다.
이 모듈은 **개인정보보호법에 저촉되는 자연인 식별정보만 제외하고 나머지 전부**를
구조화해 보존한다(`CourtAuctionRecord.raw` = 정제된 원본 전체, 명시 필드 = 사용 편의 레이어).

설계 원칙:
- 리스트 검색 응답에는 채무자/소유자/임차인 등 개인식별정보가 **없다**(실측 확인).
  그래도 추후 물건상세(PGJ15BM01) 확장 시를 대비해 PII 차단 가드를 미리 둔다(현재는 무동작).
- `tel`은 경매계(법원 부서) 대표전화 = 공공기관 연락처이므로 개인정보 아님 → 보존.
- 좌표: `xCordi/yCordi`는 한국 투영좌표(정밀), `wgs84Xcordi/Ycordi`는 정수부만 잘려와 무의미.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .models import AuctionListing

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. 개인정보 가드 — 자연인 식별정보로 의심되는 필드/패턴은 저장 전 제거
# ---------------------------------------------------------------------------
# 리스트 응답엔 해당 없음. 물건상세(PGJ15BM01) 확장 시 채무자/소유자/임차인 필드가 들어오면 차단.
PERSONAL_INFO_KEYS: frozenset[str] = frozenset({
    "dpryNm", "dpryNmNm", "ownrNm", "owner", "ownerNm", "debtorNm", "debtor",
    "lessee", "lesseeNm", "tenantNm", "creditorNm", "obligorNm", "applicantNm",
    "rrn", "juminNo", "rsdnRegNo",
})
# 키 이름에 아래 토큰이 포함되면 개인정보로 간주(보수적, 자연인 역할 키 위주).
# 'nm' 단독은 기관/물건명(jiwonNm·jpDeptNm·dspslUsgNm·buldNm)과 충돌하므로 넣지 않는다.
# 'tel'(담당계 전화)은 기관 연락처라 제외 대상 아님(개인정보보호법 제2조: 특정 자연인 식별정보 아님).
_PII_NAME_TOKENS = (
    "jumin", "rrn", "rsdnreg",                       # 주민등록번호
    "owner", "ownr", "dpry", "debtor", "obligor",    # 소유자/채무자/(의무자)
    "lessee", "tenant",                              # 임차인
    "creditor", "applicant",                         # 채권자/신청인 (상세 API 출현 가능)
)

# 자유텍스트 필드: 한국어 성명이 문맥과 함께 들어올 수 있어 마스킹 대상.
# (감사 2026-07-15) maejibun(매각지분) 누락 → 채무자·공유자 실명 1,870행이 무방비 저장돼 있었다.
# (2026-07-22 C1) convAddr(정제 소재지) 추가 — 지분물건에서 "…120-10 채무자 홍길동 지분" 같은
# 실명이 raw_listings 에 무마스킹 저장돼 주소 폴백으로 노출될 수 있었다(실측 ~1,336행).
_FREE_TEXT_FIELDS = frozenset({"mulBigo", "alias", "maejibun", "convAddr"})

# 패턴 1 — 역할라벨이 앞: "채무자 홍길동", "유치권신고인 홍길동".
# ⚠ 정규식 교대(|)는 왼쪽 우선이라 **긴 역할어를 먼저** 둬야 한다 — '임차권자'가 앞서면
#   '주택임차권자 홍길동'에서 '주택'만 남고 매칭이 어긋난다.
#   group3 = 뒤따르는 쉼표 나열("…, 박화란, 김철수") — 같은 역할의 추가 성명.
# (2026-07-22 C1) senior_lien(최선순위 설정)·감정요항에 실명이 무마스킹 저장돼 서빙되던 것 수정.
# 그 필드는 근저당권자·가압류권자·채권자 어순이 흔한데 종전 역할목록에 없어 성명이 새어나갔다.
# ⚠ 이 역할어들 뒤엔 법인(신한은행·주식회사 우리캐피탈)이 오는 경우가 많다 — _looks_like_name의
#   법인 접미사 가드(_CORP_SUFFIX)가 그것을 보존한다("근저당권자 신한은행"은 마스킹하지 않음).
_PII_CONTEXT_RE = re.compile(
    r"(주택임차권자|유치권신고인|유치권자|임차권자|근저당권설정자|근저당권자|가압류권자"
    r"|전세권자|지상권자|전세권설정자|채권자|채무자|소유자|공유자|임차인|임대인"
    r"|점유자|신청인|배우자|상속인|연고자)[\s:：]+([가-힣]{2,4})"
    r"((?:\s*[,·]\s*[가-힣]{2,4})*)"
)
# 나열 꼬리 안의 개별 항목 — (구분자, 이름, 빈문자) 로 쪼개 이름만 치환.
_PII_NAME_LIST_RE = re.compile(r"(\s*[,·]\s*)([가-힣]{2,4})()")
# 패턴 2 — 이름이 앞: "홍길동 지분", "홍길동 소유" (maejibun의 지배적 표기. 패턴1로는 미탐)
# (2026-07-22 C1) '소유' 뒤에 '자/권'이 오면(소유자·소유권=역할·권리) 앞 토큰은 이름이 아니라
# 부사·공시어일 때가 많다("현장조사 당시 소유자") → 오탐 방지 위해 소유자/소유권은 이 패턴에서 제외
# (진짜 소유자 성명은 패턴1의 역할라벨 '소유자 홍길동' 어순으로 잡는다).
_PII_NAME_FIRST_RE = re.compile(r"([가-힣]{2,4})\s*(지분|소유(?![자권]))")

# 이름 자리에 오지만 자연인이 아닌 어휘. **정확일치(+조사)로만** 제외한다 —
# ⚠ 접두일치로 하면 "전원"이 실명 **전원철**을, "소유"가 **소유진**을 삼켜 마스킹을 빠져나간다
#   (실측 2026-07-15: 천안 2025타경11313 "임차인 전원철, 박화란" 무마스킹 누출).
_NOT_A_NAME = frozenset({
    "공유자", "채무자", "소유자", "소유권", "임차인", "점유자", "신청인", "배우자", "상속인",
    "전원", "갑구", "을구", "지분", "대지권", "전부", "일부", "비율", "토지", "건물",
    "청구", "매각", "소유", "부분", "구분", "등기", "명의", "증여", "매매", "공유", "성명",
    "상속", "임대인", "임차권자", "연고자", "유치권자",
    # 역할어 뒤에 오는 서술어·부사 — 이름 자리를 차지하지만 자연인이 아니다
    # (실측: "임차인 있으며", "임차인 전원", "소유자 미상의", "채무자 명의의").
    "있으며", "있음", "없으며", "없음", "미상", "불명", "다수", "수인", "본인", "모두",
    "각각", "해당", "동일", "상기", "전술", "아래", "기타", "및", "또는", "외의", "위의",
    # (2026-07-22 C1) 법인 형태어 — 이름 자리에 와도 자연인 아님("임차인 주식회사 …" 오탐 방지).
    "주식회사", "유한회사", "합자회사", "재단법인", "사단법인", "협동조합", "유한책임회사", "법인",
})

# (2026-07-22 C1) 법인 접미사 — 이 어미로 끝나는 이름자리 토큰은 자연인이 아니라 회사·기관이므로
# 마스킹하지 않는다(공시정보 보존). '신한은행'·'우리카드'처럼 2~4자 법인명을 성명 오탐에서 구한다.
# 오탐 감수: '김은행' 같은 실명은 극히 드물고, 공시된 채권자 은행명 훼손이 훨씬 흔하고 해롭다.
_CORP_SUFFIX = (
    "은행", "카드", "캐피탈", "보험", "증권", "생명", "화재", "신탁", "저축", "대부",
    "공사", "공단", "금고", "신협", "농협", "수협", "축협", "산림조합", "새마을금고",
    "재단", "협회", "공제", "조합", "센터", "관리단", "자산관리", "파이낸셜", "저축은행",
)
# 성명/명사 뒤에 붙는 조사 — 제외어 판정 시 떼어내고 본다("전원의"→'전원'=제외, "전원철"→실명).
_JOSA = ("으로", "에게", "로부터", "의", "은", "는", "이", "가", "도", "만", "에", "와", "과",
         "로", "을", "를", "께", "님", "부터")


def _strip_josa(token: str) -> str:
    """토큰 끝의 조사 1개를 떼어낸 형태(2자 이상 남을 때만). '전원의'→'전원', '전원철'→'전원철'."""
    for j in sorted(_JOSA, key=len, reverse=True):
        if len(token) - len(j) >= 2 and token.endswith(j):
            return token[: -len(j)]
    return token


def _looks_like_name(token: str) -> bool:
    """이름 자리 토큰이 자연인 성명으로 보이면 True(법률용어·역할어·서술어는 False).

    정확일치 + 조사분리로만 제외 — 접두일치는 실명을 삼킨다(위 주석 참조).
    (2026-07-22 C1) 법인 접미사(은행·카드 등)로 끝나면 회사·기관이므로 마스킹 대상이 아니다.
    """
    stripped = _strip_josa(token)
    if token in _NOT_A_NAME or stripped in _NOT_A_NAME:
        return False
    if any(token.endswith(s) or stripped.endswith(s) for s in _CORP_SUFFIX):
        return False
    return True


# 성명 뒤에 바로 붙는 조사 — `[가-힣]{2,4}`가 탐욕적이라 "윤용섭로부터"를 4자로 집어삼킨다.
# 이름(2~3자)과 조사를 갈라 조사는 원문에 되돌려준다("[성명]부터" 같은 훼손 방지).
_JOSA_TAIL = ("으로", "로", "은", "는", "이", "가", "의", "에", "와", "과", "도", "만", "께", "님")


def _split_name_josa(token: str) -> tuple[str, str]:
    """'윤용섭로' → ('윤용섭','로'). 조사가 없으면 (token, '')."""
    for j in sorted(_JOSA_TAIL, key=len, reverse=True):
        if len(token) - len(j) >= 2 and token.endswith(j):
            return token[: -len(j)], j
    return token, ""


def is_personal_field(key: str) -> bool:
    """키가 자연인 식별정보(개인정보보호법 대상)로 의심되면 True."""
    if key in PERSONAL_INFO_KEYS:
        return True
    low = key.lower()
    return any(tok in low for tok in _PII_NAME_TOKENS)


def mask_personal_names(text: str) -> str:
    """자유텍스트 내 성명을 '[성명]'으로 마스킹. 역할라벨·지분비율 등 공시정보는 보존한다.

    두 어순을 모두 처리한다 — "채무자 홍길동"(비고체) / "홍길동 지분"(매각지분체).
    법률용어(소유권·전원의·대지권 …)는 이름 자리에 와도 마스킹하지 않는다.
    """
    if not text:
        return text

    def _role_first(m: re.Match) -> str:
        if not _looks_like_name(m.group(2)):
            return m.group(0)
        _, josa = _split_name_josa(m.group(2))
        # 나열 처리 — "임차인 전원철, 박화란" 의 둘째 이후 이름은 역할라벨이 앞에 없어
        # 패턴에 안 걸린다(실측 누출). 라벨 뒤 쉼표 나열은 같은 역할의 사람들이므로 함께 마스킹.
        tail = m.group(3) or ""
        if tail:
            def _item(x: re.Match) -> str:
                if not _looks_like_name(x.group(2)):
                    return x.group(0)
                _, j = _split_name_josa(x.group(2))
                return f"{x.group(1)}[성명]{j}"
            tail = _PII_NAME_LIST_RE.sub(_item, tail)
        return f"{m.group(1)} [성명]{josa}{tail}"

    def _name_first(m: re.Match) -> str:
        return f"[성명] {m.group(2)}" if _looks_like_name(m.group(1)) else m.group(0)

    text = _PII_CONTEXT_RE.sub(_role_first, text)
    return _PII_NAME_FIRST_RE.sub(_name_first, text)


def sanitize_row(raw: dict) -> dict:
    """개인정보를 제거한 '원본 전체' dict 반환.

    (1) 자연인 식별 키는 통째 제거, (2) 자유텍스트 필드는 문맥상 성명만 마스킹.
    나머지(가격·면적·주소·법원·코드 등 공시정보)는 전부 보존.
    """
    out: dict = {}
    for k, v in raw.items():
        if is_personal_field(k):
            continue
        if k in _FREE_TEXT_FIELDS and isinstance(v, str):
            v = mask_personal_names(v)
        out[k] = v
    return out


# ---------------------------------------------------------------------------
# 3. 코드 상수 (검색·표시용)
# ---------------------------------------------------------------------------
SIDO_CODES: dict[str, str] = {
    "11": "서울", "26": "부산", "27": "대구", "28": "인천", "29": "광주", "30": "대전",
    "31": "울산", "36": "세종", "41": "경기", "43": "충북", "44": "충남", "46": "전남",
    "47": "경북", "48": "경남", "50": "제주", "51": "강원", "52": "전북",
}
SRCH_COND_REAL_ESTATE = "0004601"  # cortAuctnSrchCondCd: 부동산
SRCH_COND_MOVABLE = "0004604"      # 동산


# ---------------------------------------------------------------------------
# 4. 파싱 헬퍼
# ---------------------------------------------------------------------------
def to_won(s: str | int | float | None) -> int:
    """문자/숫자 금액(원 단위) → int. 콤마·소수점('1.5e8' 제외) 허용. 실패 시 0 + 경고로그.

    소수점 포함('150000000.0')도 int(float(...))로 처리 — 파싱 실패가 조용히
    min_bid_price=0이 되어 affordable 필터에서 매물이 누락되는 침묵실패를 막는다.
    """
    if s is None or s == "":
        return 0
    try:
        return int(float(str(s).replace(",", "").strip()))
    except (ValueError, OverflowError) as e:
        logger.warning("to_won 파싱 실패(원본=%r): %s — 0 반환", s, e)
        return 0


def to_int(s: str | int | float | None) -> int:
    if s is None or s == "":
        return 0
    try:
        return int(float(str(s).replace(",", "").strip()))
    except (ValueError, OverflowError) as e:
        logger.warning("to_int 파싱 실패(원본=%r): %s — 0 반환", s, e)
        return 0


def ymd_to_iso(yyyymmdd: str | None) -> str:
    """'20260701' → '2026-07-01'. 형식이 아니면 원문 그대로."""
    s = (yyyymmdd or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


_AREA_RE = re.compile(r"(\d+(?:\.\d+)?)\s*㎡")


def parse_area_m2(*texts: str) -> float:
    """'철근콘크리트구조\\n33.56㎡' 등에서 첫 ㎡ 면적을 추출. 없으면 0.0."""
    for t in texts:
        if not t:
            continue
        m = _AREA_RE.search(str(t))
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return 0.0


# ── 세부용도코드(sclsUtilCd) → 표준 물건유형 ──────────────────────────────
# 감사(2026-07-10 CRITICAL) 확정: dspslUsgNm 은 개별 물건 용도가 아니라 법원 검색 카테고리
# **그룹명**("상가,오피스텔,근린시설" 등 — 관측 고유값 19종 전부 그룹명)이다. 그룹명 키워드
# 매칭은 근생·사무소·지식산업센터 호실을 '오피스텔'로, 오피스텔을 '아파트'로 오분류해
# 엉뚱한 유형의 실거래 시세가 붙는 사고를 냈다(그룹 혼합 889건 중 시세 매칭 60건의 35%가
# 비오피스텔 실체, 오피스텔 코드 물건이 아파트 시세로 기본정렬 rank 10·11 노출).
# → 물건별 실체를 말하는 sclsUtilCd 를 1차 신뢰 소스로 쓰고, 없을 때만 그룹명 폴백.
_SCLS_TYPE = {
    "20101": "단독", "20102": "단독", "20103": "단독",   # 단독/다가구/다중
    "20104": "아파트",
    "20105": "연립", "20106": "다세대",
    "20110": "오피스텔",
    "20107": "빌라", "20108": "다세대",                   # 도시형생활주택 계열(관측 시 보수 매핑)
}
_SCLS_PREFIX_TYPE = [
    ("101", "토지"),      # 10101 전 / 10102 답 / 10105 임야 / 10108 대지 / 10114 잡종지 …
    ("211", "상가"),      # 21101 근생 / 21104 점포 / 21111 사무소 / 21199 기타상업 …
    ("212", "상가"),
    ("221", "공장"),      # 22101 공장(지식산업센터 포함)
]


def classify_by_scls(scls: str) -> str:
    """세부용도코드 → 표준 유형. 미상 코드는 ''(폴백 신호)."""
    s = (scls or "").strip()
    if not s:
        return ""
    if s in _SCLS_TYPE:
        return _SCLS_TYPE[s]
    for prefix, typ in _SCLS_PREFIX_TYPE:
        if s.startswith(prefix):
            return typ
    return ""


# dspslUsgNm(그룹명) 키워드 폴백 — scls 미상일 때만. 그룹명 특성상 콤마 복수 용도가 흔해
# 여기서 확정 주거유형을 말하면 위험하므로, 콤마 포함 그룹은 '혼합'(시세추정 미지원)으로 둔다.
_TYPE_KEYWORDS = [
    ("아파트형공장", "상가"),   # '아파트' 선매칭 방지 — 구체 키워드를 먼저
    ("아파트", "아파트"), ("오피스텔", "오피스텔"), ("다세대", "다세대"), ("연립", "연립"),
    ("빌라", "빌라"), ("도시형생활주택", "다세대"), ("단독", "단독"), ("다가구", "단독"),
    ("상가", "상가"), ("근린", "상가"), ("점포", "상가"), ("사무실", "상가"),
    ("공장", "공장"), ("토지", "토지"), ("대지", "토지"), ("임야", "토지"), ("전", "토지"), ("답", "토지"),
    ("주택", "단독"),
]


def classify_property_type(usg_nm: str, scls: str = "") -> str:
    """물건유형 분류 — sclsUtilCd(물건 실체) 우선, 그룹명(dspslUsgNm)은 폴백.

    그룹명이 콤마 복수 용도("상가,오피스텔,근린시설")인데 scls 도 미상이면 어느 실체인지
    알 수 없으므로 '혼합'(시세추정 미지원 유형)으로 정직하게 둔다 — 틀린 시세보다 무추정이 낫다.
    """
    by_code = classify_by_scls(scls)
    if by_code:
        return by_code
    s = (usg_nm or "").strip()
    if "," in s:
        return "혼합"
    for kw, typ in _TYPE_KEYWORDS:
        if kw in s:
            return typ
    return s[:12] if s else "기타"


# 물리 검증 대상 — 용도명이 이들인데 건물 실체 신호가 전무하면 토지 의심.
_HOUSING_TYPES = ("아파트", "오피스텔", "다세대", "연립", "빌라")


def verify_property_type(ptype: str, clean: dict) -> str:
    """용도명 기반 분류를 물건의 물리 신호로 교차검증(실측 버그 수정, 2026-07-10).

    실측(대구 2025타경7938): 나대지(지목 '대', 주거나지)가 법원 용도명 '아파트'로 등록
    → 아파트 실거래와 오매칭 → 4.45억 허상 차익이 랭킹 상위 노출.
    집합건물 전유는 건물 표식(buldNm/buldList/pjbBuldList)이 있고 지목(jimokList)이 없다.
    반대로 건물 표식이 전무한데 지목이 있으면 실체는 토지 — 용도명이 뭐라 하든 '토지'로
    교정해 기존 미지원유형 정책(아파트·오피스텔만 시세 추정)이 적용되게 한다.
    """
    if ptype not in _HOUSING_TYPES:
        return ptype
    has_building = any(clean.get(k) for k in ("buldNm", "buldList", "pjbBuldList"))
    has_jimok = bool(clean.get("jimokList"))
    if not has_building and has_jimok:
        return "토지"
    return ptype


# ---------------------------------------------------------------------------
# 5. 레코드 — 명시 필드 + 정제된 원본 전체(raw)
# ---------------------------------------------------------------------------
@dataclass
class CourtAuctionRecord:
    """경매 물건 1행. 명시 필드는 편의용, `raw`가 개인정보 제외 전체 원본."""
    # 식별
    doc_id: str
    case_no: str           # srnSaNo "2025타경1352"
    court: str             # jiwonNm
    dept: str              # jpDeptNm 경매계
    # 물건
    property_type: str     # 분류된 유형
    usage_name: str        # dspslUsgNm 원문
    address: str           # printSt(출력용 전체주소)
    sido: str
    sigu: str
    dong: str
    lawd_cd: str           # srchHjguSiguCd 5자리(국토부 LAWD_CD)
    jibun: str             # daepyoLotno
    building_name: str     # buldNm
    building_detail: str   # buldList
    area_m2: float
    # 금액/경과
    appraisal_price: int   # gamevalAmt
    min_bid_price: int     # minmaePrice
    fail_count: int        # yuchalCnt
    sale_date: str         # maeGiil ISO
    sale_place: str        # maePlace
    bid_open_date: str     # ipgiganFday ISO (입찰시작)
    bid_close_date: str    # ipgiganTday ISO
    view_count: int        # inqCnt
    interest_count: int    # gwansMulRegCnt
    note: str              # mulBigo (일괄매각 등)
    tel: str               # 담당계 전화(기관)
    # 좌표(투영 — 정밀)
    x_proj: str
    y_proj: str
    # 원본 전체(개인정보 제외)
    raw: dict = field(default_factory=dict)
    # 물건번호(maemulSer) — 한 사건에 물건 여러 개 가능. case_no 단일 식별 금지(T1).
    item_no: str = ""


def _clean_lawd(raw: str) -> str:
    """법정동코드 정제 — 콤마 오염(',47290'·'51150,42150' 실측 10건, 재검증 감사 idx29)이
    있으면 첫 유효 5자리를 취한다. 오염 그대로면 MOLIT 조회가 무조건 0건으로 침묵한다."""
    s = (raw or "").strip()
    if "," not in s:
        return s
    for part in s.split(","):
        p = part.strip()
        if len(p) == 5 and p.isdigit():
            return p
    return s.replace(",", "")[:5]


def _has_building(rec: CourtAuctionRecord) -> bool:
    """이 목적물 행에 건물 실체 표식이 있는가(집합건물 전유 행 판별)."""
    r = rec.raw or {}
    return bool(rec.building_name or rec.building_detail or r.get("pjbBuldList"))


def merge_mokmul_rows(records: list[CourtAuctionRecord]) -> list[CourtAuctionRecord]:
    """같은 물건(court,case_no,item_no)의 목적물(mokmulSer)별 다중 행을 1행으로 병합.

    감사(2026-07-10 HIGH) 확정: 검색 API 는 일괄매각 물건을 목적물 단위(docid 끝=mokmulSer)로
    여러 행 반환하고, doc_id dedup 은 이들을 전부 살린다. 그대로 채점하면 INSERT OR REPLACE
    (PK=court,case_no,item_no)에서 API 순서상 **마지막 행이 이기는 순서 의존 침묵 오류** —
    토지 목적물 행이 마지막이면 진짜 아파트가 '토지'로 강등된 실사례 3건.

    병합 규칙: 건물 표식 있는 행 우선(집합건물의 본체 — 유형·면적의 근거),
    동률이면 doc_id 낮은 행(주 목적물). raw 보존은 save_raw_records 가 전 행을 따로 담당.
    """
    groups: dict[tuple, list] = {}
    order: list[tuple] = []
    for r in records:
        k = (r.court, r.case_no, r.item_no)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(r)
    out = []
    for k in order:
        rows = groups[k]
        if len(rows) == 1:
            out.append(rows[0])
            continue
        rows.sort(key=lambda r: (not _has_building(r), r.doc_id))
        out.append(rows[0])
    dropped = len(records) - len(out)
    if dropped:
        logger.info("목적물 다중 행 병합: %d행 → %d물건(건물행 우선, %d행 병합됨)",
                    len(records), len(out), dropped)
    return out


def parse_row(raw: dict) -> CourtAuctionRecord:
    """검색응답 1행(dict) → CourtAuctionRecord. 개인정보는 raw에서 제거."""
    clean = sanitize_row(raw)
    area = parse_area_m2(clean.get("areaList", ""), clean.get("pjbBuldList", ""))
    return CourtAuctionRecord(
        doc_id=clean.get("docid", ""),
        case_no=clean.get("srnSaNo", ""),
        court=clean.get("jiwonNm", ""),
        dept=clean.get("jpDeptNm", ""),
        property_type=verify_property_type(
            classify_property_type(clean.get("dspslUsgNm", ""),
                                   clean.get("sclsUtilCd", "")), clean),
        usage_name=clean.get("dspslUsgNm", ""),
        address=clean.get("printSt") or clean.get("convAddr", ""),
        sido=clean.get("hjguSido", ""),
        sigu=clean.get("hjguSigu", ""),
        dong=clean.get("hjguDong", ""),
        lawd_cd=_clean_lawd(clean.get("srchHjguSiguCd", "")),
        jibun=clean.get("daepyoLotno", ""),
        building_name=clean.get("buldNm", ""),
        building_detail=clean.get("buldList", ""),
        area_m2=area,
        appraisal_price=to_won(clean.get("gamevalAmt")),
        # (재검증 감사 2026-07-11 idx15 CRITICAL) minmaePrice 는 '직전 회차' 가격이고,
        # 다가오는 매각기일의 실제 공고 최저가는 notifyMinmaePrice1 이다(법원 검색화면 표시값,
        # 전 매물의 73.6%에서 두 값이 다름 — 실측). 공고가 우선, 없으면 minmae 폴백.
        min_bid_price=(to_won(clean.get("notifyMinmaePrice1"))
                       or to_won(clean.get("minmaePrice"))),
        fail_count=to_int(clean.get("yuchalCnt")),
        sale_date=ymd_to_iso(clean.get("maeGiil")),
        sale_place=clean.get("maePlace", ""),
        bid_open_date=ymd_to_iso(clean.get("ipgiganFday")),
        bid_close_date=ymd_to_iso(clean.get("ipgiganTday")),
        view_count=to_int(clean.get("inqCnt")),
        interest_count=to_int(clean.get("gwansMulRegCnt")),
        note=clean.get("mulBigo", ""),
        tel=clean.get("tel", ""),
        x_proj=clean.get("xCordi", ""),
        y_proj=clean.get("yCordi", ""),
        raw=clean,
        item_no=str(clean.get("maemulSer", "") or ""),
    )


# ── 부분 지분 매각 검출 (2026-07-24, 실사고: 죽전자이2차 2025타경55336) ──
# 지분 표기는 비고(mulBigo)가 아니라 **maejibun(매각지분)** 필드에 온다 — 종전엔 비고만
# 검출기에 넣어 1/2 지분(감정 3.55억 = 온전가의 절반)이 같은 단지 온전 세대 실거래로
# 채점돼 허구 차익 3.69억·점수 95.5 '차익 유력'으로 서빙됐다(미탐 클래스 실측 5건).
# 경계(실데이터 확정 — 오탐은 온전 물건을 오배제하므로 양쪽 다 지킨다):
#   · "N분의M … 지분" / "N/M … 지분"(20자 내 결합)  → 부분 지분
#   · '전원' 포함("공유자 전원의 지분 전부")        → 온전 매각 — 제외
#   · 대지권 비율("500분의 21.7849")               → '지분' 결합 없음 — 자연 제외
#   · 비고의 '공유자 우선매수' 문구                 → 공유자 존재 = 부분 지분 보조 신호
_SHARE_FRACTION_RE = re.compile(r"(?:\d+\s*분의\s*[\d.]+|\d+\s*/\s*\d+)[^\n]{0,20}?지분")
_COOWNER_PREEMPT_RE = re.compile(r"공유자.{0,10}우선\s*매수")


def is_partial_share(maejibun: str | None, note: str | None) -> bool:
    """이 매각이 온전 소유권이 아닌 '부분 지분'인가 — maejibun 우선, 비고 공유자문구 보조."""
    mj = maejibun or ""
    if "전원" in mj:
        return False          # '공유자 전원의 지분 전부' = 100% 온전 매각
    if _SHARE_FRACTION_RE.search(mj):
        return True
    return bool(_COOWNER_PREEMPT_RE.search(note or ""))


def to_auction_listing(rec: CourtAuctionRecord) -> AuctionListing:
    """차익 스코어 파이프라인(matcher/score)이 쓰는 기존 모델로 변환.

    완전한 권리분석은 물건상세(D)에서만 가능하므로 rights_verified=False로 두어 '권리미확인'
    게이트를 유지한다. 다만 리스트 '비고(mulBigo)'에 특수권리·대항력·인수금액이 박혀 있으면
    Tier-0 힌트로 뽑아 하드게이트('위험')는 미리 발동시킨다(무료·네트워크 0).
    비고는 짧은 메모라 보수적(키워드 출현=위험)으로만 쓰고, 완전검증은 상세 보강 시 대체된다.

    (감사 2026-07-15) 여기서 detect_special_rights 만 부르고 detect_tenant_opposable·
    detect_assumed_amount 는 부르지 않아, batch 전 물건이 assumed_amount=0·tenant_opposable=False
    로 적재됐다. 그 결과 인수비율 하드게이트와 대항력 30점 페널티가 batch 경로에서 영구히 죽어
    있었다(실측: 점수 매겨진 1,023건 중 대항력 68건·인수금액 9건이 전부 무시됨). 세 검출기를
    함께 부른다 — 같은 원천(비고), 같은 보수성 규범.
    """
    from .courtauction_rights import (  # noqa: PLC0415 — 순환 import 회피
        detect_assumed_amount,
        detect_special_rights,
        detect_tenant_opposable,
    )

    special = detect_special_rights(rec.note) if rec.note else []
    # (2026-07-24) 부분 지분은 maejibun 필드로 검출 — '지분' 라벨이 있으면 matcher 의
    # estimate_market 이 SCOPE_SHARE_SALE 로 시세 자체를 거부한다(온전가 비교 무의미).
    if "지분" not in special and is_partial_share(
            (rec.raw or {}).get("maejibun"), rec.note):
        special = [*special, "지분"]
    # 비고 기반 Tier-0 권리 힌트. 상세(D) 보강 시 apply_rights 가 덮어쓴다.
    opposable = detect_tenant_opposable(rec.note) if rec.note else False
    assumed = detect_assumed_amount(rec.note) if rec.note else 0
    # (서빙감사 2026-07-12 #3) 신청채권자 매수신청 플로어 — 그 금액 이상 써야 낙찰되므로
    # 유효 최저입찰가 = max(공고최저가, 매수신청액). 공고가만 쓰면 취득원가·차익이 과대평가된다.
    min_bid = rec.min_bid_price
    floor = _creditor_bid_floor(rec.note)
    if floor > min_bid:
        min_bid = floor
        if "채권자매수신청" not in special:
            special = [*special, "채권자매수신청"]
    return AuctionListing(
        case_no=rec.case_no,
        court=rec.court,
        address=rec.address,
        lawd_cd=rec.lawd_cd,
        dong=rec.dong,
        apt_name=rec.building_name,
        property_type=rec.property_type,
        area_m2=rec.area_m2,
        appraisal_price=rec.appraisal_price,
        min_bid_price=min_bid,
        fail_count=rec.fail_count,
        sale_date=rec.sale_date,
        special_rights=special,   # 비고 힌트(Tier-0). rights_verified는 상세(D) 전까지 False 유지.
        tenant_opposable=opposable,   # 〃 — 대항력 30점 페널티 발동
        assumed_amount=assumed,       # 〃 — 인수비율 하드게이트 발동(is_hard_gated는 verified 불요)
        item_no=rec.item_no,      # T1: 같은 사건 다른 물건 덮어쓰기 방지 — 복합 식별자 관통
        doc_id=rec.doc_id,
    )


# 신청채권자 매수신청 금액 — '금 295,400,000원의 매수신청' (서빙감사 #3).
_CREDITOR_BID_RE = re.compile(r"([\d,]{7,})\s*원의?\s*매수신청")


def _creditor_bid_floor(note: str) -> int:
    """비고에서 신청채권자 매수신청액(원) — 없으면 0."""
    if not note or "매수신청" not in note:
        return 0
    best = 0
    for m in _CREDITOR_BID_RE.finditer(note):
        best = max(best, to_won(m.group(1)))
    return best
