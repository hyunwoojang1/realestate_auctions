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
        # 시세가 있는 행은 출처도 있어야 정합적이다 — 2026-07-28 부터 출처 미상은 불신
        # (fail-closed)이라, 출처 없이 시세만 있는 픽스처는 실제로 존재할 수 없는 상태다.
        "market_scope": "same_complex_same_area", "matched_trades": 12, "confidence": 1.0,
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


def test_sold_default_sort_is_recent(tmp_path, monkeypatch):
    """기본 정렬 = 매각기일 최신순(사용자 결정 2026-07-27 2차).

    점수 기본은 철회했다 — 과거 낙찰 기록은 법원 원문 백필이라 채점 정보가 없어 점수
    정렬이 무동작이었다. 기일은 모든 행이 반드시 갖는 값이라 언제나 의미 있는 순서다.
    """
    db, c = _seed_search(tmp_path)
    monkeypatch.setenv("AUCTION_DB", str(db))
    body = c.get("/sold").get_data(as_text=True)
    assert '<option value="recent" selected>' in body.replace('"recent"  selected', '"recent" selected')
    # 시드는 기일이 모두 같으므로 순서 대신 '점수순이 아님'만 고정(기일 정렬은 별도 테스트)
    assert "아직 점수가" not in body


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
    body = create_app().test_client().get("/sold?sort=score").get_data(as_text=True)
    assert "점수 값을 가진 기록이 없어" in body
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
    body = create_app().test_client().get("/sold?sort=score").get_data(as_text=True)
    assert "값을 가진 기록이 없어" not in body
    assert _names(body) == ["높은점수", "낮은점수"]


def _seed_sorts(tmp_path):
    """정렬 검증용 — 기일·낙찰가·차익이 서로 다른 3건."""
    from src.web import create_app
    db = tmp_path / "sorts.db"
    conn = store.connect(str(db))
    rows = []
    # 차익 = 시세 하한(4.7억 고정) − 낙찰가 → 싼것 +3.7억 / 중간것 +1.7억 / 비싼것 −4.3억
    for case, name, date_, price in [
        ("2025타경1", "싼것", "2026-07-01", 100_000_000),
        ("2025타경2", "중간것", "2026-07-15", 300_000_000),
        ("2025타경3", "비싼것", "2026-07-25", 900_000_000),
    ]:
        r = _sold_row(case, price=price, sale_date=date_)
        # 감정가는 낙찰가의 1.25배로 둔다(감정가율 80%) — 이 픽스처는 **정렬**을 검증하는
        # 것이라, 감정가율 이상치 게이트(30% 미만 비교 불가)에 걸리지 않게 정상 범위로 맞춘다.
        r.update({"apt_name": name, "market_band_low": 470_000_000, "arb_score": None,
                  "appraisal_price": int(price * 1.25)})
        rows.append(r)
    # 낙찰가 미공개 1건 — 어떤 금액·차익 정렬에서도 맨 뒤여야 한다(0원 취급 금지)
    r = _sold_row("2025타경4", price=None, sale_date="2026-07-20")
    r.update({"apt_name": "미공개", "market_band_low": 470_000_000, "arb_score": None})
    rows.append(r)
    store.upsert_sold(conn, rows)
    conn.close()
    return db, create_app().test_client()


@pytest.mark.parametrize(("sort", "expected"), [
    ("recent", ["비싼것", "미공개", "중간것", "싼것"]),        # 기일 최신순(기본)
    ("old", ["싼것", "중간것", "미공개", "비싼것"]),           # 기일 오래된순
    ("price", ["비싼것", "중간것", "싼것", "미공개"]),         # 낙찰가 높은순
    ("price_asc", ["싼것", "중간것", "비싼것", "미공개"]),     # 낙찰가 낮은순
    ("profit", ["싼것", "중간것", "비싼것", "미공개"]),        # 시세 대비 차익 높은순
    ("profit_asc", ["비싼것", "중간것", "싼것", "미공개"]),    # 차익 낮은순(손해 먼저)
])
def test_sold_sort_orders(tmp_path, monkeypatch, sort, expected):
    """새 정렬 6종 — 값 없는 행(미공개)은 어느 금액 정렬에서도 맨 뒤."""
    db, c = _seed_sorts(tmp_path)
    monkeypatch.setenv("AUCTION_DB", str(db))
    assert _names(c.get(f"/sold?sort={sort}").get_data(as_text=True)) == expected


def test_sold_profit_sort_degrades_when_no_profit(tmp_path, monkeypatch):
    """차익 값이 전무하면 차익 정렬도 정직하게 기일순으로 강등된다."""
    from src.web import create_app
    db = tmp_path / "noprofit.db"
    conn = store.connect(str(db))
    rows = []
    for case, name, date_ in [("2025타경1", "가", "2026-07-01"), ("2025타경2", "나", "2026-07-20")]:
        r = _sold_row(case, price=200_000_000, sale_date=date_)
        # 시세 미추정 → 차익 계산 불가(홈의 profit_low 가 아니라 market_band_low 가 기준)
        r.update({"apt_name": name, "market_band_low": None, "arb_score": None})
        rows.append(r)
    store.upsert_sold(conn, rows)
    conn.close()
    monkeypatch.setenv("AUCTION_DB", str(db))
    body = create_app().test_client().get("/sold?sort=profit").get_data(as_text=True)
    assert "시세 대비 차익 값을 가진 기록이 없어" in body
    assert _names(body) == ["나", "가"]


# ── 시세 출처 구분(2026-07-28 사용자 지적) ────────────────────────────────────
# est_market_price 만 저장하고 market_scope 를 버리면 '같은 단지 확정 실거래'와 '동 폴백
# 참고치(다른 단지 혼입 가능)'가 화면에서 똑같이 보인다 — 오염된 시세를 확정값으로 믿게 된다.


def test_sold_schema_keeps_market_scope(tmp_path):
    """sold_listings 는 시세 출처·근거를 저장할 수 있어야 한다(컬럼 존재 + 왕복 보존)."""
    conn = store.connect(str(tmp_path / "scope.db"))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sold_listings)")}
    assert {"market_scope", "matched_trades", "confidence"} <= cols
    r = _sold_row("2025타경1", price=300_000_000)
    r.update({"market_scope": "same_complex_same_area", "matched_trades": 160,
              "confidence": 1.0})
    store.upsert_sold(conn, [r])
    got = store.load_sold_one(conn, "서울중앙지방법원", "2025타경1", "1")
    assert got["market_scope"] == "same_complex_same_area"
    assert got["matched_trades"] == 160 and got["confidence"] == 1.0


def test_sold_card_hides_dong_fallback_estimate(tmp_path, monkeypatch):
    """동 폴백 시세는 값이 저장돼 있어도 화면에 차익으로 내지 않는다(이중 방어).

    저장 단계 정책(apply_sold_market_policy)이 이미 비우지만, 정책 도입 **전에 적재된 행**이
    남아 있을 수 있다 — 표시 단계(query.sold_gap)도 같은 게이트를 건다.
    """
    from src.web import create_app
    db = tmp_path / "fb.db"
    conn = store.connect(str(db))
    confirmed = _sold_row("2025타경1", price=300_000_000)
    confirmed.update({"apt_name": "확정단지", "market_band_low": 400_000_000,
                      "market_scope": "same_complex_same_area"})
    fallback = _sold_row("2025타경2", price=300_000_000)
    fallback.update({"apt_name": "폴백단지", "market_band_low": 400_000_000,
                     "market_scope": "same_dong_fallback"})
    store.upsert_sold(conn, [confirmed, fallback])
    conn.close()
    monkeypatch.setenv("AUCTION_DB", str(db))
    body = create_app().test_client().get("/sold").get_data(as_text=True)
    # 확정 1건만 차익이 뜨고, 폴백 1건은 '시세 미추정'으로 강등된다
    # (안내문에도 같은 단어가 있어 카드 마크업으로 정확히 센다)
    assert body.count('<div class="v na">시세 미추정</div>') == 1
    assert "+1.00억" in body      # 확정 물건(4.0억 − 3.0억)은 그대로 표시


def test_sold_detail_uses_stored_scope_and_evidence(tmp_path, monkeypatch):
    """낙찰 상세는 저장된 매칭 건수·신뢰를 쓴다 — 0 으로 박아 '근거 없음'처럼 보이면 안 된다."""
    from src.web import create_app
    db = tmp_path / "det.db"
    conn = store.connect(str(db))
    r = _sold_row("2025타경7", price=300_000_000)
    r.update({"market_band_low": 400_000_000, "market_scope": "same_complex_same_area",
              "matched_trades": 160, "confidence": 1.0})
    store.upsert_sold(conn, [r])
    conn.close()
    monkeypatch.setenv("AUCTION_DB", str(db))
    body = create_app().test_client().get(
        "/property/2025타경7?item=1&court=서울중앙지방법원").get_data(as_text=True)
    assert "160" in body            # 매칭 실거래 건수가 화면에 실린다
    assert "낙찰 종결 물건" in body


# ── 낙찰 시세 정책·자동 후처리(2026-07-28 사용자 결정) ────────────────────────
# ① 신뢰 출처(같은 단지 확정 실거래) 아닌 시세는 낙찰 결과에서 '시세 미추정'으로 비운다.
# ② 그 규칙은 재채점과 일일 크롤 diff **양쪽**에 걸려야 새로고침이 폴백을 되살리지 않는다.


def test_market_policy_drops_dong_fallback():
    """동 폴백 시세는 비우고 등급을 낮춘다 — 차익·근거도 함께 지운다(근거 없는 숫자 금지)."""
    row = {"market_scope": "same_dong_fallback", "est_market_price": 500_000_000,
           "market_band_low": 480_000_000, "profit_low": 100_000_000,
           "expected_profit": 120_000_000, "matched_trades": 21, "confidence": 1.0,
           "grade": "관심"}
    out = store.apply_sold_market_policy(row)
    assert out["est_market_price"] is None and out["market_band_low"] is None
    assert out["profit_low"] is None and out["matched_trades"] is None
    assert out["grade"] == "시세추정불가"
    assert row["est_market_price"] == 500_000_000        # 원본 불변


def test_market_policy_keeps_same_complex():
    """같은 단지·같은 평형 확정 실거래는 그대로 둔다."""
    row = {"market_scope": "same_complex_same_area", "est_market_price": 500_000_000,
           "market_band_low": 480_000_000, "profit_low": 100_000_000,
           "expected_profit": 120_000_000, "matched_trades": 160, "confidence": 1.0,
           "grade": "차익 유력"}
    out = store.apply_sold_market_policy(row)
    assert out["est_market_price"] == 500_000_000 and out["grade"] == "차익 유력"


def test_market_policy_leaves_unestimated_rows_alone():
    """애초에 시세가 없던 행은 등급까지 건드리지 않는다(미지원유형이 시세추정불가로 바뀌면 오분류)."""
    row = {"market_scope": "unsupported", "est_market_price": None,
           "market_band_low": None, "profit_low": None, "expected_profit": None,
           "matched_trades": None, "confidence": None, "grade": "미지원유형"}
    assert store.apply_sold_market_policy(row)["grade"] == "미지원유형"


def test_daily_snapshot_carries_scope_and_applies_policy(tmp_path):
    """일일 크롤 diff 도 시세 출처를 이어받고 폴백을 비운다 — 재채점과 같은 규칙."""
    import json

    from run import _SOLD_SNAP_COLS, _collect_sold_snapshot
    assert {"market_scope", "matched_trades", "confidence"} <= set(_SOLD_SNAP_COLS)

    conn = store.connect(str(tmp_path / "daily.db"))
    gone = _scored_obj("2025타경1", "2026-07-20")       # 기일 지남·소멸 → 낙찰로 보존
    import dataclasses
    gone = dataclasses.replace(gone, market_scope="same_dong_fallback",
                               est_market_price=500_000_000, market_band_low=480_000_000)
    store.replace_all(conn, [gone])
    store.save_rights(conn, [{
        "court": gone.court, "case_no": gone.case_no, "item_no": gone.item_no,
        "surviving_rights": "", "senior_lien": "", "lien_note": "", "remark": "",
        "claim_amt": None, "demand_end": "", "spec_write_ymd": "", "court_dept": "",
        "schedule": json.dumps([], ensure_ascii=False),
        "appraisal_notes": "[]", "fetched_at": "x",
    }])
    rows = _collect_sold_snapshot(conn, [])            # 새 스냅샷에 없음 = 소멸
    assert len(rows) == 1
    assert rows[0]["market_scope"] == "same_dong_fallback"
    assert rows[0]["est_market_price"] is None          # 폴백이라 비워짐
    assert rows[0]["grade"] == "시세추정불가"


def test_sold_card_has_naver_link_even_without_price(tmp_path, monkeypatch):
    """시세를 못 붙인 물건도 네이버 시세 링크는 있어야 한다(사용자 요청)."""
    from src import naver_store as ns
    from src.web import create_app
    db = tmp_path / "nv.db"
    conn = store.connect(str(db))
    ns.ensure_schema(conn)
    r = _sold_row("2025타경1", price=300_000_000)
    r.update({"apt_name": "링크단지", "market_band_low": None, "est_market_price": None,
              "market_scope": "no_comps"})
    store.upsert_sold(conn, [r])
    conn.execute(
        "INSERT INTO naver_prices (court, case_no, item_no, status, complex_no) "
        "VALUES (?,?,?,?,?)", ("서울중앙지방법원", "2025타경1", "1", "ok", "12345"))
    conn.commit()
    conn.close()
    monkeypatch.setenv("AUCTION_DB", str(db))
    body = create_app().test_client().get("/sold").get_data(as_text=True)
    assert "시세 미추정" in body
    assert "new.land.naver.com/complexes/12345" in body


# ── 감사 후속 수정(2026-07-28) ────────────────────────────────────────────────


def test_policy_also_clears_arb_score():
    """(감사 HIGH·2관점 교차) 점수는 무효화하는 그 시세로 계산된 값이라 함께 지워야 한다.

    시세만 지우고 arb_score 를 남기면 '근거 없는 점수'가 정렬·상세에 그대로 살아난다.
    """
    row = {"market_scope": "same_dong_fallback", "est_market_price": 500_000_000,
           "market_band_low": 480_000_000, "profit_low": 100_000_000,
           "expected_profit": 120_000_000, "matched_trades": 21, "confidence": 1.0,
           "arb_score": 88.0, "grade": "관심"}
    out = store.apply_sold_market_policy(row)
    assert out["arb_score"] is None
    assert out["grade"] == "시세추정불가"


@pytest.mark.parametrize("bad", ["inf", "Infinity", "1e400", "-inf", "nan"])
def test_budget_overflow_does_not_500(tmp_path, monkeypatch, bad):
    """(감사 HIGH) budget=inf 는 int(inf) OverflowError 로 500 이 됐다(프로덕션 재현).

    float 로 파싱되지만 유한하지 않은 값은 필터 미적용으로 흘려보낸다.
    """
    from src.web import create_app
    db, _ = _seed_search(tmp_path)
    monkeypatch.setenv("AUCTION_DB", str(db))
    c = create_app().test_client()
    assert c.get(f"/sold?budget={bad}").status_code == 200
    assert c.get(f"/?budget={bad}").status_code == 200


def test_detail_shows_undecidable_when_no_market(tmp_path, monkeypatch):
    """(감사 CRITICAL) 시세가 없으면 매도가 0원 → '거액 손해 확정'처럼 보이던 것.

    숫자를 내지 않고 '판단 불가'로 비우고, 이유를 화면에 밝혀야 한다.
    """
    from src.web import create_app
    db = tmp_path / "nomkt.db"
    conn = store.connect(str(db))
    r = _sold_row("2025타경5", price=300_000_000)
    r.update({"market_band_low": None, "est_market_price": None,
              "market_scope": "no_comps", "arb_score": None})
    store.upsert_sold(conn, [r])
    conn.close()
    monkeypatch.setenv("AUCTION_DB", str(db))
    body = create_app().test_client().get(
        "/property/2025타경5?item=1&court=서울중앙지방법원").get_data(as_text=True)
    assert "시세 추정이 없어 손익을 계산할 수 없습니다" in body
    assert 'id="sm-net">판단 불가<' in body        # 초기 렌더가 숫자가 아니어야
    assert 'id="sm-be">판단 불가<' in body
    # 손해 숫자를 내지 않는다 — 음수 순익이 헤드라인에 뜨면 안 된다
    assert 'id="sm-net">-' not in body


def test_detail_still_computes_when_market_exists(tmp_path, monkeypatch):
    """반대 방향 고정 — 시세가 있으면 종전대로 숫자가 나온다(과잉 차단 방지)."""
    from src.web import create_app
    db = tmp_path / "mkt.db"
    conn = store.connect(str(db))
    r = _sold_row("2025타경6", price=300_000_000)
    r.update({"market_band_low": 500_000_000, "market_scope": "same_complex_same_area"})
    store.upsert_sold(conn, [r])
    conn.close()
    monkeypatch.setenv("AUCTION_DB", str(db))
    body = create_app().test_client().get(
        "/property/2025타경6?item=1&court=서울중앙지방법원").get_data(as_text=True)
    assert 'id="sm-net">판단 불가<' not in body   # JS 주석에도 같은 문구가 있어 마크업으로 단언
    assert 'id="sm-nomkt"' not in body


def test_abnormal_price_ratio_blocks_comparison():
    """(감사 CRITICAL) 낙찰가가 감정가의 30% 미만이면 시세 비교를 무효화한다.

    실측 배경: 차익 높은순 상위 8건 중 4건이 감정가율 1~14%(19회·13회·6회 유찰)였다.
    지분·대지권만 매각이거나 물건 자체가 특수한 경우라 온전한 물건 시세와 비교하면
    "시세보다 2.5억 싸게 샀다"는 허구가 만들어진다(동래에코하임: 낙찰 351만 vs 시세 2.52억).
    """
    from src import query
    base = {"market_scope": "same_complex_same_area", "market_band_low": 252_000_000,
            "appraisal_price": 256_000_000}
    odd = {**base, "sold_price": 3_510_000}          # 감정가율 1.4% — 특수물건
    normal = {**base, "sold_price": 200_000_000}     # 감정가율 78% — 정상 거래
    assert query.sold_gap(odd) is None
    assert query.sold_comparable(odd) is False
    assert query.sold_gap(normal) == 52_000_000
    assert query.sold_comparable(normal) is True


def test_abnormal_gate_does_not_block_when_appraisal_unknown():
    """감정가를 모르면 판정 불가 — 모름을 이유로 정보를 지우지는 않는다(과잉 차단 방지)."""
    from src import query
    row = {"market_scope": "same_complex_same_area", "market_band_low": 400_000_000,
           "appraisal_price": None, "sold_price": 100_000_000}
    assert query.sold_comparable(row) is True
    assert query.sold_gap(row) == 300_000_000


def test_abnormal_rows_drop_out_of_profit_ranking(tmp_path, monkeypatch):
    """특수물건은 '차익 높은순' 상위를 차지하지 못하고, 카드가 이유를 밝힌다."""
    from src.web import create_app
    db = tmp_path / "odd.db"
    conn = store.connect(str(db))
    odd = _sold_row("2025타경1", price=3_510_000)
    odd.update({"apt_name": "특수물건", "appraisal_price": 256_000_000,
                "market_band_low": 252_000_000, "market_scope": "same_complex_same_area"})
    normal = _sold_row("2025타경2", price=200_000_000)
    normal.update({"apt_name": "정상물건", "appraisal_price": 256_000_000,
                   "market_band_low": 252_000_000, "market_scope": "same_complex_same_area"})
    store.upsert_sold(conn, [odd, normal])
    conn.close()
    monkeypatch.setenv("AUCTION_DB", str(db))
    body = create_app().test_client().get("/sold?sort=profit").get_data(as_text=True)
    assert _names(body)[0] == "정상물건"          # 허구 차익 2.48억이 1위를 뺏지 못한다
    assert "비교 불가" in body                    # 조용히 숨기지 않고 이유를 밝힌다


def test_special_label_only_when_market_exists(tmp_path, monkeypatch):
    """'비교 불가(특수)' 는 시세가 있는데 게이트가 막은 경우에만 — 시세 자체가 없으면
    '시세 미추정'이 정직한 표현이다(우리가 특수성을 판정한 게 아니다)."""
    from src.web import create_app
    db = tmp_path / "lbl.db"
    conn = store.connect(str(db))
    gated = _sold_row("2025타경1", price=3_000_000)          # 감정가율 낮고 시세 있음
    gated.update({"apt_name": "게이트", "appraisal_price": 256_000_000,
                  "market_band_low": 252_000_000, "market_scope": "same_complex_same_area"})
    nomkt = _sold_row("2025타경2", price=3_000_000)          # 감정가율 낮지만 시세 없음
    nomkt.update({"apt_name": "시세없음", "appraisal_price": 256_000_000,
                  "market_band_low": None, "est_market_price": None, "market_scope": "no_comps"})
    store.upsert_sold(conn, [gated, nomkt])
    conn.close()
    monkeypatch.setenv("AUCTION_DB", str(db))
    body = create_app().test_client().get("/sold").get_data(as_text=True)
    assert body.count('">비교 불가 <span') == 1                      # 게이트 건만
    assert body.count('<div class="v na">시세 미추정</div>') == 1    # 시세없음 건만
