"""Supabase Storage 사진 업로드 — 물건사진을 DB(base64)가 아닌 오브젝트 스토리지에 둔다.

배경: 사진을 base64로 DB에 넣으면 Supabase 무료티어 DB 500MB를 넘긴다(실측 사진만 ~500MB).
바이너리를 Storage로 빼고 DB에는 공개 URL만 저장 → DB 경량화 + 사진 대상 확대 가능.

인증은 store_rest 와 동일(SUPABASE_SECRET_KEY). 버킷은 공개(public) — 이미지 직접 서빙.
"""
from __future__ import annotations

import hashlib
import logging
import os

logger = logging.getLogger(__name__)


def _cfg():
    return (os.environ.get("SUPABASE_URL", "").rstrip("/"),
            os.environ.get("SUPABASE_SECRET_KEY", ""),
            os.environ.get("SUPABASE_PHOTOS_BUCKET", "auction-photos"))


def enabled() -> bool:
    url, key, _ = _cfg()
    return bool(url and key)


def _headers(key, content_type=None):
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    if content_type:
        h["Content-Type"] = content_type
    return h


def object_path(court: str, case_no: str, item_no: str, seq: int) -> str:
    """ASCII 결정키(한글/특수문자 회피) — 같은 물건·seq는 항상 같은 경로(재크롤 덮어쓰기)."""
    h = hashlib.sha1(f"{court}|{case_no}|{item_no}|{seq}".encode()).hexdigest()
    return f"{h}.jpg"


def public_url(path: str) -> str:
    url, _, bucket = _cfg()
    return f"{url}/storage/v1/object/public/{bucket}/{path}"


def ensure_bucket() -> bool:
    """버킷 없으면 생성(공개). 최초 1회, 멱등."""
    import requests  # noqa: PLC0415
    url, key, bucket = _cfg()
    if not (url and key):
        return False
    r = requests.get(f"{url}/storage/v1/bucket/{bucket}", headers=_headers(key), timeout=15)
    if r.status_code == 200:
        return True
    r = requests.post(f"{url}/storage/v1/bucket", headers=_headers(key, "application/json"),
                      json={"id": bucket, "name": bucket, "public": True,
                            "allowed_mime_types": ["image/jpeg"], "file_size_limit": 2_000_000},
                      timeout=15)
    ok = r.status_code in (200, 201) or "already exists" in r.text.lower()
    if not ok:
        logger.warning("Storage 버킷 생성 실패 HTTP %s %s", r.status_code, r.text[:120])
    return ok


def upload_photo(jpeg: bytes, court: str, case_no: str, item_no: str, seq: int) -> str | None:
    """JPEG 바이트를 업로드하고 공개 URL 반환. 실패 시 None."""
    import requests  # noqa: PLC0415
    url, key, bucket = _cfg()
    if not (url and key and jpeg):
        return None
    path = object_path(court, case_no, item_no, seq)
    r = requests.post(f"{url}/storage/v1/object/{bucket}/{path}",
                      headers={**_headers(key, "image/jpeg"), "x-upsert": "true"},
                      data=jpeg, timeout=30)
    if r.status_code in (200, 201):
        return public_url(path)
    logger.warning("사진 업로드 실패 HTTP %s %s", r.status_code, r.text[:120])
    return None
