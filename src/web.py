"""Flask 웹 레이어 — 차익 큐레이션 JSON API (W1).

엔드포인트:
  GET /health                 헬스체크
  GET /api/listings           차익 스코어순 목록 (min_score/type/region/sort 쿼리 필터)
  GET /api/listings/<case_no> 단건 상세 (없으면 404)

데이터는 PoC 샘플(pipeline.run). 키가 있으면 추후 라이브로 전환(F10). FastAPI/pydantic 미사용
(Python 3.14 빌드 리스크 회피) — 순수 파이썬 Flask.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Flask, abort, g, jsonify, redirect, render_template, request

from . import (
    backtest,
    compare,
    digest,
    pipeline,
    query,
    report,
    sale_calendar,
    score,
    stats,
    store,
    store_rest,
    watchlist,
)
from .models import AuctionListing

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DB_ENV = "AUCTION_DB"   # 설정 시 라이브 적재 DB에서 서빙, 미설정 시 샘플 계산


def _mark_source(src: str) -> None:
    """이번 응답이 어떤 데이터 출처(db/sample*)인지 flask.g에 기록. after_request가 헤더로 노출.

    요청 컨텍스트 밖(테스트에서 _scored 직접 호출)에서는 조용히 무시한다.
    """
    try:
        g.data_source = src
    except RuntimeError:
        pass


def _probe_source() -> str:
    """데이터 출처를 '읽기전용'으로 판정(로드 없이). /health·헤더용.

    반환: db | sample(db-empty) | sample(db-error) | sample(no-db)
    """
    db_path = os.environ.get(DB_ENV)
    if db_path:
        try:
            conn = store.connect(db_path)
            try:
                return "db" if store.has_rows(conn) else "sample(db-empty)"
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — 상태 조회 실패도 폴백 상태의 일부
            return "sample(db-error)"
    if store_rest.enabled():
        try:
            return "db" if store_rest.has_rows() else "sample(db-empty)"
        except Exception:  # noqa: BLE001
            return "sample(db-error)"
    return "sample(no-db)"


def _scored():
    """채점된 매물 목록.

    AUCTION_DB 환경변수가 가리키는 DB에 적재된 결과가 있으면 그것을 서빙한다
    (새로고침 작업이 `run.py --live`로 채워둔 라이브 결과 — 매 요청 API 호출 회피).
    DB가 없거나 비었으면 샘플 데이터로 폴백하되, '라이브인 줄 오인'을 막기 위해
    폴백 사유를 로그로 남기고 출처를 표시한다(응답 헤더 X-Data-Source, /health).
    """
    db_path = os.environ.get(DB_ENV)
    if db_path:
        try:
            conn = store.connect(db_path)
            try:
                if store.has_rows(conn):
                    _mark_source("db")
                    return store.load_scored(conn)
            finally:
                conn.close()
            # 연결은 됐지만 적재 결과 0건 — 조용히 샘플로 넘어가지 않도록 경고(침묵실패 방지).
            logger.warning(
                "AUCTION_DB(%s) 연결됐으나 적재 결과 0건 → 샘플 폴백(라이브 데이터 아님). "
                "새로고침(run.py --live)이 실패했거나 아직 실행 전일 수 있음.", db_path)
            _mark_source("sample(db-empty)")
        except Exception as e:  # noqa: BLE001 — DB 문제 시 샘플로 안전 폴백
            logger.error("DB 서빙 실패(%s) → 샘플 폴백: %s", db_path, e, exc_info=True)
            _mark_source("sample(db-error)")
    elif store_rest.enabled():
        # 클라우드 서빙(Vercel): AUCTION_DB 미설정 + SUPABASE_URL 있으면 REST 에서 읽는다.
        try:
            rows = store_rest.load_scored()
            if rows:
                _mark_source("db")
                return rows
            logger.warning(
                "Supabase 연결됐으나 적재 결과 0건 → 샘플 폴백(라이브 데이터 아님). "
                "새로고침(run.py --live)이 실패했거나 아직 실행 전일 수 있음.")
            _mark_source("sample(db-empty)")
        except Exception as e:  # noqa: BLE001 — REST 문제 시 샘플로 안전 폴백
            logger.error("Supabase 서빙 실패 → 샘플 폴백: %s", e, exc_info=True)
            _mark_source("sample(db-error)")
    else:
        _mark_source("sample(no-db)")
    return pipeline.run()


def _rights_badges() -> dict:
    """(court|case_no|item_no) → RightsBadge. 크롤된 물건만 담긴다(없으면 '미확인' 렌더).

    목록의 '인수 부담' 칩·예상 투입 계산용. rights 테이블은 수백 행 수준이라 요청당
    로드해도 가볍고, 클라우드는 store_rest 가 TTL 캐시로 왕복을 줄인다.
    """
    from .courtauction_detail import CaseRights, summarize  # noqa: PLC0415
    rows: list[dict] = []
    db_path = os.environ.get(DB_ENV)
    try:
        if db_path:
            conn = store.connect(db_path)
            try:
                rows = store.fetch_all_rights(conn)
            finally:
                conn.close()
        elif store_rest.enabled():
            rows = store_rest.load_all_rights()
    except Exception as e:  # noqa: BLE001 — 배지 실패는 목록을 막지 않음(미확인으로 폴백)
        logger.warning("권리 배지 로드 실패 → 전량 '미확인' 폴백: %s", e)
        return {}
    out = {}
    for r in rows:
        cr = CaseRights.from_row(r)
        out[f"{cr.court}|{cr.case_no}|{cr.item_no}"] = summarize(cr)
    return out


def _burden_of(badges: dict):
    """badges → (물건 → 인수금액 원) 콜러블. 미크롤 물건은 0(차감 없음)."""
    def f(s):
        b = badges.get(f"{s.court}|{s.case_no}|{s.item_no}")
        return b.assumed if b else 0
    return f


def _filtered(args, badges: dict | None = None):
    """요청 쿼리(min_profit[억]/min_score/type/region/sort)로 필터·정렬된 목록.

    정렬·최소차익 필터는 인수금 차감 후 유효 차익 기준 — 화면 표시(p_adj)와 일치
    (감사 2026-07-10: 순위-표시 역전 해소).
    """
    min_score = args.get("min_score", type=float)          # API 하위호환용
    min_profit_eok = args.get("min_profit", type=float)    # UI: 억 단위 입력
    min_profit = int(min_profit_eok * 1e8) if min_profit_eok else None
    ptype = args.get("type")
    region = args.get("region")
    sort = args.get("sort", query.DEFAULT_SORT)
    if sort not in query.SORT_KEYS:
        sort = query.DEFAULT_SORT
    burden = _burden_of(badges if badges is not None else _rights_badges())
    return query.sort_items(
        query.apply_filters(_scored(), min_score, ptype, region,
                            min_profit=min_profit, burden_of=burden),
        sort, burden_of=burden)


def create_app() -> Flask:
    app = Flask(__name__, template_folder=str(ROOT / "templates"))
    app.json.ensure_ascii = False   # 한글 그대로 직렬화
    app.json.sort_keys = False

    @app.after_request
    def _tag_data_source(resp):
        # 모든 응답에 데이터 출처를 노출 — 샘플을 라이브로 오인하는 것을 방지.
        resp.headers["X-Data-Source"] = getattr(g, "data_source", "n/a")
        return resp

    @app.get("/")
    def index():
        from . import tax  # noqa: PLC0415
        # 인수 부담 배지(법원 명세서 크롤분) — '낙찰가 외 추가 부담' 여부를 목록에서 구분.
        badges = _rights_badges()
        items = _filtered(request.args, badges=badges)
        filters = {
            "min_profit": request.args.get("min_profit", ""),
            "type": request.args.get("type", ""),
            "region": request.args.get("region", ""),
            "sort": request.args.get("sort", query.DEFAULT_SORT),
            "clean": request.args.get("clean", ""),
        }
        if filters["clean"]:
            # '추가 인수 없음만' — 명세서로 clean 확인된 물건만(미확인은 보수적으로 제외).
            # (재검증 감사 idx5) 시세추정불가·미지원유형 등 값이 전부 '—'인 물건도 제외 —
            # 권리만 깨끗하고 판단 근거(시세·차익)가 없는 행이 섞이면 필터 취지가 흐려진다.
            items = [s for s in items
                     if (b := badges.get(f"{s.court}|{s.case_no}|{s.item_no}"))
                     and b.is_clean
                     and (s.profit_low is not None or s.expected_profit is not None)]
        # (T7→T8 감사 수정) 히어로 스포트라이트는 최대 추천 표면 — digest와 동일 게이트를
        # 레거시 통과 없이(strict) 적용한다: 보수차익 양수·같은단지같은평형·basis≥5·비위험.
        # 게이트 정보가 없는 구 DB에서는 히어로를 띄우지 않는다(검증 안 된 헤드라인 금지).
        from .digest import passes_recommend_gates  # noqa: PLC0415
        # 히어로는 최대 추천 표면 — 채점 게이트에 더해 인수 부담 배지도 본다(감사 2026-07-10:
        # '대항력+인수 3.3억, 실질 음수' 물건이 hero 로 뽑히던 사각). 명세서로 clean 확인된
        # 물건만 허용하되, 배지 데이터가 아예 없는 환경(rights 미크롤 DB)은 기존 동작 유지.
        def _hero_ok(s):
            if not passes_recommend_gates(s, allow_legacy=False):
                return False
            if not badges:
                return True
            b = badges.get(f"{s.court}|{s.case_no}|{s.item_no}")
            return b is not None and b.is_clean
        hero = next((s for s in items if _hero_ok(s)), None)
        # (T8 감사 HIGH) 표시 물건 전부가 레거시(보수차익 미계산·구 채점)면 라벨-값 불일치가
        # 생기므로 배너로 고지하고 컬럼 라벨도 구 기준임을 표기한다.
        legacy_only = bool(items) and all(s.profit_low is None for s in items)
        return render_template(
            "listings.html", items=items, count=len(items), filters=filters,
            hero=hero, legacy_only=legacy_only, badges=badges,
            won=report.won, pct=report.pct, meter=report.gap_meter_html,
            tax_label=tax.PROFILE.label(),
            watched=watchlist.load_watchlist(watchlist.watchlist_path()),
            data_source=getattr(g, "data_source", "n/a"))

    @app.get("/health")
    def health():
        src = _probe_source()
        _mark_source(src)
        return {"status": "ok", "data_source": src}

    @app.get("/api/listings")
    def listings():
        # (재검증 감사 idx4) API 에도 인수 부담 필드 병기 — 소비자가 인수 미반영 profit 만
        # 보고 실질 음수 물건을 양수로 오인하지 않게.
        badges = _rights_badges()
        out = []
        for s in _filtered(request.args, badges=badges):
            row = s.to_row()
            b = badges.get(f"{s.court}|{s.case_no}|{s.item_no}")
            row["burden_status"] = ("clean" if b and b.is_clean
                                    else "burden" if b else "unknown")
            row["assumed_amount"] = b.assumed if b else None
            out.append(row)
        return jsonify(out)

    @app.get("/export.csv")
    def export_csv():
        # 목록과 동일 필터·정렬 결과를 CSV로 다운로드(엑셀 검토용). 순수 조회.
        badges = _rights_badges()
        items = _filtered(request.args, badges=badges)
        # 저장된 CSV 파일만 봐도 출처를 알 수 있게 파일명에 각인 — 샘플 폴백(DB 장애/미적재)을
        # 라이브로 오인하는 침묵실패 방지(감사 #12 HIGH). db가 아니면 _SAMPLE 접미사.
        src = getattr(g, "data_source", "n/a")
        fname = "auction_arbitrage.csv" if src == "db" else "auction_arbitrage_SAMPLE.csv"
        # UTF-8-SIG BOM: 엑셀이 한글을 깨지 않게. report.csv_text 재사용(중복 구현 금지).
        body = "﻿" + report.csv_text(items, badges=badges)
        resp = app.response_class(body, mimetype="text/csv")
        resp.headers["Content-Disposition"] = f'attachment; filename="{fname}"'
        resp.charset = "utf-8"
        return resp

    def _find_by_case(case_no: str):
        """(T8 감사 수정 — B14 핵심) 사건번호 매칭 물건 전부.

        T1 복합키 도입으로 같은 사건의 물건 여러 개가 공존한다 — 단건 next()는 임의의
        한 물건만 반환해 다른 물건의 수치(아파트 vs 상가)를 보여주는 침묵 오표시를 만든다.
        item(물건번호)·court 쿼리 파라미터로 좁힐 수 있다.
        """
        matches = [s for s in _scored() if s.case_no == case_no]
        item = request.args.get("item")
        court = request.args.get("court")
        if item is not None:
            matches = [s for s in matches if s.item_no == item]
        if court:
            matches = [s for s in matches if s.court == court]
        return matches

    @app.get("/api/listings.geojson")
    def listings_geojson():
        """지도용 GeoJSON — 목록과 동일 필터. 좌표는 KATEC→WGS84 캐시(coords.py) 조인."""
        from . import coords  # noqa: PLC0415
        from . import query as q
        cache = coords.load_coord_cache()
        feats = []
        skipped = 0
        geo_badges = _rights_badges()
        for s in _filtered(request.args, badges=geo_badges):
            pt = coords.lookup(cache, s.uid, s.case_no, court=s.court)
            if not pt:
                skipped += 1
                continue
            p = q.decision_profit(s)
            # (재검증 감사 idx4) 지도 핀 차익도 인수금 차감한 유효 차익으로.
            b = geo_badges.get(f"{s.court}|{s.case_no}|{s.item_no}")
            if p is not None and b and b.assumed:
                p = p - b.assumed
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [pt[1], pt[0]]},
                "properties": {
                    "case_no": s.case_no, "item_no": s.item_no,
                    "apt_name": s.apt_name, "property_type": s.property_type,
                    "grade": s.grade, "profit": p,
                    "burden": ("clean" if b and b.is_clean else "burden" if b else "unknown"),
                    "conservative": s.profit_low is not None,
                    "min_bid": s.min_bid_price,
                    "url": f"/property/{s.case_no}" + (f"?item={s.item_no}" if s.item_no else ""),
                },
            })
        return jsonify({"type": "FeatureCollection", "features": feats,
                        "no_coord_count": skipped})

    @app.get("/map")
    def map_page():
        # 지도 페이지는 _scored()를 직접 안 부르므로 출처를 명시 탐지(헤더 '샘플' 오표시 방지).
        src = _probe_source()
        _mark_source(src)
        return render_template("map.html", data_source=src)

    @app.get("/api/listings/<case_no>")
    def listing_detail(case_no: str):
        matches = _find_by_case(case_no)
        if not matches:
            abort(404)
        if len(matches) > 1:
            # 같은 사건에 물건 여러 개 — 임의 1건을 주지 않고 선택지를 반환(300 Multiple Choices).
            return jsonify({
                "error": "multiple_items",
                "case_no": case_no,
                "items": [{"item_no": s.item_no, "court": s.court,
                           "property_type": s.property_type, "apt_name": s.apt_name,
                           "url": f"/api/listings/{case_no}?item={s.item_no}"}
                          for s in matches],
            }), 300
        return jsonify(matches[0].to_row())

    @app.get("/property/<case_no>")
    def property_detail(case_no: str):
        from . import tax  # noqa: PLC0415
        matches = _find_by_case(case_no)
        if not matches:
            abort(404)
        if len(matches) > 1:
            # 물건 선택 페이지 — 어떤 물건인지 사용자가 고른다(잘못된 물건 수치 표시 방지).
            return render_template("choose_item.html", case_no=case_no, items=matches,
                                   won=report.won,
                                   data_source=getattr(g, "data_source", "n/a"))
        s = matches[0]
        listing = next((a for a in pipeline.load_sample_auctions() if a.case_no == case_no), None)
        if listing is None:
            # DB 서빙(courtauction 등 비-샘플) 매물 — 스코어 행에서 최소 listing 복원.
            # 권리 필드(특수권리/인수금액/점유)는 물건상세 미수집이라 기본값(게이트 비적용).
            listing = AuctionListing(
                case_no=s.case_no, court="", address=s.address, lawd_cd="", dong="",
                apt_name=s.apt_name, property_type=s.property_type, area_m2=s.area_m2,
                appraisal_price=s.appraisal_price, min_bid_price=s.min_bid_price,
                fail_count=s.fail_count, sale_date=s.sale_date,
            )
        # 권리·기일 요지 로드(물건상세 크롤분: 로컬 SQLite 우선, 클라우드는 REST) —
        # 있으면 매각물건명세서 문구로 권리 필드를 실채움해 '권리미확인'을 해제하고
        # 하드게이트가 실제 문서 기반으로 작동하게 한다.
        rights = None
        rights_row = None
        db_path = os.environ.get(DB_ENV)
        try:
            if db_path:
                rconn = store.connect(db_path)
                try:
                    rights_row = store.load_rights(rconn, s.court, s.case_no, s.item_no)
                finally:
                    rconn.close()
            elif store_rest.enabled():
                rights_row = store_rest.fetch_rights(s.court, s.case_no, s.item_no)
        except Exception as e:  # noqa: BLE001 — 권리 요지 실패는 상세 페이지를 막지 않음
            logger.warning("권리 요지 로드 실패(%s %s): %s", s.court, s.case_no, e)
        badge = None
        if rights_row:
            import dataclasses  # noqa: PLC0415

            from .courtauction_detail import CaseRights, summarize  # noqa: PLC0415
            rights = CaseRights.from_row(rights_row)
            badge = summarize(rights)
            listing = dataclasses.replace(
                listing,
                special_rights=badge.special,
                tenant_opposable=badge.opposable,
                assumed_amount=badge.assumed,
                rights_verified=True,
            )
        gated = score.is_hard_gated(listing)
        gate_reasons = []
        if gated:
            ratio = listing.assumed_amount / listing.min_bid_price if listing.min_bid_price else 1.0
            if ratio > score.CONFIG.assumed_ratio_gate:
                gate_reasons.append(f"인수금액 비율 {ratio * 100:.0f}% (>{score.CONFIG.assumed_ratio_gate * 100:.0f}%)")
            fatal = [r for r in listing.special_rights if r in score.CONFIG.fatal_special]
            if fatal:
                gate_reasons.append("·".join(fatal) + " 신고")
        tax_parts = tax.acquisition_tax_breakdown(s.min_bid_price, s.property_type, s.area_m2)
        # (T5) 표본 게이트 상태 — 실기반 표본이 추천 기준 미만이면 '낮은 신뢰' 경고 노출.
        # (T8 감사 수정) 시세 자체가 없는 물건(미지원유형·시세추정불가)에는 "시세·차익은 참고만"
        # 경고가 무의미·혼란 — est가 있을 때만 발동.
        from .matcher import band_confident_basis  # noqa: PLC0415
        sample_gate_low = (s.est_market_price is not None
                           and s.market_sample_basis is not None
                           and s.market_sample_basis < band_confident_basis())
        # (T6) 호가 스텁 — 수동 입력 파일에 있으면 점으로 표시, 없으면 완전 무표시.
        from . import asking as asking_mod  # noqa: PLC0415
        askings = asking_mod.load_asking_prices().get(case_no, [])
        ask_points = asking_mod.asking_points(askings, s.market_band_low, s.market_band_high)
        ask_overstated = asking_mod.band_overstated(askings, s.market_band_low)
        # 가격 지도 — 감정가·최저입찰가·취득원가·밴드·호가를 한 축에 그릴 좌표(UX 개편).
        from . import pricemap  # noqa: PLC0415
        pmap = pricemap.build(s, ask_points)
        return render_template(
            "detail.html", s=s, listing=listing, pmap=pmap, rights=rights, badge=badge,
            meter=report.gap_meter_html(s, askings=ask_points), won=report.won, pct=report.pct,
            gated=gated, gate_reason=", ".join(gate_reasons),
            tax_parts=tax_parts, tax_label=tax.PROFILE.label(),
            watching=watchlist.is_watched(
                watchlist.load_watchlist(watchlist.watchlist_path()), s),
            data_source=getattr(g, "data_source", "n/a"),
            sample_gate_low=sample_gate_low, band_confident=band_confident_basis(),
            ask_points=ask_points, ask_overstated=ask_overstated,
        )

    @app.get("/digest")
    def digest_page():
        from . import tax  # noqa: PLC0415
        n = request.args.get("n", default=10, type=int)
        min_profit_eok = request.args.get("min_profit", type=float)
        min_profit = int(min_profit_eok * 1e8) if min_profit_eok else None
        digest_badges = _rights_badges()
        items = digest.top_listings(_scored(), n=n, min_profit=min_profit,
                                    badges=digest_badges)
        filters = {"min_profit": request.args.get("min_profit", ""), "type": "", "region": "",
                   "sort": query.DEFAULT_SORT, "clean": ""}
        return render_template(
            "listings.html", items=items, count=len(items), filters=filters,
            badges=digest_badges,
            won=report.won, pct=report.pct, meter=report.gap_meter_html,
            tax_label=tax.PROFILE.label(),
            data_source=getattr(g, "data_source", "n/a"))

    def _case_exists(case_no: str) -> bool:
        return any(s.case_no == case_no for s in _scored())

    def _safe_back() -> str:
        """토글 후 복귀 경로 — 같은 호스트의 referrer만 허용(open redirect 방지)."""
        ref = request.referrer or ""
        if ref.startswith(request.host_url):
            return ref
        return "/watchlist"

    @app.get("/watchlist")
    def watchlist_page():
        from . import tax  # noqa: PLC0415
        items = _scored()
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

    @app.get("/api/watchlist")
    def watchlist_api_list():
        return jsonify(sorted(watchlist.load_watchlist(watchlist.watchlist_path())))

    def _wl_key_from_request(case_no: str) -> str:
        """요청의 court/item 파라미터로 복합키 구성 — 없으면(레거시 클라이언트) case_no 단독.

        동명 사건(복수 법원)·다물건 사건에서 정확한 물건 하나만 등록/해제되게 한다(감사 2026-07-10).
        """
        court = request.values.get("court", "")
        item = request.values.get("item", "")
        if court:
            return watchlist.wl_key(court, case_no, item)
        return case_no

    @app.post("/api/watchlist/<case_no>")
    def watchlist_api_add(case_no: str):
        if not _case_exists(case_no):
            abort(404)
        watchlist.add_watch(_wl_key_from_request(case_no), watchlist.watchlist_path())
        return {"ok": True, "watching": True}

    @app.delete("/api/watchlist/<case_no>")
    def watchlist_api_remove(case_no: str):
        p = watchlist.watchlist_path()
        # 복합키·레거시 둘 다 제거(어느 쪽으로 등록됐든 해제되게)
        watchlist.remove_watch(_wl_key_from_request(case_no), p)
        watchlist.remove_watch(case_no, p)
        return {"ok": True, "watching": False}

    @app.post("/watchlist/toggle/<case_no>")
    def watchlist_toggle(case_no: str):
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

    @app.get("/calendar")
    def calendar_page():
        from datetime import date as _date  # noqa: PLC0415
        items = _scored()
        show_all = request.args.get("all") == "1"
        today = _date.today().isoformat()
        upcoming, past = sale_calendar.split_upcoming(items, today)
        months = sale_calendar.month_groups(items if show_all else upcoming)
        return render_template(
            "calendar.html", months=months, today=today, show_all=show_all,
            upcoming_count=len(upcoming), past_count=len(past),
            unknown_count=sale_calendar.unknown_date_count(items),
            won=report.won, weekday=sale_calendar.weekday_kr,
            data_source=getattr(g, "data_source", "n/a"))

    @app.get("/stats")
    def stats_page():
        items = _scored()
        d = stats.summarize(items)
        # 권리 도넛 정직화(재검증 감사 idx2 HIGH): 과거 '전체 − 권리미확인 = 확인 완료(96%)'는
        # 명세서를 한 번도 안 본 물건(차익없음·미지원유형 등)까지 '확인 완료'로 세는 왜곡.
        # 실제 확인(=법원 명세서 크롤) 여부는 badges 존재로 센다.
        badges = _rights_badges()
        keys = {f"{s.court}|{s.case_no}|{s.item_no}" for s in items}
        crawled = [k for k in keys if k in badges]
        rights_stats = {
            "crawled": len(crawled),
            "burden": sum(1 for k in crawled if not badges[k].is_clean),
            "clean": sum(1 for k in crawled if badges[k].is_clean),
            "uncrawled": len(keys) - len(crawled),
        }
        return render_template("stats.html", d=d, rights=rights_stats,
                               won=report.won, pct=report.pct,
                               data_source=getattr(g, "data_source", "n/a"))

    @app.get("/compare")
    def compare_page():
        from . import tax  # noqa: PLC0415
        cases = request.args.getlist("case")[:compare.MAX_COMPARE]  # 입력 개수 하드캡(방어)
        items = compare.select_for_compare(_scored(), cases)
        # 요청했으나 조회 결과에 없는 사건(매각·취하 등으로 목록에서 사라짐) — 침묵 드롭 방지.
        # 식별자는 복합키/레거시 혼재 — 둘 다 found 로 인정.
        found = ({s.case_no for s in items}
                 | {f"{s.court}|{s.case_no}|{s.item_no}" for s in items})
        missing_cases = [c.split("|")[1] if "|" in c else c
                         for c in cases if c not in found]
        taxes = {s.case_no: tax.acquisition_tax_breakdown(s.min_bid_price, s.property_type, s.area_m2)
                 for s in items}
        return render_template("compare.html", items=items, taxes=taxes,
                               requested=len(cases), missing_cases=missing_cases,
                               won=report.won, pct=report.pct, tax_label=tax.PROFILE.label(),
                               data_source=getattr(g, "data_source", "n/a"))

    @app.get("/api/stats")
    def stats_api():
        return jsonify(stats.summarize(_scored()))

    @app.get("/guide")
    def guide():
        # 정적 경매 지식(권리분석·세금·체크리스트). 헤더 데이터배지는 실제 출처 반영(일관성).
        _mark_source(_probe_source())
        return render_template("guide.html",
                               data_source=getattr(g, "data_source", "n/a"))

    @app.get("/methodology")
    def methodology():
        from . import tax  # noqa: PLC0415
        rows = backtest.evaluate()
        cal = backtest.calibration(rows)
        prec = {t: backtest.precision_at(rows, t) for t in (80, 60, 40)}
        # 헤더 데이터배지는 실제 출처 반영(백테스트 표본과 별개 — 다른 페이지와 일관).
        _mark_source(_probe_source())
        return render_template("methodology.html", cfg=score.CONFIG, cal=cal, prec=prec,
                               won=report.won, tax_label=tax.PROFILE.label(),
                               data_source=getattr(g, "data_source", "n/a"))

    return app


app = create_app()


def _truthy(val: str | None) -> bool:
    """env flag → bool. 미설정/빈값/0/false/no/off 는 False."""
    return (val or "").strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    # 개발 편의용 진입점. 프로덕션 서빙은 waitress(scripts/start.ps1 / src.serve)를 쓴다.
    # debug/reloader 는 명시적 env flag(AUCTION_DEBUG=1)로만 켜지고, 기본값은 항상 off.
    debug = _truthy(os.environ.get("AUCTION_DEBUG"))
    host = os.environ.get("AUCTION_HOST", "127.0.0.1")
    port = int(os.environ.get("AUCTION_PORT", "8000"))
    app.run(host=host, port=port, debug=debug, use_reloader=debug)
