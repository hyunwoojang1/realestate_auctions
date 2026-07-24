"""원문 바로가기(법원·네이버·등기) — 상세페이지 아웃바운드 링크 계약.

배경(2026-07-24): 사용자가 "이 물건, 법원경매에선 실제로 어떻게 나와 있지?"를 원클릭으로
확인하고 싶다는 요구. 라이브 프로브 결과:
  · 법원 SPA 는 물건 딥링크 불가(파라미터 메모리 전달) → **경매사건검색 화면(PGJ159M00)
    w2xPath 직행**(검증됨) + 사건번호 클립보드 복사.
  · 네이버는 complex_no 로 완전 딥링크(new.land) — 매칭 물건만 노출.
  · 등기소는 딥링크 없음(로그인·유료) → 첫 화면 + 주소 복사.
"""
import json

from src import store
from src.models import ScoredListing
from src.web import create_app

COURT_SEARCH = "w2xPath=/pgj/ui/pgj100/PGJ159M00.xml"


def _seed(tmp_path, with_naver: bool):
    # 변형별 별도 파일 — 같은 파일을 재사용하면 앞선 시드의 naver 행이 남아 오염된다.
    db = tmp_path / f"t_{'naver' if with_naver else 'plain'}.db"
    conn = store.connect(str(db))
    s = ScoredListing(
        case_no="2024타경777", apt_name="테스트단지", address="서울 강남구 역삼동 1-2",
        property_type="아파트", area_m2=84.0, appraisal_price=500_000_000,
        min_bid_price=200_000_000, fail_count=1, sale_date="2026-08-01",
        est_market_price=400_000_000, matched_trades=5, confidence=1.0,
        real_acquisition_cost=202_200_000, expected_profit=197_800_000, gap_rate=0.49,
        gap_score=90.0, rights_score=100.0, liquidity_score=80.0, arb_score=88.0,
        grade="차익 유력", court="서울중앙지방법원", item_no="1", rights_verified=True,
        market_band_low=380_000_000, profit_low=177_800_000,
    )
    store.replace_all(conn, [s])
    store.save_rights(conn, [{
        "court": s.court, "case_no": s.case_no, "item_no": s.item_no,
        "surviving_rights": "", "senior_lien": "2020. 1. 1. 근저당권", "lien_note": "",
        "remark": "특이사항 없음", "claim_amt": None, "demand_end": "",
        "spec_write_ymd": "2026-06-01", "court_dept": "",
        "schedule": json.dumps([{"ymd": "2026-08-01", "kind": "매각기일",
                                 "result": "", "price": 200000000}], ensure_ascii=False),
        "appraisal_notes": "[]", "fetched_at": "2026-07-24",
    }])
    if with_naver:
        store.save_naver_price(conn, {
            "court": s.court, "case_no": s.case_no, "item_no": s.item_no,
            "status": "matched_kb", "complex_no": "12345", "complex_name": "테스트단지",
            "area_no": "1", "match_conf": 0.9, "kb_low": 380_000_000,
            "kb_avg": 400_000_000, "kb_high": 420_000_000, "lease_avg": 250_000_000,
            "ask_min": 0, "ask_max": 0, "ask_count": 0, "base_ymd": "20260720",
            "fetched_at": "2026-07-24", "lease_low": 0, "lease_high": 0,
        })
    conn.close()
    return db


def _detail(tmp_path, with_naver, monkeypatch):
    monkeypatch.setenv("AUCTION_DB", str(_seed(tmp_path, with_naver)))
    c = create_app().test_client()
    return c.get("/property/2024타경777").get_data(as_text=True)


def test_court_link_targets_case_search_and_copies_case_no(tmp_path, monkeypatch):
    """법원 버튼 = 경매사건검색 화면 직행 + 사건번호 복사(붙여넣기 1회로 도착)."""
    body = _detail(tmp_path, False, monkeypatch)
    assert COURT_SEARCH in body, "법원 링크가 사건검색 화면(PGJ159M00)을 가리키지 않는다"
    assert 'data-copy="2024타경777"' in body
    assert "서울중앙지방법원 선택 후 붙여넣" in body     # 어느 법원을 고를지 토스트가 안내


def test_naver_link_only_when_complex_matched(tmp_path, monkeypatch):
    """네이버 버튼은 complex_no 매칭 물건에만 — 없는 물건에 죽은 링크를 주지 않는다."""
    with_n = _detail(tmp_path, True, monkeypatch)
    assert "new.land.naver.com/complexes/12345" in with_n
    without = _detail(tmp_path, False, monkeypatch)
    assert "new.land.naver.com" not in without


def test_iros_link_copies_address(tmp_path, monkeypatch):
    """등기 버튼 = 등기소 첫 화면 + 소재지 주소 복사(딥링크가 없는 소스의 최선)."""
    body = _detail(tmp_path, False, monkeypatch)
    assert "iros.go.kr" in body
    assert 'data-copy="서울 강남구 역삼동 1-2"' in body


def test_all_outbound_links_are_noopener_blank(tmp_path, monkeypatch):
    """아웃바운드 3종 전부 새 탭 + noopener(탭 하이재킹 방지)."""
    import re
    body = _detail(tmp_path, True, monkeypatch)
    for host in ("courtauction.go.kr", "new.land.naver.com", "iros.go.kr"):
        for m in re.finditer(rf'<a[^>]+{re.escape(host)}[^>]*>', body):
            tag = m.group(0)
            assert 'target="_blank"' in tag, tag[:120]
            assert "noopener" in tag, tag[:120]


def test_copy_script_wired(tmp_path, monkeypatch):
    """클립보드+토스트 스크립트가 페이지에 배선돼 있다(클래스·클립보드 API·토스트)."""
    body = _detail(tmp_path, False, monkeypatch)
    assert "js-copyopen" in body
    assert "navigator.clipboard" in body
    assert "copy-toast" in body
