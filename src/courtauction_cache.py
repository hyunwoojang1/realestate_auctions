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
# full-record 캐시: 오프라인 dry-run(--from-cache)이 실제 마지막 수집분을 재생하도록
# '정제된(sanitize된) 원본 전체'를 보존한다. rec.raw 는 parse_row 가 이미 PII를 제거한
# dict 이므로(개인정보 미저장 원칙 유지), 그대로 저장해도 안전하다.
DEFAULT_FULL_CACHE = "data/courtauction_full_cache.json"


def record_key(rec) -> str:
    """행 고유키 — docid 우선, 없으면 법원+사건번호+물건번호 복합키(T1).

    사건번호는 법원 간 중복 가능(연도+타경 일련). 구키(case_no-maemulSer)는 법원이 빠져
    타법원 동번호 사건과 충돌할 수 있었다. 키 형식 변경으로 기존 캐시 diff가 1회 전량
    '신규'로 보일 수 있음(스냅샷 저장 후 정상화).
    """
    return rec.doc_id or f"{rec.court}|{rec.case_no}|{rec.item_no}"


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


def save_full_records(records: list, path: str | Path = DEFAULT_FULL_CACHE) -> int:
    """정제된 원본 전체(rec.raw)를 `{"records": [...]}` 형식으로 저장.

    라이브 수집 직후 호출하면, 이후 `--from-cache` 오프라인 dry-run이 fixture가 아니라
    '마지막으로 실제 수집한 데이터'를 재생할 수 있다(pipeline._records_from_full_cache).
    rec.raw 는 parse_row 가 이미 sanitize한 dict라 PII가 없다. raw 없는 레코드는 건너뛴다.
    """
    rows = [r.raw for r in records if getattr(r, "raw", None)]
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"records": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(rows)
