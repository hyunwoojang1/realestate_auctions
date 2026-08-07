# watchdog.ps1 - unattended health monitor (runs every 30 min via AuctionArbitrage-Watchdog task).
# Read-only checks -> pushes alerts via scripts\notify.ps1 (ntfy + harness\ALERTS.log).
# Dedup: same alert key is suppressed for 6 hours (harness\watchdog_state.json).
# Heartbeat: writes harness\WATCHDOG_LAST.txt every run (freshness signal for dashboards).
$ErrorActionPreference = "Continue"
$RepoRoot  = Split-Path -Parent $PSScriptRoot
$Notify    = Join-Path $RepoRoot "scripts\notify.ps1"
$StatePath = Join-Path $RepoRoot "harness\watchdog_state.json"
$HeartPath = Join-Path $RepoRoot "harness\WATCHDOG_LAST.txt"
$SuppressHours = 6
$results = @()

# --- dedup state ---
$state = @{}
if (Test-Path $StatePath) {
    try {
        $obj = Get-Content $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
        $obj.PSObject.Properties | ForEach-Object { $state[$_.Name] = $_.Value }
    } catch {}
}

function Alert($key, $title, $msg, $prio) {
    $script:results += "ALERT[$key] $title"
    $last = $state[$key]
    if ($last) {
        try {
            if (((Get-Date) - [datetime]$last).TotalHours -lt $SuppressHours) { return }
        } catch {}
    }
    $state[$key] = (Get-Date).ToString("o")
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Notify -Title $title -Message $msg -Priority $prio -Tags "warning" | Out-Null
}

# --- 1) DailyRefresh scheduler: disabled or stale? ---
$task = Get-ScheduledTask -TaskName "AuctionArbitrage-DailyRefresh" -ErrorAction SilentlyContinue
if ($null -eq $task) {
    $results += "check1: DailyRefresh task not found"
} elseif ($task.State -eq "Disabled") {
    Alert "dailyrefresh-disabled" "[auction] DailyRefresh is DISABLED" "Daily refresh task is disabled - site data is aging silently. Re-enable when crawl work is done: Enable-ScheduledTask AuctionArbitrage-DailyRefresh" "high"
} else {
    $info = Get-ScheduledTaskInfo -TaskName "AuctionArbitrage-DailyRefresh" -ErrorAction SilentlyContinue
    if ($info -and $info.LastRunTime -and ((Get-Date) - $info.LastRunTime).TotalHours -gt 36) {
        Alert "dailyrefresh-stale" "[auction] DailyRefresh stale >36h" ("Last run: " + $info.LastRunTime + " / result: " + $info.LastTaskResult) "high"
    } elseif ($info -and $info.LastTaskResult -ne 0) {
        # 제때 돌지만 **계속 실패로 끝나는** 경우 — 종전엔 신선도만 봐서 "OK" 로 보고했다.
        # refresh-daily.ps1 이 exit 4(낙찰보존/권리미러)·5(run.py 미러)·6(사진 도달성)을
        # $code 로 접어 내보내는데, 그 신호를 여기서 안 읽으면 워치독이 2선 방어 역할을
        # 못 한다. notify.ps1 은 푸시 실패를
        # 삼키고 exit 0 으로 끝나므로(설계), 푸시를 놓치면 이게 유일한 기록이 된다.
        # (2026-08-05 세트2 재감사)
        Alert "dailyrefresh-exit" "[auction] DailyRefresh exit != 0" (
            "Last run: " + $info.LastRunTime + " / exit: " + $info.LastTaskResult +
            "  (4=낙찰 보존 실패(run.py) 또는 권리/임차인 미러 실패(crawl_rights)" +
            " · 5=run.py 클라우드 미러 실패 · 6=사진 도달성 실패" +
            " — 원인은 evidence\refresh-*.log 의 stderr 줄로 구분)") "high"
    } else {
        $results += "check1: DailyRefresh OK"
    }
}

# --- 1b) 사이클 미완주 / 정체 감지 (2026-08-07 도입) ---
# 왜 필요한가: refresh-daily.ps1 의 운영자 알림은 스크립트 **맨 끝**(exit 직전)에 있다.
# 중간에 죽으면 그 알림이 아예 발송되지 않아 **실패가 조용해진다.**
# 실사고: 8/4~8/7 나흘 연속 [4/5] 네이버 단계에서 죽었는데 스크립트발 알림은 8/3 07:39 이후
# 0건이었다. 그동안 [5/5] 재채점이 통째로 미실행이었고, 사용자가 직접 물어볼 때까지 아무도 몰랐다.
# check1 의 LastTaskResult 만으로는 부족하다 — 267009(아직 실행중)·267014(제한시간 종료)처럼
# '실패'로 안 보이는 코드가 섞이고, 무엇보다 "어느 단계까지 갔나"를 말해주지 못한다.
# 그래서 로그의 **종료표식**('exit : N', 스크립트가 완주했을 때만 쓴다)을 직접 확인한다.
$lastLog = $null
try {
    $lastLog = Get-ChildItem (Join-Path $RepoRoot "evidence\refresh-*.log") -ErrorAction Stop |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
} catch {}
if ($null -eq $lastLog) {
    $results += "check1b: refresh 로그 없음(스킵)"
} else {
    # 로그는 Tee-Object 가 쓴 UTF-16 — Select-String 이 인코딩을 알아서 처리한다.
    $marks = @()
    try {
        $marks = @(Select-String -Path $lastLog.FullName -Pattern '^exit\s*:\s*\d+' -ErrorAction Stop)
    } catch {}
    $finished = ($marks.Count -gt 0)
    $ageMin = [math]::Round(((Get-Date) - $lastLog.LastWriteTime).TotalMinutes)
    $running = $false
    if ($task -and $task.State -eq "Running") { $running = $true }
    if ($running -and $ageMin -gt 40) {
        # 돌고는 있는데 로그가 안 늘어난다 = 어딘가에 매달림(실사고: 브라우저 크래시 후 _launch 무한대기).
        # 40분: 네이버 한 쌍이 최악(25페이지 x 2.2초)이어도 2분 내라 40분 무진행은 정상이 아니다.
        Alert "dailyrefresh-stall" "[auction] DailyRefresh 정체 - $ageMin분 무진행" (
            "State=Running 인데 로그가 " + $ageMin + "분간 안 늘어남: " + $lastLog.Name +
            " — 어딘가에 매달린 상태. 5시간 제한(PT5H)에 강제 종료되면 그 뒤 단계" +
            "([5/5] 재채점 - 사진 점검)가 통째로 안 돈다.") "high"
    } elseif ((-not $running) -and (-not $finished)) {
        # 스케줄러는 끝났는데 종료표식이 없다 = 중간에 죽었다. 이게 나흘간 조용했던 그 상태다.
        # 마지막 진행률만 뽑는다. 로그 본문은 이중 인코딩(python cp949 -> Tee UTF-16)이라
        # 한글이 깨져 나오므로 원문을 그대로 붙이면 폰 알림이 읽을 수 없게 된다.
        # '[ 64%] 330/513' 같은 ASCII 진행표시가 "어디까지 갔나"를 말해주는 핵심 정보다.
        $where = "진행률 미상"
        try {
            $hits = @(Select-String -Path $lastLog.FullName -Pattern '\[\s*\d+%\]\s*\d+/\d+' `
                        -AllMatches -ErrorAction Stop)
            if ($hits.Count -gt 0) {
                $where = $hits[-1].Matches[$hits[-1].Matches.Count - 1].Value
            }
        } catch {}
        Alert "dailyrefresh-incomplete" "[auction] DailyRefresh 미완주 - 종료표식 없음" (
            "로그: " + $lastLog.Name + " (마지막 기록 " + $ageMin + "분 전)" +
            "  마지막 진행: " + $where +
            "  => 스크립트가 'exit : N' 을 쓰기 전에 죽었다. 뒤따르는 [5/5] 재채점-사진 점검이" +
            " 실행되지 않았으므로 그날 보강분(권리-임차인-네이버 시세)이 등급-미러에 반영되지 않았다.") "high"
    } else {
        $results += "check1b: cycle OK (finished=$finished, running=$running, logAge=${ageMin}m)"
    }
}

# --- 2) last rights-crawl report verdict (within 24h) ---
$report = Join-Path $RepoRoot "harness\RIGHTS_CRAWL_REPORT.md"
if (Test-Path $report) {
    $age = ((Get-Date) - (Get-Item $report).LastWriteTime).TotalHours
    if ($age -lt 24) {
        $verdict = (Select-String -Path $report -Pattern "ABORT|HALT" -Encoding UTF8 | Select-Object -First 1)
        if ($verdict) {
            Alert "rightscrawl-fail" "[auction] rights crawl ABORT/HALT" ("Report says: " + $verdict.Line.Trim() + " - see harness\RIGHTS_CRAWL_REPORT.md") "urgent"
        } else { $results += "check2: last crawl report OK (age ${([int]$age)}h)" }
    } else { $results += "check2: no recent crawl report (age >24h, skip)" }
} else { $results += "check2: no crawl report file" }

# --- 3) local serving alive? (phone access via tailscale proxies this) ---
$ports = @(8055, 8000)
$alive = $false
foreach ($p in $ports) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$p/health" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $alive = $true; $results += "check3: serving OK on :$p"; break }
    } catch {}
}
if (-not $alive) {
    # (QA 2026-07-26) 알림만 보내던 것을 자동 재기동으로 격상 — 노트북 재부팅(12:53) 후 :8000이
    # 8시간 죽어 있어 폰에서 "물건 선택 안 됨"(홈은 SW 캐시로 떠 보이고 상세만 실패) 실사고.
    # start.ps1 을 분리 프로세스로 띄우고 재확인한다. 실패 시에만 사람 호출(high).
    Start-Process -WindowStyle Hidden -FilePath "powershell.exe" -ArgumentList `
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $RepoRoot "scripts\start.ps1")
    Start-Sleep -Seconds 12
    $revived = $false
    try {
        $r2 = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 8 -ErrorAction Stop
        if ($r2.StatusCode -eq 200) { $revived = $true }
    } catch {}
    if ($revived) {
        $results += "check3: serving was DOWN -> auto-restarted OK on :8000"
        Alert "serving-restarted" "[auction] serving auto-restarted" "Local web serving was down - watchdog restarted it on :8000 (phone access restored)." "default"
    } else {
        Alert "serving-down" "[auction] local web serving DOWN (auto-restart FAILED)" "No /health 200 on :8055 or :8000 and start.ps1 revive failed - phone access is broken. Investigate manually." "high"
    }
}

# --- 4) unpushed local commits piling up ---
try {
    $ahead = [int](git -C $RepoRoot rev-list --count origin/main..main 2>$null)
    if ($ahead -ge 25) {
        Alert "push-backlog" "[auction] $ahead unpushed commits" "Local main is $ahead commits ahead of origin - laptop is the only copy. Consider push + deploy." "default"
    } else { $results += "check4: unpushed=$ahead" }
} catch { $results += "check4: git check failed" }

# --- persist state + heartbeat ---
try { ($state | ConvertTo-Json) | Out-File -FilePath $StatePath -Encoding utf8 } catch {}
try {
    $hb = @("last run : " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) + $results
    $hb -join "`r`n" | Out-File -FilePath $HeartPath -Encoding utf8
} catch {}
exit 0
