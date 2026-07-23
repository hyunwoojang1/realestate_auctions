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
