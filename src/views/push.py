"""PWA Web Push 구독 API — iOS 16.4+ 홈 화면 앱의 자체 푸시 (2026-08-25).

흐름: base.html 의 '알림 받기' 버튼(standalone 에서만 노출) → Notification.
requestPermission(사용자 제스처 필수) → pushManager.subscribe(VAPID 공개키) →
여기로 POST → Supabase Storage 저장. 발송은 deploy/notify_picks(로컬)가 담당.
rate limit(/api/*)은 create_app 훅이 이미 커버한다.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import push_subs

bp = Blueprint("push", __name__)


def _valid_sub(d) -> bool:
    if not isinstance(d, dict):
        return False
    ep = d.get("endpoint")
    keys = d.get("keys") or {}
    return (isinstance(ep, str) and ep.startswith("https://") and len(ep) < 1024
            and isinstance(keys.get("p256dh"), str) and isinstance(keys.get("auth"), str))


@bp.post("/api/push/subscribe")
def push_subscribe():
    if not push_subs.enabled():
        return jsonify({"ok": False, "error": "storage_unconfigured"}), 503
    sub = request.get_json(silent=True)
    if not _valid_sub(sub):
        return jsonify({"ok": False, "error": "bad_subscription"}), 400
    # 표준 필드만 저장(임의 페이로드 저장 방지)
    clean = {"endpoint": sub["endpoint"],
             "keys": {"p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]}}
    if not push_subs.save_sub(clean):
        return jsonify({"ok": False, "error": "store_failed"}), 503
    return jsonify({"ok": True})


@bp.post("/api/push/unsubscribe")
def push_unsubscribe():
    if not push_subs.enabled():
        return jsonify({"ok": False, "error": "storage_unconfigured"}), 503
    d = request.get_json(silent=True) or {}
    ep = d.get("endpoint")
    if not isinstance(ep, str) or not ep.startswith("https://"):
        return jsonify({"ok": False, "error": "bad_endpoint"}), 400
    push_subs.delete_sub(ep)
    return jsonify({"ok": True})
