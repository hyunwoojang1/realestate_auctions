# REJECTED.md — 오버플래그(오탐) 기록 (append-only)

> 감사·진단이 "문제"라 지목했으나 **적대검증에서 재현 실패 → 수정 안 함**. 사용자 요청("너무 오바해서
> 문제 없는 걸 문제라 한 건 아닌지 보고")에 따라 기각 사유를 남긴다.

## 2026-07-16 00:40 KST — ❌ [기각] "사진 미표시 = 클라우드 photo_url 컬럼 부재→PostgREST 400"
- **주장(진단 wf_a6fa366d-db9, severity=high, confidence=confirmed)**: Phase5가 `store_rest.fetch_photos`의
  select에 `photo_url`을 추가했는데 클라우드 `auction_listing_photos`에 컬럼을 안 만들어 400→`except:return []`가
  삼켜 사진이 전부 안 뜬다. 수정안=Supabase ALTER ADD COLUMN photo_url.
- **적대검증(라이브)**: 전부 반증됨 —
  1. `GET .../auction_listing_photos?select=photo_url,thumb_b64,seq` → **HTTP 200**(400 아님). 컬럼 존재.
  2. `photo_url` 실제 채워짐(Storage 공개URL), 빈 photo_url **0행/8,076행**.
  3. 샘플 5건 photo_url **전부 200 image/jpeg**(버킷 public 정상).
  4. **배포 사이트**(`/property/2025타경9153`) 직접 fetch → 사진 6장 정상 렌더, 에러 없음.
- **판정**: 진단의 핵심 가정("컬럼 없음")이 거짓. DB·백엔드·배포 프론트 모두 정상. **DB ALTER 미실행(불필요).**
- **교훈**: SQL 파일/`git show`만 보고 클라우드 상태를 단정한 오류. 스키마는 라이브로 확인해야 함(진단관도
  verifyHow에 "라이브 확인" 적어뒀으나 confidence를 confirmed로 과신).

### ↳ 그럼 사용자가 본 "사진 안 뜸"의 진짜 원인(재진단, 재현됨)
- 전체 8,245건 중 **사진 보유 7%(647건)** — 93%는 크롤 미수집이라 사진이 없음. 그런데 `detail.html`의
  `{% if photos %}`가 **빈 상태 안내 없이 사진 블록을 통째로 숨김** → 사용자엔 "안 뜬다"로 보임.
- 추천 대상(evaluable 645건)은 82%가 사진 보유, 상위 10건 전부 보유 → 추천 물건을 봤다면 사진이 떴을 것.
- ⇒ 진짜 개선점(재현됨) = **①사진 없는 물건에 명시적 빈 상태('사진 미수집') ②이미지 로딩 힌트(loading/치수)**.
  (DB 버그 아님 — 이건 GOAL_UX B1을 이 방향으로 수정)
