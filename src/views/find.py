"""사건번호 검색(/find) — 로컬 큐레이션 우선, 없으면 대법원 라이브 단건 조회.

_find_by_case 는 상세(views.detail)·단건 API(views.core_api)도 함께 쓴다.
"""
from __future__ import annotations

import logging
import os

from flask import Blueprint, g, redirect, render_template, request

from .. import casesearch, report, store_rest, web
from ..web import DB_ENV
from .auth import _is_admin

logger = logging.getLogger(__name__)

bp = Blueprint("find", __name__)


def _find_by_case(case_no: str):
    """(T8 감사 수정 — B14 핵심) 사건번호 매칭 물건 전부.

    T1 복합키 도입으로 같은 사건의 물건 여러 개가 공존한다 — 단건 next()는 임의의
    한 물건만 반환해 다른 물건의 수치(아파트 vs 상가)를 보여주는 침묵 오표시를 만든다.
    item(물건번호)·court 쿼리 파라미터로 좁힐 수 있다.

    (2026-07-20) 입력 정규화 — 친구가 "2025-101763"처럼 보내도 저장 포맷("2025타경101763")과
    맞도록 casesearch로 환원해 매칭한다. 파싱 불가하면 원문 완전일치로 폴백(회귀 없음).

    (A5 2026-07-27) 클라우드 상세 fast path — 전량 캐시가 콜드인 REST 서빙에서 상세가
    15k행 로드를 통과하지 않도록 사건번호 단건 REST로 조회(콜드 상세 수 초→수백 ms).
    캐시가 신선하면 기존 경로(왕복 0)가 더 싸므로 그대로 둔다. 실패 시 전체 경로 폴백.
    """
    if (not os.environ.get(DB_ENV) and store_rest.enabled()
            and not store_rest.scored_cache_fresh()):
        try:
            matches = store_rest.fetch_scored_by_case(case_no)
            if not matches:
                q = casesearch.parse_case_query(case_no)
                if q and q.canonical and q.canonical != case_no:
                    matches = store_rest.fetch_scored_by_case(q.canonical)
            if matches:
                web._mark_source("db")
                from ..score import market_view  # noqa: PLC0415
                matches = [market_view(
                    s, store_rest.fetch_naver_price(s.court, s.case_no, s.item_no))
                    for s in matches]
                item = request.args.get("item")
                court = request.args.get("court")
                if item is not None:
                    matches = [s for s in matches if s.item_no == item]
                if court:
                    matches = [s for s in matches if s.court == court]
                return matches
        except Exception as e:  # noqa: BLE001 — 단건 실패는 전량 경로로 폴백(회귀 없음)
            logger.warning("단건 조회 실패 → 전량 경로 폴백(%s): %s", case_no, e)
    scored = web._scored()
    # 완전일치 우선(회귀 방지 — 합성 case_no 'LIVE-1' 등 그대로 동작).
    matches = [s for s in scored if s.case_no == case_no]
    if not matches:
        # 실패 시에만 정규화 폴백 — 친구 포맷 '2025-101763' → '2025타경101763'.
        q = casesearch.parse_case_query(case_no)
        if q:
            matches = casesearch.match_local(scored, q)
    item = request.args.get("item")
    court = request.args.get("court")
    if item is not None:
        matches = [s for s in matches if s.item_no == item]
    if court:
        matches = [s for s in matches if s.court == court]
    return matches


@bp.get("/find")
def find_case():
    """사건번호 검색 — 로컬 큐레이션 우선, 없으면 대법원 라이브 단건 조회.

    흐름(명확한 논리, casesearch 계약):
      1) 입력 정규화(parse_case_query). 인식 실패 → 안내.
      2) 로컬 매칭(match_local): 1건이면 상세로, 여러 건이면 목록, 0건이면 3)로.
      3) 라이브: 표준형(연도O)+법원명이 있으면 case_detail로 공식정보 조회.
         연도/법원이 부족하면 입력폼(법원 드롭다운)으로 되묻는다.
    """
    raw = (request.args.get("q") or "").strip()
    court = (request.args.get("court") or "").strip()
    courts = casesearch.list_courts()
    ctx = {"raw": raw, "court_sel": court, "courts": courts,
           "won": report.won, "data_source": getattr(g, "data_source", "n/a")}
    if not raw:
        return render_template("find.html", stage="prompt", **ctx)

    q = casesearch.parse_case_query(raw)
    if q is None:
        return render_template("find.html", stage="unparsed", **ctx)
    # 힌트로 들어온 법원(입력에 섞인 것)과 드롭다운 선택을 합침(드롭다운 우선).
    court = court or (q.court or "")

    # 1) 로컬 큐레이션
    local = casesearch.match_local(web._scored(), q)
    if court:
        cn = casesearch._norm_court(court)
        local = [s for s in local if cn in casesearch._norm_court(s.court)]
    if len(local) == 1:
        s = local[0]
        url = f"/property/{s.case_no}"
        if s.item_no:
            url += f"?item={s.item_no}"
        return redirect(url)
    if len(local) > 1:
        return render_template("find.html", stage="local_multi", matches=local, query=q, **ctx)

    # 2) 라이브 — 연도+법원이 있어야 관할을 특정할 수 있다.
    # (2026-08-24 보안감사) 라이브 조회는 익명 요청 1건당 대법원 사이트 실요청을
    # 유발한다 — 공개 배포에서 악용되면 공유 IP 밴 → 크롤 파이프라인 전체 마비.
    # 로컬 큐레이션 검색(위 1)은 누구나, 라이브 단건 조회는 운영자만.
    if not _is_admin():
        return render_template(
            "find.html", stage="error", query=q,
            message="큐레이션에 없는 사건의 실시간 조회는 운영자 전용입니다.", **ctx)
    if not q.canonical:
        return render_template("find.html", stage="need_year", query=q, **ctx)
    if not court:
        return render_template("find.html", stage="need_court", query=q, **ctx)
    try:
        from ..courtauction_client import CourtAuctionBlocked  # noqa: PLC0415
        view = casesearch.live_lookup(q, court_name=court)
    except casesearch.CaseSearchError as e:
        return render_template("find.html", stage="error", message=str(e), query=q, **ctx)
    except CourtAuctionBlocked as e:
        logger.warning("사건 라이브 조회 차단/실패: %s", e)
        return render_template("find.html", stage="error", query=q,
                               message="대법원 접속이 일시 제한되었습니다. 잠시 후 다시 시도해 주세요.",
                               **{k: v for k, v in ctx.items()})
    except Exception as e:  # noqa: BLE001 — 라이브 실패가 페이지를 500으로 떨구지 않게
        logger.warning("사건 라이브 조회 오류(%s): %s", type(e).__name__, e)
        return render_template("find.html", stage="error", query=q,
                               message="사건 조회 중 오류가 발생했습니다. 법원·사건번호를 확인해 주세요.",
                               **ctx)
    return render_template("find.html", stage="live", view=view, query=q, **ctx)
