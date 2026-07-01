<#
.SYNOPSIS
  auction-arbitrage 프로덕션 서빙 — waitress WSGI 서버로 차익 큐레이션 웹을 띄운다.

.DESCRIPTION
  Flask 내장 dev server(flask run) 대신 waitress 로 src.web:create_app() 을 서빙한다.
  waitress 는 순수 파이썬이라 Windows 올-로컬에서 gunicorn 없이 동작하고,
  debug/reloader 가 없어 프로덕션에서 안전하다(debug=False 구조적 보장).

  .venv 파이썬을 절대경로로 호출하므로 작업 디렉터리와 무관하게 동작한다.
  기본 바인드는 127.0.0.1(로컬 전용). 외부 노출은 Tailscale/Cloudflare 터널을 통해서만 한다
  (필요 시에만 -BindAll 로 0.0.0.0 바인드).

.PARAMETER Port
  바인드 포트(기본 8000).

.PARAMETER DbPath
  서빙할 라이브 적재 DB(AUCTION_DB). 미지정 시 리포지토리 루트 auction.db.
  DB 가 없거나 비었으면 web 레이어가 샘플 데이터로 안전 폴백한다.

.PARAMETER BindAll
  0.0.0.0 로 바인드(모든 인터페이스). 지정하지 않으면 127.0.0.1(로컬 전용).

.PARAMETER Threads
  waitress 워커 스레드 수(기본 4).

.EXAMPLE
  # 로컬 전용 서빙(기본): http://127.0.0.1:8000
  .\scripts\start.ps1

.EXAMPLE
  # 포트/DB 지정
  .\scripts\start.ps1 -Port 8080 -DbPath D:\data\auction.db
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$DbPath = "",
    [switch]$BindAll,
    [int]$Threads = 4
)

$ErrorActionPreference = "Stop"

# --- path resolution: repository root from script location ---
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python   = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not $DbPath) { $DbPath = Join-Path $RepoRoot "auction.db" }
if (-not (Test-Path $Python)) { throw "venv python not found: $Python (create .venv first)" }

$BindHost = if ($BindAll) { "0.0.0.0" } else { "127.0.0.1" }

# --- production serving env (no debug, no reloader) ---
$env:PYTHONUTF8    = "1"
$env:AUCTION_DB    = $DbPath
$env:AUCTION_HOST  = $BindHost
$env:AUCTION_PORT  = "$Port"
$env:AUCTION_THREADS = "$Threads"
# explicitly ensure dev debug flag is off in production serving
$env:AUCTION_DEBUG = "0"

Write-Host "=== auction-arbitrage start (waitress) ==="
Write-Host "host    : $BindHost"
Write-Host "port    : $Port"
Write-Host "db      : $DbPath"
Write-Host "threads : $Threads"
Write-Host "url     : http://$BindHost`:$Port"
Write-Host "------------------------------------------"

Push-Location $RepoRoot
try {
    & $Python -m src.serve
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}

exit $code
