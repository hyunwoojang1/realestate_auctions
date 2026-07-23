"""증분 크롤 타겟팅 테스트 (2026-07-23 배선: 변경 재보강 · tenant_checks · P-08 우선순위).

발견(리스트 diff) → 보강(신규+변경만) 구조의 핵심인 세 함수를 검증한다:
  _stale_rights_keys  — 유찰 새 회차·기일변경으로 낡은 요지 감지(변경축)
  _tenant_targets     — 현황조사서 백필 대상(tenant_checks 마커 + P-08 추천물건 클래스)
  _targets(stale=)    — 낡은 요지를 재크롤 대상에 환원
"""
from __future__ import annotations

import json

from deploy.crawl_rights import (
    _ensure_tenant_checks,
    _record_tenant_check,
    _stale_rights_keys,
    _targets,
    _tenant_targets,
)
from src import store
from src.courtauction_detail import CaseRights
from src.models import ScoredListing

FUTURE = "2099-01-01"


def _seed(conn, court, case_no, *, grade="권리미확인", verified=False, sale_date=FUTURE,
          surviving="", senior="2002. 4. 23. 근저당권", schedule=None, item_no="1",
          fail_count=2):
    """scored + raw(boCd) + rights 를 한 물건분 시드."""
    s = ScoredListing(
        case_no=case_no, apt_name="T", address="A", property_type="아파트", area_m2=84.9,
        appraisal_price=100, min_bid_price=80, fail_count=fail_count, sale_date=sale_date,
        est_market_price=None, matched_trades=0, confidence=0.6, real_acquisition_cost=90,
        expected_profit=None, gap_rate=None, gap_score=0.0, rights_score=0.0,
        liquidity_score=0.0, arb_score=None, grade=grade, rights_verified=verified,
        court=court, item_no=item_no)
    store.upsert(conn, [s])
    conn.execute(
        "INSERT OR REPLACE INTO raw_listings (uid, doc_id, court, case_no, item_no, raw_json, fetched_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (f"{court}|{case_no}|{item_no}", "d", court, case_no, item_no,
         json.dumps({"boCd": "B000210"}), "2026-07-23"))
    cr = CaseRights(court=court, case_no=case_no, item_no=item_no, senior_lien=senior,
                    surviving_rights=surviving, spec_write_ymd="2026-01-01",
                    schedule=schedule or [])
    store.save_rights(conn, [cr.to_row()])
    conn.commit()


def _ev(ymd, kind="매각기일"):
    return {"ymd": ymd, "kind": kind, "result": "", "price": 0}


# ---- _stale_rights_keys: 변경(유찰 새 회차·기일변경) 감지 ----

def test_stale_detects_new_round_missing_from_schedule(tmp_path):
    """유찰 후 리스트 sale_date는 새 회차인데 저장 schedule엔 옛 기일뿐 → stale."""
    conn = store.connect(str(tmp_path / "t.db"))
    _seed(conn, "법원A", "2025타경1", sale_date="2099-02-01",
          schedule=[_ev("2099-01-01")])                      # 옛 회차만
    assert ("법원A", "2025타경1", "1") in _stale_rights_keys(conn)
    conn.close()


def test_not_stale_when_schedule_covers_current_sale_date(tmp_path):
    """schedule에 현재 sale_date와 같은/이후의 매각기일이 있으면 최신 — 재크롤 불필요."""
    conn = store.connect(str(tmp_path / "t.db"))
    _seed(conn, "법원A", "2025타경2", sale_date="2099-02-01",
          schedule=[_ev("2099-02-01")])
    assert _stale_rights_keys(conn) == set()
    conn.close()


def test_stale_kind_filter_ignores_decision_dates(tmp_path):
    """매각결정기일(매각기일+~1주)이 sale_date 이후여도 '매각기일'이 없으면 stale —
    기일변경으로 앞당겨진 물건을 결정기일이 가리는 것 방지."""
    conn = store.connect(str(tmp_path / "t.db"))
    _seed(conn, "법원A", "2025타경3", sale_date="2099-02-01",
          schedule=[_ev("2099-01-25"), _ev("2099-02-03", kind="매각결정기일")])
    assert ("법원A", "2025타경3", "1") in _stale_rights_keys(conn)
    conn.close()


def test_stale_excludes_past_sale_dates(tmp_path):
    """지난 기일 물건은 재크롤 낭비 — stale 대상 아님."""
    conn = store.connect(str(tmp_path / "t.db"))
    _seed(conn, "법원A", "2025타경4", sale_date="2020-01-01", schedule=[])
    assert _stale_rights_keys(conn) == set()
    conn.close()


def test_targets_reinstate_stale_keys(tmp_path):
    """rights가 이미 있으면 통상 제외되지만, stale로 지정되면 재크롤 대상에 환원된다."""
    conn = store.connect(str(tmp_path / "t.db"))
    _seed(conn, "법원A", "2025타경5", sale_date="2099-02-01", schedule=[_ev("2099-01-01")])
    assert _targets(conn, None, refresh=False) == []          # 크롤됨 → 제외(기존 동작)
    stale = _stale_rights_keys(conn)
    got = _targets(conn, None, refresh=False, stale=stale)
    assert [t["case_no"] for t in got] == ["2025타경5"]        # 환원됨
    conn.close()


# ---- _tenant_targets: tenant_checks 마커 + P-08 우선순위 ----

def test_tenant_targets_p08_reco_empty_rights_top_priority(tmp_path):
    """P-08: 추천등급 + 인수권리란 빈칸(verified=1)이 최우선(prio 0)으로 포함 —
    종전 필터(verified=0만)는 이 클래스를 구조적으로 영원히 배제했다."""
    conn = store.connect(str(tmp_path / "t.db"))
    _ensure_tenant_checks(conn)
    _seed(conn, "법원A", "2025타경10", grade="양호", verified=True, surviving="")
    _seed(conn, "법원A", "2025타경11", grade="권리미확인", verified=False)
    got = _tenant_targets(conn, None)
    assert [(t["case_no"], t["prio"]) for t in got] == [("2025타경10", 0), ("2025타경11", 1)]
    conn.close()


def test_tenant_targets_excludes_verified_with_burden_text(tmp_path):
    """추천이라도 인수권리란에 문구가 있으면(요지가 이미 말함) 백필 대상 아님."""
    conn = store.connect(str(tmp_path / "t.db"))
    _ensure_tenant_checks(conn)
    _seed(conn, "법원A", "2025타경12", grade="양호", verified=True,
          surviving="을구 3번 임차권등기 매수인 인수")
    assert _tenant_targets(conn, None) == []
    conn.close()


def test_tenant_targets_skips_checked_and_records_persist(tmp_path):
    """tenant_checks 기록된 물건은 제외 — 빈 현황조사서(공실)도 기록되므로 매일 재크롤 안 함."""
    conn = store.connect(str(tmp_path / "t.db"))
    _ensure_tenant_checks(conn)
    _seed(conn, "법원A", "2025타경13", grade="권리미확인", verified=False)
    assert len(_tenant_targets(conn, None)) == 1
    _record_tenant_check(conn, "법원A", "2025타경13", "1", "2026-07-23 18:00:00")
    assert _tenant_targets(conn, None) == []                  # 시도 완료 → 제외
    conn.close()


def test_tenant_targets_excludes_past_and_unsupported_and_no_senior(tmp_path):
    """지난 기일 / 미지원유형 / 말소기준 없음(여지 판정 불가)은 대상 아님."""
    conn = store.connect(str(tmp_path / "t.db"))
    _ensure_tenant_checks(conn)
    _seed(conn, "법원A", "2025타경14", sale_date="2020-01-01")        # 지난 기일
    _seed(conn, "법원A", "2025타경15", grade="미지원유형")             # 미지원
    _seed(conn, "법원A", "2025타경16", senior="")                     # 말소기준 없음
    assert _tenant_targets(conn, None) == []
    conn.close()


def test_ensure_tenant_checks_seeds_from_existing_tenants(tmp_path):
    """listing_tenants 보유 물건은 '확실히 시도됨' — ensure가 자동 시드해 재크롤 방지(멱등)."""
    conn = store.connect(str(tmp_path / "t.db"))
    _seed(conn, "법원A", "2025타경17", grade="권리미확인", verified=False)
    store.save_tenants(conn, "법원A", "2025타경17", "1",
                       [{"movein_ymd": "1996-10-14", "confirm_ymd": "", "deposit": 0,
                         "possession": "", "usage": "", "part": "", "is_tenant_like": False}],
                       fetched_at="2026-07-23")
    _ensure_tenant_checks(conn)
    assert _tenant_targets(conn, None) == []                  # 시드됨 → 제외
    _ensure_tenant_checks(conn)                               # 멱등
    assert conn.execute("SELECT COUNT(*) FROM tenant_checks").fetchone()[0] == 1
    conn.close()
