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
from datetime import datetime, timedelta, timezone

import requests

from .models import ScoredListing
from .store import _COLS  # DRY: 컬럼 정의는 store.py 단일 출처

logger = logging.getLogger(__name__)

# PostgREST 기본 max-rows(응답 상한). 3천여 건은 페이지네이션(offset)으로 전량 수집.
_PAGE = 1000
# 한 번에 POST 하는 upsert 행 수(요청 크기·타임아웃 균형).
_WRITE_CHUNK = 500
_KST = timezone(timedelta(hours=9))

# 서빙 캐시: Vercel 매 요청마다 3천여 건 REST 페치를 피한다(warm 인스턴스 내에서만 유효).
# 새로고침은 로컬 nightly 라 TTL 만큼 지연돼도 무방. write 후 즉시 반영이 필요하면 invalidate().
_CACHE_TTL = float(os.environ.get("SUPABASE_CACHE_TTL", "120"))
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


def _now_iso() -> str:
    return datetime.now(_KST).isoformat()


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
    select = ",".join(_COLS)
    rows: list[dict] = []
    offset = 0
    while True:
        r = requests.get(
            _endpoint(url, table),
            headers=_headers(key),
            params={
                "select": select,
                "order": "arb_score.desc.nullslast",
                "limit": _PAGE,
                "offset": offset,
            },
            timeout=30,
        )
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < _PAGE:
            break
        offset += _PAGE
    result = [ScoredListing(**{c: row.get(c) for c in _COLS},
                            market_comps=row.get("market_comps") or [])
              for row in rows]
    _cache["rows"] = result
    _cache["at"] = time.time()
    return result


def _payload(s: ScoredListing) -> dict:
    row = s.to_row()
    d = {c: row[c] for c in _COLS}
    # market_comps(상세 차트 실거래 점)는 _COLS 밖 별도 컬럼 — Supabase jsonb 로 그대로 미러.
    # 이게 빠져 프로덕션 차트에 실거래 점이 안 찍히던 문제 수정(2026-07-13). 컬럼 없으면 mirror가
    # 400 → run.py 가 로컬 보존하고 넘어감(supabase_setup.sql 의 ALTER 선행 필요).
    d["market_comps"] = row.get("market_comps") or []
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
    """권리 요지 전량(목록 배지 조인용) — listings 와 같은 TTL 캐시."""
    if use_cache and _rights_cache["rows"] is not None and (
            time.time() - _rights_cache["at"] < _CACHE_TTL):
        return _rights_cache["rows"]
    url, key, _ = _cfg()
    rows: list[dict] = []
    offset = 0
    while True:
        r = requests.get(_endpoint(url, RIGHTS_TABLE), headers=_headers(key),
                         params={"select": "*", "limit": _PAGE, "offset": offset},
                         timeout=30)
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < _PAGE:
            break
        offset += _PAGE
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
    except Exception:  # noqa: BLE001 — 사진 없음/테이블 미배포는 히어로 생략으로 강등
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
    rows: list[dict] = []
    offset = 0
    while True:
        r = requests.get(_endpoint(url, NAVER_TABLE), headers=_headers(key),
                         params={"select": "*", "limit": _PAGE, "offset": offset},
                         timeout=30)
        r.raise_for_status()
        batch = r.json()
        rows.extend(batch)
        if len(batch) < _PAGE:
            break
        offset += _PAGE
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


def replace_all(items: Iterable[ScoredListing]) -> int:
    """전량 스냅샷 교체. 이번 run 시각으로 전부 upsert 후, 그보다 오래된 행을 삭제.

    SQLite replace_all(DELETE 후 INSERT)의 REST 등가물. 단일 트랜잭션은 아니지만
    '먼저 채우고(upsert) 나중에 만료 삭제' 순서라, 삭제 실패해도 최신 데이터는 이미 반영된다
    (반쯤 비워진 상태를 서빙하지 않음). 팔림/취하로 이번 크롤에 없는 매물은 만료 삭제로 제거.
    """
    url, key, table = _cfg()
    stamp = _now_iso()
    rows = [{**_payload(s), "refreshed_at": stamp} for s in items]
    n = _post_upsert(url, key, table, rows)
    # 만료 삭제: 이번 run 보다 오래된 행(= 이번 크롤에 없던 매물).
    r = requests.delete(
        _endpoint(url, table),
        headers=_headers(key, {"Prefer": "return=minimal"}),
        params={"refreshed_at": f"lt.{stamp}"},
        timeout=60,
    )
    r.raise_for_status()
    invalidate()
    return n
