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
    if not db_path:
        return "sample(no-db)"
    try:
        conn = store.connect(db_path)
        try:
            return "db" if store.has_rows(conn) else "sample(db-empty)"
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — 상태 조회 실패도 폴백 상태의 일부
        return "sample(db-error)"


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
    else:
        _mark_source("sample(no-db)")
    return pipeline.run()


def _filtered(args):
    """요청 쿼리(min_profit[억]/min_score/type/region/sort)로 필터·정렬된 목록."""
    min_score = args.get("min_score", type=float)          # API 하위호환용
    min_profit_eok = args.get("min_profit", type=float)    # UI: 억 단위 입력
    min_profit = int(min_profit_eok * 1e8) if min_profit_eok else None
    ptype = args.get("type")
    region = args.get("region")
    sort = args.get("sort", query.DEFAULT_SORT)
    if sort not in query.SORT_KEYS:
        sort = query.DEFAULT_SORT
    return query.sort_items(
        query.apply_filters(_scored(), min_score, ptype, region, min_profit=min_profit), sort)


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
        items = _filtered(request.args)
        filters = {
            "min_profit": request.args.get("min_profit", ""),
            "type": request.args.get("type", ""),
            "region": request.args.get("region", ""),
            "sort": request.args.get("sort", query.DEFAULT_SORT),
        }
        # (T7→T8 감사 수정) 히어로 스포트라이트는 최대 추천 표면 — digest와 동일 게이트를
        # 레거시 통과 없이(strict) 적용한다: 보수차익 양수·같은단지같은평형·basis≥5·비위험.
        # 게이트 정보가 없는 구 DB에서는 히어로를 띄우지 않는다(검증 안 된 헤드라인 금지).
        from .digest import passes_recommend_gates  # noqa: PLC0415
        hero = next((s for s in items if passes_recommend_gates(s, allow_legacy=False)), None)
        # (T8 감사 HIGH) 표시 물건 전부가 레거시(보수차익 미계산·구 채점)면 라벨-값 불일치가
        # 생기므로 배너로 고지하고 컬럼 라벨도 구 기준임을 표기한다.
        legacy_only = bool(items) and all(s.profit_low is None for s in items)
        return render_template(
            "listings.html", items=items, count=len(items), filters=filters,
            hero=hero, legacy_only=legacy_only,
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
        return jsonify([s.to_row() for s in _filtered(request.args)])

    @app.get("/export.csv")
    def export_csv():
        # 목록과 동일 필터·정렬 결과를 CSV로 다운로드(엑셀 검토용). 순수 조회.
        items = _filtered(request.args)
        # 저장된 CSV 파일만 봐도 출처를 알 수 있게 파일명에 각인 — 샘플 폴백(DB 장애/미적재)을
        # 라이브로 오인하는 침묵실패 방지(감사 #12 HIGH). db가 아니면 _SAMPLE 접미사.
        src = getattr(g, "data_source", "n/a")
        fname = "auction_arbitrage.csv" if src == "db" else "auction_arbitrage_SAMPLE.csv"
        # UTF-8-SIG BOM: 엑셀이 한글을 깨지 않게. report.csv_text 재사용(중복 구현 금지).
        body = "﻿" + report.csv_text(items)
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
        for s in _filtered(request.args):
            pt = coords.lookup(cache, s.uid, s.case_no)
            if not pt:
                skipped += 1
                continue
            p = q.decision_profit(s)
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [pt[1], pt[0]]},
                "properties": {
                    "case_no": s.case_no, "item_no": s.item_no,
                    "apt_name": s.apt_name, "property_type": s.property_type,
                    "grade": s.grade, "profit": p,
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
        return render_template(
            "detail.html", s=s, listing=listing,
            meter=report.gap_meter_html(s), won=report.won, pct=report.pct,
            gated=gated, gate_reason=", ".join(gate_reasons),
            tax_parts=tax_parts, tax_label=tax.PROFILE.label(),
            watching=case_no in watchlist.load_watchlist(watchlist.watchlist_path()),
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
        items = digest.top_listings(_scored(), n=n, min_profit=min_profit)
        filters = {"min_profit": request.args.get("min_profit", ""), "type": "", "region": "",
                   "sort": query.DEFAULT_SORT}
        return render_template(
            "listings.html", items=items, count=len(items), filters=filters,
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
        watched = query.sort_items([s for s in items if s.case_no in wl])
        missing = sorted(wl - {s.case_no for s in items})
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

    @app.post("/api/watchlist/<case_no>")
    def watchlist_api_add(case_no: str):
        if not _case_exists(case_no):
            abort(404)
        watchlist.add_watch(case_no, watchlist.watchlist_path())
        return {"ok": True, "watching": True}

    @app.delete("/api/watchlist/<case_no>")
    def watchlist_api_remove(case_no: str):
        watchlist.remove_watch(case_no, watchlist.watchlist_path())
        return {"ok": True, "watching": False}

    @app.post("/watchlist/toggle/<case_no>")
    def watchlist_toggle(case_no: str):
        p = watchlist.watchlist_path()
        wl = watchlist.load_watchlist(p)
        if case_no in wl:
            watchlist.remove_watch(case_no, p)
        else:
            if not _case_exists(case_no):
                abort(404)
            watchlist.add_watch(case_no, p)
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
        d = stats.summarize(_scored())
        return render_template("stats.html", d=d, won=report.won, pct=report.pct,
                               data_source=getattr(g, "data_source", "n/a"))

    @app.get("/compare")
    def compare_page():
        from . import tax  # noqa: PLC0415
        cases = request.args.getlist("case")[:compare.MAX_COMPARE]  # 입력 개수 하드캡(방어)
        items = compare.select_for_compare(_scored(), cases)
        # 요청했으나 조회 결과에 없는 사건(매각·취하 등으로 목록에서 사라짐) — 침묵 드롭 방지
        found = {s.case_no for s in items}
        missing_cases = [c for c in cases if c not in found]
        taxes = {s.case_no: tax.acquisition_tax_breakdown(s.min_bid_price, s.property_type, s.area_m2)
                 for s in items}
        return render_template("compare.html", items=items, taxes=taxes,
                               requested=len(cases), missing_cases=missing_cases,
                               won=report.won, pct=report.pct, tax_label=tax.PROFILE.label(),
                               data_source=getattr(g, "data_source", "n/a"))

    @app.get("/api/stats")
    def stats_api():
        return jsonify(stats.summarize(_scored()))

    @app.get("/methodology")
    def methodology():
        from . import tax  # noqa: PLC0415
        rows = backtest.evaluate()
        cal = backtest.calibration(rows)
        prec = {t: backtest.precision_at(rows, t) for t in (80, 60, 40)}
        return render_template("methodology.html", cfg=score.CONFIG, cal=cal, prec=prec,
                               won=report.won, tax_label=tax.PROFILE.label(),
                               data_source="sample")

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
