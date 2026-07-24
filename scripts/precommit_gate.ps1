# precommit_gate.ps1 - automated QA gate, runs on every `git commit` (installed by install-hooks.ps1).
# Blocks the commit (exit 1) on: staged scratch/db files, PII leftovers, ruff violations, pytest failures.
# Non-blocking: appends a doc-sync task to harness\DOC_SYNC_QUEUE.md when crawler/judgment code changes
# without a matching README/docs update (consumed by the next Claude session - see CLAUDE.md).
# Escape hatch (emergencies only): AUCTION_SKIP_GATE=1 skips tests/ruff/PII but never the scratch/db block.
$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python   = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Ruff     = Join-Path $RepoRoot ".venv\Scripts\ruff.exe"
$env:PYTHONUTF8 = "1"
Set-Location $RepoRoot

# fail-closed: missing venv tools would otherwise leave stale $LASTEXITCODE=0 and silently pass (review F2).
if (-not (Test-Path $Python) -or -not (Test-Path $Ruff)) {
    Write-Host "[GATE BLOCKED] .venv tools missing ($Python / $Ruff) - cannot verify, failing closed." -ForegroundColor Red
    exit 1
}

function Fail($msg) {
    Write-Host ""
    Write-Host "[GATE BLOCKED] $msg" -ForegroundColor Red
    Write-Host "(emergency escape: set AUCTION_SKIP_GATE=1 -- scratch/db block cannot be skipped)"
    exit 1
}

# --- staged files (quotepath=false: keep non-ASCII paths verbatim, review) ---
$staged = @(git -c core.quotepath=false diff --cached --name-only | ForEach-Object { $_.Trim('"') } | Where-Object { $_ })
if ($staged.Count -eq 0) { exit 0 }

# --- 1) hard block: scratch artifacts / database files (incl. -wal/-shm/-journal sidecars) ---
$banned = $staged | Where-Object { $_ -match '(^|/)scratch' -or $_ -match '\.db($|[.\-])' }
if ($banned) {
    Fail ("staged forbidden files (scratch/db, PII risk): " + ($banned -join ", "))
}

# --- 2) doc-drift detection (non-blocking): crawler/judgment code staged without docs ---
$docSensitive = $staged | Where-Object {
    $_ -match '^src/(courtauction_|molit_|naver_|building_|data_gates|score|pipeline|matcher)' -or
    $_ -match '^deploy/crawl_'
}
$docsTouched = $staged | Where-Object { $_ -match '^README\.md$' -or $_ -match '^docs/' }
if ($docSensitive -and -not $docsTouched) {
    $queue = Join-Path $RepoRoot "harness\DOC_SYNC_QUEUE.md"
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm"
    $entry = "- [ ] $stamp KST | code-without-docs commit | files: " + (($docSensitive | Select-Object -First 8) -join ", ")
    try { $entry | Out-File -FilePath $queue -Append -Encoding utf8 } catch {}
    Write-Host "[gate] doc-sync task queued (crawler/judgment code changed without README/docs) -> harness\DOC_SYNC_QUEUE.md" -ForegroundColor Yellow
}

if ($env:AUCTION_SKIP_GATE -eq "1") {
    Write-Host "[gate] AUCTION_SKIP_GATE=1 -- skipping ruff/pytest/PII checks (scratch/db block already passed)" -ForegroundColor Yellow
    exit 0
}

# --- 3) ruff on staged .py files only (fast, scoped) ---
# @() forces an array: a single match would otherwise collapse to a scalar string, and splatting a
# string in PS5.1 explodes it character-by-character (ruff then gets one-letter args and fails).
# Caught in production on the first single-file commit; pass the array variable, never @splat.
$stagedPy = @($staged | Where-Object { $_ -match '\.py$' -and (Test-Path (Join-Path $RepoRoot $_)) })
if ($stagedPy.Count -gt 0) {
    & $Ruff check $stagedPy
    if ($LASTEXITCODE -ne 0) { Fail "ruff violations in staged files (see above). Fix or run: .venv\Scripts\ruff.exe check --fix <file>" }
}

# --- 4) PII leftover scan when crawler-layer code changes (masking-wiring regressions) ---
$crawlerTouched = $staged | Where-Object { $_ -match '^src/' -or $_ -match '^deploy/' }
if ($crawlerTouched) {
    $out = & $Python (Join-Path $RepoRoot "scripts\backfill_pii_mask.py") --check 2>&1
    $out | ForEach-Object { Write-Host $_ }
    $hitLine = $out | Where-Object { $_ -match ':\s*(\d+)\s*$' } | Select-Object -First 1
    if ($hitLine -match ':\s*(\d+)\s*$') {
        if ([int]$Matches[1] -gt 0) { Fail "PII scan found $($Matches[1]) suspected real names in DB. Run backfill_pii_mask.py (no --check) or fix masking wiring first." }
    } else {
        Fail "PII scan produced no parsable result (script error above?) - failing closed."
    }
}

# --- 4.5) untracked-import check: staged .py importing repo-local modules that are
# neither tracked nor staged (clean-checkout boot-failure class, 2026-07-24 500 incident).
# Local pytest cannot catch this (file exists in the working tree), so it runs here.
$stagedAnyPy = @($staged | Where-Object { $_ -match '\.py$' })
if ($stagedAnyPy.Count -gt 0) {
    & $Python (Join-Path $RepoRoot "scripts\check_untracked_imports.py")
    if ($LASTEXITCODE -ne 0) { Fail "untracked-import check failed - commit would break clean checkout (Vercel). git add the missing module(s) first." }
}

# --- 5) full pytest when code/tests staged (the actual regression gate) ---
$codeTouched = $staged | Where-Object { $_ -match '^(src|tests|deploy)/' -or $_ -eq 'run.py' }
if ($codeTouched) {
    Write-Host "[gate] running pytest -q (staged code changes) ..."
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { Fail "pytest failed - commit blocked until tests pass." }
}

Write-Host "[gate] all checks passed." -ForegroundColor Green
exit 0
