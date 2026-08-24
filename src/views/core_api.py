"""JSON API·CSV — /health, /api/listings(.geojson), /api/listings/<case>, /api/stats,
/api/bidsim, /export.csv.

공유 헬퍼(_filtered·_scored 등)는 `web.` 모듈 속성으로 늦게 바인딩(monkeypatch 보존).
"""
from __future__ import annotations

import os

from flask import Blueprint, abort, current_app, g, jsonify, request

from .. import bidsim, report, stats, store_rest, web
from ..web import DB_ENV, STALE_HOURS
from .find import _find_by_case

bp = Blueprint("core_api", __name__)


@bp.get("/health")
def health():
    import time as _time  # noqa: PLC0415
    src = web._probe_source()
    web._mark_source(src)
    # (적대감사 F7) 권리 로드 최근 실패를 노출 — 배지 전멸(스키마 드리프트 400 등)이
    # 경고 로그 한 줄로 침묵하지 않게 운영자가 헬스체크에서 바로 본다. 15분 지나면 ok 복귀.
    rights = "ok"
    if web._rights_last_error["at"] and _time.time() - web._rights_last_error["at"] < 900:
        rights = f"failed: {web._rights_last_error['msg']}"
    # (2026-08-24 감사 H-3) 백엔드가 구성돼 있는데 샘플 폴백 중이면 = 프로덕션이 가짜
    # 데이터를 서빙 중 — 503 degraded 로 배포 검증·워치독이 잡게 한다. 백엔드 자체가
    # 없는 순수 로컬 데모(sample(no-db))는 정상 상태이므로 종전대로 200 ok.
    asof, age_h = web.data_freshness()
    backend_expected = bool(os.environ.get(DB_ENV)) or store_rest.enabled()
    degraded = src.startswith("sample") and backend_expected
    body = {
        "status": "degraded" if degraded else "ok",
        "data_source": src, "rights_source": rights,
        "data_asof": asof,
        "data_age_hours": round(age_h, 1) if age_h is not None else None,
        "data_stale": bool(age_h is not None and age_h > STALE_HOURS),
    }
    return (body, 503) if degraded else body


@bp.get("/api/listings")
def listings():
    # (재검증 감사 idx4) API 에도 인수 부담 필드 병기 — 소비자가 인수 미반영 profit 만
    # 보고 실질 음수 물건을 양수로 오인하지 않게.
    # (2026-08-24 성능감사 CRITICAL) 홈(index)에만 있던 병렬 워밍을 API 에도 배선 —
    # 종전엔 목록→권리→네이버 3개 데이터셋을 콜드에서 **순차** 전량 로드해 22.7초
    # (실측, /health 2.7초 대비). 홈과 같은 캐시를 쓰므로 의미는 그대로다.
    store_rest.warm_caches()
    badges = web._rights_badges()
    out = []
    for s in web._filtered(request.args, badges=badges):
        row = s.to_row()
        b = badges.get(f"{s.court}|{s.case_no}|{s.item_no}")
        row["burden_status"] = ("clean" if b and b.is_clean
                                else "burden" if b else "unknown")
        row["assumed_amount"] = b.assumed if b else None
        # (감사 2026-07-23 P-13) 상세 화면은 "매수인이 인수함"을 표시하는데 API 소비자는
        # assumed_amount 가 비어 있어 인수를 0으로 계산했다(표본 53/53). 금액 미상을 명시한다 —
        # CSV(report.csv_text)는 이미 '있음(금액 미상)'·유효차익 공란으로 처리하고 있었다.
        row["assumed_amount_unknown"] = bool(b and b.amount_unknown)
        out.append(row)
    return jsonify(out)


@bp.get("/export.csv")
def export_csv():
    # 목록과 동일 필터·정렬 결과를 CSV로 다운로드(엑셀 검토용). 순수 조회.
    badges = web._rights_badges()
    items = web._filtered(request.args, badges=badges)
    # 저장된 CSV 파일만 봐도 출처를 알 수 있게 파일명에 각인 — 샘플 폴백(DB 장애/미적재)을
    # 라이브로 오인하는 침묵실패 방지(감사 #12 HIGH). db가 아니면 _SAMPLE 접미사.
    src = getattr(g, "data_source", "n/a")
    fname = "auction_arbitrage.csv" if src == "db" else "auction_arbitrage_SAMPLE.csv"
    # UTF-8-SIG BOM: 엑셀이 한글을 깨지 않게. report.csv_text 재사용(중복 구현 금지).
    body = "﻿" + report.csv_text(items, badges=badges)
    resp = current_app.response_class(body, mimetype="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="{fname}"'
    resp.charset = "utf-8"
    return resp


@bp.get("/api/listings.geojson")
def listings_geojson():
    """지도용 GeoJSON — 목록과 동일 필터. 좌표는 KATEC→WGS84 캐시(coords.py) 조인."""
    store_rest.warm_caches()   # (2026-08-24 성능감사) 콜드 순차 로드 → 병렬 워밍
    from .. import coords  # noqa: PLC0415
    from .. import query as q
    from .. import region as reg  # noqa: PLC0415
    cache = coords.load_coord_cache()
    feats = []
    skipped = 0
    geo_badges = web._rights_badges()
    # 지도 3단 스코프:
    #   기본(profit) = 효과 차익(인수 차감 후) > 0 인 '차익 양수만' — 진짜 살 만한 것.
    #   scope=evaluable = 시세 추정된 것 전부(양수·음수 무관, 평가 가능).
    #   all=1 = 미지원·시세추정불가까지 전부 탐색.
    # 효과 차익·인수금액은 요청마다 권리 배지에서 계산 — 권리분석 크롤이 인수금액을
    # 채우는 대로 양수만 집합이 자동 재계산된다(정적 목록 아님).
    items = web._filtered(request.args, badges=geo_badges, evaluable_default=True)
    scope = "all" if web._truthy(request.args.get("all")) else request.args.get("scope", "profit")
    if scope == "profit":
        items = q.positive_only(items, burden_of=web._burden_of(geo_badges),
                                uncertain_of=web._uncertain_of(geo_badges))
    for s in items:
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
                "sido": reg.sido_of(s.address) or "기타",   # 클라이언트 지역 필터/집계용
                # 인수 부담인데 금액 미상 → 효과 차익 마이너스일 수 있음(−α). 평가가능/전체
                # 뷰에서 표시하고 '차익 양수만' 기본에서는 제외(query.positive_only).
                "uncertain": bool(b and b.amount_unknown),
                "burden": ("clean" if b and b.is_clean else "burden" if b else "unknown"),
                "conservative": s.profit_low is not None,
                "min_bid": s.min_bid_price,
                "url": f"/property/{s.case_no}" + (f"?item={s.item_no}" if s.item_no else ""),
            },
        })
    # '어디에 몇 건' — 후보 전수 기준 시도별 카운트(좌표 유무 무관).
    return jsonify({"type": "FeatureCollection", "features": feats,
                    "no_coord_count": skipped,
                    "by_sido": q.count_by_sido(items)})


@bp.get("/api/listings/<case_no>")
def listing_detail(case_no: str):
    store_rest.warm_caches()   # (2026-08-24 성능감사) 콜드 순차 로드 → 병렬 워밍
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


@bp.get("/api/bidsim")
def bidsim_api():
    """입찰가 시뮬레이터 — 순수 계산(DB 무접근). 상세페이지 슬라이더가 입력마다 호출한다.

    물건 식별자가 아니라 **가정 전부를 쿼리로** 받는다 — DB 왕복이 없어 빠르고,
    계산 로직이 파이썬 한 곳에만 존재한다(JS에 세율을 복제하지 않는다 = 단일 출처).
    """
    a = request.args
    inp = bidsim.SimInput(
        bid_price=web._clamp_int(a.get("bid"), 0, web._MAX_WON),
        property_type=(a.get("type") or "")[:40],
        area_m2=web._clamp_float(a.get("area"), 0.0, 100_000.0, 0.0),
        sell_price=web._clamp_int(a.get("sell"), 0, web._MAX_WON),
        holding_months=web._clamp_int(a.get("months"), 0, 600,
                                      bidsim.DEFAULT_HOLDING_MONTHS),
        assumed_amount=web._clamp_int(a.get("assumed"), 0, web._MAX_WON),
        eviction_cost=web._clamp_int(a.get("eviction"), 0, web._MAX_COST,
                                     bidsim.DEFAULT_EVICTION),
        repair_cost=web._clamp_int(a.get("repair"), 0, web._MAX_COST),
        unpaid_fees=web._clamp_int(a.get("unpaid"), 0, web._MAX_COST),
        registry_cost=web._clamp_int(a.get("registry"), 0, web._MAX_COST,
                                     bidsim.DEFAULT_REGISTRY),
        loan_ltv=web._clamp_float(a.get("ltv"), 0.0, 1.0, bidsim.DEFAULT_LTV),
        loan_rate=web._clamp_float(a.get("rate"), 0.0, 0.30, bidsim.DEFAULT_LOAN_RATE),
        # 매도 세금 기준(개인/매매사업자/미계산). 모르는 값은 기본값으로 — 조작된 쿼리로
        # 세금 0 을 만들 수 있지만 개인 도구라 무해하고, 화면이 어떤 기준인지 항상 표시한다.
        tax_mode=(a.get("taxmode") or bidsim.DEFAULT_TAX_MODE),
    )
    return jsonify(web._sim_payload(inp))


@bp.get("/api/stats")
def stats_api():
    return jsonify(stats.summarize(web._scored()))
