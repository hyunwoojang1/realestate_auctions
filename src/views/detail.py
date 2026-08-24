"""물건 상세(/property/<case_no>) — 활성·낙찰 종결 겸용 상세 페이지.

권리·사진·임차인·점유관계·건축물대장 5종 병렬 로드(A3), 낙찰 종결 물건은 sold
스냅샷으로 동일 상세를 서빙(C4). 로직은 web.py 에서 이동만(2026-08-24 blueprint 분리).
"""
from __future__ import annotations

import logging
import os

from flask import Blueprint, abort, current_app, g, render_template

from .. import bidsim, pipeline, query, report, score, store, store_rest, watchlist, web
from ..models import AuctionListing, ScoredListing
from ..web import DB_ENV
from .find import _find_by_case
from .sold import _sold_naver_row, _sold_one

logger = logging.getLogger(__name__)

bp = Blueprint("detail", __name__)


@bp.get("/property/<case_no>")
def property_detail(case_no: str):
    from .. import tax  # noqa: PLC0415
    matches = _find_by_case(case_no)
    sold = None
    if not matches:
        # (C4 2026-07-27) 낙찰 종결 물건 — 활성 목록에 없으면 보존 스냅샷으로 동일 상세 서빙.
        # 자식 데이터(사진·권리)는 C2 프룬 보존 덕에 남아 있으면 그대로 렌더된다.
        sold = _sold_one(case_no)
        if not sold:
            abort(404)
        matches = [ScoredListing(
            case_no=sold["case_no"], court=sold.get("court") or "",
            item_no=sold.get("item_no") or "",
            apt_name=sold.get("apt_name") or "", address=sold.get("address") or "",
            property_type=sold.get("property_type") or "",
            area_m2=sold.get("area_m2") or 0.0,
            appraisal_price=sold.get("appraisal_price") or 0,
            min_bid_price=sold.get("min_bid_price") or 0,
            fail_count=sold.get("fail_count") or 0,
            sale_date=sold.get("sale_date") or "",
            est_market_price=sold.get("est_market_price"),
            market_band_low=sold.get("market_band_low"),
            profit_low=sold.get("profit_low"),
            expected_profit=sold.get("expected_profit"),
            arb_score=sold.get("arb_score"),
            grade=sold.get("grade") or "낙찰 종결",
            # (2026-07-28) 재채점이 저장한 시세 출처·근거를 그대로 쓴다. 종전엔 0/''로
            # 박아 넣어, 확정 실거래 160건으로 추정한 물건도 '매칭 0건'으로 보였고
            # 동 폴백 참고치가 확정 시세와 구분되지 않았다.
            market_scope=sold.get("market_scope") or "",
            matched_trades=sold.get("matched_trades") or 0,
            confidence=sold.get("confidence") or 0.0,
            real_acquisition_cost=sold.get("min_bid_price") or 0,
            gap_rate=None, gap_score=0.0, rights_score=0.0, liquidity_score=0.0,
            # 네이버 단지 매핑을 붙여 상세의 '네이버 시세 ↗' 링크·KB 카드를 살린다.
            # ⚠ market_view(KB 폴백 재계산)는 태우지 않는다 — 낙찰 경로는 확정 실거래만
            # 시세로 인정하기로 했는데(store.SOLD_TRUSTED_SCOPES) KB 폴백이 그 결정을
            # 우회해 차익을 되살리면 안 된다. 표시용 페이로드만 첨부한다.
            naver=_sold_naver_row(sold),
        )]
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

    # (A3 2026-07-27) 상세의 독립 조회 5종(권리·사진·임차인·점유관계·건축물대장)을 병렬로.
    # 종전엔 순차 REST 왕복(콜당 ~400ms × 4~5)이 워밍에도 2~4초 — "클릭했는데 안 넘어간다"
    # 체감의 주범. 각 조회의 실패 폴백(개별 try + warning 로그)은 그대로 유지하고,
    # SQLite 경로는 스레드별 자체 커넥션을 열므로 병렬에도 안전하다.
    def _q_sqlite(loader):
        conn_ = store.connect(db_path)
        try:
            return loader(conn_)
        finally:
            conn_.close()

    def _load_rights_job():
        if db_path:
            return _q_sqlite(lambda c: store.load_rights(c, s.court, s.case_no, s.item_no))
        if store_rest.enabled():
            return store_rest.fetch_rights(s.court, s.case_no, s.item_no)
        return None

    def _load_photos_job():
        if db_path:
            return _q_sqlite(lambda c: store.load_photos(c, s.court, s.case_no, s.item_no))
        if store_rest.enabled():
            return store_rest.fetch_photos(s.court, s.case_no, s.item_no)
        return []

    def _load_tenants_job():
        if db_path:
            return _q_sqlite(lambda c: store.load_tenants(c, s.court, s.case_no, s.item_no))
        if store_rest.enabled():
            return store_rest.fetch_tenants(s.court, s.case_no, s.item_no)
        return []

    def _load_survey_job():
        if db_path:
            _sv_raw = _q_sqlite(
                lambda c: store.load_detail_raw(c, s.court, s.case_no, s.item_no, "curst"))
            if _sv_raw:
                from ..courtauction_detail import curst_possession  # noqa: PLC0415
                return curst_possession(_sv_raw)
            return None
        if store_rest.enabled():
            return store_rest.fetch_survey(s.court, s.case_no, s.item_no)
        return None

    def _load_building_job():
        if db_path:
            return _q_sqlite(lambda c: store.load_building(c, s.court, s.case_no, s.item_no))
        if store_rest.enabled() and hasattr(store_rest, "fetch_building"):
            return store_rest.fetch_building(s.court, s.case_no, s.item_no)
        return None

    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415
    _jobs = {"rights": _load_rights_job, "photos": _load_photos_job,
             "tenants": _load_tenants_job, "survey": _load_survey_job,
             "building": _load_building_job}
    _got: dict = {}
    with ThreadPoolExecutor(max_workers=len(_jobs)) as _ex:
        _futs = {k: _ex.submit(fn) for k, fn in _jobs.items()}
        for _k, _f in _futs.items():
            try:
                _got[_k] = _f.result()
            except Exception as e:  # noqa: BLE001 — 개별 조회 실패는 상세 페이지를 막지 않음
                logger.warning("%s 로드 실패(%s %s): %s", _k, s.court, s.case_no, e)
                _got[_k] = None

    rights_row = _got.get("rights")
    photos: list[str] = _got.get("photos") or []
    tenants: list[dict] = _got.get("tenants") or []
    _brow_prefetched = _got.get("building")

    # (2026-07-25) 현황조사서 '부동산의 점유관계' — 사용자 요구("최선순위만 보여주면
    # 내가 뭘 보고 판단하냐"). 크롤 시 보존한 curst 원본(listing_detail_raw)에서 집행관
    # 조사 원문(폐문부재/전입세대확인/기타)을 요지로 파싱해 명세서 아래 섹션으로 노출.
    # 원본 미보존(구크롤·REST 클라우드 경로)이면 None → 섹션 미표시(모름≠없음).
    survey = _got.get("survey")   # (A3) 위 병렬 배치에서 로드 — 실패 폴백 동일(None=섹션 미표시)

    # (2026-07-23) 재매각 이력 — 위 rights_row 를 그대로 쓰므로 추가 조회 0.
    # 권리 요지가 비어 배지가 안 만들어지는 물건도 기일 이력은 살아 있으므로 별도로 계산한다
    # (rights_row 를 None 으로 되돌리는 아래 가드보다 **먼저** 뽑아야 한다).
    from ..courtauction_detail import CaseRights as _CR  # noqa: PLC0415
    from ..courtauction_detail import resale_history  # noqa: PLC0415
    resale = None
    # (2026-07-31) 기일 이력을 **실제로 읽었는지** 를 따로 넘긴다. resale=None 은 두 가지
    # 뜻이 섞여 있다 — "이력을 봤는데 재매각이 아니다"(확정)와 "이력 자체가 없다"(모름).
    # 보증금 경고는 이 둘을 다르게 다뤄야 한다: 확정이면 그대로 믿고, 모름이면 유찰0회+저감
    # 추정식으로 보수적 경고를 남긴다(권리 크롤은 전체의 약 절반이라 모름이 흔하다).
    resale_known = False
    if rights_row:
        try:
            _sched = _CR.from_row(rights_row).schedule
            resale_known = bool(_sched)
            resale = resale_history(_sched)
        except Exception as e:  # noqa: BLE001 — 재매각 표시는 부가 정보, 페이지를 막지 않음
            logger.warning("재매각 이력 판정 실패(%s %s): %s", s.court, s.case_no, e)
            resale_known = False

    # (UX 감사 U-01, 2026-07-23) 입찰보증금 — 통상 최저가의 10%지만 **재매각·특별매각조건은
    # 20~30%** 이고 법원이 그 비율을 명세서 비고에 문장으로 준다(실측 609건). 종전엔 화면이 늘
    # 10%로 계산해 재매각 물건의 필요 현금을 **절반으로** 알려줬다 → 그대로 법정에 가면 입찰 무효.
    # ⚠️ 재매각과 같은 이유로 **가드보다 먼저** 계산한다 — 비율은 비고에 있는데, 자유기술란이
    # 비면 아래에서 rights_row 가 None 이 되어 비고까지 함께 사라진다.
    # 명시가 없으면 stated=False → 화면이 '통상 10% 가정'임을 밝힌다(모름을 확정으로 바꾸지 않음).
    from ..courtauction_rights import bid_deposit  # noqa: PLC0415
    _rr = rights_row or {}
    deposit_amount, deposit_rate, deposit_stated = bid_deposit(
        s.min_bid_price, _rr.get("remark") or "", _rr.get("lien_note") or "",
        _rr.get("surviving_rights") or "")

    badge = None
    priority = None
    if rights_row:
        import dataclasses  # noqa: PLC0415

        from ..courtauction_detail import (  # noqa: PLC0415
            CaseRights,
            ambiguous_moveins,
            analyze_priority,
            opposable_deposit,
            summarize,
            tenant_moveins,
        )
        _cr = CaseRights.from_row(rights_row)
        tmoveins = tenant_moveins(tenants)   # 임차인(소유자 전입 제외) 전입일 실데이터
        amoveins = ambiguous_moveins(tenants)  # 관계 미상 전입세대(대항력 여지 후보 — 소유자 단정 금지)
        # (서빙감사 2026-07-12 #13) 빈/부분 명세서는 판정 근거 0 — 배지·rights 둘 다 미표시로
        # 폴백해 '✓ 인수 없음/권리분석 반영됨'으로 오판하지 않는다(목록 가드와 정합).
        if _cr.is_empty:
            rights_row = None
        elif _cr.opposability_assessable or tmoveins or amoveins:
            # 판정 가능: 요지 자유텍스트가 있거나(구경로), 현황조사서 임차인 전입일 실데이터가 있거나,
            # (2026-07-23) 관계 미상 전입세대가 있어 대항력 '여지' 판정이 가능함.
            rights = _cr
            badge = summarize(_cr)
            # 대항력 근거(2026-07-13): 실전입일 있으면 항상 날짜비교, 없으면 부담물건에만(구동작).
            # (2026-07-23) 관계 미상 전입세대(amoveins)만 있어도 '여지' 판정을 위해 분석한다.
            if tmoveins or amoveins or not badge.is_clean:
                priority = analyze_priority(_cr, tenant_moveins=tmoveins,
                                            ambiguous_moveins=amoveins)
            # 현황조사서 전입일로 대항력 확정(전입 ≤ 말소기준)이면 opposable·인수액 보정.
            confirmed = priority is not None and priority.verdict == "confirmed_opposable"
            assumed = badge.assumed
            if confirmed:
                assumed = max(assumed, opposable_deposit(tenants, priority.senior_date))
            listing = dataclasses.replace(
                listing,
                special_rights=badge.special,
                tenant_opposable=badge.opposable or confirmed,
                assumed_amount=assumed,
                rights_verified=True,
            )
        else:
            # (2026-07-22) 자유기술란 전부 빈 요지 + 임차인표도 미크롤 = 대항력 판정근거 0 →
            # 배지·판정 미생성으로 '발견 안 됨' 초록 오표시를 막되(삼환 2022타경3289), 참고정보
            # (명세서 요지 말소기준·기일·감정요항)는 그대로 보여준다.
            rights = _cr
            rights_row = None
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
    from ..matcher import band_confident_basis  # noqa: PLC0415
    sample_gate_low = (s.est_market_price is not None
                       and s.market_sample_basis is not None
                       and s.market_sample_basis < band_confident_basis())
    # (T6) 호가 스텁 — 수동 입력 파일에 있으면 점으로 표시, 없으면 완전 무표시.
    from .. import asking as asking_mod  # noqa: PLC0415
    askings = asking_mod.load_asking_prices().get(case_no, [])
    ask_points = asking_mod.asking_points(askings, s.market_band_low, s.market_band_high)
    ask_overstated = asking_mod.band_overstated(askings, s.market_band_low)
    # 가격-시간 차트(세로/시간축 개편) — 개별 실거래(월별)·호가 시점·유찰 저감·롤링 밴드.
    # (구 pricemap 가로 스냅샷은 이 차트로 교체·제거됨 — 2026-07-13 정리)
    # 기일 이력(schedule)은 권리 요지에서, 개별 실거래는 s.market_comps에서.
    from .. import pricechart  # noqa: PLC0415
    chart = pricechart.build_timechart(
        s, s.market_comps, rights.schedule if rights else None, ask_points,
        assumed=(badge.assumed if badge else 0))
    # 현장 탭 인라인 지도용 좌표(KATEC→WGS84 캐시). 없으면 None → 템플릿이 주소검색 폴백.
    from .. import coords  # noqa: PLC0415
    coord = None
    try:
        pt = coords.lookup(coords.load_coord_cache(), s.uid, s.case_no, court=s.court)
        if pt:
            coord = [pt[0], pt[1]]  # [lat, lon]
    except Exception as e:  # noqa: BLE001 — 좌표 실패는 지도 폴백, 페이지는 정상
        logger.warning("좌표 조회 실패(%s %s): %s", s.court, s.case_no, e)
    # (E 배선 2026-07-20) 건축물대장 요약 — 준공연도(노후도)·위반건축물 리스크.
    # 키 미설정·실패는 None → 카드 미표시(페이지 정상). 건물형 유형만 조회
    # (감사 2026-07-20: 토지·임야 상세에서 무의미한 외부 2콜 차단).
    from .. import building_info  # noqa: PLC0415
    _BLDG_TYPES = ("아파트", "오피스텔", "연립", "다세대", "단독", "다가구", "근린주택", "빌라")
    bldg = None
    if any(t in (s.property_type or "") for t in _BLDG_TYPES):
        # (2026-07-22) 배치 precompute 캐시(listing_building) 우선 — 페이지뷰마다 VWorld+대장
        # 라이브 3초 왕복을 없애고 쿼터 소진에도 견딘다. 캐시 미스/미ok면 라이브 폴백(그리고
        # deploy/enrich_building 배치가 다음 회차에 채운다).
        brow = _brow_prefetched   # (A3) 위 병렬 배치에서 로드 — 실패=None(라이브 폴백 동일)
        if brow and brow.get("status") == "ok":
            bldg = brow
        else:
            bldg = building_info.get_building_summary(s.address)
    # (2026-07-23) 입찰가 시뮬레이터 초기값 — 서버에서 한 벌 계산해 넘긴다(JS 없어도 값이 보이게).
    # 이후 슬라이더 조작은 /api/bidsim 이 같은 _sim_payload 로 계산 — 두 경로가 갈리지 않는다.
    # 매도가 기본 = 검증 하한가(보수) → 추정시세 → 없으면 0(사용자가 직접 입력).
    sim_input = bidsim.SimInput(
        # (C4) 낙찰 종결 물건은 실낙찰가로 고정 시작 — "그 가격에 샀다면"의 손익.
        # 미공개(sold_price None)면 최저가 그대로(값 지어내기 금지).
        bid_price=(sold["sold_price"] if sold and sold.get("sold_price")
                   else s.min_bid_price),
        property_type=s.property_type,
        area_m2=s.area_m2 or 0.0,
        sell_price=s.market_band_low or s.est_market_price or 0,
        assumed_amount=(badge.assumed if badge else 0),
    )
    html = render_template(
        "detail.html", s=s, listing=listing, chart=chart, rights=rights, badge=badge,
        survey=survey, tenants=tenants, sold=sold,
        deposit_amount=deposit_amount, deposit_rate=deposit_rate,
        deposit_stated=deposit_stated,
        sim=web._sim_payload(sim_input), sim_in=sim_input, bidsim_cfg=bidsim, resale=resale,
        resale_known=resale_known,
        coord=coord, days_until=query.days_until,
        bldg=bldg, vworld_key=os.environ.get("VWORLD_API_KEY", "").strip(),
        priority=priority,
        # 상세 본문은 원 단위 콤마 표기(사용자 결정 2026-07-24) — 홈/리스트는 억 유지.
        # won_fine(계산서)도 콤마로: 컨텍스트 인자가 jinja 전역보다 우선한다.
        won=report.won_num, won_fine=report.won_num, pct=report.pct,
        gated=gated, gate_reason=", ".join(gate_reasons),
        tax_parts=tax_parts, tax_label=tax.PROFILE.label(),
        watching=watchlist.is_watched(
            watchlist.load_watchlist(watchlist.watchlist_path()), s),
        data_source=getattr(g, "data_source", "n/a"),
        sample_gate_low=sample_gate_low, band_confident=band_confident_basis(),
        ask_points=ask_points, ask_overstated=ask_overstated, photos=photos,
    )
    # (B1 2026-07-27) 목록의 터치-프리페치가 탭 시점에 재사용되도록 짧은 private 캐시.
    # 데이터는 일 1회 새로고침이라 45초 스테일은 무해. 관심 토글 복귀는 _safe_back이
    # 캐시버스터(_r)를 붙여 최신 별 상태를 강제한다(짝 계약 — 함께 수정할 것).
    resp = current_app.make_response(html)
    resp.headers["Cache-Control"] = "private, max-age=45"
    return resp
