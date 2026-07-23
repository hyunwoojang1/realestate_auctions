# notify.ps1 - unified operator notification (ntfy push + local alert log).
# Usage: .\scripts\notify.ps1 -Title "crawl done" -Message "rights +492" [-Priority high] [-Tags "warning"]
# Never throws, always exits 0: a broken notification must not break the pipeline that calls it.
# Config: harness\notify.json  {"enabled": true, "ntfy_topic": "..."}
#   - topic empty or enabled=false -> log-only mode (harness\ALERTS.log still written).
#   - phone setup: install the ntfy app, subscribe to the topic in notify.json.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Title,
    [Parameter(Mandatory = $true)][string]$Message,
    [string]$Priority = "default",   # min | low | default | high | urgent
    [string]$Tags = ""               # comma-separated ntfy tags, e.g. "warning,rotating_light"
)

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogPath  = Join-Path $RepoRoot "harness\ALERTS.log"
$CfgPath  = Join-Path $RepoRoot "harness\notify.json"

# 1) Always append to the local alert log (UTF-8), even when push is disabled.
try {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$stamp] [$Priority] $Title :: $Message"
    $line | Out-File -FilePath $LogPath -Append -Encoding utf8
} catch {}

# 2) Push via ntfy JSON publish (UTF-8 safe for Korean text) when configured.
try {
    if (-not (Test-Path $CfgPath)) { exit 0 }
    $cfg = Get-Content $CfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $cfg.enabled) { exit 0 }
    $topic = [string]$cfg.ntfy_topic
    if ([string]::IsNullOrWhiteSpace($topic)) { exit 0 }

    # ntfy JSON publish requires priority as an integer 1-5 (string -> HTTP 400).
    $prioMap = @{ min = 1; low = 2; default = 3; high = 4; urgent = 5 }
    $prioNum = $prioMap[$Priority.ToLower()]
    if (-not $prioNum) { $prioNum = 3 }
    $payload = @{ topic = $topic; title = $Title; message = $Message; priority = $prioNum }
    if ($Tags) { $payload.tags = @($Tags -split ",") }
    $json  = $payload | ConvertTo-Json -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    Invoke-RestMethod -Method Post -Uri "https://ntfy.sh" -Body $bytes -ContentType "application/json; charset=utf-8" -TimeoutSec 10 | Out-Null
} catch {
    try { "[notify] push failed: $($_.Exception.Message)" | Out-File -FilePath $LogPath -Append -Encoding utf8 } catch {}
}
exit 0
