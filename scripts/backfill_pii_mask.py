"""C1 백필: 기존 listing_rights 의 senior_lien·appraisal_notes 를 현행 마스커로 재마스킹.

이미 무마스킹 실명이 저장·서빙 중이므로 로컬 DB 를 교정한다(Supabase 재미러는 축 A 재채점에서).
--check 만 주면 잔여 실명 스캔만(쓰기 없음). 기본은 UPDATE 실행.
"""
import argparse
import json
import os
import re
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from src.courtauction_fields import _corp_word, _looks_like_name, mask_personal_names  # noqa: E402

# 잔여 실명 스캔 — 역할라벨 + 구분자 + 토큰. 토큰이 마스커 기준으로 '성명처럼 보이면' 누락 실명.
# (마스커와 동일한 _looks_like_name 을 써서 법률용어·법인·조사 오탐을 제거 = 정렬된 CI 가드)
# 세 번째 그룹 = 토큰 뒤에 이어지는 한글 — 마스커의 _role_first 와 **같은 것을 보게** 하려고 잡는다.
# 이게 없으면 앞 2~4자만 보고 판정해 5자+ 법인을 자연인으로 오인한다(2026-08-05 실측:
# '지상권자 : 월롱농업협동조합'을 '월롱농업'으로 잘라 실명 의심 1건 → 커밋 게이트가 막았다.
# 마스커는 전체 단어의 '조합'을 보고 이미 올바로 보존하고 있었으니, 틀린 쪽은 게이트였다).
_ROLE = ("주택임차권자|유치권신고인|유치권자|임차권자|근저당권설정자|근저당권자|가압류권자"
         "|전세권자|지상권자|채권자|채무자|소유자|공유자|임차인|임대인|점유자|신청인|배우자|상속인")
_LEAK_RE = re.compile(rf"({_ROLE})[\s:：]+([가-힣]{{2,4}})([가-힣]*)")


def _remask_notes(aj):
    try:
        notes = json.loads(aj or "[]")
    except Exception:
        return aj, False
    changed = False
    for n in notes:
        t = n.get("text", "")
        m = mask_personal_names(t)
        if m != t:
            n["text"] = m
            changed = True
    return json.dumps(notes, ensure_ascii=False), changed


def leak_scan(conn):
    hits = []
    for r in conn.execute("SELECT court,case_no,senior_lien,appraisal_notes FROM listing_rights"):
        blobs = [r["senior_lien"] or ""]
        try:
            blobs += [n.get("text", "") for n in json.loads(r["appraisal_notes"] or "[]")]
        except Exception:
            pass
        for b in blobs:
            for m in _LEAK_RE.finditer(b):
                nm, rest = m.group(2), m.group(3)
                if rest and _corp_word(nm + rest):    # 전체 단어가 법인·기관 = 자연인 아님
                    continue
                if nm != "성명" and _looks_like_name(nm):   # 마스커 기준 '성명처럼 보임' = 누락 실명
                    hits.append((r["court"], r["case_no"], m.group(0)))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="쓰기 없이 잔여 실명 스캔만")
    args = ap.parse_args()
    conn = sqlite3.connect(os.path.join(ROOT, "auction.db"))
    conn.row_factory = sqlite3.Row

    if args.check:
        hits = leak_scan(conn)
        print(f"[스캔] 잔여 실명 의심(역할+성명): {len(hits)}")
        for h in hits[:20]:
            print("  ", h)
        return

    n_sl, n_ap = 0, 0
    rows = conn.execute("SELECT court,case_no,item_no,senior_lien,appraisal_notes FROM listing_rights").fetchall()
    with conn:
        for r in rows:
            sl = r["senior_lien"] or ""
            msl = mask_personal_names(sl)
            new_ap, ap_changed = _remask_notes(r["appraisal_notes"])
            if msl != sl:
                n_sl += 1
            if ap_changed:
                n_ap += 1
            if msl != sl or ap_changed:
                conn.execute(
                    "UPDATE listing_rights SET senior_lien=?, appraisal_notes=? "
                    "WHERE court=? AND case_no=? AND item_no=?",
                    (msl, new_ap, r["court"], r["case_no"], r["item_no"]))
    print(f"[백필] senior_lien 교정 {n_sl}행 · appraisal_notes 교정 {n_ap}행")
    hits = leak_scan(conn)
    print(f"[백필후 스캔] 잔여 실명 의심: {len(hits)}")
    for h in hits[:20]:
        print("  ", h)


if __name__ == "__main__":
    main()
