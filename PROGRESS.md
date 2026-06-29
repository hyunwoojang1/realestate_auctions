# PROGRESS — auction-arbitrage PoC

> 매 세션 시작 시 이 파일을 먼저 읽는다. 한 번에 기능 하나. 완료 시 증거 확인 → 커밋 → versions.md 기록.

## Done
- **F1 scaffold** — venv(Python 3.14) + requests/pytest 설치, `python run.py` 동작
- **F2 score-engine** — 차익 스코어(가격갭50/권리30/환금20 ×신뢰), 하드게이트(인수>30%·유치권→권리0), 단위테스트
- **F3 molit-client** — 국토부 실거래 API 클라이언트 + XML 파서(한글/영문 태그·만원→원), 파서 테스트
- **F4 sample-data** — 경매 5건 fixture(확실한차익/신건/권리폭탄/시세추정불가/보통) + 실거래 11건 XML
- **F5 matcher** — 단지명+면적±10% → 법정동 폴백, 평단가 중앙값 추정시세, 테스트
- **F6 store** — SQLite 저장/조회(차익순 정렬, NULL 후순위)
- **F7 pipeline** — 샘플 end-to-end 완주
- **F8 report** — 콘솔 랭킹표 + CSV + HTML(잉크블루+시그널그린)
- **F9 docs+tests** — README + pytest 15건 통과
- (평가자 피드백 반영) 순차익 음수 → "차익없음" 등급으로 정직 표기

## In progress
- (사이클 1 완료. 다음 사이클 대기)

## Next
- **F10** 🔒 운영자 국토부 API 키 발급 → `.env` MOLIT_API_KEY → `python run.py --live` 검증
- **v1** 실제 법원경매(courtauction.go.kr) 크롤러 — 샘플 fixture 대체 (anti-bot/JS 렌더 리스크: 막히면 운영자 결정 필요로 표기하고 정지)

## Notes
- 환경: Windows 11, Python 3.14.5. venv = `.venv`. 실행 시 `$env:PYTHONUTF8="1"` 권장.
- 검증 결과(증거): 상계주공 95점(확실한차익) / 해운대마린시티 인수2억→하드게이트→38점(주의) / 화곡빌라 매칭0→시세추정불가. **핵심 가설(차익 큰데 권리 폭탄을 걸러냄) 작동 확인.**
- 종료조건: F1~F9 PASS·커밋 완료 → 사이클1 종료. 다음은 F10(키 대기) 또는 v1 크롤러. 연속 3회 무변화/막힘 → AGENT_STOP.
