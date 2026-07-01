# 신뢰계수 표본 개선 — 원인 규명과 개선 (GOAL_DEPLOY C)

> 작성: 2026-07-01 · 브랜치 `feat/deploy-prep` · 오프라인(fixture) 분석
> 관련 코드: `src/matcher.py`, `src/pipeline.py`, `src/score.py`, `src/config.py`

## 1. 문제 정의

라이브 실행 시 다수 물건이 **신뢰계수 낮음(0.60~0.70) / 시세추정불가**로 떨어졌다.
"다월 수집이 안 돼서"라는 초기 가설이 있었으나, 코드를 읽어보면 **다월 수집은 이미 배선돼 있다**:

- `src/pipeline.py`의 `load_live_trades()`는 `recent_ymds(deal_ymd, LIVE_MONTHS)`(기본 3개월)로
  연월 목록을 만들고 `fetch_trades_months(kind, lawd_cd, ymds, key)`로 3개월치를 합쳐 수집한다.
- 즉 "월수 배선"은 원인이 아니다.

따라서 진짜 원인은 **수집된 표본이 매칭 단계에서 과도하게 걸러지는 것**과
**수집 폭(개월수)·매칭 허용범위가 코드에 하드코딩돼 조정 불가**라는 두 가지다.

## 2. 원인 규명 (근거)

### (A) matcher의 면적밴드가 좁다 — 가장 큰 원인

`src/matcher.py::match_trades`는 두 단계로 comps를 고른다.

1. **단지명 부분일치 + 면적 ±AREA_BAND(=0.10)**
2. (1이 0건이면) **같은 법정동 + 면적 ±AREA_BAND(=0.10)**

`_area_ok(a, b) = |a-b|/b <= 0.10`. 84.9㎡ 물건은 **76.4~93.4㎡** comps만 채택한다.
그런데 한국 아파트 실거래는 같은 단지라도 74/84/96㎡ 등 **인접 평형**으로 흩어진다.
±10%는 이 인접 평형을 잘라내 — 3개월치를 다 모아도 정확 평형 거래가 1~2건뿐이면
표본이 빈약해진다. (근거 테스트: `test_wider_area_band_increases_sample_count` —
±10%는 정확 평형만(=다월 4건), ±15%로 넓히면 인접 평형 2건이 추가돼 표본이 는다.)

### (B) 수집 개월수(LIVE_MONTHS)가 하드코딩·저값

`LIVE_MONTHS = 3`이 pipeline 모듈 상수로 박혀 있어, 거래가 드문 지역/평형에서
표본을 늘리려면 코드를 고쳐야 했다. 수집 폭이 곧 표본 상한인데 조정 창구가 없었다.
(근거: `recent_ymds`가 반환하는 연월 수 = 수집 대상 폭. `test_more_months_collect_more_ymds`.)

### (C) 신뢰계수 공식은 '표본수를 이미 올바르게 반영'한다 — 여기는 원인이 아님

`src/score.py::confidence_from_matches(n)`는 `CONFIG.confidence_ladder`
(`[[3,1.0],[2,0.85],[1,0.70],[0,0.60]]`, 내림차순)를 훑어 **표본수에 단조 비감소**로 매핑한다.
- 0건 → 0.60, 1건 → 0.70, 2건 → 0.85, 3건↑ → 1.0.

즉 공식 자체는 "표본 많을수록 신뢰 높음"을 이미 만족한다(회귀 위험이 있어 기본 사다리는 유지).
문제는 공식이 아니라 **사다리에 먹일 표본수 n이 (A)(B) 때문에 작게 들어온 것**이다.

**결론**: 빈약의 실제 원인은 *matcher 과필터(좁은 면적밴드) + 조정 불가한 짧은 수집 개월수*이며,
신뢰계수 공식은 정상이다. 개선의 초점은 **표본을 늘릴 수 있게 튜닝 창구를 여는 것**이다.

## 3. 개선 내용

### 3.1 튜닝 파라미터 외부화 (`src/config.py`)

`SampleConfig(live_months=3, area_band=0.10)` 추가. 우선순위 **CLI > 환경변수 > JSON파일 > 기본값**.
- JSON: `data/sample_config.json`
- 환경변수: `AUCTION_LIVE_MONTHS`, `AUCTION_AREA_BAND`
- 전역: `config.SAMPLE` (pipeline/matcher가 참조, monkeypatch로 교체 가능)
- 하한 방어: 개월수 최소 1, 면적밴드 0 이하는 0.10으로 보정.
- **기본값은 기존 동작과 동일 → 무회귀.**

### 3.2 pipeline / matcher 배선

- `pipeline._live_months()` → `recent_ymds`에 주입 (기존 상수 `LIVE_MONTHS`는 기본값으로 존치).
- `matcher._area_band()` → `match_trades`의 `_area_ok`에 주입 (기존 상수 `AREA_BAND` 존치).
- 둘 다 런타임에 `config.SAMPLE`를 읽어 monkeypatch/CLI 반영.

### 3.3 CLI 노출 (`run.py`)

- `--live-months N` : 라이브 수집 개월수(표본 폭).
- `--area-band 0.15` : 매칭 전용면적 허용밴드(±비율).
- 지정 시 `config.SAMPLE` 갱신 후 실행, 적용값을 콘솔에 출력.

### 3.4 검증 테스트 (`tests/test_confidence_samples.py`, 다월 fixture, 외부호출 0)

- 신뢰계수 **단조 비감소**(n=0..8) 및 사다리 **단조 증가**(1<2<3).
- `estimate → score` 경로에서도 표본↑에 confidence 비감소.
- **밴드 확대가 표본을 실제로 늘림**(±10% 4건 → ±15% 6건).
- 개월수 확대가 수집 연월 수를 늘림.
- env/JSON/CLI 오버라이드 및 기본값=레거시(무회귀), 불량값 하한 보정.

## 4. 운영 가이드 (라이브는 사람이 아침에)

거래가 드문 지역/평형에서 표본을 늘리려면:

```powershell
# 6개월 수집 + 면적밴드 ±15%로 comps 확대
python run.py --source courtauction --nationwide --cash <원> --live --ym 202606 `
  --live-months 6 --area-band 0.15
```

또는 코드수정 없이 `data/sample_config.json`:

```json
{ "live_months": 6, "area_band": 0.15 }
```

주의: 밴드를 너무 넓히면(예 ±25%↑) 다른 평형이 섞여 시세 편향이 생길 수 있다.
±15% 안팎에서 시작하고, 표본수·신뢰계수 변화를 보며 조정할 것.
