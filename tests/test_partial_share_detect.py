"""부분 지분 매각 검출 — maejibun(매각지분) 필드 기반.

사고(2026-07-24, 사용자 발견): 죽전자이2차 2025타경55336 — 1/2 지분 매각(감정 3.55억 =
온전가의 절반)인데 special_rights 검출이 **비고(mulBigo)만** 읽어 지분을 놓쳤고, 같은 단지
온전 세대 실거래(6.2~7.25억)로 시세가 매겨져 **허구 차익 3.69억 · 점수 95.5 '차익 유력'**
으로 서빙됐다. 지분 표기는 법원 검색결과의 **maejibun** 필드에 있었다("갑구 2번 2분의 1
[성명] 지분 전부"). 미탐 클래스 실측 5건(추천 계열 3건, 허구 차익 합 7.5억).

경계(실데이터로 확정 — 오탐이 온전 물건을 오배제하면 그것대로 손실):
  · "N분의M … 지분" / "N/M … 지분"  → 부분 지분 (죽전·동보·백석)
  · "공유자 전원의 지분 전부"        → **온전 매각** — 게이트 금지 (천안두정역)
  · "대지권 비율: 500분의 21.7849"   → 집합건물 정상 표기('지분' 결합 없음) — 게이트 금지 (동래)
  · 비고의 "공유자 우선매수신고" 문구 → 공유자 존재 = 부분 지분의 강한 보조 신호
"""
from src.courtauction_fields import CourtAuctionRecord, is_partial_share, to_auction_listing

# ── 판정 함수 — 실측 케이스 그대로 ──

def test_jukjeon_partial_share_detected():
    """죽전자이2차 실표기 — 이 미탐이 허구 차익 3.69억을 만들었다."""
    assert is_partial_share("갑구 2번 2분의 1 [성명] 지분 전부", "") is True


def test_dongbo_no_gapgu_prefix():
    """갑구 번호 없는 변형(동보아파트)."""
    assert is_partial_share("2분의 1 [성명] 지분 전부", "") is True


def test_slash_fraction_variant():
    """분수 슬래시 표기(백석우림보보카운티): '갑구 5번  1/2  [성명] 지분 전부'."""
    assert is_partial_share("갑구 5번  1/2  [성명] 지분 전부", "") is True


def test_all_owners_share_is_whole_sale():
    """'공유자 전원의 지분 전부'(천안두정역) = 100% 온전 매각 — 게이트하면 오배제."""
    assert is_partial_share("공유자 전원의 지분 전부", "") is False


def test_land_right_ratio_is_not_share():
    """대지권 비율(동래에코하임) — 모든 집합건물의 정상 표기. 'N분의M'만 보고 잡으면 안 된다."""
    mj = ("대지권의 표시\n\t토지의 표시 : 부산광역시 동래구 안락동 308  대 500㎡\n"
          "\t대지권 종류 : 소유권대지권\n\t대지권 비율 : 500분의 21.7849")
    assert is_partial_share(mj, "") is False


def test_coowner_preemption_note_is_share_signal():
    """maejibun 이 비어도 비고의 '공유자 우선매수신고' = 공유자 존재 = 부분 지분."""
    bigo = ("공유자의 우선매수신고는 1회에 한하여 행사할 수 있음(공유자가 우선매수권 신고 후 "
            "매각기일까지 매수보증금을 미납하여 실효되는 경우에는 …)")
    assert is_partial_share("", bigo) is True


def test_empty_inputs_are_not_share():
    assert is_partial_share("", "") is False
    assert is_partial_share(None, None) is False


# ── 2026-08-25 미러 차단 실사고(2025타경56099 푸르뫼금강에스쁘아) — 미탐 2변형 ──

def test_reversed_order_share_fraction():
    """어순 반대 '지분 2분의 1 전부' — 기존 정규식은 분수→지분 순서만 봐서 미탐.
    이 한 건이 게이트 FAIL → 8/17부터 클라우드 미러 전체 차단(프로덕션 7일 stale)."""
    assert is_partial_share("갑구10번 공유자 [성명] 지분 2분의 1 전부", "") is True


def test_reversed_order_coowner_share_of():
    """'공유자지분 중 100분의 15' 계열(전수 실측 4,463건 전부 진성) — 어순반대 포괄."""
    assert is_partial_share("공유자지분 중 100분의 15 [성명] 지분", "") is True


def test_note_share_sale_keyword():
    """비고의 명시적 '지분매각'(실측 4,958건 전부 진성) — 게이트 백업 키워드와 채점
    검출이 갈라져 있던 불일치 봉합. 게이트만 알고 검출은 모르면 미러가 볼모가 된다."""
    assert is_partial_share("", "지분매각, 공유자 [성명]신고 제한있음(우선매수신청을 한 …)") is True


def test_all_owners_still_whole_even_with_fraction_absent():
    """'전원' 우선 규칙 유지 — maejibun 이 온전을 명시하면 비고 '지분매각'(다물건 타목록
    지칭, 전수 모순 1건 실측)보다 신뢰한다."""
    assert is_partial_share("공유자 전원의 지분 전부", "- 일괄매각, 목록2,3 지분매각") is False


def test_decimal_fraction_with_share_word():
    """소수 비율 + 지분 결합('3분의 1.5 지분')도 부분 지분."""
    assert is_partial_share("3분의 1.5 지분", "") is True


# ── 통합 — to_auction_listing 이 special_rights 에 '지분'을 심는가 ──

def _rec(maejibun: str, bigo: str) -> CourtAuctionRecord:
    raw = {"maejibun": maejibun, "mulBigo": bigo}
    return CourtAuctionRecord(
        doc_id="D1", case_no="2025타경55336", court="수원지방법원", dept="경매1계",
        property_type="아파트", usage_name="아파트",
        address="경기도 용인시 기흥구 보정동 1291", sido="경기도", sigu="용인시 기흥구",
        dong="보정동", lawd_cd="41463", jibun="1291", building_name="죽전자이2차",
        building_detail="1동 12층1210호", area_m2=84.89,
        appraisal_price=355_000_000, min_bid_price=248_500_000, fail_count=1,
        sale_date="2026-07-24", sale_place="", bid_open_date="", bid_close_date="",
        view_count=0, interest_count=0, note=bigo, tel="", x_proj="", y_proj="",
        raw=raw, item_no="1",
    )


def test_to_listing_flags_share_from_maejibun():
    """maejibun 의 부분 지분이 special_rights '지분'으로 — matcher 가 온전가 시세를 거부하게.

    이 라벨이 있으면 estimate_market 이 SCOPE_SHARE_SALE 을 반환해(기존 게이트)
    시세추정불가가 된다 — 허구 차익·점수의 뿌리를 끊는다.
    """
    lst = to_auction_listing(_rec("갑구 2번 2분의 1 [성명] 지분 전부",
                                  "공유자의 우선매수신고는 1회에 한하여 행사할 수 있음"))
    assert "지분" in lst.special_rights


def test_to_listing_whole_sale_not_flagged():
    """'전원 지분'(온전)·대지권 비율은 플래그되지 않는다 — 정상 물건 오배제 금지."""
    whole = to_auction_listing(_rec("공유자 전원의 지분 전부", "일괄매각"))
    assert "지분" not in whole.special_rights
    land = to_auction_listing(_rec("대지권 비율 : 500분의 21.7849", "일괄매각"))
    assert "지분" not in land.special_rights


def test_share_flag_kills_market_estimate():
    """E2E: 지분 플래그 → estimate_market 이 시세를 거부(SCOPE_SHARE_SALE)."""
    from src.matcher import SCOPE_SHARE_SALE, estimate_market
    lst = to_auction_listing(_rec("갑구 2번 2분의 1 [성명] 지분 전부", ""))
    est = estimate_market(lst, trades=[])
    assert est.scope == SCOPE_SHARE_SALE
    assert est.est is None


def test_serving_kb_fallback_respects_share_sale():
    """서빙 폴백(KB→호가→전세)이 지분 물건을 온전가로 되살리지 않는다 — **두 번째 사고 경로**.

    실사고(2026-07-24, 프로덕션 검산에서 발견): 죽전자이의 est 를 지워 '시세추정불가'로
    교정했더니, market_view 의 KB 폴백이 **온전 세대 KB 시세 6.6억**으로 차익·등급을
    재계산해 되살렸다(신뢰 0.75 · 매칭 0건). 채점층이 '온전가 비교 무의미'로 정한 물건은
    서빙층의 어떤 시세 사다리도 타면 안 된다 — 표시용 페이로드만 첨부한다.
    """
    from src.matcher import SCOPE_SHARE_SALE
    from src.models import ScoredListing
    from src.score import market_view
    s = ScoredListing(
        case_no="2025타경55336", apt_name="죽전자이2차", address="경기도 용인시",
        property_type="아파트", area_m2=84.89, appraisal_price=355_000_000,
        min_bid_price=248_500_000, fail_count=1, sale_date="2026-07-24",
        est_market_price=None, matched_trades=0, confidence=0.6,
        real_acquisition_cost=251_233_500, expected_profit=None, gap_rate=None,
        gap_score=0.0, rights_score=85.0, liquidity_score=100.0, arb_score=None,
        grade="시세추정불가", court="수원지방법원", item_no="1", rights_verified=True,
        market_scope=SCOPE_SHARE_SALE,
    )
    naver = {"status": "matched_kb", "kb_low": 630_000_000, "kb_avg": 660_000_000,
             "kb_high": 710_000_000, "complex_no": "17540"}
    out = market_view(s, naver)
    assert out.est_market_price is None          # KB 로 시세가 되살아나면 안 된다
    assert out.arb_score is None and out.profit_low is None
    assert out.grade == "시세추정불가"
    assert out.naver == naver                    # 참고 표시용 페이로드는 유지(정보 은폐 아님)
