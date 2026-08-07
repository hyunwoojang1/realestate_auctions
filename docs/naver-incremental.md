# 네이버 증분 갱신 — 설계와 실패 이력 (2026-08-07)

`deploy/crawl_naver.py --backfill-real --incremental` 이 매일 아침 사이클 [4/5] 에서 하는 일과,
2026-08-04~07 나흘 연속 사이클을 무너뜨린 원인 4개의 기록.

## 1. 무엇을 갱신하는가

단위는 **(complex_no, area_no) 쌍** = 네이버 단지번호 + 평형번호다. 이게 아파트·오피스텔에서
"같은 단지·같은 평형" 확정 comps 를 만드는 키이고, `estimate_from_complex_trades()` 가
이름 매칭을 우회할 수 있게 해 준다(→ `same_complex_same_area` = 추천 인정 등급).

상태는 `naver_pair_status` 에 남는다:

| 컬럼 | 의미 |
|---|---|
| `last_checked` | **언제 확인했나.** ⚠ 반드시 `YYYY-MM-DD HH:MM:SS` 형태 — §4 참조 |
| `latest_ymd` | **보유한 최신 거래일**(YYYYMMDD). 조기 종료의 기준선 — §2 |
| `trade_count` | 보유 거래 수. 0 이어도 '확인함'으로 남긴다(재크롤 방지) |
| `exhausted` | 페이지 상한에 걸려 잘렸는가(False=잘림). 조기 종료는 이 값을 **갱신하지 않는다** |

대상 선정은 `_backfill_pairs()` → `naver_store.fresh_pairs()`.
"확인한 지 오래된 쌍 + 아직 확인 안 된 쌍"만 고른다.

## 2. 조기 종료 (`stop_before_ymd`)

`naver_client.real_prices(stop_before_ymd=...)`.
보유 최신 거래일보다 **새로운 거래가 없는 페이지**가 `safety_pages`(기본 1)+1 연속 나오면 멈춘다.

정렬 근거: 이 응답은 **최신 → 과거** 순이다 — `data/naver_cache.json` 의 **2,786쌍 전수 검증,
순서 역전 0쌍**. 그래도 정렬 가정에만 기대지 않는다:
- 안전마진 1페이지를 **더 본다**(정렬이 한 페이지쯤 흔들려도 새 거래를 놓치지 않는다).
- 날짜를 못 읽은 행은 `batch_has_new()` 가 **"새 거래일 수 있음"** 으로 본다(모름≠없음).
- 처음 보는 쌍(`latest_ymd` 없음)은 `stop_before_ymd=None` → **전량 수집**.

왜 필요했나(실측 2026-08-07): 증분 모드인데 **매번 20년치를 처음부터 다시 받고 있었다.**

```
325쌍 처리 → 34,614행 재기록 (쌍당 107행)
  그중 최근 14일 새 거래:      53행 (0.15%)
  → 낭비 34,561행 (99.8%)
가장 오래된 거래: 2006년 1월
```

쌍당 32.7초의 거의 전부가 **이미 가진 데이터를 다시 받는 대기시간**이었다
(요청당 1.0~2.2초 안티밴 지연 × 20~25페이지).

효과(라이브 실측):

| | 종전 | 조기 종료 |
|---|---|---|
| 쌍당 요청 | 20~25회 | **2.5회** |
| 쌍당 시간 | 32.7초 | **5.75초** |
| 대단지 1쌍(보유 798건) | 25페이지 상한 | **3페이지** |

요청 수가 줄어드니 **차단 위험도 함께 내려간다** — 지연시간을 깎아 속도를 사는 것과 반대 방향이다.
(지연 단축·병렬화는 금지: `naver_client` 독스트링 "순차 전용, 병렬 절대 금지")

## 3. 시간 예산 (`--max-minutes`)

증분 모드 기본 **90분**(env `AUCTION_NAVER_MAX_MINUTES`, `0`=무제한).
초과하면 **남은 쌍 수를 출력하고 정상 종료(exit 0)** 한다 — 조용히 줄이지 않는다.

이유: 네이버는 사이클의 **[4/5]** 이고 그 뒤에 **[5/5] 재채점**(그날 보강분을 당일 등급·미러에
반영)과 사진 도달성 점검이 있다. 네이버가 시간을 다 먹으면 작업 스케줄러 `ExecutionTimeLimit=PT5H`
에 프로세스째 죽고, **뒤 단계가 통째로 실행되지 않는다.** 부분 수집보다 완주가 중요하다.

미처리분은 `last_checked` 가 갱신되지 않으므로 다음 회차에 자동으로 다시 대상이 된다.

## 4. `last_checked` 는 날짜여야 한다

`fresh_pairs()`·`checked_pairs()` 의 신선도 판정은 **문자열 비교**다. 그래서 이 칸에 날짜가
아닌 값이 들어가면 조용히 무너진다:

```
'reprocess:2026-07-19 21:30' >= '2026-07-24 09:55:18'  →  True
                              ('r'=0x72 > '2'=0x32)
```

실사고: `scripts/reprocess_real_trades.py` 가 접두어를 붙여 기록해 **1,096행이 영구히 '신선'**
으로 분류됐다(그중 갱신 대상 모집단에 남아 있던 **407쌍**이 2~3주치 거래를 놓치는 중이었다).

방어 2중:
- `_DATE_GLOB` 가드 — 날짜 형태가 아닌 행은 **신선하다고 보지 않는다**(모름을 신선으로 바꾸지 않음).
- `reprocess_real_trades.py` 가 정상 날짜를 쓴다. 재처리 여부는 **출력 로그**로 남긴다.

## 5. 만기 지터

`fresh_pairs(stale_days, jitter_days=7)` — 임계 = `stale_days + crc32(쌍) % jitter_days`.

실사고: 7/20 에 **853쌍을 한 번에** 확인해 두면 그 853쌍의 `last_checked` 가 전부 7/20 이 되고,
단일 임계(14일)에서는 **14일 뒤 같은 날 한꺼번에** 만기된다. 하루 대상이 105건(8/3) →
**716건(8/4)** → 836 → 775 로 튀었고, 못 끝낸 쌍은 갱신 기록이 안 남아 다음날 또 대상이 되면서
**백로그가 스스로 유지**됐다.

⚠ 내장 `hash()` 금지 — `PYTHONHASHSEED` 때문에 프로세스마다 값이 달라 만기일이 실행마다 흔들린다.
`zlib.crc32` 로 고정한다.

## 6. 브라우저가 죽었을 때

`is_driver_dead()` 가 True 면 `_hard_restart()`(playwright 인스턴스 통째 교체), False 면
`_refresh()`(브라우저만 재생성). **판정을 틀리면 `_launch()` 가 죽은 드라이버를 붙잡고 무한 대기한다.**

실사고: 실제 에러는 `Page.evaluate: Target crashed` 인데 판정 목록에 `target closed` 만 있어
(**closed ≠ crashed**) 8/4·8/5·8/6 사흘 연속 무한 대기 → PT5H 강제종료. 로그가 매번
`[naver] 세션 갱신`에서 끊기고 `세션 확보`가 없었던 게 그 증거다.
지금은 `crashed` 토큰을 포함하고, `chromium.launch(timeout=60s)` 를 명시해 launch 자체도
영원히 매달리지 않는다.

## 7. 실패가 조용해지지 않게 — 워치독 check1b

`refresh-daily.ps1` 의 운영자 알림은 **스크립트 맨 끝**에 있다. 중간에 죽으면 알림이 아예 안 간다
(실측: 8/3 07:39 이후 스크립트발 알림 **0건**, 나흘간 아무도 몰랐다).

`scripts/watchdog.ps1` 이 30분마다 최신 `evidence/refresh-*.log` 에서 **종료표식 `^exit : N`**
(완주했을 때만 쓴다)을 직접 확인한다:

| 조건 | 알림 |
|---|---|
| State≠Running + 종료표식 없음 | **미완주**(high) — 진행률 포함 |
| State=Running + 로그 40분 무진행 | **정체**(high) |

알림 문구엔 `[ 64%] 330/513` 같은 **ASCII 진행표시만** 넣는다 — 로그 본문은 이중 인코딩
(python cp949 → Tee UTF-16)이라 한글이 깨져 폰 알림에서 읽을 수 없다.

## 8. 운영 체크리스트

```bash
# 아침 확인 — 완주했는가
tail -3 evidence/refresh-$(date +%Y%m%d)-*.log        # 'exit : 0' 이 있어야 정상

# 증분 대상 건수·구성 확인(네트워크 0)
python -c "import sqlite3,sys; sys.path.insert(0,'.'); \
from deploy.crawl_naver import _backfill_pairs; \
print(len(_backfill_pairs(sqlite3.connect('auction.db'), None, False, stale_days=14)))"

# 수동 소량 실행(안전) — 예산·상한 둘 다 걸고
python -m deploy.crawl_naver --backfill-real --incremental --limit 8 --max-minutes 8

# 긴급 중단
touch NAVER_STOP        # kill-switch(즉시 중단, 우회 금지)
```

관련 튜닝 환경변수: `AUCTION_NAVER_MIN`/`MAX`(지연 1.0~2.2초) ·
`AUCTION_NAVER_REAL_PAGES`(페이지 상한 25) · `AUCTION_NAVER_MAX_MINUTES`(예산 90분) ·
`AUCTION_NAVER_STALE_JITTER`(만기 지터 7일).
