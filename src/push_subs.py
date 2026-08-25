"""PWA Web Push 구독 저장소 — Supabase Storage 기반 (2026-08-25).

왜 Storage 인가: 구독은 Vercel(서버리스)에서 저장돼야 하는데, 새 Postgres 테이블
DDL 은 Management API 토큰이 필요하다(현재 .env 에서 제거됨 — 보안 정리). Storage 는
이미 배포된 서비스 키(SUPABASE_SECRET_KEY)로 버킷 생성·읽기·쓰기가 전부 되므로
사용자 개입 0으로 배선된다. 구독은 폰 1~2대 규모(개인 서비스) — KV 로 충분하다.

구조: 비공개 버킷 `push-subs` 에 구독 1건 = 오브젝트 1개(`<sha1(endpoint)>.json`).
발송자(deploy/notify_picks, 로컬)는 목록→다운로드→pywebpush, 410/404 응답이면 삭제.

VAPID 공개키는 비밀이 아니라 여기 상수로 커밋한다(클라이언트 JS 가 구독 시 사용).
개인키는 harness/vapid.json(gitignore) — 발송이 로컬에서만 일어나므로 서버 배포 불필요.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os

import requests

logger = logging.getLogger(__name__)

BUCKET = "push-subs"
# 공개키(비밀 아님) — harness/vapid.json 의 키쌍과 짝. 재생성 시 여기도 갱신할 것
# (불일치하면 기존 구독 전체가 무효 = 재구독 필요).
VAPID_PUBLIC_KEY = "BFzoKj1cl6OQ8BL1xjLpwE-MUk91tS-GsTfQ93szrSsk44G7F-LG6u-SOt_KyjmDR4fR336Vhqesf3nxtwS1INs"

_MAX_SUB_BYTES = 4096   # 정상 구독 JSON 은 ~300B — 방어적 상한


def _cfg() -> tuple[str, str] | None:
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return None
    return url, key


def _headers(key: str, extra: dict | None = None) -> dict:
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    if extra:
        h.update(extra)
    return h


def enabled() -> bool:
    return _cfg() is not None


def sub_key(endpoint: str) -> str:
    return hashlib.sha1(endpoint.encode("utf-8")).hexdigest() + ".json"


def ensure_bucket() -> None:
    """비공개 버킷 생성(멱등) — 이미 있으면 409 를 조용히 통과."""
    cfg = _cfg()
    if not cfg:
        return
    url, key = cfg
    r = requests.post(f"{url}/storage/v1/bucket", headers=_headers(key),
                      json={"id": BUCKET, "name": BUCKET, "public": False}, timeout=15)
    if r.status_code not in (200, 201, 409, 400):
        logger.warning("push-subs 버킷 생성 실패(%s): %s", r.status_code, r.text[:150])


def save_sub(sub: dict) -> bool:
    """구독 저장(업서트). 반환 False = 저장 실패(호출부가 503 처리)."""
    cfg = _cfg()
    if not cfg:
        return False
    url, key = cfg
    endpoint = sub.get("endpoint") or ""
    body = json.dumps(sub, ensure_ascii=False)
    if len(body.encode()) > _MAX_SUB_BYTES:
        return False
    try:
        r = requests.post(
            f"{url}/storage/v1/object/{BUCKET}/{sub_key(endpoint)}",
            headers=_headers(key, {"Content-Type": "application/json", "x-upsert": "true"}),
            data=body.encode("utf-8"), timeout=15)
        if r.status_code == 404:            # 버킷 없음 → 만들고 1회 재시도
            ensure_bucket()
            r = requests.post(
                f"{url}/storage/v1/object/{BUCKET}/{sub_key(endpoint)}",
                headers=_headers(key, {"Content-Type": "application/json", "x-upsert": "true"}),
                data=body.encode("utf-8"), timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001 — 구독 실패는 호출부가 사용자에게 알림
        logger.warning("push 구독 저장 실패: %s", e)
        return False


def delete_sub(endpoint: str) -> bool:
    cfg = _cfg()
    if not cfg:
        return False
    url, key = cfg
    try:
        r = requests.delete(f"{url}/storage/v1/object/{BUCKET}/{sub_key(endpoint)}",
                            headers=_headers(key), timeout=15)
        return r.status_code in (200, 404)
    except Exception as e:  # noqa: BLE001
        logger.warning("push 구독 삭제 실패: %s", e)
        return False


def list_subs() -> list[dict]:
    """저장된 구독 전부 — 발송자(로컬) 전용. 실패는 빈 리스트(발송 스킵)."""
    cfg = _cfg()
    if not cfg:
        return []
    url, key = cfg
    try:
        r = requests.post(f"{url}/storage/v1/object/list/{BUCKET}",
                          headers=_headers(key),
                          json={"prefix": "", "limit": 200}, timeout=15)
        r.raise_for_status()
        out = []
        for obj in r.json():
            name = obj.get("name") or ""
            if not name.endswith(".json"):
                continue
            g = requests.get(f"{url}/storage/v1/object/{BUCKET}/{name}",
                             headers=_headers(key), timeout=15)
            if g.status_code == 200:
                try:
                    out.append(g.json())
                except ValueError:
                    logger.warning("구독 파싱 실패 — 손상 오브젝트 삭제: %s", name)
                    requests.delete(f"{url}/storage/v1/object/{BUCKET}/{name}",
                                    headers=_headers(key), timeout=15)
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("push 구독 목록 실패: %s", e)
        return []
