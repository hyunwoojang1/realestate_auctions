"""백테스트 하네스 테스트 (V1)."""
from src import backtest


def _rows():
    return backtest.evaluate()


def test_join_produces_rows():
    rows = _rows()
    # T2: 샘플 6건 중 화곡 빌라(다세대)는 미지원 유형 → arb_score None → 백테스트 제외.
    assert len(rows) == 5
    assert all("realized_profit" in r and "hit" in r for r in rows)


def test_high_score_profits_low_score_loses():
    rows = {r["case_no"]: r for r in _rows()}
    assert rows["2024타경51234"]["realized_profit"] > 0   # 상계주공 95 → 수익
    assert rows["2024타경88765"]["realized_profit"] < 0   # 해운대(인수 게이트) → 손실
    # T2: 화곡 빌라(2024타경44102, 다세대)는 미지원 유형이라 채점·백테스트 대상이 아니다.
    assert "2024타경44102" not in rows


def test_calibration_is_monotonic():
    cal = {c["bucket"]: c for c in backtest.calibration(_rows())}
    assert cal["≥80 차익유력"]["hit_rate"] == 1.0
    assert cal["<40 주의·위험"]["hit_rate"] == 0.0
    # 고스코어 평균 실현차익 > 저스코어
    assert cal["≥80 차익유력"]["avg_profit"] > cal["<40 주의·위험"]["avg_profit"]


def test_precision_at_thresholds():
    rows = _rows()
    assert backtest.precision_at(rows, 80) == 1.0   # ≥80 전부 수익
    p40 = backtest.precision_at(rows, 40)
    assert 0.5 <= p40 <= 1.0                          # 광교(손실) 섞여 100% 미만
