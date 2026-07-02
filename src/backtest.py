"""백테스트 — 차익 스코어가 실제 수익으로 이어졌는지 검증 (V1).

낙찰결과 outcomes(case_no → 실제 낙찰가·실현 매도가)와 채점 결과(scored)를 조인해,
실현차익 = 실현매도가 − (실제 낙찰가 + 부대비용)을 계산하고, 스코어 구간별 적중률
(실현차익>0 비율)·평균 실현차익과 스코어 임계별 precision을 산출한다.

지금은 합성 fixture(data/backtest_outcomes.json)로 하네스 동작을 검증한다.
실제 낙찰결과 데이터가 들어오면 fixture만 교체하면 그대로 진짜 적중률이 측정된다.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import pipeline, score

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# 스코어 구간 (lo ≤ score < hi, label)
BUCKETS = [
    (80.0, 1e9, "≥80 차익유력"),
    (60.0, 80.0, "60–79 양호"),
    (40.0, 60.0, "40–59 관심"),
    (-1e9, 40.0, "<40 주의·위험"),
]


def load_outcomes(path: str | Path | None = None) -> dict:
    p = Path(path) if path else (DATA / "backtest_outcomes.json")
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if isinstance(v, dict) and "actual_nakchal" in v}


def realized_cost(listing, actual_nakchal: int) -> int:
    """실제 낙찰가 기준 취득원가(객관) = 낙찰가 + 취득세."""
    return actual_nakchal + score.acquisition_tax(actual_nakchal, listing.property_type)


def evaluate(scored=None, auctions=None, outcomes=None) -> list[dict]:
    """scored × outcomes 조인 → 물건별 실현차익·적중 여부."""
    scored = scored if scored is not None else pipeline.run()
    amap = {a.case_no: a for a in (auctions if auctions is not None else pipeline.load_sample_auctions())}
    outcomes = outcomes if outcomes is not None else load_outcomes()

    rows: list[dict] = []
    for s in scored:
        o = outcomes.get(s.case_no)
        if o is None or s.arb_score is None or s.case_no not in amap:
            continue
        cost = realized_cost(amap[s.case_no], o["actual_nakchal"])
        realized = o["realized_sale"] - cost
        rows.append({
            "case_no": s.case_no, "apt_name": s.apt_name, "arb_score": s.arb_score,
            "grade": s.grade, "actual_nakchal": o["actual_nakchal"],
            "realized_sale": o["realized_sale"], "realized_cost": cost,
            "realized_profit": realized, "hit": realized > 0,
        })
    return rows


def calibration(rows: list[dict]) -> list[dict]:
    """스코어 구간별 건수·적중률·평균 실현차익."""
    out = []
    for lo, hi, label in BUCKETS:
        b = [r for r in rows if lo <= r["arb_score"] < hi]
        if not b:
            out.append({"bucket": label, "n": 0, "hit_rate": None, "avg_profit": None})
            continue
        hits = sum(1 for r in b if r["hit"])
        out.append({
            "bucket": label, "n": len(b), "hit_rate": hits / len(b),
            "avg_profit": sum(r["realized_profit"] for r in b) / len(b),
        })
    return out


def precision_at(rows: list[dict], threshold: float) -> float | None:
    """스코어 ≥ threshold 인 물건 중 실제 수익(hit) 비율."""
    sel = [r for r in rows if r["arb_score"] >= threshold]
    if not sel:
        return None
    return sum(1 for r in sel if r["hit"]) / len(sel)
