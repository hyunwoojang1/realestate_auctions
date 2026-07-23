# install-hooks.ps1 - installs versioned git hooks from scripts\git-hooks\ into .git\hooks\.
# Re-run after clone or whenever scripts\git-hooks\ changes. Idempotent.
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$SrcDir   = Join-Path $RepoRoot "scripts\git-hooks"
$DstDir   = Join-Path $RepoRoot ".git\hooks"

if (-not (Test-Path $DstDir)) { throw "not a git repo (missing .git\hooks): $DstDir" }
Get-ChildItem $SrcDir -File | ForEach-Object {
    $dst = Join-Path $DstDir $_.Name
    Copy-Item $_.FullName $dst -Force
    Write-Host "installed hook: $($_.Name) -> $dst"
}
Write-Host "done. gate script: scripts\precommit_gate.ps1 (escape: AUCTION_SKIP_GATE=1)"
