"""운영자 인증 — AUCTION_ADMIN_KEY 기반 (2026-08-24 보안감사 도입, blueprint 분리 2026-08-24).

정책: 키는 create_app 이 env 에서 읽어 app.config["AUCTION_ADMIN_KEY"] 로 스냅샷한다
(구 클로저 `_ADMIN_KEY` 와 동일 시맨틱 — 앱 생성 시점 고정, 테스트 격리 유지).
키 미설정 시 — 클라우드 서빙(store_rest = 공개 Vercel)은 fail-closed(잠금),
로컬(SQLite/샘플)은 종전대로 열림(개인 PC 워크플로·기존 테스트 계약 유지).
운영자는 /admin/login?key=... 1회 방문으로 1년 쿠키를 받는다.
쿠키는 SameSite=Lax → 타 사이트발 POST(CSRF)에 실리지 않는다.
"""
from __future__ import annotations

import hmac

from flask import Blueprint, abort, current_app, redirect, request

from .. import store_rest

bp = Blueprint("auth", __name__)


def _admin_key() -> str:
    return current_app.config.get("AUCTION_ADMIN_KEY", "")


def _key_ok(supplied: str) -> bool:
    # compare_digest 는 비ASCII str 에 TypeError — 항상 utf-8 bytes 로 비교한다.
    return hmac.compare_digest(supplied.encode("utf-8"), _admin_key().encode("utf-8"))


def _is_admin() -> bool:
    if not _admin_key():
        return not store_rest.enabled()
    supplied = (request.cookies.get("aak", "")
                or request.headers.get("X-Admin-Key", ""))
    return _key_ok(supplied)


@bp.get("/admin/login")
def admin_login():
    key = request.args.get("key", "")
    if not _admin_key() or not _key_ok(key):
        abort(403)
    resp = redirect("/")
    resp.set_cookie("aak", key, max_age=365 * 24 * 3600, httponly=True,
                    samesite="Lax", secure=request.is_secure)
    return resp
