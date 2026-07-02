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

# 자유텍스트 필드: 한국어 성명이 문맥과 함께 들어올 수 있어 역할라벨 뒤 이름만 마스킹.
_FREE_TEXT_FIELDS = frozenset({"mulBigo", "alias"})
_PII_CONTEXT_RE = re.compile(
    r"(채무자|소유자|임차인|점유자|신청인|배우자|상속인)\s*[:：]?\s*([가-힣]{2,4})"
)


def is_personal_field(key: str) -> bool:
    """키가 자연인 식별정보(개인정보보호법 대상)로 의심되면 True."""
    if key in PERSONAL_INFO_KEYS:
        return True
    low = key.lower()
    return any(tok in low for tok in _PII_NAME_TOKENS)


def mask_personal_names(text: str) -> str:
    """자유텍스트 내 '채무자 홍길동' 류의 성명을 '[성명]'으로 마스킹(역할라벨은 보존)."""
    if not text:
        return text
    return _PII_CONTEXT_RE.sub(lambda m: f"{m.group(1)} [성명]", text)


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
# 2. 필드 한글 라벨 (사용자 가독성용) — 검색응답 117필드
# ---------------------------------------------------------------------------
FIELD_LABELS: dict[str, str] = {
    "docid": "문서ID", "boCd": "기관코드", "saNo": "사건일련번호", "maemulSer": "매물일련번호",
    "mokmulSer": "목적물일련번호", "srnSaNo": "사건번호", "jpDeptCd": "담당계코드",
    "jinstatCd": "진행상태코드", "mulStatcd": "물건상태코드", "mulJinYn": "물건진행여부",
    "maemulUtilCd": "매물용도코드", "mulBigo": "비고", "gamevalAmt": "감정평가액(원)",
    "minmaePrice": "최저매각가격(원)", "yuchalCnt": "유찰횟수", "maeAmt": "매각가격(원)",
    "inqCnt": "조회수", "gwansMulRegCnt": "관심물건등록수", "remaeordDay": "재매각명령일",
    "ipchalGbncd": "입찰구분코드", "maeGiil": "매각기일", "maegyuljGiil": "매각결정기일",
    "maeHh1": "매각시각1", "maeHh2": "매각시각2", "maeHh3": "매각시각3", "maeHh4": "매각시각4",
    "notifyMinmaePrice1": "공고최저가1(원)", "notifyMinmaePrice2": "공고최저가2(원)",
    "notifyMinmaePrice3": "공고최저가3(원)", "notifyMinmaePrice4": "공고최저가4(원)",
    "notifyMinmaePriceRate1": "공고최저가율1(%)", "notifyMinmaePriceRate2": "공고최저가율2(%)",
    "maeGiilCnt": "매각기일횟수", "ipgiganFday": "입찰기간시작", "ipgiganTday": "입찰기간종료",
    "maePlace": "매각장소", "spJogCd": "특수조건코드", "mokGbncd": "목적물구분코드",
    "jongCd": "종별코드", "stopsaGbncd": "정지사건구분코드",
    "daepyoSidoCd": "대표시도코드", "daepyoSiguCd": "대표시군구코드", "daepyoDongCd": "대표읍면동코드",
    "daepyoRdCd": "대표도로코드", "hjguSido": "행정구역시도", "hjguSigu": "행정구역시군구",
    "hjguDong": "행정구역읍면동", "hjguRd": "행정구역도로", "daepyoLotno": "대표지번",
    "buldNm": "건물명", "buldList": "건물내역", "areaList": "면적내역", "jimokList": "지목내역",
    "lclsUtilCd": "용도대분류코드", "mclsUtilCd": "용도중분류코드", "sclsUtilCd": "용도소분류코드",
    "jejosaNm": "제조사명", "fuelKindcd": "연료종류코드", "bsgFormCd": "차량형태코드",
    "carNm": "차량명", "carYrtype": "차량연식", "xCordi": "X좌표(투영)", "yCordi": "Y좌표(투영)",
    "cordiLvl": "좌표정밀도", "bgPlaceSidoCd": "보관장소시도코드", "bgPlaceSiguCd": "보관장소시군구코드",
    "bgPlaceDongCd": "보관장소읍면동코드", "bgPlaceRdCd": "보관장소도로코드", "bgPlaceLotno": "보관장소지번",
    "bgPlaceSido": "보관장소시도", "bgPlaceSigu": "보관장소시군구", "bgPlaceDong": "보관장소읍면동",
    "bgPlaceRd": "보관장소도로", "srchHjguBgFlg": "검색행정구역보관플래그", "pjbBuldList": "표제부건물내역",
    "minArea": "최소면적", "maxArea": "최대면적", "groupmaemulser": "그룹매물일련번호",
    "bocdsano": "기관사건번호", "dupSaNo": "중복사건번호", "byungSaNo": "병합사건번호",
    "srchLclsUtilCd": "검색용도대분류", "srchMclsUtilCd": "검색용도중분류", "srchSclsUtilCd": "검색용도소분류",
    "srchHjguSidoCd": "검색시도코드", "srchHjguSiguCd": "검색시군구코드(LAWD5)",
    "srchHjguDongCd": "검색읍면동코드", "srchHjguRdCd": "검색도로코드", "srchHjguLotno": "검색지번",
    "jiwonNm": "관할법원", "jpDeptNm": "담당계", "tel": "담당계전화(기관)", "maejibun": "매각지번",
    "wgs84Xcordi": "WGS84경도(정수부)", "wgs84Ycordi": "WGS84위도(정수부)",
    "rd1Cd": "도로명시도코드", "rd2Cd": "도로명시군구코드", "rd3Rd4Cd": "도로명읍면동코드",
    "rd1Nm": "도로명시도", "rd2Nm": "도로명시군구", "rdEubMyun": "도로명읍면", "rdNm": "도로명",
    "buldNo": "건물번호", "rdAddrSub": "도로명부가정보", "addrGbncd": "주소구분코드",
    "bgPlaceRdAllAddr": "보관장소도로전체주소", "bgPlaceAddrGbncd": "보관장소주소구분",
    "srchRd1Cd": "검색도로시도", "srchRd2Cd": "검색도로시군구", "srchRd3Rd4Cd": "검색도로읍면동",
    "alias": "별칭", "dummyField": "더미", "dspslUsgNm": "매각물건용도명", "convAddr": "정제주소",
    "printSt": "출력용주소", "printCsNo": "출력용사건번호", "colMerge": "행병합키",
}


def label_row(raw: dict) -> dict:
    """원본 dict를 {한글라벨: 값}으로 — 사람이 읽는 덤프용(미정의 키는 원본 키 유지)."""
    return {FIELD_LABELS.get(k, k): v for k, v in raw.items()}


# ---------------------------------------------------------------------------
# 3. 코드 상수 (검색·표시용)
# ---------------------------------------------------------------------------
SIDO_CODES: dict[str, str] = {
    "11": "서울", "26": "부산", "27": "대구", "28": "인천", "29": "광주", "30": "대전",
    "31": "울산", "36": "세종", "41": "경기", "43": "충북", "44": "충남", "46": "전남",
    "47": "경북", "48": "경남", "50": "제주", "51": "강원", "52": "전북",
}
# 부동산 용도 대분류(lclsUtilCd) — 매물 분류·환금성 매핑용 (관측 기반, 점진 보강)
USAGE_LCLS = {
    "10000": "토지", "20000": "건물", "30000": "집합건물",
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


# dspslUsgNm 키워드 → AuctionListing.property_type (matcher 환금성 버킷과 정합)
_TYPE_KEYWORDS = [
    ("아파트", "아파트"), ("오피스텔", "오피스텔"), ("다세대", "다세대"), ("연립", "연립"),
    ("빌라", "빌라"), ("도시형생활주택", "다세대"), ("단독", "단독"), ("다가구", "단독"),
    ("상가", "상가"), ("근린", "상가"), ("점포", "상가"), ("사무실", "상가"),
    ("공장", "공장"), ("토지", "토지"), ("대지", "토지"), ("임야", "토지"), ("전", "토지"), ("답", "토지"),
    ("주택", "단독"), ("아파트형공장", "상가"),
]


def classify_property_type(usg_nm: str) -> str:
    """매각물건용도명(dspslUsgNm) → 표준 물건유형. 미상이면 원문(앞 12자)."""
    s = (usg_nm or "").strip()
    for kw, typ in _TYPE_KEYWORDS:
        if kw in s:
            return typ
    return s[:12] if s else "기타"


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

    @property
    def discount_vs_appraisal(self) -> float:
        if self.appraisal_price <= 0:
            return 0.0
        return 1 - self.min_bid_price / self.appraisal_price

    def labeled(self) -> dict:
        """{한글라벨: 값} 전체 덤프(사람이 읽는 용도)."""
        return label_row(self.raw)


def parse_row(raw: dict) -> CourtAuctionRecord:
    """검색응답 1행(dict) → CourtAuctionRecord. 개인정보는 raw에서 제거."""
    clean = sanitize_row(raw)
    area = parse_area_m2(clean.get("areaList", ""), clean.get("pjbBuldList", ""))
    return CourtAuctionRecord(
        doc_id=clean.get("docid", ""),
        case_no=clean.get("srnSaNo", ""),
        court=clean.get("jiwonNm", ""),
        dept=clean.get("jpDeptNm", ""),
        property_type=classify_property_type(clean.get("dspslUsgNm", "")),
        usage_name=clean.get("dspslUsgNm", ""),
        address=clean.get("printSt") or clean.get("convAddr", ""),
        sido=clean.get("hjguSido", ""),
        sigu=clean.get("hjguSigu", ""),
        dong=clean.get("hjguDong", ""),
        lawd_cd=clean.get("srchHjguSiguCd", ""),
        jibun=clean.get("daepyoLotno", ""),
        building_name=clean.get("buldNm", ""),
        building_detail=clean.get("buldList", ""),
        area_m2=area,
        appraisal_price=to_won(clean.get("gamevalAmt")),
        min_bid_price=to_won(clean.get("minmaePrice")),
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
    )


def to_auction_listing(rec: CourtAuctionRecord) -> AuctionListing:
    """차익 스코어 파이프라인(matcher/score)이 쓰는 기존 모델로 변환.

    대항력·인수금액·점유 등 완전한 권리분석은 물건상세(D)에서만 가능하므로 rights_verified=False로
    두어 '권리미확인' 게이트를 유지한다. 다만 리스트 '비고(mulBigo)'에 유치권·지분 등 특수권리
    플래그가 박혀 있으면 Tier-0 힌트로 뽑아 하드게이트('위험')는 미리 발동시킨다(무료·네트워크 0).
    비고는 짧은 메모라 보수적(키워드 출현=위험)으로만 쓰고, 완전검증은 상세 보강 시 대체된다.
    """
    from .courtauction_rights import detect_special_rights  # noqa: PLC0415 — 순환 import 회피

    special = detect_special_rights(rec.note) if rec.note else []
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
        min_bid_price=rec.min_bid_price,
        fail_count=rec.fail_count,
        sale_date=rec.sale_date,
        special_rights=special,   # 비고 힌트(Tier-0). rights_verified는 상세(D) 전까지 False 유지.
    )
