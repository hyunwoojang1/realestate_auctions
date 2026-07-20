<#
.SYNOPSIS
  권리 크롤 자동 실행: 사전 검수 → 권리 크롤 → 사후 검수 → 푸시·배포 (Default-FAIL 게이트).
  절차 상세는 harness/RIGHTS_CRAWL_RUNBOOK.md 참조. 1회성 예약작업이 호출.
.PARAMETER Limit
  권리 크롤 물건 수 상한(기본 500, 일일캡 내).
.PARAMETER SkipDeploy
  Vercel 배포 생략(크롤·검수·푸시까지만).
.PARAMETER WaitMinutes
  Phase 0에서 다른 크롤이 돌면 최대 이만큼 대기(기본 0=대기 안 함).
#>
[CmdletBinding()]
param(
    [int]$Limit = 500,
    [switch]$SkipDeploy,
    [int]$WaitMinutes = 0
)

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python   = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$DbPath   = Join-Path $RepoRoot "auction.db"
$Stamp    = Get-Date -Format "yyyyMMdd-HHmmss"
$EvidDir  = Join-Path $RepoRoot "evidence"
if (-not (Test-Path $EvidDir)) { New-Item -ItemType Directory -Path $EvidDir | Out-Null }
$LogPath  = Join-Path $EvidDir "rights-crawl-$Stamp.log"
$Report   = Join-Path $RepoRoot "harness\RIGHTS_CRAWL_REPORT.md"
$env:PYTHONUTF8 = "1"

function Log($m) {
    $line = "{0}  {1}" -f (Get-Date -Format "HH:mm:ss"), $m
    $line | Tee-Object -FilePath $LogPath -Append
}
function RunPy($pyargs) {   # .venv python 실행, exit code 반환
    & $Python @pyargs 2>&1 | Tee-Object -FilePath $LogPath -Append
    return $LASTEXITCODE
}
function Count($table) {
    $v = & $Python -c "from src import store;c=store.connect(r'$DbPath');print(c.execute('select count(*) from $table').fetchone()[0])" 2>$null
    return [int]($v | Select-Object -Last 1)
}

Set-Location $RepoRoot
Log "=== 권리 크롤 자동실행 시작 (limit=$Limit, skipDeploy=$SkipDeploy) ==="
$result = [ordered]@{ started=(Get-Date -Format "yyyy-MM-dd HH:mm"); phase1=""; crawl_exit=""; phase3=""; rights_before=0; rights_after=0; deployed="no"; verdict="" }

# ---------- Phase 0: 리스트 크롤 종료 확인 ----------
if (Test-Path (Join-Path $RepoRoot "COURTAUCTION_STOP")) {
    Log "[Phase0] COURTAUCTION_STOP 존재 — 킬스위치 켜짐. 중단."
    $result.verdict = "ABORT: kill-switch"
    ""; break
}
$waited = 0
while ($WaitMinutes -gt 0 -and $waited -lt $WaitMinutes) {
    $busy = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -match "crawl_naver|run.py|--nationwide" }
    if (-not $busy) { break }
    Log "[Phase0] 다른 크롤 진행 중 — 60초 대기 ($waited/$WaitMinutes분)"
    Start-Sleep -Seconds 60; $waited++
}
Log "[Phase0] 진행 조건 충족."

# ---------- Phase 1: 사전 감사·검수 (게이트) ----------
Log "[Phase1] 사전 검수 시작"
$pre_ok = $true
Log "[Phase1] pytest ..."
if ((RunPy @("-m","pytest","-q")) -ne 0) { $pre_ok = $false; Log "[Phase1] pytest FAIL" }
Log "[Phase1] ruff (비차단) ..."
RunPy @("-m","ruff","check","src","deploy") | Out-Null
Log "[Phase1] data_gates ..."
$gate_pre = RunPy @("-c","from src import store,data_gates;import sys;c=store.connect(r'$DbPath');g=data_gates.run_gates(c);print(data_gates.report(g));sys.exit(0 if data_gates.all_pass(g) else 1)")
if ($gate_pre -ne 0) { $pre_ok = $false; Log "[Phase1] data_gates FAIL" }
$result.rights_before = Count "listing_rights"
Log "[Phase1] listing_rights before = $($result.rights_before)"
$result.phase1 = if ($pre_ok) { "PASS" } else { "FAIL" }

if (-not $pre_ok) {
    Log "[Phase1] 사전 검수 실패 — 크롤/배포 안 함."
    $result.verdict = "ABORT: 사전검수 FAIL (크롤 안 함)"
} else {
    # ---------- Phase 2: 권리 크롤 ----------
    Log "[Phase2] crawl_rights --limit $Limit ..."
    $result.crawl_exit = RunPy @("-m","deploy.crawl_rights","--db",$DbPath,"--limit","$Limit")
    Log "[Phase2] crawl_rights exit = $($result.crawl_exit)  (0=정상·2=차단·3=실패율과다)"

    # ---------- Phase 3: 사후 감사·검수 (게이트) ----------
    Log "[Phase3] 사후 검수 시작"
    $post_ok = $true
    $result.rights_after = Count "listing_rights"
    $delta = $result.rights_after - $result.rights_before
    Log "[Phase3] listing_rights after = $($result.rights_after) (델타 +$delta)"
    if ($delta -le 0) { $post_ok = $false; Log "[Phase3] 권리 증가 없음 — FAIL" }
    if ([int]$result.crawl_exit -ne 0) { $post_ok = $false; Log "[Phase3] 크롤 exit 비정상 — FAIL" }
    Log "[Phase3] data_gates ..."
    $gate_post = RunPy @("-c","from src import store,data_gates;import sys;c=store.connect(r'$DbPath');g=data_gates.run_gates(c);print(data_gates.report(g));sys.exit(0 if data_gates.all_pass(g) else 1)")
    if ($gate_post -ne 0) { $post_ok = $false; Log "[Phase3] data_gates FAIL" }
    Log "[Phase3] pytest 회귀 ..."
    if ((RunPy @("-m","pytest","-q")) -ne 0) { $post_ok = $false; Log "[Phase3] pytest FAIL" }
    $result.phase3 = if ($post_ok) { "PASS" } else { "FAIL" }

    # ---------- Phase 4: 푸시 & 배포 ----------
    if ($post_ok) {
        Log "[Phase4] git push ..."
        git push origin main 2>&1 | Tee-Object -FilePath $LogPath -Append
        if (-not $SkipDeploy) {
            $bash = @("C:\Program Files\Git\bin\bash.exe","C:\Program Files (x86)\Git\bin\bash.exe") |
                    Where-Object { Test-Path $_ } | Select-Object -First 1
            if ($bash) {
                Log "[Phase4] deploy_prod.sh via $bash ..."
                & $bash "scripts/deploy_prod.sh" 2>&1 | Tee-Object -FilePath $LogPath -Append
                if ($LASTEXITCODE -eq 0) { $result.deployed = "yes"; Log "[Phase4] 배포 성공" }
                else { $result.deployed = "FAILED"; Log "[Phase4] 배포 실패(exit $LASTEXITCODE) — 수동 배포 필요" }
            } else {
                $result.deployed = "skip(no-bash)"; Log "[Phase4] Git Bash 못 찾음 — 배포 스킵, 수동 배포 필요"
            }
        } else { $result.deployed = "skip(-SkipDeploy)"; Log "[Phase4] -SkipDeploy — 배포 생략" }
        $result.verdict = "OK: 크롤+검수 PASS, 배포=$($result.deployed)"
    } else {
        $result.verdict = "HALT: 사후검수 FAIL — 배포 차단(크롤분은 로컬·Supabase 유지)"
        Log "[Phase4] 사후 검수 실패 — 배포 안 함."
    }
}

# ---------- 리포트 ----------
$md = @"
# 권리 크롤 자동실행 리포트

- 실행: $($result.started) ~ $(Get-Date -Format 'yyyy-MM-dd HH:mm') KST
- **판정: $($result.verdict)**

| 단계 | 결과 |
|------|------|
| Phase1 사전검수 | $($result.phase1) |
| Phase2 크롤 exit | $($result.crawl_exit) (0=정상·2=차단·3=실패율과다) |
| Phase3 사후검수 | $($result.phase3) |
| listing_rights | $($result.rights_before) → $($result.rights_after) |
| 배포 | $($result.deployed) |

- 로그: $LogPath
- 절차: harness/RIGHTS_CRAWL_RUNBOOK.md
"@
$md | Out-File -FilePath $Report -Encoding utf8
Log "=== 종료: $($result.verdict) ==="
Log "리포트: $Report"
