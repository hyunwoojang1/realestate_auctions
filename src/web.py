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

from flask import Flask, abort, jsonify, render_template, request

from . import backtest, digest, pipeline, query, report, score, store
from .models import AuctionListing

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DB_ENV = "AUCTION_DB"   # 설정 시 라이브 적재 DB에서 서빙, 미설정 시 샘플 계산


def _scored():
    """채점된 매물 목록.

    AUCTION_DB 환경변수가 가리키는 DB에 적재된 결과가 있으면 그것을 서빙한다
    (새로고침 작업이 `run.py --live`로 채워둔 라이브 결과 — 매 요청 API 호출 회피).
    DB가 없거나 비었으면 샘플 데이터로 폴백(개발/테스트 결정성 유지).
    """
    db_path = os.environ.get(DB_ENV)
    if db_path:
        try:
            conn = store.connect(db_path)
            try:
                if store.has_rows(conn):
                    return store.load_scored(conn)
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 — DB 문제 시 샘플로 안전 폴백
            logger.warning("DB 서빙 실패(%s) → 샘플 폴백: %s", db_path, e)
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
        return {"status": "ok"}

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
