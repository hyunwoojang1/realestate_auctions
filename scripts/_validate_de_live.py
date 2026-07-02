"""D/E 스캐폴딩 라이브 검증(1회): 새 국토부 확장/건축물대장 엔드포인트 실호출 → 파서 정합 확인.

오프라인 개발이 끝난 뒤 '아침 통제된 라이브 1회'로 실응답 구조가 파서 가정과 맞는지 본다.
결과는 evidence/de_live_validate.txt 에도 남긴다. (네트워크 필요 — dangerouslyDisableSandbox)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# .env 로드
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

KEY = os.environ.get("MOLIT_API_KEY", "").strip()
LAWD = "11680"   # 강남구
YM = "202504"
out_lines: list[str] = []


def log(msg: str) -> None:
    print(msg)
    out_lines.append(msg)


log(f"MOLIT_API_KEY 설정됨: {bool(KEY) and not KEY.startswith('여기에')}")
log(f"검증 파라미터: lawd_cd={LAWD}(강남구) ym={YM}\n")

# --- E: 확장 실거래 3종(단독/상업/토지) ---
from src.molit_extra_client import fetch_extra_trades  # noqa: E402

for kind in ("sh", "nrg", "land"):
    try:
        trades = fetch_extra_trades(kind, LAWD, YM, KEY, num_rows=5)
        log(f"[E/{kind}] OK — {len(trades)}건 수집")
        if trades:
            t = trades[0]
            log(f"      샘플: area_m2={t.area_m2} price={t.price} deal_ym={t.deal_ym} "
                f"dong={t.dong!r} kind={t.kind}")
            # 파서가 핵심 필드를 실제로 채웠는지(구조 정합)
            filled = t.area_m2 > 0 and t.price > 0 and len(t.deal_ym) == 6
            log(f"      파서 정합: {'OK(핵심필드 채워짐)' if filled else '⚠️ 필드 비어있음 — 태그 불일치 의심'}")
        else:
            log("      (0건 — 해당 월 거래 없음이거나 응답 비어있음)")
    except Exception as e:  # noqa: BLE001
        log(f"[E/{kind}] ❌ 실패: {type(e).__name__}: {str(e)[:200]}")
    log("")

# --- D 인접: 건축물대장 표제부(노후도/위반건축물) ---
from src.building_register_client import fetch_building_titles  # noqa: E402

# 강남구 역삼동: sigungu=11680, bjdong=10100
try:
    recs = fetch_building_titles("11680", "10100", KEY, num_rows=5)
    log(f"[건축물대장] OK — {len(recs)}건")
    if recs:
        r = recs[0]
        age = r.age_years(2026)
        log(f"      샘플: name={r.name!r} 주용도={r.main_purpose!r} 승인일={r.use_approval_day!r} "
            f"노후도={age}년 위반={r.is_violation}")
        filled = bool(r.use_approval_day) or r.total_area_m2 > 0
        log(f"      파서 정합: {'OK' if filled else '⚠️ 필드 비어있음 — 태그 불일치 의심'}")
    else:
        log("      (0건)")
except Exception as e:  # noqa: BLE001
    log(f"[건축물대장] ❌ 실패: {type(e).__name__}: {str(e)[:200]}")

# 증거 저장
ev = ROOT / "evidence" / "de_live_validate.txt"
ev.parent.mkdir(exist_ok=True)
ev.write_text("\n".join(out_lines), encoding="utf-8")
log(f"\n증거: {ev}")
