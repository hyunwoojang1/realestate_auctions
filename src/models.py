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
    # 시군구(LAWD_CD 5자리) — 전국 풀에서 타지역 동명(洞名) 혼입 방지(감사 2026-07-10 CRITICAL).
    # ""=레거시(스코프 제약 미적용, 하위호환).
    lawd_cd: str = ""
    # 해제거래(신고 후 취소된 계약) — cdealType="O"면 해제. comps에 섞이면 시세가 부풀려진다
    # (감사 실측: 강남·분당 2개월 182/1,628건 해제 → 평균 +11.18% 과대). 파싱은 보존, 매칭에서 제외.
    cdeal_type: str = ""   # 국토부 cdealType/해제여부. "O"=해제
    cdeal_day: str = ""    # cdealDay/해제사유발생일(YY.MM.DD, 감사용)
    # 거래유형(2026-07-19 네이버 실거래 개편): 직거래는 가족 간 저가양도 가능성이 있어 표본 경계값이 된다
    # (진천태왕아너스 실측 S3 — 직거래 1건이 '시세 2.8억' vs '시세추정불가'를 가름). 제외하지 않고
    # 표시·감사용으로 보존한다(제외 시 표본 고갈 실측 확인).
    dealing_gbn: str = ""  # 국토부 dealingGbn/거래유형: "중개거래" | "직거래" | ""(미상·구년도)
    rgst_date: str = ""    # 국토부 rgstDate/등기일자(YY.MM.DD) — 등기완료=확정 체결 신호. ""=미등록/미상

    def price_per_m2(self) -> float:
        return self.price / self.area_m2 if self.area_m2 else 0.0

    @property
    def is_cancelled(self) -> bool:
        """해제(취소)된 거래인가 — cdealType 'O' 또는 해제일 존재 시 True(이중 신호)."""
        return self.cdeal_type.strip().upper() == "O" or bool(self.cdeal_day.strip())

    @property
    def is_direct(self) -> bool:
        """직거래(중개사 미경유)인가 — 표시·신뢰 판단용. 미상("")은 False(단정하지 않음)."""
        return "직거래" in self.dealing_gbn


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
    grade: str                        # 차익 유력 / 양호 / 관심 / 주의 / 권리미확인 / 차익없음 / 위험 / 시세추정불가 / 미지원유형
    rights_verified: bool = False     # 권리분석 수행 여부 — 상세페이지 안전문구·뱃지 게이트
    # ---- 식별 보강 (T1): case_no 단일키는 같은 사건의 다른 물건번호를 덮어쓴다 ----
    court: str = ""                   # 관할 법원 (복합 식별자 구성요소)
    item_no: str = ""                 # 물건번호(maemulSer). 샘플/미상은 ""
    doc_id: str = ""                  # courtauction 고유 문서 id
    # ---- 비교군 scope (T3): 시세가 "어떤 집합"에서 나왔는지 — 신뢰 등급의 근거 ----
    # same_complex_same_area(추천 인정) / same_complex_near_area / same_dong_fallback(참고치)
    # / unsupported / no_comps. ""=레거시(스코프 미기록).
    market_scope: str = ""
    # ---- 가격 밴드 (T4): 단일 추정가 과신 방지 — 두 선으로 말한다 ----
    # 표본수는 matched_trades가 그 값(market_sample_count 역할). None=밴드 없음(추정불가/레거시).
    market_band_low: int | None = None    # 검증 하한가(트림 후 최저 평단가 기준, 보수)
    market_band_high: int | None = None   # 검증 기준가(트림 후 중앙값 = est_market_price)
    profit_low: int | None = None         # 보수 차익 = 검증 하한가 − 취득원가 − 인수보증금 (추천 판단 기준)
    profit_high: int | None = None        # 기준 차익 = 검증 기준가 − 취득원가 (= expected_profit, 표면)
    # (2026-07-22) 낙찰자 인수금액(원, 대항력 보증금 상한). 보수차익(profit_low)에서 차감해
    # '표면차익 양수인데 보증금 빼면 손해'인 함정 매물이 추천에 뜨지 않게 한다(빈틈1 수정).
    assumed_amount: int = 0
    # (T5) 밴드 실기반 표본수(최근성+트림 후 실사용 건수). matched_trades(원 매칭수)와 구분.
    # None=레거시(게이트 미적용). 게이트: < band_confident_basis(기본5) → 낮은 신뢰·추천 제외.
    market_sample_basis: int | None = None
    # 상세 시간축 차트용 개별 실거래 점 [(deal_ym, price)] — 최신순, 다년치 맥락 포함.
    # 밴드 산정과 독립(맥락 표시용). 저장 시 JSON 텍스트로 직렬화(store). 기본 빈 리스트.
    market_comps: list[tuple[str, int]] = field(default_factory=list)
    # 서빙 전용(비영속 — store._COLS 밖이라 마이그레이션 불필요). 네이버 KB시세·호가 원본 페이로드와
    # 시세·차익 출처. market_source: "kb"(KB부동산) | "molit"(국토부 추정) | "none"(시세없음).
    naver: dict | None = None
    market_source: str = ""

    @property
    def uid(self) -> str:
        """저장·중복제거용 복합 식별자. doc_id가 있으면 그것이 원천 고유키."""
        return self.doc_id or f"{self.court}|{self.case_no}|{self.item_no}"

    def to_row(self) -> dict:
        return asdict(self)
