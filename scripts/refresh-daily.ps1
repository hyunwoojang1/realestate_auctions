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
    [int]$RightsLimit = 300,   # 권리 크롤 물건 수 상한(일일캡 500 내, 보수차익 우선순 상위부터)
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

    # --- 권리 크롤(물건상세 매각물건명세서 요지) — 신규 물건이 '권리미확인'으로 영영 남는 것 방지 ---
    #     (감사 2026-07-20 F1) 종전 일일 새로고침엔 권리 크롤이 아예 없어, 신규 courtauction 물건의
    #     인수권리·대항력·기일이 수동 실행 전엔 절대 안 채워졌다(전국 24개 법원 통째 미크롤의 원인).
    #     run.py **앞**에 두어 같은 사이클의 채점(apply_rights_from_rows)이 갓 크롤한 권리를 반영한다.
    #     상위 N건(보수차익 우선), 일일캡·킬스위치(COURTAUCTION_STOP)는 CourtAuctionClient가 관리.
    #     실패해도 채점을 막지 않는다. -SkipRights 로 건너뜀(안티밴 사고 시).
    if (-not $SkipRights) {
        "--- 권리 크롤(물건상세, 상위 $RightsLimit건) ---" | Tee-Object -FilePath $LogPath -Append
        & $Python -m deploy.crawl_rights --db $DbPath --limit $RightsLimit 2>&1 | Tee-Object -FilePath $LogPath -Append
    } else {
        "--- 권리 크롤 건너뜀(-SkipRights) ---" | Tee-Object -FilePath $LogPath -Append
    }

    # --- 메인 채점(courtauction 크롤 + 국토부 시세 + 네이버 실거래 주입 + Supabase) ---
    & $Python @runArgs 2>&1 | Tee-Object -FilePath $LogPath -Append
    $code = $LASTEXITCODE
} finally {
    Pop-Location
    $ErrorActionPreference = $prevEAP
}

"----------------------------------------" | Tee-Object -FilePath $LogPath -Append
"exit : $code" | Tee-Object -FilePath $LogPath -Append
"log  : $LogPath" | Tee-Object -FilePath $LogPath -Append

exit $code
