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
- [x] 2026-08-24 19:4x KST 일괄 처리(12건, 2026-07-23~08-24 적체분) — 커밋 121b0bd(P-14 해소표현
  정규식→README §2), ef58422(U-01 보증금 비율→재매각 절), f9bd3c7(증분 3축→일일 새로고침),
  1a35e31(§5 기존 기술로 충분), 26be7c0+fb37aa5(점유관계 파서·미러→서빙 절), e36cff5(법인
  전체단어 판정→PII 절+연대기), 9ce2900·cf8a8cc·0825815(**README '낙찰 결과' 섹션 신설**),
  94b9d2b(게이트 10종+침묵실패 카나리→적재 안전장치). 부수 정정: DailyRefresh 'Disabled' 낡은
  상태 주석을 현행(활성·새 경로·배터리 옵션)으로 갱신. 그 이전 처리분 4건은 규칙(>10 청소)에
  따라 삭제.
- [x] 2026-08-25 13:19 KST | code-without-docs commit | files: src/courtauction_fields.py — 처리(14:1x): README §6 부분 지분 경계표에 어순반대·'지분매각' 2행 추가 + 미러 볼모 사고 경고 박스.
