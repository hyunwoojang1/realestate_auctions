"""데이터 품질 게이트 — 크롤·적재 결과가 '조용히 틀린' 채 서빙되는 것을 차단.

배경(2026-07-10 실사고): 법원 용도명 '아파트'로 등록된 나대지가 아파트 실거래와 오매칭돼
4.45억 허상 차익이 랭킹 상위에 노출 — 사용자 눈으로 발견됨. 같은 부류의 오류를 사람이 아니라
시스템이 매 적재마다 잡도록, 서로 독립적인 불변식 검사를 배치로 돌린다.

운영 규칙: **모든 게이트 PASS 여야 클라우드 미러(서빙 반영) 진행** — run.py / crawl_rights.py 가
미러 직전에 호출한다. 게이트는 서로 다른 각도(물리 신호·가격 괴리·조인 무결성·문구 판정)로
같은 데이터를 보므로, 하나가 놓쳐도 다른 게이트가 걸리게 설계한다.

각 게이트는 GateResult(ok, count, samples)를 반환하고 절대 예외로 죽지 않는다
(게이트 자체의 버그가 적재를 막는 것도, 조용히 통과시키는 것도 안 되므로 실패 시 ok=False + error).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date

# 감정가 대비 추정시세 배율 상한 — 감정평가사가 이 이상을 놓칠 확률은 사실상 0이므로
# 초과분은 유형 오분류·지분·대지권 문제의 시그널로 본다(침산동 사고는 3.5배였다).
EST_VS_APPRAISAL_MAX = 2.5
# 매각기일이 이만큼 지난 물건이 남아 있으면 새로고침 누락 신호.
STALE_SALE_GRACE_DAYS = 2

_HOUSING = ("아파트", "오피스텔", "다세대", "연립", "빌라")


@dataclass
class GateResult:
    name: str
    ok: bool
    count: int = 0                      # 위반 건수
    samples: list = field(default_factory=list)   # 위반 사례(최대 5)
    detail: str = ""
    error: str = ""                     # 게이트 자체 실행 실패 시

    def line(self) -> str:
        mark = "PASS" if self.ok else "FAIL"
        extra = f" — {self.detail}" if self.detail else ""
        err = f" [게이트 오류: {self.error}]" if self.error else ""
        return f"[{mark}] {self.name}: 위반 {self.count}건{extra}{err}"


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


# ---------------------------------------------------------------------------
# 게이트들 — 각각 독립 관점. 추가 시 GATES 리스트에 등록.
# ---------------------------------------------------------------------------

def gate_type_physical(conn) -> GateResult:
    """주거유형인데 건물 표식 전무 + 지목 존재 = 토지 둔갑(침산동 사고 재발 감지).

    일괄매각 물건은 목적물(mokmulSer)별 raw 행이 여러 개다(건물행+토지행) — 그룹 내
    **모든** 행이 건물표식 없음일 때만 위반으로 본다(건물행이 하나라도 있으면 정상 물건).
    """
    groups: dict[tuple, list[dict]] = {}
    for r in _rows(conn, """
        select s.court, s.case_no, s.item_no, r.raw_json
        from scored_listings s join raw_listings r
          on r.court=s.court and r.case_no=s.case_no and r.item_no=s.item_no
        where s.property_type in ({})
    """.format(",".join("?" * len(_HOUSING))), _HOUSING):
        groups.setdefault((r["court"], r["case_no"], r["item_no"]), []).append(
            json.loads(r["raw_json"]))
    bad = []
    for (court, cn, _it), ds in groups.items():
        any_building = any(
            any(d.get(k) for k in ("buldNm", "buldList", "pjbBuldList")) for d in ds)
        any_jimok = any(d.get("jimokList") for d in ds)
        if not any_building and any_jimok:
            bad.append(f"{court[:6]} {cn}")
    return GateResult("유형-물리 정합(주거인데 건물표식無+지목有)", ok=not bad,
                      count=len(bad), samples=bad[:5])


# 세부용도코드(sclsUtilCd) ↔ 저장 유형 모순 검사용 — 주거유형이 아니어야 할 코드 프리픽스.
_SCLS_NONHOUSING_PREFIX = ("101", "211", "212", "221")
_SCLS_HOUSING = {"20104": "아파트", "20110": "오피스텔", "20105": "연립", "20106": "다세대"}


def _naver_verified_keys(conn) -> set:
    """네이버 complexNo(건물 고유번호)로 고/중신뢰 매칭돼 실거래가 주입된 물건 키 집합.

    (2026-07-19) 이 물건들의 시세는 유형코드가 아니라 **건물 ID + 전용면적**으로 검증된 같은
    단지·같은 평형 실거래에서 나온다. courtauction 세부용도코드(scls)가 변종/오분류여도 comps는
    엉뚱한 유형이 아니므로, scls 정합 게이트의 전제(유형 불일치=잘못된 comps)가 성립하지 않는다.
    테이블이 없으면(구 DB·dryrun) 빈 집합 — 게이트는 종전대로 전량 검사(안전측).
    """
    try:
        rows = conn.execute(
            "SELECT DISTINCT np.court, np.case_no, np.item_no FROM naver_prices np "
            "JOIN naver_real_trades nrt ON nrt.complex_no=np.complex_no AND nrt.area_no=np.area_no "
            "WHERE np.complex_no IS NOT NULL AND np.complex_no != '' "
            "  AND np.match_conf IN ('고신뢰','중신뢰') AND nrt.deleted=0")
    except sqlite3.DatabaseError:
        return set()
    return {(r[0], r[1], str(r[2] or "")) for r in rows}


def gate_scls_consistency(conn) -> GateResult:
    """세부용도코드 교차검증(감사 2026-07-10 CRITICAL) — 시세가 매겨진 물건의 저장 유형이
    코드 실체와 모순되면 위반: ①주거유형인데 코드=비주거(근생/공장/토지) ②아파트인데
    코드=오피스텔(또는 그 역) — 엉뚱한 유형의 실거래 comps 로 시세가 산정된 신호.

    (2026-07-19) 네이버 complexNo 고/중신뢰 매칭 물건은 제외 — 시세가 건물 ID로 검증된
    같은 단지 실거래에서 나와 scls 변종(예: 아파트 코드 10108)이 오탐을 낸다(실측: 덕원아파트
    고신뢰·실거래 137건이 접두 '101' 규칙에 걸려 전체 적재를 막았다)."""
    bad = []
    verified = _naver_verified_keys(conn)
    for r in _rows(conn, """
        select s.court, s.case_no, s.item_no, s.property_type, s.market_scope, r.raw_json
        from scored_listings s join raw_listings r
          on r.court=s.court and r.case_no=s.case_no and r.item_no=s.item_no
        where s.est_market_price is not null and s.property_type in ({})
    """.format(",".join("?" * len(_HOUSING))), _HOUSING):
        # (감사 2026-07-20 MEDIUM1) 면제는 '네이버 매칭 존재'가 아니라 '네이버 실거래가 실제 주입돼
        # 같은단지·같은평형(same_complex_same_area) 시세가 된' 물건만. 네이버 표본이 게이트 미달이라
        # 국토부 이름매칭으로 폴백한 est는 유형코드 검증을 계속 받아야 한다(출처-기반 면제).
        if (r["court"], r["case_no"], str(r["item_no"] or "")) in verified \
                and r["market_scope"] == "same_complex_same_area":
            continue   # 건물 ID 검증 comps — scls 유형코드 무관
        scls = str(json.loads(r["raw_json"]).get("sclsUtilCd") or "")
        if not scls:
            continue
        if any(scls.startswith(p) for p in _SCLS_NONHOUSING_PREFIX):
            bad.append(f"{r['case_no']}({r['property_type']}⟂코드{scls})")
        elif scls in _SCLS_HOUSING and _SCLS_HOUSING[scls] != r["property_type"] \
                and {r["property_type"], _SCLS_HOUSING[scls]} == {"아파트", "오피스텔"}:
            # 아파트↔오피스텔 교차만 위반(연립/다세대는 시세추정 미지원이라 무해)
            bad.append(f"{r['case_no']}({r['property_type']}⟂코드{_SCLS_HOUSING[scls]})")
    return GateResult("세부용도코드-유형 정합(시세 매칭 물건)", ok=not bad,
                      count=len(bad), samples=bad[:5])


def gate_extreme_est(conn) -> GateResult:
    """추정시세가 감정가의 EST_VS_APPRAISAL_MAX 배 초과 — 오분류/지분/대지권 시그널."""
    bad = _rows(conn, """
        select court, case_no, est_market_price, appraisal_price
        from scored_listings
        where appraisal_price > 0 and est_market_price is not null
          and est_market_price > appraisal_price * ?
    """, (EST_VS_APPRAISAL_MAX,))
    return GateResult(f"가격 괴리(시세>감정가×{EST_VS_APPRAISAL_MAX})", ok=not bad,
                      count=len(bad),
                      samples=[f"{b['case_no']} 감정{b['appraisal_price']/1e8:.1f}억"
                               f"→시세{b['est_market_price']/1e8:.1f}억" for b in bad[:5]])


def gate_join_integrity(conn) -> GateResult:
    """scored ↔ raw 조인 고아 — 수집·적재 사이 유실/키 불일치 감지(법원 포함 복합키)."""
    orphan_scored = _rows(conn, """
        select s.court, s.case_no from scored_listings s
        left join raw_listings r
          on r.court=s.court and r.case_no=s.case_no and r.item_no=s.item_no
        where r.case_no is null and s.court != ''
    """)
    return GateResult("조인 무결성(scored→raw 고아)", ok=not orphan_scored,
                      count=len(orphan_scored),
                      samples=[f"{o['court'][:6]} {o['case_no']}" for o in orphan_scored[:5]])


def gate_share_sale(conn) -> GateResult:
    """지분/건물만/대지권미등기 신호가 원본에 있는데 온전 물건처럼 시세가 매겨진 경우.

    (2026-07-24 강화 — 죽전자이2차 실사고) 종전엔 비고(mulBigo)만 봤는데 지분 표기는
    **maejibun 필드**에 온다("갑구 2번 2분의 1 [성명] 지분 전부") — 이 게이트도 검출기와
    똑같은 사각지대라 1/2 지분이 온전가 시세·차익 3.69억으로 서빙되는 것을 통과시켰다.
    검출기와 **같은 판정 함수**(courtauction_fields.is_partial_share)를 공유한다 —
    채점 검출과 적재 게이트가 다른 규칙을 쓰면 한쪽 사각이 다른 쪽에서 재현된다.
    비고 키워드 검사(건물만·대지권 계열 + 명시적 '지분')는 백업으로 유지.
    """
    from .courtauction_fields import is_partial_share  # noqa: PLC0415 — 순환 회피
    bad = []
    for r in _rows(conn, """
        select s.court, s.case_no, s.item_no, r.raw_json
        from scored_listings s join raw_listings r
          on r.court=s.court and r.case_no=s.case_no and r.item_no=s.item_no
        where s.est_market_price is not null
    """):
        d = json.loads(r["raw_json"])
        bigo = (d.get("mulBigo") or "") + (d.get("alias") or "")
        if is_partial_share(d.get("maejibun"), d.get("mulBigo")):
            bad.append(f"{r['case_no']}(지분:{(d.get('maejibun') or bigo)[:20]})")
        elif any(k in bigo for k in ("지분", "건물만", "대지권없", "대지권 없", "대지권미등기",
                                     "대지권 미등기")):   # 띄어쓰기 변형(번영로 실사고 2026-07-24)
            bad.append(f"{r['case_no']}({bigo[:20]})")
    return GateResult("지분·건물만·대지권 신호인데 시세 매칭됨", ok=not bad,
                      count=len(bad), samples=bad[:5])


def gate_stale_sale(conn) -> GateResult:
    """매각기일이 지난 물건이 서빙 DB에 남음 — 전량 새로고침 누락 신호."""
    today = date.today().isoformat()
    bad = _rows(conn, """
        select case_no, sale_date from scored_listings
        where sale_date != '' and sale_date < date(?, ?)
    """, (today, f"-{STALE_SALE_GRACE_DAYS} days"))
    return GateResult(f"만료 매물(매각기일+{STALE_SALE_GRACE_DAYS}일 경과)", ok=not bad,
                      count=len(bad),
                      samples=[f"{b['case_no']}({b['sale_date']})" for b in bad[:5]])


def gate_pk_sanity(conn) -> GateResult:
    """빈 식별자·0원 가격 등 저장 불변식."""
    bad = _rows(conn, """
        select case_no from scored_listings
        where case_no = '' or min_bid_price <= 0
    """)
    return GateResult("PK·가격 sanity(빈 사건번호/0원 최저가)", ok=not bad, count=len(bad),
                      samples=[b["case_no"] or "(빈값)" for b in bad[:5]])


def gate_rights_json(conn) -> GateResult:
    """listing_rights.schedule JSON 파싱 가능 + 키 무결성(빈 case_no 금지)."""
    bad = []
    try:
        for r in _rows(conn, "select court, case_no, item_no, schedule from listing_rights"):
            if not r["case_no"]:
                bad.append("(빈 case_no)")
                continue
            try:
                json.loads(r["schedule"] or "[]")
            except json.JSONDecodeError:
                bad.append(f"{r['case_no']} schedule 파싱불가")
    except sqlite3.OperationalError:
        return GateResult("권리 요지 무결성", ok=True, detail="listing_rights 없음(스킵)")
    return GateResult("권리 요지 무결성(JSON·키)", ok=not bad, count=len(bad), samples=bad[:5])


def gate_band_order(conn) -> GateResult:
    """밴드 하한 ≤ 기준 불변식 + 보수차익 부호 정합(profit_low ≤ profit_high)."""
    bad = _rows(conn, """
        select case_no from scored_listings
        where (market_band_low is not null and market_band_high is not null
               and market_band_low > market_band_high)
           or (profit_low is not null and profit_high is not null
               and profit_low > profit_high)
    """)
    return GateResult("밴드·차익 순서 불변식", ok=not bad, count=len(bad),
                      samples=[b["case_no"] for b in bad[:5]])


GATES = [
    gate_type_physical,
    gate_scls_consistency,
    gate_extreme_est,
    gate_join_integrity,
    gate_share_sale,
    gate_stale_sale,
    gate_pk_sanity,
    gate_rights_json,
    gate_band_order,
]


def run_gates(conn: sqlite3.Connection, gates=None) -> list[GateResult]:
    """전 게이트 실행 — 개별 게이트 예외는 그 게이트 FAIL로 기록(조용한 통과 금지)."""
    results = []
    for g in (gates or GATES):
        try:
            results.append(g(conn))
        except Exception as e:  # noqa: BLE001 — 게이트 버그도 FAIL로 드러낸다
            results.append(GateResult(g.__name__, ok=False, error=str(e)))
    return results


def all_pass(results: list[GateResult]) -> bool:
    return all(r.ok for r in results)


def report(results: list[GateResult]) -> str:
    lines = [r.line() for r in results]
    verdict = "✅ 전 게이트 PASS — 서빙 반영 가능" if all_pass(results) \
        else "⛔ 게이트 FAIL — 클라우드 미러 차단(원인 수정 후 재시도)"
    return "\n".join(lines + [verdict])
