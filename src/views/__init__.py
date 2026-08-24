"""서빙 라우트 blueprint 묶음 — web.py 1,872줄 분리(2026-08-24 감사 코드품질 CRITICAL).

구성(도메인별):
  auth            /admin/login + _is_admin (워치리스트·/find 라이브·rate limit 이 공유)
  assets          PWA 정적(base.css·sw.js·manifest·아이콘·dealsim.js)
  home            / (홈 목록·칩·페이지네이션)
  core_api        /health·/api/*·/export.csv
  sold            /sold + 낙찰 로더(상세와 공유)
  find            /find + _find_by_case(상세·단건 API와 공유)
  detail          /property/<case_no>
  watchlist_pages /watchlist·/api/watchlist* (운영자 전용)
  pages           /map·/digest·/calendar·/stats·/compare·/guide·/methodology

설계 계약:
  - 공유 헬퍼(_scored·_rights_badges·_filtered…)는 web 모듈에 남고, 뷰는 반드시
    `web.<이름>` 모듈 속성으로 **늦게 바인딩**해 호출한다 — 테스트가
    monkeypatch.setattr(web, "_scored", ...) 로 갈아끼우는 계약을 보존하기 위함.
  - 앱 단위 상태(관리자 키 스냅샷·CSS 해시)는 app.config, 요청 훅(rate limit·
    X-Data-Source·신선도 주입)은 create_app 에 남긴다.
"""
from __future__ import annotations

from flask import Flask


def register_views(app: Flask) -> None:
    from . import (  # noqa: PLC0415 — create_app 시점 임포트(web ↔ views 순환 차단)
        assets,
        auth,
        core_api,
        detail,
        find,
        home,
        pages,
        sold,
        watchlist_pages,
    )
    for mod in (auth, assets, home, core_api, sold, find, detail, watchlist_pages, pages):
        app.register_blueprint(mod.bp)
