"""정적 자산 라우트 — PWA(홈 화면 앱)·CSS·서비스워커·아이콘·딜시뮬 엔진.

Vercel rewrite 가 모든 경로를 Flask 로 보내므로 정적 폴더 대신 명시 라우트로 서빙한다.
base.css 의 콘텐츠 해시는 create_app 이 계산해 app.config["BASE_CSS_V"] 로 준다.
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request, send_file

from ..web import ROOT

bp = Blueprint("assets", __name__)

_STATIC = ROOT / "static"


@bp.get("/base.css")
def base_css():
    resp = send_file(str(_STATIC / "base.css"), mimetype="text/css")
    # (적대감사 F9) 영구 캐시는 **현재 해시와 일치하는 v** 에만 준다 — 배포 경계에서
    # 옛 v URL 로 새 내용이 immutable 1년 고정되는 것을 막는다(불일치·무버전은 no-cache).
    if request.args.get("v") == current_app.config.get("BASE_CSS_V"):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.get("/sw.js")
def service_worker():
    resp = send_file(str(_STATIC / "sw.js"), mimetype="application/javascript")
    # SW 스크립트는 no-cache — 브라우저가 매 로드마다 갱신 여부를 확인해야
    # VERSION 올림(옛 캐시 청소)이 지체 없이 전파된다.
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.get("/manifest.webmanifest")
def manifest():
    return jsonify({
        "name": "아파트 경매 1차 필터",
        "short_name": "아파트 경매",
        "description": "시세>최저가 차익 매물 큐레이션 — 실거래 검증·권리분석",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#2563eb",
        "lang": "ko",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
    })


def _png(name: str):
    resp = send_file(str(_STATIC / name), mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=604800"   # 1주 캐시
    return resp


# ── 딜 시뮬(신분·기간 전략 비교, GOAL_DEAL_SIM Phase 3) ──
# 엔진은 정적 JS(세율 하드코딩 없음), 세율·규제 데이터는 Jinja 전역으로 템플릿에 주입 —
# detail 라우트 시그니처를 건드리지 않아 _dealsim.html include 만으로 동작한다.
@bp.get("/dealsim.js")
def dealsim_engine():
    resp = send_file(str(_STATIC / "dealsim.js"), mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"   # 배포 즉시 새 엔진 반영(용량 작아 재검증 비용 미미)
    return resp


@bp.get("/apple-touch-icon.png")
def apple_icon():
    return _png("apple-touch-icon.png")


# iOS 가 접미사 변형(-precomposed·-120 등)으로도 요청 — 같은 아이콘으로 응답.
@bp.get("/apple-touch-icon-precomposed.png")
@bp.get("/apple-touch-icon-120x120.png")
@bp.get("/apple-touch-icon-152x152.png")
@bp.get("/apple-touch-icon-180x180.png")
def apple_icon_variants():
    return _png("apple-touch-icon.png")


@bp.get("/icon-192.png")
def icon192():
    return _png("icon-192.png")


@bp.get("/icon-512.png")
def icon512():
    return _png("icon-512.png")
