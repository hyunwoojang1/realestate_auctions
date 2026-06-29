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
    occupant_type: str = "공실"        # 공실 / 임차인 / 소유자점유 / 다수점유

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
    est_market_price: int | None   # 추정 실거래 시세(원). 매칭 0건이면 None
    matched_trades: int
    confidence: float                 # 신뢰계수 0.6~1.0
    real_acquisition_cost: int        # 실질취득원가(부대비용 포함)
    expected_profit: int | None    # 예상 순차익(원)
    gap_rate: float | None         # 시세 대비 할인율
    gap_score: float
    rights_score: float
    liquidity_score: float
    arb_score: float | None        # 최종 차익 스코어 0~100. 시세추정불가면 None
    grade: str                        # 확실한 차익 / 양호 / 관심 / 주의 / 시세추정불가

    def to_row(self) -> dict:
        return asdict(self)
