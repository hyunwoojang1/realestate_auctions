"""molit_cache — 국토부 실거래 영구 캐시 동작 검증."""
from src import molit_cache
from src.models import Trade


def _trade(name="테스트아파트", price=500_000_000, ymd="202506"):
    return Trade(apt_name=name, area_m2=84.9, price=price, deal_ym=ymd,
                 dong="역삼동", floor=10, kind="apt", lawd_cd="11680")


def _conn(tmp_path):
    return molit_cache.connect(tmp_path / "molit_trades.db")


def test_closed_month_fetches_once_then_hits_cache(tmp_path):
    """닫힌 달: 첫 호출은 fetch, 두 번째는 캐시 적중(fetch_fn 미호출)."""
    conn = _conn(tmp_path)
    calls = {"n": 0}

    def fetch_fn():
        calls["n"] += 1
        return [_trade()]

    got1, from_cache1 = molit_cache.get_or_fetch(
        conn, "apt", "11680", "202506", fetch_fn,
        cacheable=True, now="2026-07-14 00:00:00", throttle_s=0)
    got2, from_cache2 = molit_cache.get_or_fetch(
        conn, "apt", "11680", "202506", fetch_fn,
        cacheable=True, now="2026-07-14 00:00:00", throttle_s=0)

    assert calls["n"] == 1            # 두 번째는 fetch_fn 안 부름
    assert from_cache1 is False
    assert from_cache2 is True
    assert len(got1) == len(got2) == 1
    assert got2[0].apt_name == "테스트아파트"   # 라운드트립 복원 정확


def test_open_month_always_fetches_and_not_cached(tmp_path):
    """열린 달(cacheable=False): 매번 fetch, 캐시에 저장 안 됨."""
    conn = _conn(tmp_path)
    calls = {"n": 0}

    def fetch_fn():
        calls["n"] += 1
        return [_trade(ymd="202507")]

    for _ in range(3):
        _, from_cache = molit_cache.get_or_fetch(
            conn, "apt", "11680", "202507", fetch_fn,
            cacheable=False, now="2026-07-14 00:00:00", throttle_s=0)
        assert from_cache is False

    assert calls["n"] == 3
    assert molit_cache._load(conn, "apt", "11680", "202507") is None  # 미저장


def test_cancelled_flag_roundtrips_and_old_cache_backward_compatible(tmp_path):
    """cdeal_type가 캐시 라운드트립에 보존되고, 구(舊)캐시(필드 없음)는 기본값으로 하위호환 복원."""
    import json
    conn = _conn(tmp_path)
    # 신규: 해제 플래그 저장 → 복원 보존
    t = Trade(apt_name="해제", area_m2=84.9, price=9_0000_0000, deal_ym="202504",
              dong="역삼동", floor=10, kind="apt", lawd_cd="11680",
              cdeal_type="O", cdeal_day="26.05.10")
    molit_cache._save(conn, "apt", "11680", "202504", [t], "2026-07-14 00:00:00")
    got = molit_cache._load(conn, "apt", "11680", "202504")
    assert got and got[0].is_cancelled is True
    assert got[0].cdeal_day == "26.05.10"
    # 구캐시: cdeal_type 키 없는 dict를 직접 심어도 Trade(**d) 기본값으로 복원(크래시 X, is_cancelled=False)
    old = json.dumps([{"apt_name": "구", "area_m2": 84.9, "price": 5_0000_0000,
                       "deal_ym": "202503", "dong": "역삼동", "floor": 3,
                       "kind": "apt", "lawd_cd": "11680"}], ensure_ascii=False)
    conn.execute("INSERT OR REPLACE INTO molit_trades (kind,lawd_cd,ymd,trades_json,n,fetched_at) "
                 "VALUES (?,?,?,?,?,?)", ("apt", "11680", "202503", old, 1, "2026-07-14 00:00:00"))
    conn.commit()
    got_old = molit_cache._load(conn, "apt", "11680", "202503")
    assert got_old and got_old[0].is_cancelled is False


def test_empty_result_is_cached_and_distinguished_from_miss(tmp_path):
    """거래 0건인 달도 캐시(빈 리스트) — 미캐시(None)와 구분해 재fetch 방지."""
    conn = _conn(tmp_path)
    calls = {"n": 0}

    def fetch_fn():
        calls["n"] += 1
        return []

    assert molit_cache._load(conn, "apt", "11680", "202505") is None  # 아직 미캐시
    got1, _ = molit_cache.get_or_fetch(
        conn, "apt", "11680", "202505", fetch_fn,
        cacheable=True, now="2026-07-14 00:00:00", throttle_s=0)
    got2, from_cache2 = molit_cache.get_or_fetch(
        conn, "apt", "11680", "202505", fetch_fn,
        cacheable=True, now="2026-07-14 00:00:00", throttle_s=0)

    assert got1 == [] and got2 == []
    assert from_cache2 is True        # 빈 달도 캐시 적중
    assert calls["n"] == 1            # 재fetch 안 함


def test_stats_counts_entries(tmp_path):
    conn = _conn(tmp_path)
    for ymd in ("202503", "202504"):
        molit_cache.get_or_fetch(
            conn, "apt", "11680", ymd, lambda ymd=ymd: [_trade(ymd=ymd)],
            cacheable=True, now="2026-07-14 00:00:00", throttle_s=0)
    s = molit_cache.stats(conn)
    assert s["entries"] == 2
    assert s["months"] == 2
    assert s["trades"] == 2
