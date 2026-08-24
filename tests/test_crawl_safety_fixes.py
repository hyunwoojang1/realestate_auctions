"""크롤링 안전 수정 회귀 테스트 (2026-07-15 Ponytail+정밀 리뷰 감사).

①수집 0건이 서빙 DB를 지우지 않음(치명 data-wipe 방지)
②네이버 안티봇 차단을 '데이터 없음'으로 오인하지 않음
③MOLIT 페이지네이션이 필터된 행 때문에 다음 페이지를 조기에 못 받는 버그 방지.
"""
import json

import pytest

from src import molit_client, store
from src.models import ScoredListing
from src.naver_client import NaverBlocked, NaverClient


def _scored(case_no: str, arb: float = 80.0) -> ScoredListing:
    return ScoredListing(
        case_no=case_no, apt_name="상계주공", address="서울 노원구 상계동",
        property_type="아파트", area_m2=84.9, appraisal_price=620_000_000,
        min_bid_price=397_000_000, fail_count=2, sale_date="2026-07-01",
        est_market_price=818_000_000, matched_trades=3, confidence=1.0,
        real_acquisition_cost=420_000_000, expected_profit=398_000_000,
        gap_rate=0.5, gap_score=50.0, rights_score=30.0, liquidity_score=20.0,
        arb_score=arb, grade="차익 유력",
    )


def test_prune_orphan_photos_and_naver(tmp_path):
    """E2(2026-07-22): scored에 없는 photos·naver 고아행이 rights와 동일하게 정리된다."""
    conn = store.connect(str(tmp_path / "o.db"))
    store.upsert(conn, [_scored("KEEP")])                 # 부모(court="",item_no="")
    store.save_photos(conn, "", "KEEP", "", ["b64keep"], fetched_at="x")   # 부모 있음
    store.save_photos(conn, "", "ORPH", "", ["b64orph"], fetched_at="x")   # 부모 없음(고아)
    conn.execute("INSERT OR REPLACE INTO naver_prices (court,case_no,item_no,status) "
                 "VALUES ('','ORPHN','','no_kb')")        # 부모 없는 시세(고아)
    conn.commit()
    assert store.prune_orphan_photos(conn) == 1           # ORPH 사진만 삭제
    assert store.prune_orphan_naver(conn) == 1            # ORPHN 시세만 삭제
    assert len(store.load_photos(conn, "", "KEEP", "")) == 1   # 부모 있는 사진 보존
    assert store.prune_orphan_photos(conn) == 0           # 멱등(재실행 시 0)


def test_prune_orphan_building_and_tenants(tmp_path):
    """(QA 2026-07-26) E2 때 빠졌던 building·tenants 고아 정리 — 2,399·55행 실측 누적 클래스."""
    conn = store.connect(str(tmp_path / "o2.db"))
    store.upsert(conn, [_scored("KEEP")])
    conn.execute("INSERT INTO listing_building (court,case_no,item_no,status) "
                 "VALUES ('','KEEP','','ok'), ('','ORPHB','','ok')")
    conn.execute("INSERT INTO listing_tenants (court,case_no,item_no,seq) "
                 "VALUES ('','KEEP','',0), ('','ORPHT','',0)")
    conn.commit()
    assert store.prune_orphan_building(conn) == 1          # 고아만 삭제
    assert store.prune_orphan_tenants(conn) == 1
    assert conn.execute("SELECT COUNT(*) FROM listing_building").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM listing_tenants").fetchone()[0] == 1
    assert store.prune_orphan_building(conn) == 0          # 멱등
    assert store.prune_orphan_tenants(conn) == 0


def test_request_budget_persists_across_clients(tmp_path):
    """D1(2026-07-22): 일일 요청 예산이 클라이언트(프로세스) 간 파일로 공유돼 생성자 리셋을 막는다.
    종전엔 _request_count가 생성자마다 0 → daily_cap이 프로세스마다 새로 시작(하루 6119콜 실측)."""
    from src.courtauction_client import CourtAuctionClient
    bf = str(tmp_path / "budget.json")
    c1 = CourtAuctionClient(daily_cap=5, budget_file=bf)
    for _ in range(3):                        # 요청 3회 시뮬(네트워크 없이 카운터+영속만)
        c1._request_count += 1
        c1._save_budget()
    c2 = CourtAuctionClient(daily_cap=5, budget_file=bf)   # 새 프로세스
    assert c2._request_count == 3             # 파일에서 당일 누적 이어받음
    c2._request_count = 5
    c2._save_budget()
    assert CourtAuctionClient(daily_cap=5, budget_file=bf)._request_count == 5   # 상한 도달분 이어받음


def test_request_budget_disabled_when_no_file():
    """budget_file=None(테스트/비영속)은 항상 0에서 시작하고 저장은 no-op."""
    from src.courtauction_client import CourtAuctionClient
    c = CourtAuctionClient(budget_file=None)
    assert c._request_count == 0
    c._save_budget()                          # 예외 없이 no-op


def test_targets_dedup_fanout():
    """D2(2026-07-22): raw_listings 다-docid 팬아웃(같은 사건 여러 raw행)이 _targets를 중복시키지
    않아야 한다 — 종전엔 refresh 시 같은 사건에 최대 52배 상세요청이 나가 밴예산을 낭비했다."""
    from deploy.crawl_rights import _targets
    conn = store.connect(":memory:")
    store.upsert(conn, [_scored("2025타경1")])
    for i in range(3):                        # 같은 (court,case_no,item_no)에 raw 3행(재크롤 이력)
        conn.execute(
            "INSERT INTO raw_listings (uid, doc_id, court, case_no, item_no, raw_json, fetched_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (f"uid{i}", f"doc{i}", "", "2025타경1", "", json.dumps({"boCd": "B0001"}), "2026-07-22"))
    conn.commit()
    t = _targets(conn, None, refresh=True)
    keys = [(x["court"], x["case_no"], x["item_no"]) for x in t]
    assert len(t) == 1 and len(keys) == len(set(keys))   # 3 raw행 → 타깃 1건


# ── ① 수집 0건이 서빙 DB를 지우지 않음 ──
def test_replace_all_empty_preserves_existing():
    """크롤 0건(전 샤드 차단/전량 파싱 실패)에 전량 교체를 돌려도 기존 매물이 보존된다."""
    conn = store.connect(":memory:")
    store.upsert(conn, [_scored("KEEP")])
    n = store.replace_all(conn, [])                       # 수집 0건 시나리오
    assert n == 0
    assert {s.case_no for s in store.load_scored(conn)} == {"KEEP"}  # 안 지워짐


def test_replace_all_nonempty_still_replaces():
    """정상 스냅샷은 여전히 전량 교체된다(만료 매물 제거 기능 보존)."""
    conn = store.connect(":memory:")
    store.upsert(conn, [_scored("OLD")])
    store.replace_all(conn, [_scored("NEW")])
    assert {s.case_no for s in store.load_scored(conn)} == {"NEW"}


# ── ② 네이버 안티봇 차단 vs 데이터 없음 구분 ──
class _FakePage:
    def __init__(self, resp):
        self._resp = resp

    def evaluate(self, js, arg):
        return self._resp


def _naver(resp) -> NaverClient:
    c = object.__new__(NaverClient)          # __init__(플레이라이트) 우회
    c._page = _FakePage(resp)
    c.token = "t"
    c.n = 0
    c.refresh_every = 9999
    c.min_delay = 0.0
    c.max_delay = 0.0
    c.calls = 0
    return c


def test_naver_200_json_returns_data():
    assert _naver({"status": 200, "body": json.dumps({"ok": 1})}).fetch("/x") == {"ok": 1}


def test_naver_200_nonjson_is_block():
    """200인데 JSON이 아니면(안티봇 챌린지 HTML) None이 아니라 차단으로 중단."""
    with pytest.raises(NaverBlocked):
        _naver({"status": 200, "body": "<html>bot check</html>"}).fetch("/x")


def test_naver_403_is_block():
    """403은 조용한 None이 아니라 차단 신호로 중단."""
    with pytest.raises(NaverBlocked):
        _naver({"status": 403, "body": ""}).fetch("/x")


def test_naver_404_is_empty_not_block():
    """404는 리소스 미존재 — 정상적 '데이터 없음'(None), 차단 아님."""
    assert _naver({"status": 404, "body": ""}).fetch("/x") is None


# ── ③ MOLIT 페이지네이션: 필터된 행에도 조기 종료하지 않음 ──
def _item(name: str, amount: str) -> str:
    return (f"<item><거래금액>{amount}</거래금액><전용면적>84.0</전용면적>"
            f"<년>2026</년><월>6</월><법정동>상계동</법정동><층>5</층>"
            f"<아파트>{name}</아파트></item>")


def _page(items: list[str]) -> str:
    return ("<response><header><resultCode>00</resultCode></header><body><items>"
            + "".join(items) + "</items></body></response>")


def test_pagination_not_early_exit_on_filtered_rows(monkeypatch):
    """page1이 raw 2건(1건은 거래금액 0으로 필터돼 parsed 1건)이어도 다음 페이지를 받는다.

    구버전은 len(page_trades)=1 < num_rows=2 로 조기 종료해 page2를 놓쳤다(comps 누락).
    """
    pages = {
        "1": _page([_item("상계주공", "45,000"), _item("걸러질행", "0")]),  # raw 2, parsed 1
        "2": _page([_item("둘째장단지", "50,000")]),                        # raw 1 < 2 → 종료
    }
    calls = []

    def fake_get(session, url, params, timeout, retries):
        calls.append(params["pageNo"])
        return pages[params["pageNo"]]

    monkeypatch.setattr(molit_client, "_get_with_retry", fake_get)
    trades = molit_client.fetch_trades("apt", "11350", "202606", "KEY",
                                       num_rows=2, max_pages=5)
    assert calls == ["1", "2"]                                   # 두 페이지 모두 요청
    assert {t.apt_name for t in trades} == {"상계주공", "둘째장단지"}


def test_final_exit_code_promotes_mirror_failure():
    """미러 실패 = 로컬은 성공했는데 서빙엔 반영 안 됨 → exit 4 로 사람을 부른다."""
    from deploy.crawl_rights import final_exit_code
    assert final_exit_code(0, 0) == 0
    assert final_exit_code(0, 1) == 4
    assert final_exit_code(0, 9) == 4
    # 이미 비0이면 원인을 덮지 않는다(차단 2·실패율 3이 더 중요한 신호).
    assert final_exit_code(2, 1) == 2
    assert final_exit_code(3, 5) == 3


def test_run_exit_code_is_output_mode_independent():
    """run.py 종료코드는 --json 여부와 무관해야 한다.

    종전엔 --json 분기가 낙찰 보존 실패(4)만 보고 먼저 반환해, `--json --live` 로 돌리면
    클라우드 미러가 통째로 실패해도 exit 0 이 나갔다(2026-08-05 세트2 재감사).
    """
    from run import _exit_code
    assert _exit_code(False, 0) == 0
    assert _exit_code(False, 3) == 5          # 미러 실패
    assert _exit_code(True, 0) == 4           # 낙찰 보존 실패
    assert _exit_code(True, 3) == 4           # 둘 다면 더 무거운 쪽(복구 불가)이 이긴다


def test_mirror_reporter_counts_only_failures():
    """`_MirrorReporter` 자체를 검증한다 — 이 클래스가 막으려던 사고의 회귀 방지.

    rights/photos/tenants/survey 4개 미러 블록이 try/except 를 복붙하다 tenants 가 카운터
    증가를 빠뜨린 실사고가 있었다(2026-08-05). 증가 로직을 클래스로 모았으니, 그 클래스가
    실제로 세는지도 못 박아야 한다 — 안 그러면 리팩터가 조용히 무력화된다.
    """
    from deploy.crawl_rights import _MirrorReporter
    m = _MirrorReporter()
    m.upsert("성공", "건", lambda: 3)
    assert m.fail_count == 0
    m.upsert("실패", "건", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert m.fail_count == 1, "예외를 흡수만 하고 세지 않는다"
    m.upsert("또실패", "건", lambda: (_ for _ in ()).throw(ValueError("x")))
    assert m.fail_count == 2


def test_all_cloud_mirror_calls_go_through_reporter():
    """미러 블록이 `mirror.upsert(` 를 우회해 인라인 try/except 로 되돌아가지 않았는지 구조 확인.

    복붙 누락이 실제로 일어났던 지점이라, 형태 자체를 계약으로 고정한다.
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "deploy" / "crawl_rights.py").read_text(
        encoding="utf-8")
    body = src[src.index("if store_rest.enabled():"):]
    assert body.count("mirror.upsert(") >= 5, "미러 호출이 리포터를 우회하고 있다"
    assert "mirror_fail += 1" not in body, "카운터를 호출부에서 직접 올리는 코드가 되살아났다"


def test_photo_mirror_uses_stale_guard():
    """미러가 `split_stale_rows` 를 **실제로 거치는지** 구조로 고정한다.

    순수함수만 테스트하면 "가드는 있는데 아무도 안 부른다"를 못 잡는다 — 이 레포가
    exit 4 에서 이미 겪은 「신호를 만들었다 ≠ 신호가 소비된다」의 사진판이다(2026-08-05 세트2).
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "deploy" / "crawl_rights.py").read_text(
        encoding="utf-8")
    body = src[src.index("if store_rest.enabled():"):]
    assert "photo_store.split_stale_rows(" in body, "미러가 스테일 가드를 거치지 않는다"
    assert "store_rest.upsert_photos(" in body
    assert body.index("split_stale_rows(") < body.index("upsert_photos("), (
        "가드보다 업서트가 먼저다 — 가드를 우회한다")
    assert "delete_photos_beyond_seq(" in body, (
        "클라우드 축소 경로가 없다 — upsert-only 라 법원이 뺀 사진이 영원히 남는다")


def test_cloud_shrink_skipped_when_local_untouched():
    """전량 업로드 실패(skip)면 **클라우드 축소 대상으로 기록하지 않아야** 한다.

    skip 은 "로컬을 일부러 안 건드린다(기존 사진 보존)"는 뜻이다. 그런데 클라우드만 줄이면
    로컬 8행 / 클라우드 3행으로 갈라져 **프로덕션에서 멀쩡한 사진이 사라진다**.
    2026-08-05 세트3 재감사에서 발각된 실제 결함(세트2 의 클라우드 축소가 만든 것).

    소스 구조로 고정한다 — 이 캐스케이드는 main() 전체를 돌려야 재현되는데 통합 테스트가 없다.
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "deploy" / "crawl_rights.py").read_text(
        encoding="utf-8")
    i = src.index("photo_kept[key]")
    window = src[max(0, i - 400):i]
    assert 'mode != "skip"' in window, (
        "persist 결과가 skip 인데도 클라우드 축소 대상으로 기록한다 — 로컬은 보존, 클라우드만 삭제")
    assert "mode = store.persist_photo_urls(" in src, "persist 반환값(mode)을 받지 않는다"
