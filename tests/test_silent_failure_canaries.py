"""침묵실패 카나리 테스트 — 2026-08-24 감사 대응 검증.

세 카나리:
  1. 필드 붕괴 게이트(data_gates.gate_field_canary) — 감정가·최저가 0 비율 10% 초과 시 FAIL.
  2. PII 미등재 역할어 카나리 — 마스킹 후 잔존 '역할어+성명'을 경고(동작 불변).
  3. 미등재 세부용도코드 경고 — 키워드 폴백으로 조용히 흐르던 것을 코드당 1회 경고.
"""
import logging
import sqlite3

from src import data_gates
from src.courtauction_fields import classify_property_type, mask_personal_names


def _db_with_scored(rows):
    """(appraisal, min_bid) 튜플 목록으로 scored_listings 최소 스키마 인메모리 DB."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE scored_listings (
        court TEXT, case_no TEXT, item_no TEXT,
        appraisal_price INTEGER, min_bid_price INTEGER)""")
    conn.executemany(
        "INSERT INTO scored_listings VALUES ('법원', ?, '1', ?, ?)",
        [(f"2026타경{i:05d}", ap, mb) for i, (ap, mb) in enumerate(rows)])
    conn.commit()
    return conn


# ---- 1. 필드 붕괴 게이트 ----

def test_field_canary_pass_when_healthy():
    conn = _db_with_scored([(100_000_000, 70_000_000)] * 60)
    r = data_gates.gate_field_canary(conn)
    assert r.ok


def test_field_canary_fail_when_appraisal_collapsed():
    # 60건 중 12건(20%)이 감정가 0 — 법원 필드명 변경 시나리오
    rows = [(0, 70_000_000)] * 12 + [(100_000_000, 70_000_000)] * 48
    r = data_gates.gate_field_canary(_db_with_scored(rows))
    assert not r.ok
    assert "감정가" in r.detail


def test_field_canary_holds_judgement_on_tiny_sample():
    # 표본 50건 미만(데모/테스트)에선 판정 보류 — 오발동 방지
    r = data_gates.gate_field_canary(_db_with_scored([(0, 0)] * 10))
    assert r.ok and "보류" in r.detail


def test_field_canary_registered_in_gates():
    assert data_gates.gate_field_canary in data_gates.GATES


# ---- 2. PII 미등재 역할어 카나리 ----

def test_pii_canary_warns_on_unknown_role(caplog):
    with caplog.at_level(logging.WARNING, logger="src.courtauction_fields"):
        out = mask_personal_names("담보가등기권자 김민수")
    assert out == "담보가등기권자 김민수"   # 동작 불변(마스킹 규칙엔 없는 역할어)
    assert any("성명카나리" in r.message for r in caplog.records)


def test_pii_canary_silent_on_known_role_and_corp(caplog):
    with caplog.at_level(logging.WARNING, logger="src.courtauction_fields"):
        masked = mask_personal_names("채무자 홍길동")          # 등재 역할어 → 마스킹됨
        corp = mask_personal_names("환매권자 국민은행")        # 법인 → 카나리 대상 아님
    assert masked == "채무자 [성명]"
    assert corp == "환매권자 국민은행"
    assert not any("성명카나리" in r.message and "환매권자" in r.message
                   for r in caplog.records)


# ---- 3. 미등재 세부용도코드 경고 ----

def test_unknown_scls_warns_once(caplog):
    with caplog.at_level(logging.WARNING, logger="src.courtauction_fields"):
        assert classify_property_type("아파트", "QQ777") == "아파트"   # 폴백 동작 불변
        classify_property_type("아파트", "QQ777")                      # 같은 코드 재호출
    hits = [r for r in caplog.records if "유형카나리" in r.message and "QQ777" in r.message]
    assert len(hits) == 1   # 코드당 1회만
