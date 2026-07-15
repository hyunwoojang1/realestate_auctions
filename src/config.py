"""차익 스코어 파라미터 외부화.

모든 튜닝 파라미터를 ScoreConfig에 모아, data/score_config.json이 있으면 그 값으로 덮어쓴다.
코드 수정 없이 가중치·세율·페널티를 조정할 수 있다. 파일이 없으면 기본값(현 동작과 동일).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ScoreConfig:
    # 가중치 (합 1.0)
    w_gap: float = 0.50
    w_rights: float = 0.30
    w_liq: float = 0.20

    # 취득세는 src/tax.py(BuyerProfile 기반 정밀 계산, docs/tax-auction-knowledge.md와 1:1)가 담당.
    # 명도비·수리비·인수금액은 물건별 편차가 큰 주관적 비용이라 취득원가에서 제외(객관성 우선).

    # 권리 페널티 / 하드게이트
    special_penalty: dict = field(default_factory=lambda: {
        "유치권": 30, "법정지상권": 25, "지분": 20, "분묘기지권": 20, "대지권미등기": 15, "위반건축물": 15,
        "지분매각": 30,
    })
    special_penalty_default: int = 10
    # 지분매각 = 통물건 실거래 comps로 시세를 매기면 지분을 온전물건 값으로 과대평가(실측 376건) →
    # 차익 추천에서 제외하기 위해 하드게이트. (감사 2026-07-15, 법원 명세서 '지분매각' 분류 기반)
    fatal_special: list = field(default_factory=lambda: ["유치권", "지분매각"])
    assumed_ratio_gate: float = 0.30
    occupant_penalty: dict = field(default_factory=lambda: {
        "공실": 0, "임차인": 10, "소유자점유": 15, "다수점유": 25,
    })
    occupant_penalty_default: int = 15
    tenant_opposable_penalty: float = 30
    gate_ceiling: float = 25.0

    # 환금성
    type_base: dict = field(default_factory=lambda: {
        "아파트": 90, "오피스텔": 75, "다세대": 60, "빌라": 60, "연립": 60, "상가": 45, "토지": 35,
    })
    type_base_default: int = 30

    # 가격갭 점수 보간점 [[갭률, 점수], ...] (오름차순)
    gap_points: list = field(default_factory=lambda: [
        [-1.0, 0.0], [0.0, 0.0], [0.10, 35.0], [0.20, 60.0], [0.30, 80.0], [0.40, 100.0], [1.0, 100.0],
    ])
    # 신뢰계수 사다리 [[최소 매칭건수, 계수], ...] (내림차순)
    confidence_ladder: list = field(default_factory=lambda: [
        [3, 1.0], [2, 0.85], [1, 0.70], [0, 0.60],
    ])
    # 등급 경계 [[최소점수, 등급], ...] (내림차순).
    # '차익 유력' — 법원경매 특성상 '확실/보장'은 방어 불가하므로 단정 표현을 피한다.
    grade_thresholds: list = field(default_factory=lambda: [
        [80, "차익 유력"], [60, "양호"], [40, "관심"], [0, "주의"],
    ])

    # 표본(comps) 게이트 — 허위 차익 방지.
    #  - min_comps_price: 이 미만이면 '시세'로 신뢰하지 않음(1건 중앙값을 시세로 쓰지 않는다).
    #  - min_comps_confident: 최상위 '차익 유력'(신뢰계수 1.0)에 필요한 최소 매칭건수.
    min_comps_price: int = 2
    min_comps_confident: int = 3


@dataclass
class SampleConfig:
    """표본 수집·매칭의 튜닝 파라미터 (신뢰계수 표본 개선용).

    라이브 매칭 표본이 빈약한 실제 원인 두 가지를 코드수정 없이 조정 가능하게 노출한다:
      - live_months: 라이브 시세를 몇 개월치 실거래로 모을지(수집 '폭').
      - area_band: 단지명/법정동 매칭 시 허용 전용면적 오차(±비율). 좁으면 comps가 적다.
    우선순위: 명시 인자 > 환경변수(AUCTION_LIVE_MONTHS/AUCTION_AREA_BAND) > 기본값.
    기본값은 기존 동작과 동일(무회귀).
    """
    live_months: int = 3        # 기존 pipeline.LIVE_MONTHS 기본값과 동일
    area_band: float = 0.10     # 기존 matcher.AREA_BAND 기본값과 동일
    # (T5) 표본 게이트 — 밴드 실기반 표본수(최근성 필터+이상치 트림 후 실제 사용 건수) 기준.
    #  - band_min_basis 미만(기본 0~2건): 밴드 생성 금지 → '시세근거 부족'(추정 자체를 안 함).
    #  - band_confident_basis 미만(기본 3~4건): 밴드는 만들되 낮은 신뢰 — 추천 제외 + 경고.
    #  - band_confident_basis 이상(기본 5건↑): 정상 추천 가능.
    # 근거: 문서 10장 — 소표본 분위수는 통계 흉내, 과감히 '시세근거 부족'이라 말해야 한다.
    band_min_basis: int = 3
    band_confident_basis: int = 5

    def __post_init__(self) -> None:
        # 하한 방어: 최소 1개월, 면적밴드 0 초과, 게이트 단조(1 ≤ min ≤ confident).
        self.live_months = max(1, int(self.live_months))
        if self.area_band <= 0:
            self.area_band = 0.10
        self.band_min_basis = max(1, int(self.band_min_basis))
        self.band_confident_basis = max(self.band_min_basis, int(self.band_confident_basis))


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "score_config.json"
DEFAULT_SAMPLE_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_config.json"


def _validate(cfg: ScoreConfig) -> None:
    """오버라이드 후 정합성 점검 — 값을 바꾸지 않고 경고만 남긴다(조용한 오설정 방지).

    가중치 합·사다리 단조성·취득세 구간 종료를 검사한다. 잘못된 config가 코드 수정 없이
    전체 랭킹을 뒤집는 사고를 로그로 드러낸다.
    """
    wsum = cfg.w_gap + cfg.w_rights + cfg.w_liq
    if abs(wsum - 1.0) > 0.01:
        logger.warning("score_config 가중치 합이 1.0이 아님(%.3f) — 스코어 스케일이 의도와 다를 수 있음", wsum)
    ladder_ns = [n for n, _ in cfg.confidence_ladder]
    if ladder_ns != sorted(ladder_ns, reverse=True):
        logger.warning("confidence_ladder 매칭건수가 내림차순이 아님 — 신뢰계수 산정이 어긋날 수 있음")
    gap_xs = [x for x, _ in cfg.gap_points]
    if gap_xs != sorted(gap_xs):
        logger.warning("gap_points 갭률이 오름차순이 아님 — 보간이 어긋날 수 있음")


def load_config(path: str | Path | None = None) -> ScoreConfig:
    """data/score_config.json(있으면)으로 기본값을 덮어써 ScoreConfig 반환."""
    cfg = ScoreConfig()
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    if p.exists():
        overrides = json.loads(p.read_text(encoding="utf-8"))
        for k, v in overrides.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
    _validate(cfg)
    return cfg


def load_sample_config(path: str | Path | None = None,
                       env: dict | None = None) -> SampleConfig:
    """표본 튜닝값 로드. 파일(sample_config.json) → 환경변수 순으로 덮어쓴다.

    우선순위(낮음→높음): 기본값 → JSON 파일 → 환경변수. 아무 것도 없으면 기존 동작 유지.
    """
    import os  # noqa: PLC0415

    cfg = SampleConfig()
    p = Path(path) if path else DEFAULT_SAMPLE_CONFIG_PATH
    if p.exists():
        overrides = json.loads(p.read_text(encoding="utf-8"))
        for k, v in overrides.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)

    e = env if env is not None else os.environ
    lm = (e.get("AUCTION_LIVE_MONTHS") or "").strip()
    ab = (e.get("AUCTION_AREA_BAND") or "").strip()
    bm = (e.get("AUCTION_BAND_MIN_BASIS") or "").strip()
    bc = (e.get("AUCTION_BAND_CONFIDENT_BASIS") or "").strip()
    if lm:
        try:
            cfg.live_months = int(lm)
        except ValueError:
            pass
    if ab:
        try:
            cfg.area_band = float(ab)
        except ValueError:
            pass
    if bm:
        try:
            cfg.band_min_basis = int(bm)
        except ValueError:
            pass
    if bc:
        try:
            cfg.band_confident_basis = int(bc)
        except ValueError:
            pass

    cfg.__post_init__()  # 오버라이드 후 하한 방어 재적용
    return cfg


# 모듈 전역 — score.py가 참조. 런타임 교체 시 monkeypatch(score.CONFIG=...) 가능.
CONFIG = load_config()
# 표본 튜닝 전역 — pipeline/matcher가 참조. monkeypatch(config.SAMPLE=...) 로 교체 가능.
SAMPLE = load_sample_config()
