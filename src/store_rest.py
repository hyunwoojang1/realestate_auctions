"""Supabase(PostgREST) 읽기/쓰기 어댑터 — 클라우드 서빙용.

SUPABASE_URL 이 설정되면 web 서빙이 로컬 SQLite 대신 이 모듈을 통해 Supabase 의
`auction_scored_listings` 테이블에서 채점결과를 읽는다. 직접 Postgres 접속(비번) 없이
REST(service key)만으로 동작 → Vercel/GitHub Actions 친화(econ 대시보드와 동일 패턴).

store.py(SQLite)와 같은 읽기 인터페이스(has_rows/load_scored)를 노출해 web.py 변경을 최소화한다.
쓰기(upsert/replace_all)는 로컬 새로고침(run.py)이 클라우드로 적재할 때 쓴다.
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests as _requests_mod

from .models import ScoredListing
from .store import _COLS, _RIGHTS_LIST_COLS  # DRY: 컬럼 정의는 store.py 단일 출처


def _make_session() -> _requests_mod.Session:
    """커넥션 풀링 세션 — (A3 2026-07-27) 종전엔 REST 콜마다 새 TLS 핸드셰이크(콜당 ~150-300ms
    추가)로 상세 페이지 4콜이 순차 1.6초까지 걸렸다. keep-alive 재사용 + 병렬 fetch(web.py)용
    풀 확장. 인터페이스는 requests 모듈과 동일(get/post/delete)이라 호출부·테스트 목킹 무변경.
    """
    s = _requests_mod.Session()
    # (감사 2026-07-28) 동시성 총합에 맞춰 확장 — 캐시 워밍 3스레드 × 페이지 워커 6 +
    # 상세 병렬 5 가 같은 세션을 공유한다. 풀보다 많으면 urllib3 이 여분 커넥션을 열었다
    # 버리므로(block=False) 교착은 없지만 keep-alive 재사용이 깨져 TLS 재핸드셰이크가 난다.
    adapter = _requests_mod.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=24)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    # ⚠ 이 세션은 web.py 상세 병렬 fetch(스레드 5개)가 공유한다. urllib3 커넥션 풀은
    # thread-safe지만 Session 객체의 **세션 레벨 상태 변경은 아니다** — s.headers.update()/
    # 쿠키 조작을 여기든 호출부든 절대 추가하지 말 것(요청별 headers= 인자만 사용). 리뷰 2026-07-27.
    return s


# 호출부 전체가 `requests.get(...)` 형태를 유지하도록 세션을 같은 이름으로 노출.
requests = _make_session()

logger = logging.getLogger(__name__)

# PostgREST 기본 max-rows(응답 상한). 3천여 건은 페이지네이션(offset)으로 전량 수집.
_PAGE = 1000
# 한 번에 POST 하는 upsert 행 수(요청 크기·타임아웃 균형).
_WRITE_CHUNK = 500
_KST = timezone(timedelta(hours=9))

# 서빙 캐시: Vercel 매 요청마다 3천여 건 REST 페치를 피한다(warm 인스턴스 내에서만 유효).
# 새로고침은 로컬 nightly 라 TTL 만큼 지연돼도 무방. write 후 즉시 반영이 필요하면 invalidate().
_CACHE_TTL = float(os.environ.get("SUPABASE_CACHE_TTL", "600"))  # 야간 nightly 새로고침이라 stale 허용 — 콜드 왕복 절감
_cache: dict = {"at": 0.0, "rows": None}


def invalidate() -> None:
    _cache["rows"] = None


def _cfg() -> tuple[str | None, str | None, str]:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    table = os.environ.get("SUPABASE_TABLE", "auction_scored_listings")
    return url, key, table


# 권리·기일 요지 미러 테이블(물건상세 크롤분) — 상세 페이지 클라우드 서빙용.
RIGHTS_TABLE = os.environ.get("SUPABASE_RIGHTS_TABLE", "auction_listing_rights")
# 물건 사진 썸네일 미러 테이블 — 상세 히어로 클라우드 서빙용.
PHOTOS_TABLE = os.environ.get("SUPABASE_PHOTOS_TABLE", "auction_listing_photos")
# 네이버 KB시세·호가 매핑 미러 테이블 — 목록 배지·상세 차익 클라우드 서빙용.
NAVER_TABLE = os.environ.get("SUPABASE_NAVER_TABLE", "auction_naver_prices")
# 건축물대장 요약 미러 테이블 — 상세 건축물대장 카드 클라우드 서빙용(라이브 외부호출 대체).
BUILDING_TABLE = os.environ.get("SUPABASE_BUILDING_TABLE", "auction_listing_building")


def enabled() -> bool:
    """SUPABASE_URL+SECRET_KEY 둘 다 있으면 REST 백엔드 사용 가능."""
    url, key, _ = _cfg()
    return bool(url and key)


def _headers(key: str, extra: dict | None = None) -> dict:
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    if extra:
        h.update(extra)
    return h


def _endpoint(url: str, table: str) -> str:
    return f"{url.rstrip('/')}/rest/v1/{table}"


# 페이지 병렬 수집 동시성. 세션 풀(pool_maxsize=8)과 Supabase 부하를 고려한 보수적 기본값 —
# 올리려면 _make_session 의 pool_maxsize 도 같이 올려야 커넥션 대기가 생기지 않는다.
_PAGE_WORKERS = int(os.environ.get("SUPABASE_PAGE_WORKERS", "6"))


def _fetch_pages(url: str, key: str, table: str, params: dict,
                 *, timeout: int = 30, label: str = "") -> list[dict]:
    """PostgREST offset 페이지네이션을 **병렬**로 전량 수집.

    (2026-07-27) 종전 순차 루프는 1,000행마다 한 왕복을 직렬로 쌓았다 — 목록 13,910행+권리
    12,679행+네이버 18,160행 ≈ 46 왕복이 줄줄이 이어져, 캐시가 빈 **콜드 인스턴스의 첫 홈
    요청이 실측 25초**였다(warm 은 0.31초). 첫 페이지에서 `Prefer: count=exact` 로 총 건수를
    받아 나머지 offset 을 한 번에 던진다.

    안전장치:
      · count 헤더가 없거나 숫자가 아니면(미상 '*') 종전 순차 루프로 폴백 — 누락보다 느린 게 낫다.
      · 마지막 페이지가 가득 찼으면(조회 중 행이 늘어난 경우) 순차로 이어받아 tail 을 마저 읽는다.
      · 반환 순서는 offset 오름차순 결정적 — 호출부의 `order` 정렬이 그대로 유지된다.
        (호출부는 반드시 유일키 타이브레이커가 포함된 order 를 넘겨야 페이지 경계 누락이 없다.)
    """
    endpoint = _endpoint(url, table)

    def _resp(offset: int, extra_headers: dict | None = None):
        p = {**params, "limit": _PAGE, "offset": offset}
        r = requests.get(endpoint, headers=_headers(key, extra_headers), params=p, timeout=timeout)
        if r.status_code >= 400:
            # (적대감사 F7) PostgREST 4xx 본문에 원인(누락 컬럼명 등)이 있다 — 삼키면
            # 스키마 드리프트를 특정할 수 없다.
            logger.error("%s REST %s: %s", label or table, r.status_code, r.text[:300])
        r.raise_for_status()
        return r

    def _page(offset: int) -> list[dict]:
        return _resp(offset).json()

    first = _resp(0, {"Prefer": "count=exact"})
    head = first.json()
    if len(head) < _PAGE:
        return head

    # 총 건수 = content-range 의 마지막 토큰(예: "0-999/13910"). '*'(미상)이면 순차 폴백.
    try:
        total = int((first.headers.get("content-range") or "").split("/")[-1])
    except ValueError:
        total = -1

    rows = list(head)
    next_offset = _PAGE
    tail_full = True
    if total > _PAGE:
        offsets = list(range(_PAGE, total, _PAGE))
        with ThreadPoolExecutor(max_workers=min(_PAGE_WORKERS, len(offsets))) as ex:
            batches = list(ex.map(_page, offsets))   # map 은 입력 순서를 보존한다
        for b in batches:
            rows.extend(b)
        tail_full = bool(batches) and len(batches[-1]) == _PAGE
        next_offset = offsets[-1] + _PAGE

    # count 가 과소했거나(적재 중) 파싱 실패(total=-1) → 남은 페이지를 순차로 마저 읽는다.
    while tail_full:
        b = _page(next_offset)
        rows.extend(b)
        tail_full = len(b) == _PAGE
        next_offset += _PAGE
    return rows


def warm_caches() -> None:
    """전량 캐시 3종(목록·권리·네이버)을 **동시에** 채운다 — 콜드 첫 요청 단축용.

    (감사 HIGH 2026-07-28) 페이지 *내부*는 `_fetch_pages` 로 병렬화했지만 데이터셋 *사이*는
    여전히 직렬이었다(목록 끝나야 권리 시작). 세 로더를 먼저 동시에 돌려 캐시를 채워두면
    이어지는 순차 호출이 전부 캐시 히트가 된다.

    호출부(web)의 `flask.g` 의존 로직은 건드리지 않는다 — 그 함수들을 통째로 스레드에 넣으면
    `has_request_context()` 가 False 라 실패 플래그(g._rights_failed 등)가 조용히 유실된다.
    여기서는 **REST 캐시만** 데우고 판정 로직은 원래 순서대로 메인 스레드에서 돈다.

    실패는 삼킨다 — 진짜 호출 경로가 같은 예외를 다시 만나 정상적인 폴백·로그를 남긴다.
    여기서 시끄럽게 굴면 같은 실패가 두 번 보고된다.
    """
    if not enabled():
        return
    jobs = (load_scored, load_all_rights, load_all_naver)
    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futs = [ex.submit(fn) for fn in jobs]
        for f in futs:
            try:
                f.result()
            except Exception as e:  # noqa: BLE001 — 실제 경로가 다시 처리한다
                logger.debug("캐시 워밍 실패(무시, 실경로가 재시도): %s", e)


def _now_iso() -> str:
    return datetime.now(_KST).isoformat()


_fresh_cache: dict = {"at": 0.0, "val": None}


def last_refreshed(use_cache: bool = True) -> str | None:
    """scored 테이블 최신 refreshed_at(KST ISO) — '데이터 기준 N시간 전' 표시용.

    (2026-08-24 침묵실패 감사) 클라우드 서빙에서 데이터 나이를 노출하는 유일한 원천.
    TTL 캐시(_CACHE_TTL 공유) — 매 요청 REST 왕복을 막는다. 실패는 None(호출부가 '미상' 처리).
    """
    if use_cache and _fresh_cache["val"] is not None and (time.time() - _fresh_cache["at"] < _CACHE_TTL):
        return _fresh_cache["val"]
    url, key, table = _cfg()
    r = requests.get(
        _endpoint(url, table), headers=_headers(key),
        params={"select": "refreshed_at", "order": "refreshed_at.desc.nullslast", "limit": "1"},
        timeout=15)
    r.raise_for_status()
    rows = r.json()
    val = rows[0].get("refreshed_at") if rows else None
    _fresh_cache["at"] = time.time()
    _fresh_cache["val"] = val
    return val


def has_rows() -> bool:
    """테이블에 1건 이상 있는지(count=exact content-range 헤더로 판정)."""
    url, key, table = _cfg()
    r = requests.get(
        _endpoint(url, table),
        headers=_headers(key, {"Prefer": "count=exact", "Range": "0-0"}),
        params={"select": "court"},
        timeout=15,
    )
    r.raise_for_status()
    total = r.headers.get("content-range", "*/0").split("/")[-1]
    return total not in ("", "0", "*")


def load_scored(use_cache: bool = True) -> list[ScoredListing]:
    """차익 스코어 내림차순(NULL 맨 뒤) 전량을 ScoredListing 으로 복원.

    PostgREST max-rows 상한 때문에 _PAGE 단위 offset 페이지네이션으로 전부 가져온다.
    warm 인스턴스에선 _CACHE_TTL 동안 결과를 재사용(반복 페이지 이동 시 REST 왕복 회피).
    """
    if use_cache and _cache["rows"] is not None and (time.time() - _cache["at"] < _CACHE_TTL):
        return _cache["rows"]
    url, key, table = _cfg()
    # market_comps 는 _COLS 밖 별도 jsonb 컬럼 — select 에 명시하지 않으면 응답에서 빠져
    # 상세 차트 실거래 점이 프로덕션에서만 0개가 되는 버그(2026-07-20 수정). 저장(_payload)과 대칭.
    # sale_time(2026-07-24): 당일 마감 컷오프 정밀 판정용 미러 — 없으면 query.bidding_closed 가
    # 10:00 폴백 가정으로만 동작한다(±30분 오차를 2h 버퍼가 흡수하던 상태).
    select = ",".join([*_COLS, "market_comps", "sale_time"])
    rows = _fetch_pages(url, key, table, {
        "select": select,
        # (감사 2026-07-15) 정렬에 유일키(court,case_no,item_no) 타이브레이커 추가 —
        # arb_score 단독은 동점·NULL 다수라 순서가 불안정해 1000건 초과 시 페이지 경계에서
        # 행이 누락/중복되던 것 방지(전 순서 결정화).
        "order": "arb_score.desc.nullslast,court.asc,case_no.asc,item_no.asc",
    }, label="auction_scored_listings")
    result = [ScoredListing(**{c: row.get(c) for c in _COLS},
                            market_comps=row.get("market_comps") or [],
                            sale_time=row.get("sale_time") or "")
              for row in rows]
    _cache["rows"] = result
    _cache["at"] = time.time()
    return result


def scored_cache_fresh() -> bool:
    """전량 캐시가 TTL 내인가 — 상세 단건 경로(fetch_scored_by_case) 분기용."""
    return _cache["rows"] is not None and (time.time() - _cache["at"] < _CACHE_TTL)


def fetch_scored_by_case(case_no: str) -> list[ScoredListing]:
    """(A5 2026-07-27) 사건번호 단건 조회 — 상세 페이지를 전체 15k행 로드에서 독립.

    종전엔 상세도 load_scored() 전량(콜드 수 초~수십 초)을 통과해야 물건을 찾았다 — 램다
    콜드/캐시 만료 요청이 전부 그 비용을 물어 "클릭했는데 안 넘어간다" 체감의 꼬리.
    전량 캐시가 신선하면 REST 왕복 0(필터만), 아니면 eq.case_no 단건 REST(수백 ms).
    """
    if scored_cache_fresh():
        return [s for s in _cache["rows"] if s.case_no == case_no]
    url, key, table = _cfg()
    select = ",".join([*_COLS, "market_comps", "sale_time"])
    r = requests.get(_endpoint(url, table), headers=_headers(key),
                     params={"select": select, "case_no": f"eq.{case_no}",
                             "order": "court.asc,item_no.asc"},
                     timeout=15)
    r.raise_for_status()
    return [ScoredListing(**{c: row.get(c) for c in _COLS},
                          market_comps=row.get("market_comps") or [],
                          sale_time=row.get("sale_time") or "")
            for row in r.json()]


def _payload(s: ScoredListing) -> dict:
    row = s.to_row()
    d = {c: row[c] for c in _COLS}
    # market_comps(상세 차트 실거래 점)는 _COLS 밖 별도 컬럼 — Supabase jsonb 로 그대로 미러.
    # 이게 빠져 프로덕션 차트에 실거래 점이 안 찍히던 문제 수정(2026-07-13). 컬럼 없으면 mirror가
    # 400 → run.py 가 로컬 보존하고 넘어감(supabase_setup.sql 의 ALTER 선행 필요).
    d["market_comps"] = row.get("market_comps") or []
    # sale_time(2026-07-24): _COLS 밖 파생 필드지만 클라우드 서빙(Vercel)은 raw_listings 가 없어
    # 스스로 파생할 수 없다 → 미러에 포함. run.py 가 미러 직전 _sale_time_map 으로 주입한다.
    d["sale_time"] = row.get("sale_time") or ""
    return d


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _post_upsert(url: str, key: str, table: str, rows: list[dict]) -> int:
    n = 0
    for chunk in _chunks(rows, _WRITE_CHUNK):
        r = requests.post(
            _endpoint(url, table),
            headers=_headers(key, {
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            }),
            json=chunk,
            timeout=60,
        )
        r.raise_for_status()
        n += len(chunk)
    return n


def upsert(items: Iterable[ScoredListing]) -> int:
    """병합 적재(부분/증분). 같은 (court,case_no,item_no)만 갱신, 새 행은 삽입."""
    url, key, table = _cfg()
    rows = [_payload(s) for s in items]
    n = _post_upsert(url, key, table, rows)
    invalidate()
    return n


_rights_cache: dict = {"at": 0.0, "rows": None}


def load_all_rights(use_cache: bool = True) -> list[dict]:
    """권리 요지 전량(목록 배지·재매각 판정용) — listings 와 같은 TTL 캐시.

    `appraisal_notes` 를 **select 에서 제외**한다(store.fetch_all_rights 와 동일 정책):
    목록 경로에서 안 쓰는데 테이블의 66%(14.1MB)라 REST 로 매번 전송하면 콜드 로딩을 지배한다.
    상세는 `fetch_rights`(단건, select=*)가 전체 컬럼을 그대로 가져온다.
    """
    if use_cache and _rights_cache["rows"] is not None and (
            time.time() - _rights_cache["at"] < _CACHE_TTL):
        return _rights_cache["rows"]
    url, key, _ = _cfg()
    select = ",".join(_RIGHTS_LIST_COLS)   # 로컬(SQLite)과 같은 컬럼 집합 — 단일 출처
    rows = _fetch_pages(url, key, RIGHTS_TABLE, {
        "select": select,
        # (2026-07-27) order 추가 — 종전엔 정렬 없이 offset 페이지네이션을 돌렸다. Postgres 는
        # ORDER BY 없는 쿼리의 행 순서를 보장하지 않아 12,679행(13페이지) 경계에서 행이
        # 누락·중복될 수 있었다(네이버 로더는 2026-07-15 감사 때 이미 같은 이유로 고쳐졌다).
        "order": "court.asc,case_no.asc,item_no.asc",
    }, label="auction_listing_rights")
    _rights_cache["rows"] = rows
    _rights_cache["at"] = time.time()
    return rows


def upsert_rights(rows: list[dict]) -> int:
    """권리·기일 요지(listing_rights 행 dict) 병합 미러."""
    url, key, _ = _cfg()
    n = _post_upsert(url, key, RIGHTS_TABLE, rows)
    _rights_cache["rows"] = None
    return n


def upsert_photos(rows: list[dict]) -> int:
    """물건 사진 썸네일(listing_photos 행 dict) 병합 미러."""
    url, key, _ = _cfg()
    return _post_upsert(url, key, PHOTOS_TABLE, rows)


def fetch_photos(court: str, case_no: str, item_no: str = "") -> list[str]:
    """단건 물건 사진의 렌더용 src seq 순 — 클라우드 상세 서빙. 실패/미배포 시 빈 리스트.

    듀얼모드: photo_url(Storage) 우선, 없으면 base64를 data URI로. 마이그레이션 중에도 무중단.
    """
    url, key, _ = _cfg()
    try:
        r = requests.get(_endpoint(url, PHOTOS_TABLE), headers=_headers(key),
                         params={"select": "photo_url,thumb_b64,seq", "court": f"eq.{court}",
                                 "case_no": f"eq.{case_no}", "item_no": f"eq.{item_no or ''}",
                                 "order": "seq"}, timeout=20)
        r.raise_for_status()
        out = []
        for row in r.json():
            if row.get("photo_url"):
                out.append(row["photo_url"])
            elif row.get("thumb_b64"):
                out.append(f"data:image/jpeg;base64,{row['thumb_b64']}")
        return out
    except Exception as e:  # noqa: BLE001 — 사진 없음/테이블 미배포는 히어로 생략으로 강등
        # 무음이면 '원래 사진 없는 물건'과 '인증 만료로 전 물건 사진이 사라짐'을 구분할 수 없다.
        # 강등은 유지하되 흔적은 남긴다(2026-08-05 리뷰).
        logger.warning("fetch_photos 실패 %s/%s/%s: %s", court, case_no, item_no,
                       type(e).__name__)
        return []


_naver_cache: dict = {"at": 0.0, "rows": None}


def load_all_naver(use_cache: bool = True) -> list[dict]:
    """네이버 KB시세 전량(목록 배지·차익 조인용) — listings 와 같은 TTL 캐시.

    반환 dict 의 키는 store._NAVER_COLS(court…fetched_at). web._naver_map 이
    (court|case_no|item_no) 맵으로 조인한다. 실패 시 예외 전파 → 호출부가 국토부 추정으로 강등.
    """
    if use_cache and _naver_cache["rows"] is not None and (
            time.time() - _naver_cache["at"] < _CACHE_TTL):
        return _naver_cache["rows"]
    url, key, _ = _cfg()
    rows = _fetch_pages(url, key, NAVER_TABLE, {
        "select": "*",
        # (감사 2026-07-15) order 추가 — 정렬 없는 offset 페이지네이션은 1000건
        # 초과(2351건) 시 페이지 경계에서 행 누락/중복. 유일키로 전 순서 결정화.
        "order": "court.asc,case_no.asc,item_no.asc",
    }, label="auction_naver_prices")
    _naver_cache["rows"] = rows
    _naver_cache["at"] = time.time()
    return rows


def upsert_naver(rows: list[dict]) -> int:
    """네이버 KB시세 매핑(naver_prices 행 dict) 병합 미러 — 500행 청크 upsert."""
    url, key, _ = _cfg()
    n = _post_upsert(url, key, NAVER_TABLE, rows)
    _naver_cache["rows"] = None
    return n


def fetch_naver_price(court: str, case_no: str, item_no: str = "") -> dict | None:
    """단건 네이버 KB시세(클라우드 상세 폴백) — (court, case_no, item_no) 정확 매칭.

    fetch_photos 와 동일하게 실패/미배포(테이블 없음)는 조용히 None 으로 강등 →
    상세 페이지가 국토부 추정만으로 서빙된다(네이버 없음 = 무해).
    """
    url, key, _ = _cfg()
    try:
        r = requests.get(_endpoint(url, NAVER_TABLE), headers=_headers(key),
                         params={"select": "*", "court": f"eq.{court}",
                                 "case_no": f"eq.{case_no}",
                                 "item_no": f"eq.{item_no or ''}", "limit": 1},
                         timeout=15)
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else None
    except Exception:  # noqa: BLE001 — 네이버 없음/테이블 미배포는 국토부 추정으로 강등
        return None


def prune_rights() -> int:
    """클라우드 고아 권리 정리 — supabase_rights.sql 의 prune_auction_orphan_rights() RPC 호출.
    scored 에 대응 물건이 없는 rights 를 서버측 단일 쿼리로 삭제(반환=삭제 건수). 함수 미배포 시
    404 → 예외를 올려 호출부가 로컬 정리만 하고 넘어가게 한다.
    """
    url, key, _ = _cfg()
    r = requests.post(f"{url.rstrip('/')}/rest/v1/rpc/prune_auction_orphan_rights",
                      headers=_headers(key, {"Content-Type": "application/json"}),
                      json={}, timeout=60)
    r.raise_for_status()
    _rights_cache["rows"] = None
    body = r.json()
    return int(body) if isinstance(body, int) else int(body or 0)


def upsert_building(rows: list[dict]) -> int:
    """건축물대장 요약(listing_building 행 dict) 병합 미러."""
    url, key, _ = _cfg()
    return _post_upsert(url, key, BUILDING_TABLE, rows)


def fetch_building(court: str, case_no: str, item_no: str = "") -> dict | None:
    """단건 건축물대장 요약 조회(클라우드 서빙). 테이블 미배포/실패는 None(라이브 폴백)."""
    url, key, _ = _cfg()
    try:
        r = requests.get(_endpoint(url, BUILDING_TABLE), headers=_headers(key),
                         params={"select": "*", "court": f"eq.{court}",
                                 "case_no": f"eq.{case_no}",
                                 "item_no": f"eq.{item_no or ''}", "limit": 1},
                         timeout=15)
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else None
    except Exception:  # noqa: BLE001 — 미배포/네트워크 실패는 라이브 폴백
        return None


def fetch_rights(court: str, case_no: str, item_no: str = "") -> dict | None:
    """단건 권리 요지 조회(클라우드 서빙) — (court, case_no, item_no) 정확 매칭만.

    (재검증 감사 idx16) 사건 단위 폴백은 형제 물건 명세서 과신 — store.load_rights 와
    동일하게 정확 매칭 실패 = 미크롤 취급.
    """
    url, key, _ = _cfg()
    r = requests.get(_endpoint(url, RIGHTS_TABLE), headers=_headers(key),
                     params={"select": "*", "court": f"eq.{court}",
                             "case_no": f"eq.{case_no}",
                             "item_no": f"eq.{item_no or ''}", "limit": 1},
                     timeout=15)
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None


# 임차인 현황(대항력 실판정) 클라우드 테이블 — 미러링은 실크롤 후 도입(현재 로컬 우선).
TENANTS_TABLE = os.environ.get("SUPABASE_TENANTS_TABLE", "auction_listing_tenants")


# 현황조사 '부동산의 점유관계' 요지 미러(2026-07-25 V6) — 상세 신설 섹션의 클라우드 서빙.
SURVEY_TABLE = os.environ.get("SUPABASE_SURVEY_TABLE", "auction_listing_survey")


def upsert_survey(rows: list[dict]) -> int:
    """점유관계 요지(store.survey_rows 산출 행) 병합 미러. 테이블 미배포면 호출측 graceful skip."""
    url, key, _ = _cfg()
    return _post_upsert(url, key, SURVEY_TABLE, rows)


def fetch_survey(court: str, case_no: str, item_no: str = "") -> dict | None:
    """단건 점유관계 요지 조회(클라우드 서빙) — 템플릿 survey 컨텍스트와 동형으로 반환.

    possession 은 저장 시 '\\n' join → 여기서 리스트로 복원(curst_possession 반환형과 동일).
    미배포/미러 전/실패는 None — 섹션 미표시(모름≠없음), 페이지는 정상."""
    try:
        url, key, _ = _cfg()
        r = requests.get(_endpoint(url, SURVEY_TABLE), headers=_headers(key),
                         params={"select": "*", "court": f"eq.{court}",
                                 "case_no": f"eq.{case_no}",
                                 "item_no": f"eq.{item_no or ''}", "limit": 1},
                         timeout=15)
        if r.status_code >= 400:      # 테이블 미배포(404/400 등) → 미크롤 취급
            return None
        rows = r.json()
        if not rows:
            return None
        row = rows[0]
        return {
            "addr": row.get("addr") or "",
            "possession": [ln for ln in (row.get("possession") or "").split("\n") if ln],
            "etc": row.get("etc") or "",
            "exam_dates": row.get("exam_dates") or "",
            "tenant_count": row.get("tenant_count"),
        }
    except Exception:  # noqa: BLE001 — 점유관계 조회 실패는 상세 페이지를 막지 않음
        return None


# 낙찰(종결) 스냅샷 미러 — (C1 2026-07-27) 상세/목록의 클라우드 서빙 원천.
SOLD_TABLE = os.environ.get("SUPABASE_SOLD_TABLE", "auction_sold_listings")

# fetch_sold TTL 캐시(2026-08-25) — 인스턴스 단위, _CACHE_TTL 공유.
_sold_cache: dict = {"rows": None, "at": 0.0}


def upsert_sold(rows: list[dict]) -> int:
    """sold_listings 행 병합 미러 — 테이블 미배포면 호출측 graceful skip."""
    url, key, _ = _cfg()
    return _post_upsert(url, key, SOLD_TABLE, rows)


def delete_photos_beyond_seq(keys: list[tuple[str, str, str, int]]) -> tuple[int, int]:
    """물건별로 `seq >= kept` 인 **클라우드** 사진 행을 지운다. 반환 `(성공 수, 시도 수)`.

    시도 수를 함께 돌려주는 이유: 개별 실패를 안에서 삼키므로(한 건이 나머지를 막지 않게)
    성공 수만 보면 **부분 실패가 완전 성공과 구별되지 않는다.** `delete_sold` 는 호출부가
    `dn < len(...)` 로 비교해 "누락 N건" 경고를 내는데 사진 쪽만 그 비교가 없었다
    (2026-08-05 세트3 재감사).

    (2026-08-05 세트2 재감사) 사진 미러는 upsert-only 라 **클라우드가 줄어들 수 없었다.**
    법원이 5장→3장으로 줄이면 로컬은 `persist_photo_urls` 가 seq 3~4 를 지우는데, 미러 페이로드엔
    그 행이 애초에 없으니 클라우드엔 옛 seq 3~4 가 영원히 남는다 → 프로덕션(클라우드를 읽는다)에
    법원이 뺀 사진이 계속 노출. `listing_rights` 에는 고아 정리 RPC 가 있는데 사진엔 없었다.
    `delete_sold` 와 같은 이유·같은 방식이다(2026-07-28 실사고의 사진판).

    이번 크롤에서 **실제로 관측한 물건만** 대상으로 한다. 관측 안 한 물건까지 건드리면
    '응답 파싱 실패'를 '법원이 뺐다'로 오판해 멀쩡한 사진을 지운다.
    """
    if not keys:
        return 0, 0
    url, key, _ = _cfg()
    n = 0
    for court, case_no, item_no, kept in keys:
        try:
            r = requests.delete(
                _endpoint(url, PHOTOS_TABLE), headers=_headers(key),
                params={"court": f"eq.{court}", "case_no": f"eq.{case_no}",
                        "item_no": f"eq.{item_no}", "seq": f"gte.{kept}"}, timeout=15)
            r.raise_for_status()
            n += 1
        except Exception as e:  # noqa: BLE001 — 한 건 실패가 나머지를 막지 않게
            logger.warning("사진 클라우드 축소 실패(%s %s %s seq>=%s): %s",
                           court, case_no, item_no, kept, e)
    return n, len(keys)


def delete_sold(keys: list[tuple[str, str, str]]) -> int:
    """낙찰 기록에서 제거된 물건을 **클라우드에서도** 지운다.

    (2026-07-28 실사고) `store.drop_sold_revived` 가 재매각 부활 물건을 로컬에서 지웠는데
    미러는 upsert 만 하고 삭제 경로가 없어 **클라우드에만 남았다** — 실측: 서울남부
    2024타경6219(물건2)가 프로덕션에서 홈(진행 중)과 /sold(낙찰 종결) 양쪽에 동시 노출.
    로컬↔클라우드 대조(scripts/verify_claims.py)가 2,475 vs 2,476 으로 잡아냈다.
    삭제는 건별로 확실하게 — 실패는 세어서 호출부가 보고하게 한다(조용한 실패 금지).
    """
    if not keys:
        return 0
    url, key, _ = _cfg()
    n = 0
    for court, case_no, item_no in keys:
        try:
            r = requests.delete(
                _endpoint(url, SOLD_TABLE), headers=_headers(key),
                params={"court": f"eq.{court}", "case_no": f"eq.{case_no}",
                        "item_no": f"eq.{item_no}"}, timeout=15)
            r.raise_for_status()
            n += 1
        except Exception as e:  # noqa: BLE001 — 한 건 실패가 나머지를 막지 않게
            logger.warning("낙찰 클라우드 삭제 실패(%s %s %s): %s", court, case_no, item_no, e)
    return n


def fetch_sold(limit: int = 200) -> list[dict]:
    """낙찰 목록(클라우드) — 매각기일 최신순. 실패는 빈 리스트(페이지 정상).

    (감사 HIGH 2026-07-28) 종전엔 단발 GET 에 limit 만 걸었다. PostgREST 는 서버 설정
    max-rows(이 프로젝트 실측 **1,000**)를 넘겨 달라고 해도 **예외 없이 1,000행만** 준다 —
    낙찰이 1,000건을 넘는 순간 오래된 기록이 조용히 사라지고 /sold 의 검색·정렬도 잘린
    1,000건 안에서만 돌게 된다(로컬 SQLite 는 LIMIT 5000 이 그대로 먹혀 로컬 테스트로는
    절대 안 잡히는 클래스). 다른 전량 로더들과 같은 `_fetch_pages` 로 통일한다.

    정렬은 **유일키 타이브레이커 포함**이 필수다 — 없으면 페이지 경계에서 행이 누락·중복된다
    (2026-07-15 감사에서 다른 로더들이 이미 같은 이유로 고쳐졌다). 종전의
    `sold_price.desc.nullslast` 선행 정렬은 로컬(load_sold)과도 의미가 달랐고 타이브레이커도
    없어 함께 정리한다 — 표시 순서는 어차피 web 계층(query.sort_sold)이 정한다.
    """
    # (2026-08-25 성능) TTL 캐시 — scored·rights·naver 는 전부 캐시가 있는데 sold 만
    # 나중에 추가되며 빠져, /sold 가 **매 요청 17k행 전량**을 다시 읽었다(웜인데 3.5초
    # 실측 — 홈 0.3초의 12배). 같은 _CACHE_TTL(기본 600초) 공유.
    if (_sold_cache["rows"] is not None
            and (time.time() - _sold_cache["at"] < _CACHE_TTL)):
        rows = _sold_cache["rows"]
        return rows[:limit] if limit and len(rows) > limit else rows
    try:
        url, key, _ = _cfg()
        rows = _fetch_pages(url, key, SOLD_TABLE, {
            "select": "*",
            "order": "sale_date.desc,court.asc,case_no.asc,item_no.asc",
        }, timeout=15, label=SOLD_TABLE)
        _sold_cache.update(rows=rows, at=time.time())
        return rows[:limit] if limit and len(rows) > limit else rows
    except Exception:  # noqa: BLE001 — 미배포/실패 = 섹션 미표시
        return []


def fetch_sold_one(court: str, case_no: str, item_no: str = "") -> dict | None:
    """단건 낙찰 스냅샷(클라우드) — 상세 '낙찰 종결' 모드."""
    try:
        url, key, _ = _cfg()
        r = requests.get(_endpoint(url, SOLD_TABLE), headers=_headers(key),
                         params={"select": "*", "court": f"eq.{court}",
                                 "case_no": f"eq.{case_no}",
                                 "item_no": f"eq.{item_no or ''}", "limit": 1},
                         timeout=15)
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else None
    except Exception:  # noqa: BLE001
        return None


def upsert_tenants(rows: list[dict]) -> int:
    """임차인 현황(listing_tenants 행 dict) 병합 미러 — 대항력 여지 판정 원천의 클라우드 서빙.

    (2026-07-23 step2) 서빙 게이트(SUPABASE_TENANTS_ENABLED)와 무관하게 데이터는 미리 채워둔다 —
    게이트는 '읽기'만 막고, 미러 '쓰기'는 데이터를 준비시켜 준다. 테이블 미배포 시 호출측이 graceful skip."""
    url, key, _ = _cfg()
    return _post_upsert(url, key, TENANTS_TABLE, rows)


def fetch_tenants(court: str, case_no: str, item_no: str = "") -> list[dict]:
    """단건 임차인 현황 조회(클라우드 서빙). 테이블 미배포/미러 전이면 조용히 빈 리스트.

    (2026-07-22) 대항력 실판정 원천. Supabase 테이블·미러 파이프라인은 실크롤로 데이터가
    쌓인 뒤 도입하므로, 그전까지 프로덕션은 이 함수가 [] 를 반환해 기존 서빙을 깨지 않는다.
    ★게이트: 임차인 미러가 준비되기 전에는 SUPABASE_TENANTS_ENABLED 미설정이라 REST 호출 없이
    즉시 [] — 지금 코드를 배포해도 프로덕션 상세 페이지에 추가 왕복·동작 변화가 전혀 없다.
    """
    if os.environ.get("SUPABASE_TENANTS_ENABLED") != "1":
        return []
    try:
        url, key, _ = _cfg()
        r = requests.get(_endpoint(url, TENANTS_TABLE), headers=_headers(key),
                         params={"select": "*", "court": f"eq.{court}",
                                 "case_no": f"eq.{case_no}",
                                 "item_no": f"eq.{item_no or ''}", "order": "seq"},
                         timeout=15)
        if r.status_code >= 400:      # 테이블 미배포(404/400 등) → 미크롤 취급
            return []
        return r.json() or []
    except Exception:  # noqa: BLE001 — 임차인 조회 실패는 상세 페이지를 막지 않음
        return []


def replace_all(items: Iterable[ScoredListing]) -> int:
    """전량 스냅샷 교체. 이번 run 시각으로 전부 upsert 후, 그보다 오래된 행을 삭제.

    SQLite replace_all(DELETE 후 INSERT)의 REST 등가물. 단일 트랜잭션은 아니지만
    '먼저 채우고(upsert) 나중에 만료 삭제' 순서라, 삭제 실패해도 최신 데이터는 이미 반영된다
    (반쯤 비워진 상태를 서빙하지 않음). 팔림/취하로 이번 크롤에 없는 매물은 만료 삭제로 제거.
    """
    url, key, table = _cfg()
    stamp = _now_iso()
    rows = [{**_payload(s), "refreshed_at": stamp} for s in items]
    if not rows:
        # 방어선: 수집 0건이면 아래 만료삭제(DELETE lt.stamp)가 클라우드 서빙 테이블을 통째로
        # 비운다. 빈 스냅샷은 보존하고 no-op(로컬 store.replace_all 과 동일 정책).
        return 0
    n = _post_upsert(url, key, table, rows)
    # 만료 삭제: 이번 run 보다 오래된 행(= 이번 크롤에 없던 매물).
    r = requests.delete(
        _endpoint(url, table),
        headers=_headers(key, {"Prefer": "return=minimal"}),
        # (감사 2026-07-15) refreshed_at 이 NULL 인 행(증분 upsert 경로로 들어온 구행)도 만료
        # 삭제한다. 전량 스냅샷에선 현재 매물은 모두 방금 stamp 를 받았으므로 NULL 은 이번 크롤에
        # 없던 팔림/취하 매물 → NULL 이 lt 비교에서 빠져 영구 잔존하던 것 제거.
        params={"or": f"(refreshed_at.lt.{stamp},refreshed_at.is.null)"},
        timeout=60,
    )
    r.raise_for_status()
    invalidate()
    return n
