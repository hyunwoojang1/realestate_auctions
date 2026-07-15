"""naver_match — 경매 물건명 ↔ 네이버 단지명 매칭(음차·정규화). 네트워크 없음.

이 모듈엔 테스트가 없었다(감사 2026-07-15). 음차맵은 오탐이 나면 브랜드명을 파괴하고
미탐이 나면 같은 단지를 놓치므로, 양방향을 다 고정한다.
"""
from __future__ import annotations

import pytest

from src.naver_match import name_score, normalize

THRESHOLD = 0.70   # crawl_naver._process 의 매칭 임계값


@pytest.mark.parametrize(("auction_name", "naver_name"), [
    # (2026-07-15 실측) no_match 로 굳어 있던 실제 물건 — 음차 미등재로 임계 미달이었다.
    ("에스씨그린아파트", "SC그린"),                       # 0.44 → 통과
    ("창원무동에스티엑스칸1차아파트", "창원무동STX칸1차"),      # 0.64 → 통과
    ("에스아이팰리스", "SI팰리스"),
    ("장전동에스제이타워", "SJ타워"),
    ("에스엠스카이빌", "SM스카이빌"),
    ("엠제이타운", "MJ타운"),
    # 기존 음차(회귀 방지)
    ("이편한세상", "e편한세상"),
    ("아이파크", "IPARK"),
])
def test_transliterated_names_match(auction_name: str, naver_name: str):
    assert name_score(auction_name, naver_name) >= THRESHOLD


@pytest.mark.parametrize(("brand", "expected"), [
    # ⚠ 한글 알파벳을 **일반 규칙**으로 변환하면 '푸르지오'→'푸르go'(지+오=G+O) 처럼 브랜드가
    # 깨진다(2026-07-15 실측으로 그 접근을 폐기). 음차맵은 다음절 명시 등재만 허용한다.
    # 아래는 정규화 후 기대값 — 음차 변환이 일어나면 안 되는 이름들.
    ("푸르지오", "푸르지오"),
    ("디오션시티푸르지오", "디오션시티푸르지오"),
    ("래미안", "래미안"),
    ("자이", "자이"),
    ("힐스테이트", "힐스테이트"),
    ("오션시티", "오션시티"),
    ("이안", "이안"),
    ("제일풍경채", "제일풍경채"),
    ("신암보성아파트", "신암보성"),      # '아파트' 접미사 제거는 정상
    ("유익아파트", "유익"),
])
def test_brand_names_are_not_mangled(brand: str, expected: str):
    assert normalize(brand) == expected


def test_different_complexes_do_not_match():
    """서로 다른 단지가 임계를 넘으면 안 된다(오매칭 = 남의 시세를 붙이는 것)."""
    assert name_score("에스씨그린아파트", "신암보성아파트") < THRESHOLD
    assert name_score("청라로데오시티포레안", "청라봄") < THRESHOLD   # 실측 오분류(14:05 수정)
