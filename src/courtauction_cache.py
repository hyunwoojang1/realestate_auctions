"""courtauction 수집결과 로컬 캐시 + 증분 diff.

재실행 시 신규/변경(유찰→최저가 하락, 기일 변경 등)/소멸(낙찰·취하) 매물을 가려
전체 재처리 대신 '달라진 것'만 본다. 같은 검색 스코프로 연속 실행해야 diff가 의미 있다.

개인정보 미저장: 스냅샷은 사건번호·최저가·감정가·유찰·기일·주소(공시정보)만.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CACHE = "data/courtauction_cache.json"


def record_key(rec) -> str:
    """행 고유키 — docid 우선, 없으면 사건번호+매물일련번호."""
    return rec.doc_id or f"{rec.case_no}-{rec.raw.get('maemulSer', '')}"


def _snapshot(rec) -> dict:
    return {
        "case_no": rec.case_no,
        "min_bid_price": rec.min_bid_price,
        "appraisal_price": rec.appraisal_price,
        "fail_count": rec.fail_count,
        "sale_date": rec.sale_date,
        "address": rec.address,
    }


# 변경으로 간주할 필드(이 값들이 달라지면 '변경')
_WATCH = ("min_bid_price", "fail_count", "sale_date")


@dataclass
class CacheDiff:
    new: list = field(default_factory=list)         # 신규 record
    changed: list = field(default_factory=list)     # (record, prev_snapshot)
    unchanged: list = field(default_factory=list)   # record
    removed: list = field(default_factory=list)     # 사라진 key(소멸: 낙찰/취하 추정)

    @property
    def summary(self) -> str:
        return (f"신규 {len(self.new)} · 변경 {len(self.changed)} · "
                f"유지 {len(self.unchanged)} · 소멸 {len(self.removed)}")


def load_cache(path: str | Path = DEFAULT_CACHE) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def diff_records(records: list, cache: dict) -> CacheDiff:
    """현재 수집분과 캐시를 비교 → 신규/변경/유지/소멸."""
    d = CacheDiff()
    seen: set[str] = set()
    for r in records:
        k = record_key(r)
        seen.add(k)
        prev = cache.get(k)
        if prev is None:
            d.new.append(r)
        elif any(prev.get(w) != getattr(r, w) for w in _WATCH):
            d.changed.append((r, prev))
        else:
            d.unchanged.append(r)
    d.removed = [k for k in cache if k not in seen]
    return d


def save_cache(records: list, path: str | Path = DEFAULT_CACHE) -> dict:
    """현재 수집분 스냅샷으로 캐시를 '교체' 저장(다음 실행의 소멸 감지를 위해 병합 아님)."""
    snap = {record_key(r): _snapshot(r) for r in records}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    return snap
