# finalize_naver_pipeline.ps1 — 크롤 완료 후 마감 파이프라인 (C1 재처리 → 재채점 → 적재)
# 실행: powershell -File scripts\finalize_naver_pipeline.ps1
# 각 단계 로그는 evidence\finalize-*.log. 실패 시 중단(재채점 전 검증 게이트).
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Repo ".venv\Scripts\python.exe"
$env:PYTHONUTF8 = "1"
$env:AUCTION_DB = Join-Path $Repo "auction.db"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Log = Join-Path $Repo "evidence\finalize-$Stamp.log"

function Log($m) { "$((Get-Date -Format 'HH:mm:ss')) $m" | Tee-Object -FilePath $Log -Append }

Log "=== STEP 1: C1 재처리 (캐시 → naver_real_trades, 취소거래 교정) ==="
& $Py (Join-Path $Repo "scripts\reprocess_real_trades.py") 2>&1 | Tee-Object -FilePath $Log -Append
if ($LASTEXITCODE -ne 0) { Log "재처리 실패 — 중단"; exit 1 }

Log "=== STEP 2: 재채점 (--live-months 24, 네이버 실거래 주입) ==="
& $Py (Join-Path $Repo "run.py") --source courtauction --from-cache --live --live-months 24 --cash 500000000 2>&1 | Tee-Object -FilePath $Log -Append
if ($LASTEXITCODE -ne 0) { Log "재채점 실패 — 중단"; exit 1 }

Log "=== 완료 — Supabase 적재는 별도 확인 후 수동 (store_rest) ==="
Log "결과: evidence\result.csv / result.html, auction.db 갱신됨"
