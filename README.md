# auction-arbitrage (PoC)

경매 물건의 **최저입찰가**와 **국토부 실거래 추정시세**를 매칭해, 부대비용(취득세·명도·수리·인수금액)을
차감한 **순차익**과 **차익 스코어(0~100)**를 계산하고 차익률 높은 순으로 큐레이션한다.

> 한 줄: *"검색 도구가 아니라 사라고 콕 집어주는 엔진."* — 시장의 경매 사이트가 비워둔 "차익 자동 큐레이션"을 채우는 PoC.

## 무엇을 검증하나
- 시세 대비 싼(=차익 큰) 물건을 자동으로 상위에 올린다.
- **차익이 커 보여도 권리가 더러우면(인수금액·유치권) 강등**한다(하드게이트).
- **시세 추정이 안 되는 물건(매칭 0건)은 거짓 차익을 만들지 않고 분리**한다.

## 설치
```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt   # Windows
# (macOS/Linux: source .venv/bin/activate; pip install -r requirements.txt)
```

## 실행
```bash
python run.py                      # 샘플 데이터로 차익 큐레이션 (키 불필요)
python run.py --live --ym 202605   # 국토부 라이브 실거래 (MOLIT_API_KEY 필요)
```
출력:
- 콘솔: 차익 스코어 랭킹표
- `evidence/result.csv` — 전 컬럼 데이터
- `evidence/result.html` — 잉크블루+시그널그린 랭킹 페이지

## 차익 스코어 공식
```
score = ( 가격갭×0.50 + 권리×0.30 + 환금성×0.20 ) × 신뢰계수(0.6~1.0)
하드게이트: 인수금액/최저가 > 30% 또는 치명 유치권 → 권리=0
실질취득원가 = 최저가 + 취득세 + 명도비 + 수리비 + 인수금액
가격갭 = (추정시세 − 실질취득원가) / 추정시세
신뢰계수: 매칭 실거래 3건↑=1.0 / 2건=0.85 / 1건=0.70 / 0건=시세추정불가
```

## 라이브 연동 (F10 — 운영자 작업)
1. [공공데이터포털](https://www.data.go.kr) 회원가입
2. "국토교통부_아파트 매매 실거래가 자료" 오픈API → **활용신청**(무료·자동승인)
3. 발급된 인증키를 `.env`에 입력 (`.env.example` 참조):
   ```
   MOLIT_API_KEY=발급키
   ```
4. `python run.py --live --ym 202605`

## 테스트
```bash
python -m pytest -q
```

## 구조
```
src/
  models.py        도메인 모델 (AuctionListing / Trade / ScoredListing)
  score.py         차익 스코어 엔진 (공식·하드게이트·부대비용)
  molit_client.py  국토부 실거래 API 클라이언트 + XML 파서
  matcher.py       경매 ↔ 실거래 매칭, 추정시세
  store.py         SQLite 저장/조회
  pipeline.py      end-to-end 오케스트레이션
  report.py        콘솔/CSV/HTML 출력
data/              샘플 fixture (경매 5건, 실거래 XML)
tests/             단위테스트
run.py             CLI 엔트리
```

## 데이터 소스 / 면책
- 시세: 국토부 실거래가 API (무료). 경매: 대법원 법원경매정보(PoC는 샘플 → v1 크롤러).
- ⚠️ 유료 경매사이트 가공데이터 크롤링은 하지 않는다(법적 리스크). 원천에서만 수집.
- 차익·권리 결과는 **투자 판단 보조이며 전문가 상담을 대체하지 않는다.**

## 상태
- PoC: F1~F9 완료(샘플 데이터 end-to-end + 14 테스트 통과). 상세는 `PROGRESS.md` / `versions.md` / `GOAL.md`.
- 다음: F10 라이브 국토부 연동(운영자 키), v1 실제 법원경매 크롤러.
