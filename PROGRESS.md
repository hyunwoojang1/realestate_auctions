# PROGRESS — auction-arbitrage 배포준비(feat/deploy-prep)

> 매 세션 시작 시 이 파일을 먼저 읽는다. 한 번에 기능 하나. 완료 시 증거 확인 → 커밋 → versions.md 기록.
> 이번 세션 범위 = GOAL_DEPLOY.md 밤샘 배포준비 루프(코어 A·B·C + 스트레치 D·E·F).
> 그 이전(F1~F9, P1~P5, W1~W4, V1~V3, X1~X3, courtauction 크롤러)은 완료 상태이며 상세는 versions.md 참조.

## Done
밤샘 배포준비 루프에서 완료(전부 오프라인 검증 — courtauction/국토부 실서버 무호출, feat/deploy-prep 로컬 커밋).

- **[A] 정기 새로고침 스케줄러** (b987529) — `scripts/refresh-daily.ps1`(run.py --source courtauction
  --nationwide --cash --live 래퍼, AUCTION_DB·PYTHONUTF8, 로그를 evidence/), `install-scheduler.ps1`(작업
  스케줄러에 **Disabled 상태로** 등록, 활성화는 사용자 수동), `uninstall-scheduler.ps1`. run.py `--from-cache`
  오프라인 dry-run 경로 추가(캐시/fixture만, 프로덕션 캐시 미오염 *.dryrun.json 분리). 증거 scheduler_dryrun.txt.
- **[B] 프로덕션 서빙(waitress)** (97b1ff3) — `src/serve.py`(waitress.serve, debug/reloader 구조적 off),
  `src/web.py` __main__ 가드(AUCTION_DEBUG env flag로만 debug on), requirements.txt waitress>=3.0,
  Dockerfile CMD를 flask dev server → `python -m src.serve`로 교체, `scripts/start.ps1`(기본 127.0.0.1
  로컬전용, -BindAll 시에만 0.0.0.0). 증거 serving_health.txt — 실 서브프로세스 부팅 → GET /health 200,
  /api/listings 200 JSON, debug=False.
- **[C] 신뢰계수 표본 개선** (77b10d8) — 원인 규명(docs/confidence-analysis.md: 라이브 매칭 빈약 원인=
  matcher 면적밴드 과필터 + 수집 개월수 하드코딩. 신뢰계수 공식 자체는 이미 표본수에 단조 비감소). 튜닝
  외부화(src/config.py SampleConfig: live_months/area_band, 우선순위 CLI>env>JSON>기본값). run.py
  `--live-months`/`--area-band`. 증거 confidence_samples.txt.
- **[D] 권리필드 파서 뼈대** (스트레치) — `src/courtauction_rights.py`(물건상세 3문서→인수금액/특수권리/
  대항력/점유유형/감정가 추출, detector·apply_rights 불변패턴·gate_reasons). fixture 5종. 증거 rights_parser.txt.
- **[E] 시세유형 확대** (스트레치) — `src/molit_extra_client.py`(단독다가구 sh·상업업무 nrg·토지 land
  endpoint + 파서), `src/building_register_client.py`(건축물대장 표제부: 노후도·위반건축물). fixture 4종.
  증거 molit_types.txt.
- **[F] 터널 접속 가이드** (스트레치, d774a5c) — `docs/remote-access.md`(Tailscale 사설VPN / Cloudflare
  Tunnel 두 방식, 바인드주소·방화벽·보안·트러블슈팅), `scripts/check-tunnel.ps1`(순수 로컬 진단, 외부호출 0).
  증거 tunnel_guide.txt.

- **무회귀/린트**: `pytest -q` → **162 passed**(밤샘 시작 120대 → 신규 테스트 누적), `ruff check .` → 클린.
- **평가자**: 코어 A·B·C 신선-컨텍스트 패널 2인 모두 PASS. 스트레치 D·E·F PASS.

## In progress
- (없음 — 밤샘 배포준비 루프 A·B·C·D·E·F 전부 PASS·로컬 커밋 완료. 루프 종료.)

## Next
**자율 성장 사이클 가동 중** (2026-07-02~, harness/LOOP.md): 탐색 사이클 #1 완료 —
레퍼런스 2곳(지지옥션·탱크옥션) 분석 → BACKLOG B1~B5 등록. 다음 = 구현 사이클(B4 통계 페이지부터).

이전 배포준비 루프의 잔여 **사람만 할 수 있는 작업**(무인 불가):
- **push** — `feat/deploy-prep`(9dc9add~d774a5c 6커밋) 아침 리뷰 후 push. **밤샘 중 push 안 함(정책).**
- **라이브 1회 검증** — 통제된 단일 라이브 실행으로 D 권리파서(물건상세 HTML)·E 확장 실거래 XML/건축물대장
  실응답 태그 구조 확인 → 파서 정규식/키워드 실데이터 보강, matcher·score에 배선.
- **터널 라이브** — Tailscale 또는 cloudflared 설치 후 폰 접속 확인(check-tunnel.ps1로 사전 진단).
- **스케줄러 활성화** — 이용약관 확인 후 `Enable-ScheduledTask`로 매일 새벽 새로고침 켜기(현재 Disabled 등록).
- **후속 튜닝** — 라이브 표본수·신뢰계수 분포 확인 후 area_band/live_months 실튜닝.

## Notes
- 환경: Windows 11, Python 3.14.5(.venv). 실행 시 `PYTHONUTF8=1` 권장. PS5.1 한글 파싱 위해 scripts/*.ps1은 UTF-8 BOM.
- 브랜치: `feat/deploy-prep`. 밤샘 정책 = build/test는 오프라인(캐시·fixture)만, 실서버 무호출. push는 아침에.
- 밤샘 정지조건(GOAL_DEPLOY): 각 코어 빌더↔평가자 최대 3회, AGENT_STOP 파일 있으면 즉시 중단. 이번 세션은
  AGENT_STOP 없이 A·B·C·D·E·F 전부 PASS로 정상 종료.
- 명령: 테스트 `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q` / 린트 `.venv/Scripts/python.exe -m ruff check .`
- 이전 단계 요약: 코어 엔진(F1~F9)·프로덕션 강화(P1~P5)·사이트화(W1~W4)·검증알림(V1~V3)·정확도운영(X1~X3)·
  courtauction 실경매 크롤러+전국샤딩+웹서빙 전부 완료(main 이전, push fac2265). 상세는 versions.md.
