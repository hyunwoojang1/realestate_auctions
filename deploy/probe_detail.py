"""물건상세(pgj15B) 원응답 구조 정찰 — 현황조사서·감정평가서가 응답에 있는지 확정한다.

목적(추측 0): 권리 크롤이 매각물건명세서만 읽고 **현황조사서(점유관계·임대차·전입일자)**를
안 가져오는 것이 확인됐다. 이걸 채우려면 두 경우 중 어느 쪽인지 **실측으로 확정**해야 한다:

  (A) pgj15B 응답(dma_result)에 이미 있는데 우리가 안 읽는 키 → normalize에 키만 추가
  (B) 응답에 없고 별도 엔드포인트(현황조사서 열람) 필요 → 크롤에 요청 하나 추가

이 스크립트는 그걸 **라이브 1콜**로 판정한다. 코드를 짜기 전에 사실부터 확보하는 단계다.
(목록 크롤과 요청이 겹치면 밴 위험이 오르므로 목록 크롤이 끝난 뒤 단독 실행 권장.)

사용:
    PYTHONUTF8=1 .venv/Scripts/python.exe -m deploy.probe_detail
    PYTHONUTF8=1 .venv/Scripts/python.exe -m deploy.probe_detail --court 고양지원 --case 2025타경63992

동작:
  1) DB에서 boCd 있는 물건 1건을 고른다(인자로 지정 가능 — 미확인 법원 물건을 우선).
  2) case_detail 1콜 → dma_result 원본을 evidence/probe_detail_raw.json 로 덤프.
  3) 모든 키를 나열하고, 점유/임대차/전입/보증/현황 신호를 **재귀 탐색**해 어느 키에 있는지 보고.
  4) 판정: 해당 신호가 응답에 있으면 (A), 전혀 없으면 (B). 다음 배선 방향을 그대로 알려준다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from deploy.migrate_to_supabase import _load_env
from src import store
from src.courtauction_client import CourtAuctionBlocked, CourtAuctionClient, CourtAuctionError

# 현황조사서/점유/임대차의 존재를 알리는 값(내용) 신호 — 응답 어디에 있든 잡아낸다.
_OCCUPANCY_VALUE_MARKERS = (
    "점유", "임차", "전입", "보증금", "임대차", "현황조사", "세대", "확정일자", "차임",
)
# 키 이름 자체가 현황/점유/임대차를 가리킬 만한 패턴(대소문자·부분일치).
_OCCUPANCY_KEY_HINTS = (
    "posspn", "posesn", "occ", "clm", "rent", "lse", "lease", "hyeon", "hjs",
    "rghtRel", "csClm", "spns", "tnnt", "lsee", "dtl",
)


def _walk(obj, path=""):
    """(경로, 키, 값) 스트림 — dict/list 재귀. 값은 스칼라만(리스트/딕트는 하위로)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = f"{path}.{k}" if path else k
            if isinstance(v, (dict, list)):
                yield from _walk(v, here)
            else:
                yield (here, k, v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")


def _pick_target(conn, court: str | None, case: str | None) -> dict | None:
    """정찰 대상 1건(boCd 포함). court/case 지정 시 그것, 아니면 미크롤 법원 물건 우선."""
    if case:
        row = conn.execute(
            "SELECT s.court, s.case_no, s.item_no, r.raw_json "
            "FROM scored_listings s JOIN raw_listings r "
            "  ON r.court=s.court AND r.case_no=s.case_no AND r.item_no=s.item_no "
            "WHERE s.case_no=? " + ("AND s.court=? " if court else "") + "LIMIT 1",
            ((case, court) if court else (case,)),
        ).fetchone()
    else:
        # 아직 권리 미크롤이면서 boCd 있는 물건을 우선(정찰 자체가 커버리지에도 도움).
        row = conn.execute(
            "SELECT s.court, s.case_no, s.item_no, r.raw_json "
            "FROM scored_listings s JOIN raw_listings r "
            "  ON r.court=s.court AND r.case_no=s.case_no AND r.item_no=s.item_no "
            "LEFT JOIN listing_rights lr "
            "  ON lr.court=s.court AND lr.case_no=s.case_no AND lr.item_no=s.item_no "
            "WHERE lr.case_no IS NULL "
            "  AND json_extract(r.raw_json,'$.boCd') IS NOT NULL "
            "  AND json_extract(r.raw_json,'$.boCd') != '' "
            "LIMIT 1"
        ).fetchone()
    if not row:
        return None
    try:
        bo = json.loads(row["raw_json"]).get("boCd") or ""
    except json.JSONDecodeError:
        bo = ""
    if not bo:
        return None
    return {"court": row["court"], "case_no": row["case_no"],
            "item_no": row["item_no"], "bo_cd": bo}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="물건상세(pgj15B) 원응답 구조 정찰(현황조사서 존재 확인)")
    ap.add_argument("--db", default=os.environ.get("AUCTION_DB", "auction.db"))
    ap.add_argument("--court", default=None, help="법원명(예: 고양지원)")
    ap.add_argument("--case", default=None, help="사건번호(예: 2025타경63992)")
    args = ap.parse_args(argv)
    _load_env()

    conn = store.connect(args.db)
    target = _pick_target(conn, args.court, args.case)
    conn.close()
    if not target:
        print("[!] 정찰 대상(boCd 있는 물건)을 찾지 못했습니다.", file=sys.stderr)
        return 1
    print(f"[*] 정찰 대상: {target['court']} {target['case_no']}/{target['item_no']} "
          f"(boCd={target['bo_cd']})")

    client = CourtAuctionClient()
    try:
        dma = client.case_detail(target["bo_cd"], target["case_no"], target["item_no"] or "1")
    except CourtAuctionBlocked as e:
        print(f"[!] 차단/상한 신호 — 중단: {e}", file=sys.stderr)
        return 2
    except CourtAuctionError as e:
        print(f"[!] 상세 조회 실패: {e}", file=sys.stderr)
        return 1

    # 1) 원본 덤프(evidence는 gitignore — 사건번호/성명 포함 가능하므로 커밋 금지).
    out_dir = Path("evidence")
    out_dir.mkdir(exist_ok=True)
    raw_path = out_dir / "probe_detail_raw.json"
    raw_path.write_text(json.dumps(dma, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[+] 원응답 덤프: {raw_path}")

    # 2) 최상위 키 구조.
    print("\n=== dma_result 최상위 키 ===")
    for k in sorted(dma.keys()):
        v = dma[k]
        if isinstance(v, list):
            sub = list(v[0].keys()) if v and isinstance(v[0], dict) else []
            print(f"  {k} (list[{len(v)}]) 원소키: {sub[:20]}")
        elif isinstance(v, dict):
            print(f"  {k} (dict) 키: {list(v.keys())[:20]}")
        else:
            print(f"  {k} = {str(v)[:60]}")

    # 3) 점유/임대차/전입/보증 신호 재귀 탐색 — 어느 키에 있는가.
    print("\n=== 점유·임대차·전입·보증 신호 위치(값 기준) ===")
    hits_by_value: dict[str, list[str]] = {}
    for pathkey, _key, val in _walk(dma):
        s = str(val)
        for m in _OCCUPANCY_VALUE_MARKERS:
            if m in s:
                hits_by_value.setdefault(pathkey, []).append(m)
    if hits_by_value:
        for pathkey, marks in list(hits_by_value.items())[:40]:
            print(f"  ✅ {pathkey}  ← {sorted(set(marks))}")
    else:
        print("  (값에서 점유/임대차 신호 없음)")

    print("\n=== 키 이름이 현황/점유/임대차를 가리키는 후보 ===")
    key_hits = set()
    for pathkey, key, _val in _walk(dma):
        kl = str(key).lower()
        if any(h.lower() in kl for h in _OCCUPANCY_KEY_HINTS):
            key_hits.add(pathkey.rsplit("[", 1)[0])
    for kh in sorted(key_hits)[:40]:
        print(f"  ? {kh}")
    if not key_hits:
        print("  (해당 키 이름 없음)")

    # 4) 판정.
    print("\n=== 판정 ===")
    if hits_by_value or key_hits:
        print("  ▶ (A) 후보 있음 — pgj15B 응답에 현황/점유/임대차 데이터가 있을 가능성.")
        print("     다음: 위 ✅ 경로의 실제 값을 raw 덤프에서 확인 → normalize()에 그 키를 추가하면 됨(크롤 무변경).")
    else:
        print("  ▶ (B) 응답에 현황조사/점유/임대차 신호 없음 — 별도 엔드포인트가 필요.")
        print("     다음: courtauction 물건상세의 '부동산현황조사서 열람' 요청(별도 service)을 정찰·추가해야 함.")
    print(f"\n  (원응답 전체는 {raw_path} 에서 직접 확인)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
