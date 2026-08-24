"""워치리스트 — 운영자 전용(입찰 관심 목록 = 개인 재무 의도, 2026-08-24 보안감사).

전 라우트가 _is_admin 가드. 모듈명이 watchlist_pages 인 이유: src.watchlist(저장 로직)와
import 혼동을 피하기 위함.
"""
from __future__ import annotations

from flask import Blueprint, abort, g, jsonify, redirect, render_template, request

from .. import query, report, watchlist, web
from .auth import _is_admin

bp = Blueprint("watchlist_pages", __name__)


def _case_exists(case_no: str) -> bool:
    return any(s.case_no == case_no for s in web._scored())


def _safe_back() -> str:
    """토글 후 복귀 경로 — 같은 호스트의 referrer만 허용(open redirect 방지).

    (B1 2026-07-27) 상세가 private max-age=45 캐시를 갖게 되어(프리페치 재사용),
    토글 직후 복귀가 캐시본(옛 별 상태)을 쓰지 않도록 캐시버스터 _r 를 붙인다.
    """
    ref = request.referrer or ""
    if ref.startswith(request.host_url):
        import time as _time  # noqa: PLC0415
        sep = "&" if "?" in ref else "?"
        return f"{ref}{sep}_r={int(_time.time())}"
    return "/watchlist"


@bp.get("/watchlist")
def watchlist_page():
    if not _is_admin():   # 입찰 관심 목록 = 운영자 개인 재무 의도(2026-08-24 보안감사)
        abort(403)
    from .. import tax  # noqa: PLC0415
    items = web._scored()
    wl, wl_corrupt = watchlist.load_watchlist_status(watchlist.watchlist_path())
    # 복합키(court|case_no|item_no) 우선 매칭 + 레거시(bare case_no) 하위호환.
    watched = query.sort_items([s for s in items if watchlist.is_watched(wl, s)])
    present = ({watchlist.wl_key(s.court, s.case_no, s.item_no) for s in items}
               | {s.case_no for s in items})
    missing = sorted(wl - present)
    snap_path = watchlist.snapshot_path()
    prev, snap_corrupt = watchlist.load_snapshot_status(snap_path)
    events = (watchlist.detect_changes(prev, watchlist.snapshot_from_scored(items), wl)
              if prev else [])
    # 침묵실패 방지: '변동 없음'의 진짜 이유를 구분해 전달(손상 vs 스냅샷없음 vs 실제무변동).
    # snapshot_missing은 파일 존재로 판정 — 빈 스냅샷({}, 매물 0건 새로고침으로 정상 저장)을
    # 'prev가 falsy'라는 이유로 '없음'으로 오판하지 않도록(B9). 손상은 corrupted 배너가 별도 처리.
    return render_template(
        "watchlist.html", watched=watched, missing=missing, events=events,
        won=report.won, pct=report.pct, tax_label=tax.PROFILE.label(),
        corrupted=(wl_corrupt or snap_corrupt),
        snapshot_missing=(not snap_path.exists() and not snap_corrupt),
        data_source=getattr(g, "data_source", "n/a"))


@bp.get("/api/watchlist")
def watchlist_api_list():
    if not _is_admin():
        abort(403)
    resp = jsonify(sorted(watchlist.load_watchlist(watchlist.watchlist_path())))
    # (D3 2026-07-27) 상세 별 재동기화의 진실 원천 — 어떤 캐시에도 걸리면 안 된다.
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _wl_key_from_request(case_no: str) -> str:
    """요청의 court/item 파라미터로 복합키 구성 — 없으면(레거시 클라이언트) case_no 단독.

    동명 사건(복수 법원)·다물건 사건에서 정확한 물건 하나만 등록/해제되게 한다(감사 2026-07-10).
    """
    court = request.values.get("court", "")
    item = request.values.get("item", "")
    if court:
        return watchlist.wl_key(court, case_no, item)
    return case_no


@bp.post("/api/watchlist/<case_no>")
def watchlist_api_add(case_no: str):
    if not _is_admin():
        abort(403)
    if not _case_exists(case_no):
        abort(404)
    watchlist.add_watch(_wl_key_from_request(case_no), watchlist.watchlist_path())
    return {"ok": True, "watching": True}


@bp.delete("/api/watchlist/<case_no>")
def watchlist_api_remove(case_no: str):
    if not _is_admin():
        abort(403)
    p = watchlist.watchlist_path()
    # 복합키·레거시 둘 다 제거(어느 쪽으로 등록됐든 해제되게)
    watchlist.remove_watch(_wl_key_from_request(case_no), p)
    watchlist.remove_watch(case_no, p)
    return {"ok": True, "watching": False}


@bp.post("/watchlist/toggle/<case_no>")
def watchlist_toggle(case_no: str):
    if not _is_admin():
        abort(403)
    p = watchlist.watchlist_path()
    wl = watchlist.load_watchlist(p)
    key = _wl_key_from_request(case_no)
    if key in wl or case_no in wl:
        watchlist.remove_watch(key, p)
        watchlist.remove_watch(case_no, p)   # 레거시 엔트리도 함께 해제
    else:
        if not _case_exists(case_no):
            abort(404)
        watchlist.add_watch(key, p)
    return redirect(_safe_back())
