# warm-ping.ps1 (2026-07-27) - keeps the Vercel function instance + its in-process cache alive.
#
# Why: serving reads Supabase REST. A cold instance has an empty _cache, so the first home
# request pages the full datasets (listings + rights + naver) before it can render. Measured
# 25s cold vs 0.31s warm. Vercel scales the function to zero after a few idle minutes, and
# personal traffic (a few phone visits a day) is almost always cold.
#
# This pings the home page every 5 minutes from the always-on laptop, so the instance stays
# warm AND store_rest._CACHE_TTL (600s) never expires. Phone visits then hit the warm path.
#
# Not a substitute for the parallel pagination fix - that bounds the worst case (laptop off,
# instance recycled). This removes the common case.

$ErrorActionPreference = "Stop"
$Url = if ($env:AUCTION_WARM_URL) { $env:AUCTION_WARM_URL } else { "https://auction-arbitrage-nine.vercel.app/" }
$LogDir = Join-Path (Split-Path -Parent $PSScriptRoot) "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force $LogDir | Out-Null }
$Log = Join-Path $LogDir "warm-ping.log"

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
try {
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 90
  $sw.Stop()
  $secs = [math]::Round($sw.Elapsed.TotalSeconds, 2)
  # A slow response means we hit a cold instance - worth seeing in the log to judge whether
  # the 5-minute interval is actually keeping it warm.
  $tag = if ($secs -gt 5) { "COLD" } else { "warm" }
  Add-Content -Path $Log -Value "$stamp  $tag  $($r.StatusCode)  ${secs}s" -Encoding utf8
}
catch {
  Add-Content -Path $Log -Value "$stamp  FAIL  $($_.Exception.Message)" -Encoding utf8
}

# Keep the log from growing without bound (one line per 5 min = ~105k lines/year).
if ((Test-Path $Log) -and ((Get-Item $Log).Length -gt 1MB)) {
  $keep = Get-Content $Log -Tail 2000
  Set-Content -Path $Log -Value $keep -Encoding utf8
}
