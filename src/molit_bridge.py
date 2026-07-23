"""국토부 병렬 하이브리드 브리지(감사 2026-07-19 H5) — 네이버 확정쌍의 최근 창을 국토부로 보충.

문제: 네이버 실거래 크롤은 안티밴 때문에 절대 순차라 쌍 수에 시간이 선형 비례(확장 상한).
통찰: 네이버 실거래의 원천이 곧 국토부다 — 확정쌍의 과거 이력(월·가격·층 정확일치)을
지문(fingerprint)으로 쓰면 이름 퍼지매칭 없이 국토부 aptNm 그룹 ↔ 네이버 complexNo 대응을
확정할 수 있다. 대응이 확정된 그룹에서 '네이버가 아직 못 본 최근 거래'만 메모리 보충한다.

안전 설계(전부 보수):
- 지문 MIN_MATCHES건 이상 정확일치 + 창내 네이버 행 MIN_COVERAGE 이상 매칭 + 압도적
  유일 승자(2위의 2배 이상)일 때만 대응 인정. 미달이면 보충 0건(종전 동작과 동일).
- 주입은 네이버 최신월 이후 & 최근 TOPUP_MONTHS개월 결측분만, 물건당 TOPUP_CAP건 상한.
- 해제거래(is_cancelled) 제외. 저장소(naver_store)에는 쓰지 않는다(메모리 전용, 채점 1회용).
- AUCTION_MOLIT_BRIDGE=0 으로 완전 비활성.
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict

from .models import AuctionListing, Trade

logger = logging.getLogger(__name__)

MIN_MATCHES = 3      # 지문 정확일치 최소 건수
MIN_COVERAGE = 0.6   # 국토부 풀 창 안의 네이버 행 중 매칭돼야 하는 비율
WINNER_MARGIN = 2.0  # 1위 매칭수가 2위의 몇 배 이상이어야 유일 승자로 인정하나
TOPUP_CAP = 20       # 물건당 보충 상한
_AREA_GROUP_DECIMALS = 1   # 그룹 면적 반올림 자리수(같은 평형 소수 흔들림 흡수)


def enabled() -> bool:
    return os.environ.get("AUCTION_MOLIT_BRIDGE", "1") != "0"


def _ym_int(ym: str) -> int | None:
    if not ym or len(ym) < 6:
        return None
    try:
        return int(ym[:4]) * 12 + int(ym[4:6])
    except ValueError:
        return None


def _naver_triples(rows: list) -> set[tuple[str, int, int]]:
    """네이버 실거래 행 → {(YYYYMM, price, floor)} 지문 집합."""
    out = set()
    for r in rows:
        get = r.get if isinstance(r, dict) else r.__getitem__
        ymd = str(get("trade_ymd") or "")
        try:
            price = int(get("price") or 0)
        except (TypeError, ValueError):
            continue
        try:
            floor = int(get("floor") or 0)
        except (TypeError, ValueError):
            floor = 0
        if len(ymd) >= 6 and price > 0:
            out.add((ymd[:6], price, floor))
    return out


class BridgeIndex:
    """국토부 trade_pool → lawd_cd별 (kind, apt_name, dong, area_r) 그룹 인덱스(채점 1회분).

    (감사 2026-07-20 HIGH) 그룹키에 **동(洞) 포함** — 같은 시군구·같은 이름 단지(압구정 현대 vs
    삼성동 현대류)가 한 그룹으로 병합돼 타단지 거래가 '확정 같은단지'로 주입되는 것을 차단.
    동이 다르면 별도 그룹 = 경쟁자로 남아 WINNER_MARGIN 게이트가 실효한다(이름매칭 경로의
    dong 제약과 동일한 방어를 브리지에도 적용).
    """

    def __init__(self, trade_pool: list[Trade]):
        self.groups: dict[str, dict[tuple, list[Trade]]] = defaultdict(lambda: defaultdict(list))
        yms = []
        for t in trade_pool:
            if t.is_cancelled or t.price <= 0:
                continue
            m = _ym_int(t.deal_ym)
            if m is None:
                continue
            yms.append(m)
            key = (t.kind, t.apt_name.strip(), t.dong.strip(),
                   round(t.area_m2, _AREA_GROUP_DECIMALS))
            self.groups[t.lawd_cd][key].append(t)
        self.pool_min_ym = min(yms) if yms else None
        self.pool_max_ym = max(yms) if yms else None

    def topup(self, listing: AuctionListing, naver_rows: list) -> list[dict]:
        """지문 대응이 확정되면 네이버 결측 최근 거래를 naver_rows 형태 dict로 반환. 아니면 []."""
        if self.pool_min_ym is None or not listing.lawd_cd:
            return []
        from .matcher import expected_kind  # noqa: PLC0415 — 순환 import 회피

        kind = expected_kind(listing.property_type)
        triples = _naver_triples(naver_rows)
        if not triples:
            return []
        naver_max_ym = max(_ym_int(ym) or 0 for ym, _, _ in triples)
        in_window = {t for t in triples if (_ym_int(t[0]) or 0) >= self.pool_min_ym}
        if len(in_window) < MIN_MATCHES:
            return []   # 풀 창과 겹치는 네이버 이력이 너무 적음 — 지문 불가

        # 그룹별 지문 매칭 수 집계 → 압도적 유일 승자만 채택.
        scoredg: list[tuple[int, tuple, list[Trade]]] = []
        for key, trs in self.groups.get(listing.lawd_cd, {}).items():
            if key[0] != kind:
                continue
            gt = {(t.deal_ym, t.price, t.floor) for t in trs}
            n = len(gt & in_window)
            if n:
                scoredg.append((n, key, trs))
        if not scoredg:
            return []
        scoredg.sort(key=lambda x: -x[0])
        best_n, best_key, best_trs = scoredg[0]
        second_n = scoredg[1][0] if len(scoredg) > 1 else 0
        if best_n < MIN_MATCHES:
            return []
        if best_n / len(in_window) < MIN_COVERAGE:
            return []
        if second_n and best_n < WINNER_MARGIN * second_n:
            logger.info("브리지 모호(%s): 1위 %d건 vs 2위 %d건 — 보충 생략",
                        listing.case_no, best_n, second_n)
            return []

        # 보충 대상: 네이버 최신월 이후(동월 포함 — 지연 신고분), 단 **열린 달(최신 2개월)만** —
        # (감사 2026-07-20 MEDIUM) 닫힌 달은 molit_cache가 영구 캐시라 뒤늦은 해제신고(취소)가
        # 풀에 반영되지 않는다. 열린 달은 매 실행 재수집이라 취소 상태가 최신이다.
        # 지문에 없는 거래만(중복 방지). 해제는 인덱스 단계에서 이미 제외.
        cutoff = max(naver_max_ym, (self.pool_max_ym or 0) - 1)
        # (감사 2026-07-20 LOW) 층 결측이 양쪽에서 0으로 붕괴돼 지문이 어긋나는 비대칭 중복 방지 —
        # 어느 한쪽 층이 0이면 (월, 가격)만으로도 동일 거래로 간주해 주입하지 않는다.
        pairs = {(ym, p) for ym, p, _fl in triples}
        zero_pairs = {(ym, p) for ym, p, fl in triples if fl == 0}
        fresh = [t for t in best_trs
                 if (_ym_int(t.deal_ym) or 0) >= cutoff
                 and (t.deal_ym, t.price, t.floor) not in triples
                 and (t.deal_ym, t.price) not in zero_pairs
                 and not (t.floor == 0 and (t.deal_ym, t.price) in pairs)]
        fresh.sort(key=lambda t: _ym_int(t.deal_ym) or 0, reverse=True)
        out = [{"trade_ymd": t.deal_ym + "01", "price": t.price, "floor": t.floor,
                "exclusive_area": t.area_m2, "src": "molit_bridge"}
               for t in fresh[:TOPUP_CAP]]
        if out:
            logger.info("브리지 보충(%s): %s ← 국토부 '%s'(%s) %.1f㎡ 최근 %d건(지문 %d/%d)",
                        listing.case_no, listing.apt_name, best_key[1], best_key[2],
                        best_key[3], len(out), best_n, len(in_window))
        return out
