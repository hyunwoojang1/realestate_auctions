"""Flask 웹 레이어 — 차익 큐레이션 JSON API (W1).

엔드포인트:
  GET /health                 헬스체크
  GET /api/listings           차익 스코어순 목록 (min_score/type/region/sort 쿼리 필터)
  GET /api/listings/<case_no> 단건 상세 (없으면 404)

데이터는 PoC 샘플(pipeline.run). 키가 있으면 추후 라이브로 전환(F10). FastAPI/pydantic 미사용
(Python 3.14 빌드 리스크 회피) — 순수 파이썬 Flask.
"""
from __future__ import annotations

from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request

from . import pipeline, query, report

ROOT = Path(__file__).resolve().parent.parent


def _scored():
    """현재 채점된 매물 목록. PoC: 샘플 데이터(요청마다 계산 — 가볍다)."""
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
        return render_template("listings.html", rows=rows, count=len(items))

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

    return app


app = create_app()
