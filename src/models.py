"""도메인 모델 — 경매 물건 / 실거래 / 채점 결과."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class AuctionListing:
    """법원경매 물건 1건 (PoC: 샘플 fixture, v1: 대법원 크롤러)."""
    case_no: str          # 사건번호 예) "2024타경12345"
    court: str            # 관할 법원
    address: str          # 소재지
    lawd_cd: str          # 법정동코드 5자리(시군구) — 국토부 API LAWD_CD
    dong: str             # 법정동명 (매칭용)
    apt_name: str         # 단지/건물명
    property_type: str    # 아파트 / 오피스텔 / 다세대 / 상가 / 토지 ...
    area_m2: float        # 전용면적
    appraisal_price: int  # 감정가(원)
    min_bid_price: int    # 최저입찰가(원)
    fail_count: int       # 유찰 횟수
    sale_date: str        # 매각기일 YYYY-MM-DD
    # ---- 권리 관련 ----
    assumed_amount: int = 0            # 낙찰자가 추가로 떠안는 인수금액(원)
    special_rights: list[str] = field(default_factory=list)  # 유치권/법정지상권/지분 등
    tenant_opposable: bool = False     # 대항력 있는(배당 못 받는) 임차인 존재
    occupant_type: str = "소유자점유"  # 공실 / 임차인 / 소유자점유 / 다수점유 (미상은 보수적으로 점유 가정)
    # 권리분석이 실제로 수행됐는가. False(라이브 크롤 등 물건상세 미수집)면 권리 점수를 신뢰하지 않고
    # '권리미확인' 등급으로 강등하며 '차익 유력' 뱃지·초록 안전문구를 부여하지 않는다(허위 안전신호 방지).
    rights_verified: bool = False
    # ---- 식별 보강 (데이터 신뢰도 개편 T1) ----
    # 한 사건번호(case_no) 안에 물건이 여러 개일 수 있다(물건번호 1=아파트, 2=상가 …).
    # case_no만으로 식별하면 같은 사건의 다른 물건이 조용히 덮어써진다 → court+case_no+item_no가 식별 단위.
    item_no: str = ""     # 물건번호(courtauction maemulSer). 샘플/미상은 ""
    doc_id: str = ""      # courtauction 고유 문서 id(있으면 최우선 식별자)

    def discount_vs_appraisal(self) -> float:
        """감정가 대비 최저가 할인율 (레거시 사이트가 보여주는 그 수치)."""
        if self.appraisal_price <= 0:
            return 0.0
        return 1 - self.min_bid_price / self.appraisal_price


@dataclass
class Trade:
    """국토부 실거래 1건."""
    apt_name: str
    area_m2: float
    price: int        # 거래금액(원)
    deal_ym: str      # YYYYMM
    dong: str = ""
    floor: int = 0
    kind: str = ""    # 실거래 물건유형: apt | rh | officetel (유형 분리 매칭용)

    def price_per_m2(self) -> float:
        return self.price / self.area_m2 if self.area_m2 else 0.0


@dataclass
class ScoredListing:
    """차익 스코어가 매겨진 물건 — 큐레이션/출력의 단위."""
    case_no: str
    apt_name: str
    address: str
    property_type: str
    area_m2: float
    appraisal_price: int
    min_bid_price: int
    fail_count: int
    sale_date: str
    # ---- 추정/계산 ----
    # 추정 실거래 시세(원). **계약: None=추정불가(항상). 그 외는 유효한 양수 추정치.**
    # 0·-1 등 sentinel 금지 — stats.est_success_rate가 "None이 아님"만으로 성공 판정하므로
    # sentinel을 넣으면 성공률이 조용히 부풀려짐(침묵실패).
    est_market_price: int | None
    matched_trades: int
    confidence: float                 # 신뢰계수 0.6~1.0
    real_acquisition_cost: int        # 실질취득원가(부대비용 포함)
    expected_profit: int | None    # 예상 순차익(원)
    gap_rate: float | None         # 시세 대비 할인율
    gap_score: float
    rights_score: float
    liquidity_score: float
    arb_score: float | None        # 최종 차익 스코어 0~100. 시세추정불가면 None
    grade: str                        # 차익 유력 / 양호 / 관심 / 주의 / 권리미확인 / 차익없음 / 위험 / 시세추정불가
    rights_verified: bool = False     # 권리분석 수행 여부 — 상세페이지 안전문구·뱃지 게이트
    # ---- 식별 보강 (T1): case_no 단일키는 같은 사건의 다른 물건번호를 덮어쓴다 ----
    court: str = ""                   # 관할 법원 (복합 식별자 구성요소)
    item_no: str = ""                 # 물건번호(maemulSer). 샘플/미상은 ""
    doc_id: str = ""                  # courtauction 고유 문서 id

    @property
    def uid(self) -> str:
        """저장·중복제거용 복합 식별자. doc_id가 있으면 그것이 원천 고유키."""
        return self.doc_id or f"{self.court}|{self.case_no}|{self.item_no}"

    def to_row(self) -> dict:
        return asdict(self)
