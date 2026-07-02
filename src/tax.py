"""취득세 엔진 — docs/tax-auction-knowledge.md 와 1:1 (기준연도 2026).

경매 낙찰 = 유상승계취득 → 일반 매매와 동일 세율. 과세표준은 입찰 전이므로
최저입찰가(보수적 하한)로 추정한다. 세율·산식 근거는 지식문서 §1~§2, 출처 §5.

매수인 가정은 BuyerProfile 1개(기본 1주택·비조정·개인)로 통일하고 UI에 명시한다.
세법 개정 시 이 파일과 지식문서만 갱신한다.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = ROOT / "data" / "buyer_profile.json"

# 주택으로 과세되는 물건유형(그 외는 전부 비주택 4.6% — 오피스텔 포함, 지식문서 §2).
HOUSING_TYPES = frozenset({
    "아파트", "연립", "연립주택", "다세대", "다세대주택", "연립다세대", "빌라",
    "단독", "단독주택", "다가구", "다가구주택", "단독다가구", "주택",
})

# 비주택 유상취득: 취득세 4% + 지방교육세 0.4% + 농어촌특별세 0.2% = 4.6%
NONHOUSING_RATES = (0.04, 0.004, 0.002)

# 농어촌특별세 '서민주택' 비과세 경계(전용면적)
RURAL_TAX_EXEMPT_AREA_M2 = 85.0


@dataclass(frozen=True)
class BuyerProfile:
    """매수인 취득 가정 — houses_after = '이번 취득을 포함한' 보유 주택 수."""
    houses_after: int = 1          # 낙찰 후 총 주택 수 (기본: 무주택자가 첫 취득 = 1)
    regulated_area: bool = False   # 취득 물건이 조정대상지역인가
    is_corporation: bool = False   # 법인 취득인가

    def label(self) -> str:
        if self.is_corporation:
            return "법인"
        region = "조정" if self.regulated_area else "비조정"
        return f"{self.houses_after}주택·{region}"


def load_profile(path: str | Path | None = None) -> BuyerProfile:
    """data/buyer_profile.json(있으면)에서 매수인 가정 로드. 없으면 기본(1주택·비조정·개인)."""
    p = Path(path) if path else PROFILE_PATH
    if not p.exists():
        return BuyerProfile()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return BuyerProfile(
            houses_after=max(1, int(raw.get("houses_after", 1))),
            regulated_area=bool(raw.get("regulated_area", False)),
            is_corporation=bool(raw.get("is_corporation", False)),
        )
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning("buyer_profile.json 파싱 실패(%s) — 기본 프로필 사용", e)
        return BuyerProfile()


PROFILE = load_profile()   # 모듈 전역 — score/web이 참조. 테스트에서 monkeypatch 가능.


def is_housing(property_type: str) -> bool:
    return (property_type or "").strip() in HOUSING_TYPES


def _housing_base_rate(price: int) -> float:
    """주택 기본 취득세율. 6~9억은 법정 산식 (가액×2/3억−3)% — %를 소수 넷째자리 반올림."""
    if price <= 600_000_000:
        return 0.01
    if price <= 900_000_000:
        rate_pct = round(price * 2 / 300_000_000 - 3, 4)   # 지방세법: 소수점 넷째자리까지
        return rate_pct / 100
    return 0.03


def _surcharge_tier(profile: BuyerProfile) -> str:
    """중과 단계: 'base' | '8' | '12' (지식문서 §1-2 표)."""
    if profile.is_corporation:
        return "12"
    n = profile.houses_after
    if profile.regulated_area:
        if n >= 3:
            return "12"
        if n == 2:
            return "8"    # 일시적 2주택 예외는 미반영(보수적) — 문서 §1-2 참고
        return "base"
    if n >= 4:
        return "12"
    if n == 3:
        return "8"
    return "base"


def effective_rates(price: int, property_type: str, area_m2: float,
                    profile: BuyerProfile | None = None) -> tuple[float, float, float]:
    """(취득세율, 지방교육세율, 농어촌특별세율). 지식문서 §1-3·§1-4·§2와 1:1."""
    prof = profile if profile is not None else PROFILE
    if not is_housing(property_type):
        return NONHOUSING_RATES
    over85 = area_m2 > RURAL_TAX_EXEMPT_AREA_M2
    tier = _surcharge_tier(prof)
    if tier == "8":
        return (0.08, 0.004, 0.006 if over85 else 0.0)
    if tier == "12":
        return (0.12, 0.004, 0.010 if over85 else 0.0)
    base = _housing_base_rate(price)
    edu = base * 0.5 * 0.20                     # 취득세율 × 1/2 × 20%
    rural = 0.002 if over85 else 0.0
    return (base, edu, rural)


def acquisition_tax_breakdown(price: int, property_type: str, area_m2: float,
                              profile: BuyerProfile | None = None) -> dict:
    """UI 상세 표시용 분해: 취득세·지방교육세·농어촌특별세·합계(원)."""
    acq, edu, rural = effective_rates(price, property_type, area_m2, profile)
    parts = {
        "취득세": round(price * acq),
        "지방교육세": round(price * edu),
        "농어촌특별세": round(price * rural),
    }
    parts["합계"] = sum(parts.values())
    parts["세율합계"] = acq + edu + rural
    return parts


def acquisition_tax(price: int, property_type: str, area_m2: float = 0.0,
                    profile: BuyerProfile | None = None) -> int:
    """취득세 총액(본세+교육세+농특세, 원)."""
    return acquisition_tax_breakdown(price, property_type, area_m2, profile)["합계"]
