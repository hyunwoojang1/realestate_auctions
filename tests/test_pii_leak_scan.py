"""scripts/backfill_pii_mask.py 의 잔여 실명 스캔(커밋 게이트) 테스트.

이 스캔은 커밋을 막는 게이트라 **오탐도 실패**다 — 실명을 놓치면 유출이고, 법인을 실명으로
오인하면 매수인이 알아야 할 공시정보(지상권자·근저당권자)를 지우게 되거나 커밋이 멈춘다.
게이트와 마스커가 같은 판정을 하는지(정렬) 고정한다.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from backfill_pii_mask import leak_scan  # noqa: E402


def _conn(senior_lien="", notes="[]"):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE listing_rights(court TEXT, case_no TEXT, item_no TEXT, "
              "senior_lien TEXT, appraisal_notes TEXT)")
    c.execute("INSERT INTO listing_rights VALUES('고양지원','2024타경88395','1',?,?)",
              (senior_lien, notes))
    return c


def test_corp_with_long_name_is_not_flagged():
    """'월롱농업협동조합'을 앞 4자('월롱농업')만 보고 실명으로 오인하지 않는다.

    2026-08-05 실측 회귀: 마스커는 전체 단어의 '조합'을 보고 올바로 보존했는데, 게이트만
    2~4자로 잘라 봐서 실명 의심 1건을 냈고 커밋이 막혔다.
    """
    c = _conn(senior_lien="지상권자 : 월롱농업협동조합)이 되어 있는 바")
    assert leak_scan(c) == []


def test_short_corp_names_are_not_flagged():
    for name in ("신한은행", "한국전력", "충주산림조합", "서울보증보험주식회사"):
        c = _conn(senior_lien=f"근저당권자 {name}")
        assert leak_scan(c) == [], name


def test_real_name_is_still_flagged():
    """가드가 실명까지 삼키면 안 된다 — 게이트가 죽는다."""
    c = _conn(senior_lien="지상권자 : 홍길동")
    hits = leak_scan(c)
    assert len(hits) == 1
    assert "홍길동" in hits[0][2]


def test_masked_placeholder_is_not_flagged():
    c = _conn(notes='[{"text": "채무자 [성명] 소유"}]')
    assert leak_scan(c) == []


def test_scans_appraisal_notes_too():
    c = _conn(notes='[{"text": "임차인 김철수 점유"}]')
    assert len(leak_scan(c)) == 1
