<#
.SYNOPSIS
  auction-arbitrage 정기 새로고침 래퍼 — 전국 courtauction 실매물 + 국토부 시세 매칭으로 auction.db 갱신.

.DESCRIPTION
  Windows 작업스케줄러(install-scheduler.ps1)가 매일 호출하는 진입점.
  - 평소(프로덕션): -Live 로 courtauction 전국 샤딩 크롤 + 국토부 라이브 시세.
  - 검증(오프라인): -FromCache 로 실크롤 대신 저장된 캐시/샘플 fixture 사용(네트워크 호출 0).
  로그는 evidence\refresh-YYYYMMDD-HHmmss.log 에 tee 된다.

  .venv 파이썬을 절대경로로 호출하므로 작업 디렉터리와 무관하게 동작한다.

.PARAMETER Live
  국토부 라이브 시세를 사용(MOLIT_API_KEY 필요). FromCache 와 동시 지정 시 FromCache 가 우선(오프라인 강제).

.PARAMETER FromCache
  오프라인 dry-run. courtauction 실크롤 대신 data\courtauction_cache.json(없으면 샘플 fixture)로 파이프라인 실행.

.PARAMETER Cash
  가용현금(원) 상한. 최저가<=현금 매물만.

.PARAMETER Ym
  국토부 조회 연월 YYYYMM. 미지정 시 run.py 가 전월로 자동.

.EXAMPLE
  # 프로덕션(약관 확인 후 스케줄러가 호출): 전국 라이브
  .\scripts\refresh-daily.ps1 -Live -Cash 100000000

.EXAMPLE
  # 오프라인 검증(밤샘/CI): 네트워크 호출 0
  .\scripts\refresh-daily.ps1 -FromCache -Cash 100000000
#>
[CmdletBinding()]
param(
    [switch]$Live,
    [switch]$FromCache,
    [long]$Cash = 1000000000,  # 사용자 결정(2026-07-20): 전국 · 현금 10억 상한(5억→상향)
    [int]$LiveMonths = 24,     # 국토부 실거래 수집창(개월). 캐시(molit_trades.db)로 닫힌 달은 1회만 호출(깊이↑=비용동일, 열린 2개월만 매번).
    [string]$Ym = "",
    [string]$DbPath = "",
    [switch]$SkipNaver,        # 네이버 증분 단계 건너뛰기(안티밴 사고 시)
    [switch]$SkipRights,       # 권리(물건상세) 크롤 건너뛰기(안티밴 사고 시)
    [switch]$SkipTenants,      # 현황조사서(B-2) 일일 백필 건너뛰기(안티밴 사고 시)
    [int]$RightsLimit = 300,   # 권리 크롤 물건 수 상한(보수차익 우선순 상위부터, 신규+기일갱신 재보강)
    [int]$TenantsLimit = 120,  # 현황조사서 일일 물건 수 상한(물건당 2요청 → ~240요청).
                               # 우선순위: 추천+빈요지(P-08) → 권리미확인. tenant_checks 로 미시도만.
    [int]$DetailCap = 800,     # 상세크롤 일일 요청예산(BUDGET_FILE 공유: 권리+현황조사서 합산 상한)
    [int]$NaverStaleDays = 14, # 네이버 실거래 증분 신선도 기준(일). 이보다 오래된 쌍만 재수집
    [int]$MaxPages = 120       # courtauction 시도당 페이지 상한(1p=40건). 25→120(2026-07-20):
                               # 현금 10억 확장으로 대형 시도가 4,515행(113p)까지 실측 — 120p로 전수.
                               # totalCnt 도달 시 조기종료라 실제 요청수는 필요분만 쓴다.
                               # 요청예산은 pipeline이 시도수×(페이지+2)+100으로 자동 산출(부분수집 방지).
)

$ErrorActionPreference = "Stop"

# --- 경로 확정: 스크립트 기준 리포지토리 루트 ---
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python   = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$RunPy    = Join-Path $RepoRoot "run.py"
$EvidDir  = Join-Path $RepoRoot "evidence"

if (-not $DbPath) { $DbPath = Join-Path $RepoRoot "auction.db" }
if (-not (Test-Path $Python)) { throw "venv 파이썬 없음: $Python (먼저 .venv 생성 필요)" }
if (-not (Test-Path $EvidDir)) { New-Item -ItemType Directory -Path $EvidDir | Out-Null }

$Stamp   = Get-Date -Format "yyyyMMdd-HHmmss"
$LogPath = Join-Path $EvidDir "refresh-$Stamp.log"

$env:PYTHONUTF8 = "1"
$env:AUCTION_DB = $DbPath

# --- run.py 인자 구성 ---
$runArgs = @("run.py", "--source", "courtauction", "--db", $DbPath, "--cash", "$Cash",
             "--max-pages", "$MaxPages")

# 물건 소스: 캐시(재크롤X) vs 전국 실크롤. --live/--ym는 국토부 시세라 둘 다에 적용(독립).
if ($FromCache) { $runArgs += "--from-cache" } else { $runArgs += "--nationwide" }
if ($Live) { $runArgs += @("--live", "--live-months", "$LiveMonths") }
if ($Ym)   { $runArgs += @("--ym", $Ym) }

$mode = if ($FromCache) { "OFFLINE(from-cache)" } elseif ($Live) { "LIVE(nationwide)" } else { "SAMPLE-PRICE(nationwide)" }

"=== auction-arbitrage refresh-daily ===" | Tee-Object -FilePath $LogPath
"time : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" | Tee-Object -FilePath $LogPath -Append
"mode : $mode"      | Tee-Object -FilePath $LogPath -Append
"db   : $DbPath"        | Tee-Object -FilePath $LogPath -Append
"cmd  : $Python $($runArgs -join ' ')" | Tee-Object -FilePath $LogPath -Append
"----------------------------------------" | Tee-Object -FilePath $LogPath -Append

# 중요: 파이썬 로그는 stderr로 나온다. PS5.1은 `2>&1`로 병합된 stderr 각 줄을 ErrorRecord로
# 감싸고, $ErrorActionPreference='Stop'이면 그 첫 줄(예: MOLIT 502 재시도 경고)이 종료오류로
# 승격돼 크롤을 통째로 죽인다(스케줄러가 매일 첫 경고에 실패). 네이티브 호출 동안만 Continue로.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
Push-Location $RepoRoot
try {
    # --- 네이버 증분 (main 채점 전) — 어제 매물 기준 신규 매칭 + 오래된 쌍 실거래 갱신 ---
    #     실패해도 채점을 막지 않는다(네이버는 보조 시세). 신규 물건은 이번 채점 후 다음날 매칭됨(1일 지연 허용).
    #     -SkipNaver 로 건너뛸 수 있다(안티밴 사고 시).
    if (-not $SkipNaver) {
        "--- 네이버 Phase A(신규 매칭) ---" | Tee-Object -FilePath $LogPath -Append
        & $Python -m deploy.crawl_naver --db $DbPath 2>&1 | Tee-Object -FilePath $LogPath -Append
        "--- 네이버 Phase B(증분 실거래 >$NaverStaleDays일) ---" | Tee-Object -FilePath $LogPath -Append
        & $Python -m deploy.crawl_naver --backfill-real --incremental --stale-days $NaverStaleDays 2>&1 | Tee-Object -FilePath $LogPath -Append
    } else {
        "--- 네이버 증분 건너뜀(-SkipNaver) ---" | Tee-Object -FilePath $LogPath -Append
    }

    # ══ 증분 파이프라인(2026-07-23 재배선): 발견 → 보강 → 재채점 ══
    # 법원엔 '변경 피드' API가 없어 증분은 diff 로 만든다: [1] 리스트 전량 스윕(싸다)이 신규·소멸을
    # 발견하고, [2][3] 상세 보강(비싸다)은 diff 가 고른 신규+변경만, [4] 재채점이 같은 날 반영한다.
    # 종전엔 권리 크롤이 run.py **앞**이라 오늘 발견된 신규 물건은 내일에야 권리가 붙었다(1일 지연).

    # --- [1/4] 발견+1차 채점: courtauction 리스트 전량 + 국토부 시세 + Supabase ---
    & $Python @runArgs 2>&1 | Tee-Object -FilePath $LogPath -Append
    $code = $LASTEXITCODE

    # --- [2/4] 권리 보강(물건상세 명세서 요지): 오늘 발견된 신규 + 기일갱신(유찰 새 회차) 재보강 ---
    #     상위 N건(보수차익 우선), 일일캡·킬스위치(COURTAUCTION_STOP)는 CourtAuctionClient가 관리.
    #     실패해도 재채점을 막지 않는다. -SkipRights 로 건너뜀(안티밴 사고 시).
    if (-not $SkipRights) {
        "--- [2/4] 권리 크롤(신규+재보강, 상위 $RightsLimit건) ---" | Tee-Object -FilePath $LogPath -Append
        & $Python -m deploy.crawl_rights --db $DbPath --limit $RightsLimit --cap $DetailCap 2>&1 | Tee-Object -FilePath $LogPath -Append
        # (D3 2026-07-22) 권리크롤 종료코드를 **같은 블록에서 즉시** 캡처 — 종전엔 뒤이은 run.py가
        # $LASTEXITCODE를 덮어써 차단(2)·드리프트/실패(3) 승격이 무시됐다(안티밴·침묵실패 방어 무력).
        $rightsCode = $LASTEXITCODE
        if ($rightsCode -eq 2) {
            "[!] 권리크롤 차단/상한(exit 2) — 정부사이트 밴 의심. 다음 사이클 -SkipRights 권장, 조사 필요." | Tee-Object -FilePath $LogPath -Append
        } elseif ($rightsCode -eq 3) {
            "[!] 권리크롤 실패율/스키마 드리프트 과다(exit 3) — 파서-응답 불일치. 파서 점검 필요." | Tee-Object -FilePath $LogPath -Append
        }
    } else {
        "--- [2/4] 권리 크롤 건너뜀(-SkipRights) ---" | Tee-Object -FilePath $LogPath -Append
    }

    # --- [3/4] 현황조사서(B-2) 일일 백필 — 대항력 여지 판정 원천 (P-08/P-10 배선) ---
    #     우선순위: 추천등급+인수권리란 빈칸(초록으로 팔리는데 검증 원천이 막혀있던 클래스) →
    #     권리미확인. tenant_checks 마커로 미시도 물건만(빈 결과도 기록 → 매일 재크롤 안 함).
    #     요청예산은 BUDGET_FILE 로 [2]와 합산 관리(-cap $DetailCap).
    if (-not $SkipRights -and -not $SkipTenants) {
        "--- [3/4] 현황조사서 백필(상위 $TenantsLimit건, 물건당 2요청) ---" | Tee-Object -FilePath $LogPath -Append
        & $Python -m deploy.crawl_rights --db $DbPath --tenants-backfill --limit $TenantsLimit --cap $DetailCap 2>&1 | Tee-Object -FilePath $LogPath -Append
        $tenantsCode = $LASTEXITCODE
        if ($tenantsCode -eq 2) {
            "[!] 현황조사서 차단/상한(exit 2) — 예산 소진 또는 밴 의심(수집분은 저장됨)." | Tee-Object -FilePath $LogPath -Append
        } elseif ($tenantsCode -eq 3) {
            "[!] 현황조사서 실패율/드리프트 과다(exit 3) — 파서 점검 필요." | Tee-Object -FilePath $LogPath -Append
        }
    } else {
        "--- [3/4] 현황조사서 백필 건너뜀 ---" | Tee-Object -FilePath $LogPath -Append
    }

    # --- [4/4] 재채점 — 오늘 보강분(권리·임차인)을 같은 날 등급·미러에 반영 ---
    #     --from-cache: [1]이 방금 저장한 캐시 재사용(courtauction 재크롤 0). 국토부는 캐시DB로
    #     닫힌 달 0호출. FromCache 모드(오프라인 검증)에선 [1]과 동일 실행이라 생략.
    if (-not $FromCache) {
        "--- [4/4] 재채점(--from-cache, 보강분 반영) ---" | Tee-Object -FilePath $LogPath -Append
        $rescoreArgs = @("run.py", "--source", "courtauction", "--db", $DbPath, "--cash", "$Cash",
                         "--max-pages", "$MaxPages", "--from-cache")
        if ($Live) { $rescoreArgs += @("--live", "--live-months", "$LiveMonths") }
        if ($Ym)   { $rescoreArgs += @("--ym", $Ym) }
        & $Python @rescoreArgs 2>&1 | Tee-Object -FilePath $LogPath -Append
        $rescoreCode = $LASTEXITCODE
        if ($code -eq 0 -and $rescoreCode -ne 0) { $code = $rescoreCode }  # 미완 사이클을 가시화
    }
} finally {
    Pop-Location
    $ErrorActionPreference = $prevEAP
}

"----------------------------------------" | Tee-Object -FilePath $LogPath -Append
"exit : $code" | Tee-Object -FilePath $LogPath -Append
"log  : $LogPath" | Tee-Object -FilePath $LogPath -Append

# --- 운영자 알림 (2026-07-23 도입) — 실패=urgent, 권리크롤 차단/드리프트=high, 성공=min(무음성) ---
$rightsNote = ""
if (-not $SkipRights -and (Test-Path variable:rightsCode)) { $rightsNote = " rights_exit=$rightsCode" }
if ((Test-Path variable:tenantsCode)) { $rightsNote += " tenants_exit=$tenantsCode" }
$prio = "min"
if ($code -ne 0) { $prio = "urgent" }
elseif ($rightsNote -match "exit=[23]") { $prio = "high" }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $RepoRoot "scripts\notify.ps1") `
    -Title "[auction] daily refresh exit=$code" `
    -Message "mode=$mode$rightsNote db=$(Split-Path $DbPath -Leaf) log=$(Split-Path $LogPath -Leaf)" `
    -Priority $prio | Out-Null

exit $code
