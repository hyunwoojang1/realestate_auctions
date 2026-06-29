"""차익 스코어 파라미터 외부화.

모든 튜닝 파라미터를 ScoreConfig에 모아, data/score_config.json이 있으면 그 값으로 덮어쓴다.
코드 수정 없이 가중치·세율·페널티를 조정할 수 있다. 파일이 없으면 기본값(현 동작과 동일).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ScoreConfig:
    # 가중치 (합 1.0)
    w_gap: float = 0.50
    w_rights: float = 0.30
    w_liq: float = 0.20

    # 부대비용
    repair_per_m2: int = 100_000
    eviction_cost: dict = field(default_factory=lambda: {
        "공실": 0, "임차인": 3_000_000, "소유자점유": 5_000_000, "다수점유": 10_000_000,
    })
    eviction_cost_default: int = 5_000_000
    # 취득세 구간: [[상한(원), 세율], ...] 마지막 상한은 null(=초과 전체)
    acq_tax_brackets: list = field(default_factory=lambda: [
        [600_000_000, 0.011], [900_000_000, 0.022], [None, 0.033],
    ])

    # 권리 페널티 / 하드게이트
    special_penalty: dict = field(default_factory=lambda: {
        "유치권": 30, "법정지상권": 25, "지분": 20, "분묘기지권": 20, "대지권미등기": 15, "위반건축물": 15,
    })
    special_penalty_default: int = 10
    fatal_special: list = field(default_factory=lambda: ["유치권"])
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
    # 등급 경계 [[최소점수, 등급], ...] (내림차순)
    grade_thresholds: list = field(default_factory=lambda: [
        [80, "확실한 차익"], [60, "양호"], [40, "관심"], [0, "주의"],
    ])


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "score_config.json"


def load_config(path: str | Path | None = None) -> ScoreConfig:
    """data/score_config.json(있으면)으로 기본값을 덮어써 ScoreConfig 반환."""
    cfg = ScoreConfig()
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    if p.exists():
        overrides = json.loads(p.read_text(encoding="utf-8"))
        for k, v in overrides.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
    return cfg


# 모듈 전역 — score.py가 참조. 런타임 교체 시 monkeypatch(score.CONFIG=...) 가능.
CONFIG = load_config()
