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
function RunPy($pyargs) {   # .venv python 실행, exit code만 반환(출력은 로그로, 반환값 오염 금지)
    # (버그수정 2026-07-21) 종전엔 Tee 통과출력이 함수 반환값에 섞여 배열이 돼 -ne 0 검사가 항상
    # 참(FAIL)이 됐다(618 passed인데 pytest FAIL 오판). Out-Null로 통과출력을 버려 exit code만 반환.
    & $Python @pyargs 2>&1 | Tee-Object -FilePath $LogPath -Append | Out-Null
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
# (2026-07-23 감사수정) exit code 체계: 0=OK / 10=사전검수FAIL / 20=사후검수FAIL / 30=푸시·배포실패 / 40=킬스위치.
# 종전엔 어떤 실패든 exit 0이라 작업스케줄러 LastTaskResult가 항상 성공으로 찍혔고(7/21 ABORT 실증),
# 킬스위치 경로는 `break`로 즉사해 리포트조차 안 남았다. 이제 모든 경로가 리포트+알림+exit code를 남긴다.
$exitCode = 0
$killed = Test-Path (Join-Path $RepoRoot "COURTAUCTION_STOP")
if ($killed) {
    Log "[Phase0] COURTAUCTION_STOP 존재 — 킬스위치 켜짐. 중단."
    $result.verdict = "ABORT: kill-switch"
    $result.phase1 = "SKIP"
    $exitCode = 40
}
if (-not $killed) {
$waited = 0
while ($WaitMinutes -gt 0 -and $waited -lt $WaitMinutes) {
    $busy = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -match "crawl_naver|crawl_rights|run.py|--nationwide" }
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
    $exitCode = 10
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
        # (2026-07-23 감사수정) push 실패가 'OK'로 위장되던 것 차단 — exit code 검사.
        if ($LASTEXITCODE -ne 0) {
            Log "[Phase4] git push 실패(exit $LASTEXITCODE) — 수동 push 필요"
            $result.deployed = "push FAILED"
            $exitCode = 30
        }
        if ($exitCode -ne 30 -and -not $SkipDeploy) {
            $bash = @("C:\Program Files\Git\bin\bash.exe","C:\Program Files (x86)\Git\bin\bash.exe") |
                    Where-Object { Test-Path $_ } | Select-Object -First 1
            if ($bash) {
                Log "[Phase4] deploy_prod.sh via $bash ..."
                & $bash "scripts/deploy_prod.sh" 2>&1 | Tee-Object -FilePath $LogPath -Append
                if ($LASTEXITCODE -eq 0) { $result.deployed = "yes"; Log "[Phase4] 배포 성공" }
                else { $result.deployed = "FAILED"; $exitCode = 30; Log "[Phase4] 배포 실패(exit $LASTEXITCODE) — 수동 배포 필요" }
            } else {
                $result.deployed = "skip(no-bash)"; Log "[Phase4] Git Bash 못 찾음 — 배포 스킵, 수동 배포 필요"
            }
        } elseif ($exitCode -ne 30) { $result.deployed = "skip(-SkipDeploy)"; Log "[Phase4] -SkipDeploy — 배포 생략" }
        if ($exitCode -eq 30) {
            $result.verdict = "HALT: 크롤+검수 PASS, 푸시/배포 실패($($result.deployed)) — 수동 조치 필요"
        } else {
            $result.verdict = "OK: 크롤+검수 PASS, 배포=$($result.deployed)"
        }
    } else {
        $result.verdict = "HALT: 사후검수 FAIL — 배포 차단(크롤분은 로컬·Supabase 유지)"
        $exitCode = 20
        Log "[Phase4] 사후 검수 실패 — 배포 안 함."
    }
}
}  # end if (-not $killed)

# ---------- 리포트 ----------
# (2026-07-23 감사수정) 미실행 단계는 '—'로 표기 — 종전엔 Phase3 미실행 시 'N → 0'으로 찍혀
# 비개발자에게 '데이터 유실'로 오인됐다(7/21 실증).
$p2Disp = if ("$($result.crawl_exit)" -ne "") { "$($result.crawl_exit) (0=정상·2=차단·3=실패율과다)" } else { "— (미실행)" }
$p3Disp = if ($result.phase3) { $result.phase3 } else { "— (미실행)" }
$raDisp = if ($result.phase3) { "$($result.rights_after)" } else { "— (미실행)" }
$md = @"
# 권리 크롤 자동실행 리포트

- 실행: $($result.started) ~ $(Get-Date -Format 'yyyy-MM-dd HH:mm') KST
- **판정: $($result.verdict)** (exit $exitCode)

| 단계 | 결과 |
|------|------|
| Phase1 사전검수 | $($result.phase1) |
| Phase2 크롤 exit | $p2Disp |
| Phase3 사후검수 | $p3Disp |
| listing_rights | $($result.rights_before) → $raDisp |
| 배포 | $($result.deployed) |

- 로그: $LogPath
- 절차: harness/RIGHTS_CRAWL_RUNBOOK.md
"@
$md | Out-File -FilePath $Report -Encoding utf8
Log "=== 종료: $($result.verdict) (exit $exitCode) ==="
Log "리포트: $Report"

# ---------- 운영자 알림 (2026-07-23 도입: 세션 없이도 결과가 폰/ALERTS.log에 남는다) ----------
$prio = if ($exitCode -eq 0) { "default" } else { "urgent" }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $RepoRoot "scripts\notify.ps1") `
    -Title "[auction] rights crawl exit=$exitCode" `
    -Message "$($result.verdict) | phase1=$($result.phase1) crawl=$($result.crawl_exit) phase3=$p3Disp rights=$($result.rights_before)->$raDisp deploy=$($result.deployed)" `
    -Priority $prio | Out-Null

exit $exitCode
