# DOC_SYNC_QUEUE — 코드↔문서 동기화 대기열 (자동 적재)

> **적재 주체**: `scripts/precommit_gate.ps1` — 크롤러/판정 코드(`src/courtauction_*`, `src/molit_*`,
> `src/naver_*`, `src/building_*`, `data_gates`, `score`, `pipeline`, `matcher`, `deploy/crawl_*`)가
> README/docs 변경 없이 커밋되면 한 줄씩 쌓인다.
>
> **소비 주체**: Claude 세션. 세션/사이클 시작 시 이 파일을 확인하고, 미처리 항목이 있으면
> ① 해당 커밋들의 diff를 읽고 ② README의 해당 섹션(크롤링/권리판정/게이트)과 `docs/crawler_qa_*.md`를
> 실코드 기준으로 갱신한 뒤 ③ 항목을 `- [x]`로 체크한다. 처리 완료 항목이 10개 넘으면 삭제해 청소.
> (규칙 출처: CLAUDE.md ## 문서 동기화, 2026-07-23 도입)

<!-- 이 아래로 자동 적재 -->
- [x] 2026-07-23 11:24 KST | code-without-docs commit | files: deploy/crawl_naver.py, src/building_info.py, src/molit_bridge.py, src/molit_client.py, src/pipeline.py — 처리(2026-07-23 11:50): ruff 임포트 정렬 등 기계적 정리만이라 문서 영향 없음. (첫 자동 적재 — 게이트 정상 작동 확인)
- [x] 2026-07-23 14:32 KST | code-without-docs commit | files: src/courtauction_detail.py, src/pipeline.py, src/score.py — 처리(14:50): README §5에 3항(burden_amount_unknown → 추천 금지) 추가, 확약 미반영 한계 명기. README §5-3
- [ ] 2026-07-23 15:03 KST | code-without-docs commit | files: src/courtauction_rights.py
- [ ] 2026-07-23 16:03 KST | code-without-docs commit | files: src/courtauction_rights.py
- [ ] 2026-07-23 22:49 KST | code-without-docs commit | files: deploy/crawl_rights.py
- [ ] 2026-07-24 10:22 KST | code-without-docs commit | files: src/pipeline.py, src/score.py
- [x] 2026-07-24 13:21 KST | code-without-docs commit | files: src/courtauction_fields.py — 처리(2026-07-24 14:2x): 지분 검출(is_partial_share·maejibun) — README §6에 '부분 지분 검출' 절+경계표 추가, docs/시세_가정_명세서.md G5 신설(깨진 가정 실증·수정 기록).
- [x] 2026-07-24 13:27 KST | code-without-docs commit | files: src/score.py — 처리(2026-07-24 14:2x): market_view share_sale 폴백 차단 — README §6 '서빙 폴백 차단' 문단 + 명세서 G5-3에 함께 기술.
- [ ] 2026-07-25 12:10 KST | code-without-docs commit | files: src/courtauction_detail.py
- [ ] 2026-07-25 12:16 KST | code-without-docs commit | files: src/courtauction_detail.py
- [ ] 2026-07-25 12:45 KST | code-without-docs commit | files: deploy/crawl_rights.py
- [ ] 2026-07-26 15:07 KST | code-without-docs commit | files: src/courtauction_fields.py
- [ ] 2026-07-27 14:59 KST | code-without-docs commit | files: deploy/crawl_naver.py
- [ ] 2026-07-27 16:28 KST | code-without-docs commit | files: deploy/crawl_rights.py
- [ ] 2026-07-31 09:58 KST | code-without-docs commit | files: src/courtauction_fields.py
- [x] 2026-08-07 18:09 KST | code-without-docs commit | files: deploy/crawl_naver.py, src/naver_client.py, src/naver_store.py — 처리(2026-08-07 18:2x): 네이버 증분 갱신 4종 수정(조기종료·시간예산·crashed복구·날짜칸 가드·만기지터) + 워치독 미완주 감지 → **docs/naver-incremental.md 신설**(설계·실패이력·운영 체크리스트·env 목록). README 는 타 세션 미커밋 상태라 손대지 않고 독립 문서로 분리.
