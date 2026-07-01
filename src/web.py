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

from flask import Flask, abort, g, jsonify, render_template, request

from . import backtest, digest, pipeline, query, report, score, store
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
    """요청 쿼리(min_score/type/region/sort)로 필터·정렬된 목록."""
    min_score = args.get("min_score", type=float)
    ptype = args.get("type")
    region = args.get("region")
    sort = args.get("sort", "score")
    if sort not in query.SORT_KEYS:
        sort = "score"
    return query.sort_items(query.apply_filters(_scored(), min_score, ptype, region), sort)


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
        items = _filtered(request.args)
        rows = [
            {"s": s, "badge": report.score_badge_html(s),
             "meter": report.gap_meter_html(s), "profit": report.won(s.expected_profit)}
            for s in items
        ]
        filters = {
            "min_score": request.args.get("min_score", ""),
            "type": request.args.get("type", ""),
            "region": request.args.get("region", ""),
            "sort": request.args.get("sort", "score"),
        }
        return render_template("listings.html", rows=rows, count=len(items), filters=filters)

    @app.get("/health")
    def health():
        src = _probe_source()
        _mark_source(src)
        return {"status": "ok", "data_source": src}

    @app.get("/api/listings")
    def listings():
        return jsonify([s.to_row() for s in _filtered(request.args)])

    @app.get("/api/listings/<case_no>")
    def listing_detail(case_no: str):
        match = next((s for s in _scored() if s.case_no == case_no), None)
        if match is None:
            abort(404)
        return jsonify(match.to_row())

    @app.get("/property/<case_no>")
    def property_detail(case_no: str):
        s = next((x for x in _scored() if x.case_no == case_no), None)
        if s is None:
            abort(404)
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
        return render_template(
            "detail.html", s=s, listing=listing,
            badge=report.score_badge_html(s), meter=report.gap_meter_html(s),
            won=report.won, pct=report.pct,
            gated=gated, gate_reason=", ".join(gate_reasons),
        )

    @app.get("/digest")
    def digest_page():
        n = request.args.get("n", default=10, type=int)
        min_score = request.args.get("min_score", type=float)
        items = digest.top_listings(_scored(), n=n, min_score=min_score)
        rows = [
            {"s": s, "badge": report.score_badge_html(s),
             "meter": report.gap_meter_html(s), "profit": report.won(s.expected_profit)}
            for s in items
        ]
        filters = {"min_score": request.args.get("min_score", ""), "type": "", "region": "", "sort": "score"}
        return render_template("listings.html", rows=rows, count=len(items), filters=filters)

    @app.get("/methodology")
    def methodology():
        rows = backtest.evaluate()
        cal = backtest.calibration(rows)
        prec = {t: backtest.precision_at(rows, t) for t in (80, 60, 40)}
        return render_template("methodology.html", cfg=score.CONFIG, cal=cal, prec=prec, won=report.won)

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
