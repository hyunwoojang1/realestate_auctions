# -*- coding: utf-8 -*-
"""국토부 병렬 하이브리드 브리지(H5) 테스트 — 지문 확정·보수 게이트·보충 주입."""
from src.models import AuctionListing, Trade
from src.molit_bridge import BridgeIndex, MIN_MATCHES


def _lst(**kw) -> AuctionListing:
    d = dict(case_no="T", court="c", address="충북 진천군 진천읍", lawd_cd="43750",
             dong="진천읍", apt_name="진천태왕아너스1단지", property_type="아파트",
             area_m2=84.96, appraisal_price=3_5300_0000, min_bid_price=2_3000_0000,
             fail_count=1, sale_date="2026-08-01")
    d.update(kw)
    return AuctionListing(**d)


def _tr(ym, price, floor, name="태왕아너스1단지", area=84.96, lawd="43750", **kw) -> Trade:
    return Trade(apt_name=name, area_m2=area, price=price, deal_ym=ym,
                 dong="진천읍", floor=floor, kind="apt", lawd_cd=lawd, **kw)


def _naver(ym, price, floor):
    return {"trade_ymd": ym + "15", "price": price, "floor": floor, "exclusive_area": 0.0}


# 네이버 확정 이력 5건(풀 창 내) — 국토부 그룹과 (ym·price·floor) 정확일치 지문.
NAVER_ROWS = [
    _naver("202605", 330_000_000, 10),
    _naver("202603", 328_000_000, 6),
    _naver("202601", 325_000_000, 3),
    _naver("202511", 320_000_000, 12),
    _naver("202508", 315_000_000, 7),
]
POOL_MATCH = [
    _tr("202605", 330_000_000, 10),
    _tr("202603", 328_000_000, 6),
    _tr("202601", 325_000_000, 3),
    _tr("202511", 320_000_000, 12),
    _tr("202508", 315_000_000, 7),
]
FRESH = _tr("202606", 335_000_000, 9)   # 네이버가 아직 못 본 최신 거래


def test_topup_injects_fresh_trade_only():
    idx = BridgeIndex(POOL_MATCH + [FRESH])
    out = idx.topup(_lst(), NAVER_ROWS)
    assert len(out) == 1
    assert out[0]["price"] == 335_000_000
    assert out[0]["trade_ymd"].startswith("202606")
    assert out[0]["src"] == "molit_bridge"


def test_no_duplicate_of_known_trades():
    idx = BridgeIndex(POOL_MATCH)          # 신규분 없음 — 전부 지문과 동일
    assert idx.topup(_lst(), NAVER_ROWS) == []


def test_insufficient_fingerprint_no_topup():
    # 정확일치가 MIN_MATCHES 미만이면 보충하지 않는다(대응 미확정).
    pool = POOL_MATCH[: MIN_MATCHES - 1] + [FRESH]
    idx = BridgeIndex(pool)
    assert idx.topup(_lst(), NAVER_ROWS) == []


def test_coverage_gate_blocks_partial_match():
    # 창내 네이버 6건 중 3건만 매칭(50% < 60%) → MIN_MATCHES는 충족해도 커버리지로 차단.
    naver6 = NAVER_ROWS + [_naver("202510", 318_000_000, 4)]
    pool = POOL_MATCH[:3] + [
        _tr("202511", 999_000_000, 12),    # 가격 불일치 — 지문 미스
        _tr("202508", 888_000_000, 7),
        FRESH,
    ]
    idx = BridgeIndex(pool)
    assert idx.topup(_lst(), naver6) == []


def test_ambiguous_two_groups_no_topup():
    # 두 이름 그룹이 비슷하게 매칭(1위 < 2위×2) → 모호 — 보충 생략.
    rival = [_tr(t.deal_ym, t.price, t.floor, name="태왕아너스") for t in POOL_MATCH[:4]]
    idx = BridgeIndex(POOL_MATCH + rival + [FRESH])
    assert idx.topup(_lst(), NAVER_ROWS) == []


def test_same_name_other_dong_not_merged():
    """(감사 2026-07-20 HIGH) 같은 시군구·같은 이름·같은 면적이라도 동이 다르면 별도 그룹 —
    타단지(동명이단지) 거래가 '확정 같은단지'로 주입되지 않는다."""
    alien = [_tr("202606", 990_000_000, 9), _tr("202605", 985_000_000, 3)]
    alien = [Trade(apt_name=t.apt_name, area_m2=t.area_m2, price=t.price, deal_ym=t.deal_ym,
                   dong="덕산읍", floor=t.floor, kind="apt", lawd_cd="43750") for t in alien]
    idx = BridgeIndex(POOL_MATCH + alien + [FRESH])
    out = idx.topup(_lst(), NAVER_ROWS)
    assert [o["price"] for o in out] == [335_000_000]   # 진천읍(지문 일치) 그룹의 신규분만


def test_floor_zero_asymmetric_dedup():
    """(감사 2026-07-20 LOW) 층 결측(0) 비대칭 — 네이버가 층 없이 아는 거래를
    국토부 층 있는 동일 (월·가격) 행으로 재주입(중복 카운트)하지 않는다."""
    naver = NAVER_ROWS + [_naver("202606", 335_000_000, 0)]   # FRESH와 같은 월·가격, 층 결측
    idx = BridgeIndex(POOL_MATCH + [FRESH])
    assert idx.topup(_lst(), naver) == []


def test_cancelled_and_other_lawd_excluded():
    cancelled = Trade(apt_name="태왕아너스1단지", area_m2=84.96, price=400_000_000,
                      deal_ym="202606", dong="진천읍", floor=5, kind="apt",
                      lawd_cd="43750", cdeal_type="O")
    other_lawd = _tr("202606", 336_000_000, 2, lawd="11350")
    idx = BridgeIndex(POOL_MATCH + [cancelled, other_lawd])
    out = idx.topup(_lst(), NAVER_ROWS)
    assert all(o["price"] not in (400_000_000, 336_000_000) for o in out)


def test_disabled_by_env(monkeypatch):
    from src import molit_bridge
    monkeypatch.setenv("AUCTION_MOLIT_BRIDGE", "0")
    assert not molit_bridge.enabled()
    monkeypatch.delenv("AUCTION_MOLIT_BRIDGE", raising=False)
    assert molit_bridge.enabled()


def test_pipeline_wiring_appends_bridge_rows():
    """pipeline.run 통합 — 브리지 보충분이 추정 rows에 합류해 최신월이 반영된다."""
    from src import pipeline

    lst = _lst()
    lookup = {("c", "T", ""): list(NAVER_ROWS)}
    scored = pipeline.run(
        auctions=[lst], trades=POOL_MATCH + [FRESH],
        real_trades_lookup=lambda l: lookup.get((l.court, l.case_no, str(l.item_no or ""))),
    )
    assert len(scored) == 1
    s = scored[0]
    assert s.market_scope == "same_complex_same_area"
    # 보충된 202606 거래가 comps에 존재
    assert any(c[0] == "202606" and c[1] == 335_000_000 for c in s.market_comps)
