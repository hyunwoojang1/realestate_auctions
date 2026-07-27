"""낙찰(종결) 보존·표시 — sold_listings 계약 (Phase C 2026-07-27).

핵심 정직성 계약: 실낙찰가는 재매각(maeAmt)에만 존재 — 미공개 물건의 sold_price 는 NULL 이며
0원·회차 최저가(last_sold_floor)·추정가로 채우지 않는다.
"""
import pytest

from src import store


def _sold_row(case_no: str, price=None, evidence="disappeared", sale_date="2026-07-20"):
    return {
        "court": "서울중앙지방법원", "case_no": case_no, "item_no": "1",
        "apt_name": "낙찰단지", "address": "서울 강남구 1-2", "property_type": "아파트",
        "area_m2": 84.0, "appraisal_price": 500_000_000, "min_bid_price": 256_000_000,
        "fail_count": 2, "sale_date": sale_date, "est_market_price": 480_000_000,
        "market_band_low": 470_000_000, "profit_low": 150_000_000,
        "expected_profit": 170_000_000, "arb_score": 88.0, "grade": "차익 유력",
        "sold_price": price, "sold_evidence": evidence, "snapshot_at": "2026-07-27 14:00",
    }


def test_upsert_and_load_sold_ordering(tmp_path):
    """실낙찰가 보유가 미공개보다 먼저, 그 안에서 매각기일 최신순."""
    conn = store.connect(str(tmp_path / "s.db"))
    store.upsert_sold(conn, [
        _sold_row("2025타경1", price=None, sale_date="2026-07-25"),
        _sold_row("2025타경2", price=310_000_000, evidence="maeAmt", sale_date="2026-07-10"),
        _sold_row("2025타경3", price=220_000_000, evidence="maeAmt", sale_date="2026-07-15"),
    ])
    rows = store.load_sold(conn)
    assert [r["case_no"] for r in rows] == ["2025타경3", "2025타경2", "2025타경1"]


def test_upsert_sold_idempotent(tmp_path):
    conn = store.connect(str(tmp_path / "s.db"))
    store.upsert_sold(conn, [_sold_row("2025타경1")])
    store.upsert_sold(conn, [_sold_row("2025타경1", price=300_000_000, evidence="maeAmt")])
    rows = store.load_sold(conn)
    assert len(rows) == 1
    assert rows[0]["sold_price"] == 300_000_000       # 재적재가 갱신(멱등 병합)


def test_load_sold_one_exact_key(tmp_path):
    conn = store.connect(str(tmp_path / "s.db"))
    store.upsert_sold(conn, [_sold_row("2025타경9")])
    assert store.load_sold_one(conn, "서울중앙지방법원", "2025타경9", "1") is not None
    assert store.load_sold_one(conn, "다른법원", "2025타경9", "1") is None   # 키 폴백 금지


def _scored_obj(case_no: str, sale_date: str):
    from src.models import ScoredListing
    return ScoredListing(
        case_no=case_no, apt_name="교체단지", address="서울 강남구 1-2",
        property_type="아파트", area_m2=84.0, appraisal_price=500_000_000,
        min_bid_price=256_000_000, fail_count=2, sale_date=sale_date,
        est_market_price=480_000_000, matched_trades=3, confidence=1.0,
        real_acquisition_cost=270_000_000, expected_profit=170_000_000, gap_rate=0.4,
        gap_score=90.0, rights_score=100.0, liquidity_score=80.0, arb_score=88.0,
        grade="차익 유력", court="서울중앙지방법원", item_no="1",
    )


def test_collect_sold_snapshot_diff_rules(tmp_path):
    """(C2) diff 규칙 — 기일 지난 소멸=보존, 기일 남은 소멸=제외(취하 가능), 잔존=제외.
    재매각 이력('sold' 키)이 있으면 실낙찰가+evidence=maeAmt, 없으면 NULL+disappeared."""
    import json

    from run import _collect_sold_snapshot
    conn = store.connect(str(tmp_path / "c2.db"))
    past1 = _scored_obj("2025타경1", "2026-07-20")     # 기일 지남·소멸 → 보존(미공개)
    past2 = _scored_obj("2025타경2", "2026-07-18")     # 기일 지남·소멸·sold 이력 → 보존(실낙찰가)
    future = _scored_obj("2025타경3", "2099-01-01")    # 기일 남음·소멸 → 제외
    stay = _scored_obj("2025타경4", "2026-07-19")      # 새 스냅샷에도 있음 → 제외
    store.replace_all(conn, [past1, past2, future, stay])
    store.save_rights(conn, [{
        "court": past2.court, "case_no": past2.case_no, "item_no": past2.item_no,
        "surviving_rights": "", "senior_lien": "", "lien_note": "", "remark": "",
        "claim_amt": None, "demand_end": "", "spec_write_ymd": "", "court_dept": "",
        "schedule": json.dumps([
            {"ymd": "2026-05-01", "kind": "매각기일", "result": "매각",
             "price": 200_000_000, "sold": 231_000_000},
            {"ymd": "2026-07-18", "kind": "매각기일", "result": "", "price": 179_000_000},
        ], ensure_ascii=False),
        "appraisal_notes": "[]", "fetched_at": "x",
    }])
    rows = _collect_sold_snapshot(conn, [stay])
    got = {r["case_no"]: r for r in rows}
    assert set(got) == {"2025타경1", "2025타경2"}
    assert got["2025타경1"]["sold_price"] is None
    assert got["2025타경1"]["sold_evidence"] == "disappeared"
    assert got["2025타경2"]["sold_price"] == 231_000_000     # 'sold'(실낙찰) — price(최저가) 아님
    assert got["2025타경2"]["sold_evidence"] == "maeAmt"


def test_prune_keeps_children_of_sold(tmp_path):
    """(C2) 낙찰 보존 물건의 자식(사진 등)은 prune 에서 살아남는다 — 낙찰 상세의 아카이브."""
    conn = store.connect(str(tmp_path / "c2p.db"))
    keep = _scored_obj("2025타경대기", "2026-08-01")
    store.replace_all(conn, [keep])
    store.upsert_sold(conn, [_sold_row("2025타경낙찰")])
    store.save_photos(conn, "서울중앙지방법원", "2025타경낙찰", "1", ["YQ=="], fetched_at="x")
    store.save_photos(conn, "", "ORPH", "", ["Yg=="], fetched_at="x")
    assert store.prune_orphan_photos(conn) == 1               # 진짜 고아만 삭제
    assert len(store.load_photos(conn, "서울중앙지방법원", "2025타경낙찰", "1")) == 1


def test_unknown_price_stays_null(tmp_path):
    """미공개 낙찰가는 NULL 그대로 — 0원·최저가 대입 금지(정직성)."""
    conn = store.connect(str(tmp_path / "s.db"))
    store.upsert_sold(conn, [_sold_row("2025타경1", price=None)])
    row = store.load_sold_one(conn, "서울중앙지방법원", "2025타경1", "1")
    assert row["sold_price"] is None
    assert row["sold_price"] != row["min_bid_price"]


# ══ C4 UI — /sold 목록 + 상세 낙찰모드 ══

def _seed_web(tmp_path):
    from src.web import create_app
    db = tmp_path / "web.db"
    conn = store.connect(str(db))
    store.upsert_sold(conn, [
        _sold_row("2025타경100", price=310_000_000, evidence="maeAmt"),
        _sold_row("2025타경200", price=None, evidence="disappeared"),
    ])
    conn.close()
    return db, create_app().test_client()


def test_sold_page_lists_records(tmp_path, monkeypatch):
    db, c = _seed_web(tmp_path)
    monkeypatch.setenv("AUCTION_DB", str(db))
    body = c.get("/sold").get_data(as_text=True)
    assert "낙찰 결과" in body
    assert 'href="/sold" class="on"' in body                  # 헤더 메뉴 활성 상태(2026-07-27 QA·2차)
    assert "3.10억" in body                                   # 실낙찰가 표기
    assert "미공개" in body                                   # 가격 없는 건 정직 표기
    assert "0.00억" not in body                               # (C5) 미공개를 0으로 지어내지 않음


def test_sold_detail_mode_locks_simulator(tmp_path, monkeypatch):
    """낙찰 종결 + 실낙찰가 → 상세 200 + 배너 + 시뮬레이터 실낙찰가 고정(disabled)."""
    db, c = _seed_web(tmp_path)
    monkeypatch.setenv("AUCTION_DB", str(db))
    body = c.get("/property/2025타경100?item=1&court=서울중앙지방법원").get_data(as_text=True)
    assert "낙찰 종결 물건" in body
    assert "310,000,000원" in body                            # 상세는 원 콤마 표기
    assert 'disabled data-sold-price="310000000"' in body     # 슬라이더 고정
    assert "실낙찰가 고정" in body


def test_sold_detail_unknown_price_no_lock(tmp_path, monkeypatch):
    """낙찰가 미공개 → 배너에 '미공개', 시뮬레이터는 고정 없이 최저가 시작(값 지어내기 금지)."""
    db, c = _seed_web(tmp_path)
    monkeypatch.setenv("AUCTION_DB", str(db))
    body = c.get("/property/2025타경200?item=1&court=서울중앙지방법원").get_data(as_text=True)
    assert "낙찰 종결 물건" in body and "미공개" in body
    assert "data-sold-price" not in body
    assert "추정 낙찰가" not in body                           # 지어낸 금액 라벨 금지
    assert "실낙찰가 고정" not in body                         # 가격 없는데 고정 UI 금지


def test_active_listing_detail_has_no_sold_banner(tmp_path, monkeypatch):
    """활성 물건 상세엔 낙찰 배너가 없다(오표시 방지)."""
    from src.web import create_app
    db = tmp_path / "act.db"
    conn = store.connect(str(db))
    s = _scored_obj("2025타경300", "2026-08-01")
    store.replace_all(conn, [s])
    conn.close()
    monkeypatch.setenv("AUCTION_DB", str(db))
    body = create_app().test_client().get("/property/2025타경300").get_data(as_text=True)
    assert "낙찰 종결 물건" not in body


# ── 낙찰 결과 검색·정렬(2026-07-27 사용자 요청) ──────────────────────────────
# 홈과 같은 검색 카드를 낙찰 결과에도 붙이고, 기본 정렬을 홈과 같은 '점수 높은순'으로.


def _seed_search(tmp_path):
    """검색·정렬 검증용 — 지역·종류·면적·유찰·점수가 서로 다른 4건."""
    from src.web import create_app
    db = tmp_path / "search.db"
    conn = store.connect(str(db))
    rows = []
    for case, name, addr, ptype, area, fails, score, price in [
        ("2025타경11", "강남래미안", "서울 강남구 1", "아파트", 84.0, 2, 91.0, 500_000_000),
        ("2025타경22", "부산롯데캐슬", "부산 해운대구 2", "아파트", 59.0, 1, 55.0, 300_000_000),
        ("2025타경33", "서울오피스", "서울 마포구 3", "오피스텔", 30.0, 3, 77.0, None),
        ("2025타경44", "무점수단지", "경기 성남시 4", "아파트", 120.0, 0, None, 900_000_000),
    ]:
        r = _sold_row(case, price=price)
        r.update({"apt_name": name, "address": addr, "property_type": ptype,
                  "area_m2": area, "fail_count": fails, "arb_score": score,
                  "min_bid_price": 256_000_000, "appraisal_price": 1_000_000_000})
        rows.append(r)
    store.upsert_sold(conn, rows)
    conn.close()
    return db, create_app().test_client()


def _names(body: str) -> list[str]:
    """결과 카드에 나온 단지명을 화면 순서대로."""
    import re
    return re.findall(r'<div class="sc-nm">([^<]+)</div>', body)


def test_sold_default_sort_is_score_desc(tmp_path, monkeypatch):
    """기본 정렬 = 점수 높은순(홈과 동일). 점수 없는 건은 맨 뒤 — 0점으로 섞지 않는다."""
    db, c = _seed_search(tmp_path)
    monkeypatch.setenv("AUCTION_DB", str(db))
    names = _names(c.get("/sold").get_data(as_text=True))
    assert names == ["강남래미안", "서울오피스", "부산롯데캐슬", "무점수단지"]


def test_sold_search_card_present_and_shared(tmp_path, monkeypatch):
    """홈과 같은 검색 카드가 낙찰 결과에도 있고, 폼은 /sold 로 전송된다."""
    db, c = _seed_search(tmp_path)
    monkeypatch.setenv("AUCTION_DB", str(db))
    body = c.get("/sold").get_data(as_text=True)
    assert 'class="hsearch"' in body
    assert 'action="/sold"' in body
    assert "사건번호" in body                       # 사건검색 흡수 안내


@pytest.mark.parametrize(("qs", "expected"), [
    ("?q=래미안", ["강남래미안"]),
    ("?q=2025타경22", ["부산롯데캐슬"]),          # 사건번호로도 찾힌다
    ("?region=서울", ["강남래미안", "서울오피스"]),
    ("?type=오피스텔", ["서울오피스"]),
    ("?fails=2", ["강남래미안", "서울오피스"]),
    ("?area=20", ["강남래미안"]),                  # 20평대 = 84㎡(25.4평)
    ("?area=30", ["무점수단지"]),                  # 30평대 = 120㎡(36.3평)
])
def test_sold_filters(tmp_path, monkeypatch, qs, expected):
    db, c = _seed_search(tmp_path)
    monkeypatch.setenv("AUCTION_DB", str(db))
    assert _names(c.get("/sold" + qs).get_data(as_text=True)) == expected


def test_sold_sort_options(tmp_path, monkeypatch):
    db, c = _seed_search(tmp_path)
    monkeypatch.setenv("AUCTION_DB", str(db))
    # 낙찰가 높은순 — 미공개(None)는 맨 뒤
    assert _names(c.get("/sold?sort=price").get_data(as_text=True))[0] == "무점수단지"
    assert _names(c.get("/sold?sort=price").get_data(as_text=True))[-1] == "서울오피스"
    # 감정가율 낮은순 — 감정가 10억 기준 3억(30%)이 최상단
    assert _names(c.get("/sold?sort=rate").get_data(as_text=True))[0] == "부산롯데캐슬"
    # 알 수 없는 정렬키는 기본값으로 안전 폴백(500 금지)
    assert c.get("/sold?sort=쓰레기").status_code == 200


def test_sold_case_query_offers_active_auction_link(tmp_path, monkeypatch):
    """낙찰 기록에 없는 사건번호 → 자동 이동 대신 '진행 중 경매에서 찾기' 링크를 준다."""
    db, c = _seed_search(tmp_path)
    monkeypatch.setenv("AUCTION_DB", str(db))
    body = c.get("/sold?q=2025타경99999").get_data(as_text=True)
    assert "조건에 맞는 낙찰 기록이 없습니다" in body
    assert "/find?q=" in body


def test_sold_score_sort_degrades_honestly_when_no_scores(tmp_path, monkeypatch):
    """채점된 기록이 하나도 없으면 '점수순'인 척하지 않고 기일 최신순 + 사유를 밝힌다.

    과거 낙찰 기록은 법원 원문 백필이라 arb_score 가 없다(2026-07-27 실측 281건 전부 NULL).
    """
    from src.web import create_app
    db = tmp_path / "noscore.db"
    conn = store.connect(str(db))
    rows = []
    for case, date_ in [("2025타경1", "2026-07-10"), ("2025타경2", "2026-07-25")]:
        r = _sold_row(case, price=300_000_000, sale_date=date_)
        r["arb_score"] = None
        r["apt_name"] = "단지" + case[-1]
        rows.append(r)
    store.upsert_sold(conn, rows)
    conn.close()
    monkeypatch.setenv("AUCTION_DB", str(db))
    body = create_app().test_client().get("/sold").get_data(as_text=True)
    assert "아직 점수가 매겨진 기록이 없어" in body
    assert _names(body) == ["단지2", "단지1"]          # 기일 최신순으로 강등


def test_sold_score_sort_used_when_scores_exist(tmp_path, monkeypatch):
    """채점된 행이 하나라도 있으면 점수순 정렬이 실제로 동작하고 안내는 나오지 않는다."""
    from src.web import create_app
    db = tmp_path / "score.db"
    conn = store.connect(str(db))
    rows = []
    for case, score, name in [("2025타경1", 40.0, "낮은점수"), ("2025타경2", 95.0, "높은점수")]:
        r = _sold_row(case, price=300_000_000)
        r.update({"arb_score": score, "apt_name": name})
        rows.append(r)
    store.upsert_sold(conn, rows)
    conn.close()
    monkeypatch.setenv("AUCTION_DB", str(db))
    body = create_app().test_client().get("/sold").get_data(as_text=True)
    assert "아직 점수가 매겨진 기록이 없어" not in body
    assert _names(body) == ["높은점수", "낮은점수"]
