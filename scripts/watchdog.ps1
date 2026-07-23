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
    } else {
        $results += "check1: DailyRefresh OK"
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
    Alert "serving-down" "[auction] local web serving DOWN" "No /health 200 on :8055 or :8000 - phone access is broken. Restart: scripts\start.ps1" "high"
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
