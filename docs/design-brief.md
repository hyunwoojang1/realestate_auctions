# Design Brief — auction-arbitrage ("사라고 콕 집어주는 엔진")

> 방향: **Warm-Paper Financial Broadsheet** — FT 페이퍼(#FFF1E5) 위 슬레이트 잉크,
> 색 예산은 오직 두 신호(머니-그린=차익, 클라레=권리위험)에만. "furniture는 조용히, 숫자는 크게."
> 라이트 테마(스캔 최적). 다관점 디자인 리서치(2026-07-02) 종합.

## 핵심 원칙
- **verdict + evidence 한 줄**: 뱃지는 단독으로 절대 안 나온다 — 항상 `최저가 vs 시세 · gap(−%)`와 같은 밴드.
- **스코어가 카드당 가장 큰 요소**: 32px tabular 숫자 + 5px 크기-색 막대.
- **신뢰도는 별도의 더 조용한 채널**(teal 칩). 저신뢰=뱃지 채도↓ + "추정치·현장확인".
- **표는 hairline만**: 헤더 밑/마지막 행 밑 1px, 세로줄·얼룩 없음. 숫자 우측정렬 tabular.
- **#1 픽은 그리드를 깬다**(히어로 타일). 나머지는 조밀한 랭킹 테이블.
- 상호작용색(옥스포드 블루)은 링크 전용 — "차익"이 "버튼"으로 읽히면 안 됨.

## 안전(도메인) 규칙 — 감사 반영
- 권리 미검증(라이브 크롤) 물건은 **초록 안전문구 금지** → 중립 "권리 미확인" + "직접 확인" 경고.
- 최상위 "차익 유력"은 권리검증 + 표본 ≥3건에만.
- 방법론 백테스트는 **"샘플/합성 기준"** 명시(실제 낙찰결과 아님).
- 목록 상단에 **데이터 출처 배너**(라이브 DB vs 샘플).

## 토큰(요약) — 전체는 templates/base.html
- paper #FFF1E5 / surface #FFFBF7 / ink #262A33 / muted #6B7280 / hairline #E7D8C9
- accent(링크) #0F5499 · pos(차익) #00994D · risk #CC0000 · warn #C4600E · conf teal #0D7680
- 스코어 크기색: 90+ #0A7A3E / 75+ #2E9E5B / 60+ #C4600E / <60 #9AA0AB
- 타입: 히어로 serif(숫자만) · UI Pretendard/그로테스크 · 식별자(사건번호) mono+slashed-zero
- 행 높이 44px · radius 5/10px · 그림자 hover만

## 레퍼런스 shortlist
CarGurus(뱃지+델타 항상 페어) · FT o-colors(팔레트) · Redfin Hot(신호색≠버튼색) ·
Urban Institute(테이블 룰) · Linear(톤으로 깊이·mono=ID) · Wirecutter(#1 픽 그리드 깨기) ·
Pencil&Paper(행높이·우측정렬) · 호갱노노(억/만원 표기).
