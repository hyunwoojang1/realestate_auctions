"""인수금액 확인 물건의 목록 차익 숨김 — 2026-07-26 사용자 결정.

인수금액이 **확인된**(badge.assumed>0) 물건은 최악치를 뺀 값이라도 단일 숫자로 보이면
'확정 차익'으로 오독된다 → 홈 카드·전체탐색 테이블·모바일 카드에서 숫자 대신
'인수 확인'을 띄운다. 금액 **미상**(+α) 물건은 현행 유지(⚠·−α 표기 그대로).
물건 상세는 범위(최악~최선)와 라벨로 이미 설명하므로 변경하지 않는다.
"""
import json

from src import store
from src.models import ScoredListing
from src.web import create_app


def _listing(case_no: str, apt: str, profit_low: int) -> ScoredListing:
    return ScoredListing(
        case_no=case_no, apt_name=apt, address="서울 강남구 역삼동 1-2",
        property_type="아파트", area_m2=84.0, appraisal_price=500_000_000,
        min_bid_price=256_000_000, fail_count=1, sale_date="2026-08-01",
        est_market_price=480_000_000, matched_trades=5, confidence=1.0,
        real_acquisition_cost=270_000_000, expected_profit=profit_low, gap_rate=0.4,
        gap_score=90.0, rights_score=100.0, liquidity_score=80.0, arb_score=88.0,
        grade="차익 유력", court="서울중앙지방법원", item_no="1", rights_verified=True,
        market_band_low=470_000_000, profit_low=profit_low,
    )


def _rights_row(s: ScoredListing, surviving: str) -> dict:
    return {
        "court": s.court, "case_no": s.case_no, "item_no": s.item_no,
        "surviving_rights": surviving, "senior_lien": "2020. 1. 1. 근저당권",
        "lien_note": "", "remark": "", "claim_amt": None, "demand_end": "",
        "spec_write_ymd": "2026-06-01", "court_dept": "",
        "schedule": json.dumps([{"ymd": "2026-08-01", "kind": "매각기일",
                                 "result": "", "price": 256000000}], ensure_ascii=False),
        "appraisal_notes": "[]", "fetched_at": "2026-07-24",
    }


def _bodies(tmp_path, monkeypatch):
    db = tmp_path / "t_burden_hide.db"
    conn = store.connect(str(db))
    # A = 인수금액 확인(보증금 6,500만원 인수 → assumed 6천5백) · B = 부담 있으나 금액 미상(+α)
    a = _listing("2024타경100", "부담확인단지", 200_000_000)   # p_adj = 2.00 − 0.65 = 1.35억
    b = _listing("2024타경200", "부담미상단지", 321_000_000)   # 3.21억 — 현행대로 노출돼야 함
    store.replace_all(conn, [a, b])
    store.save_rights(conn, [
        _rights_row(a, "임차인 보증금 6,500만원 인수 부담"),
        _rights_row(b, "대항력 있는 임차인 점유 — 보증금 액수는 알 수 없음"),
    ])
    conn.close()
    monkeypatch.setenv("AUCTION_DB", str(db))
    c = create_app().test_client()
    return (c.get("/").get_data(as_text=True),
            c.get("/?all=1").get_data(as_text=True))


def test_assumed_burden_hides_profit_number(tmp_path, monkeypatch):
    """금액 확인 물건: 세 표면(홈 카드·테이블·모바일 카드) 모두 차익 숫자 금지."""
    home, allpage = _bodies(tmp_path, monkeypatch)
    for body in (home, allpage):
        assert "1.35억" not in body, "조정 차익이 여전히 노출된다"
        assert "2.00억" not in body, "무조정 차익이 노출된다"
        assert "인수 확인" in body
    assert "인수 확인 필요" in allpage           # 테이블 서브라벨


def test_unknown_burden_keeps_current_display(tmp_path, monkeypatch):
    """금액 미상 물건은 현행 유지 — 차익 숫자 + ⚠/−α 경고 표기."""
    home, allpage = _bodies(tmp_path, monkeypatch)
    assert "3.21억" in home
    assert "3.21억" in allpage
    assert "−α" in allpage                        # 테이블 −α 표기 유지


def test_hidden_rows_are_dimmed(tmp_path, monkeypatch):
    """차익을 못 보여주는 행은 dim — 기존 '표시 차익 없음 행' 규칙과 동일하게."""
    _, allpage = _bodies(tmp_path, monkeypatch)
    # 부담확인단지 행(prow)이 dim 클래스를 갖는다 — 행 블록을 잘라 확인.
    import re
    m = re.search(r'<div class="prow[^"]*">(?:(?!class="prow).)*?부담확인단지', allpage, re.S)
    assert m, "부담확인단지 행을 찾지 못함"
    assert "dim" in m.group(0).split(">")[0], "인수금액 확인 행이 dim 처리되지 않음"
