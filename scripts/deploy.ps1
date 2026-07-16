# 안전 배포 (2026-07-16) — Vercel 프로덕션 CLI 배포.
#
# 왜 이 스크립트가 필요한가:
#   vercel.json 의 `includeFiles:"{templates,data,static}/**"` 는 서빙에 필요한 소형 data 파일
#   (coords_cache·lawd_codes·sample_*)을 함수 번들에 넣기 위한 것인데, **includeFiles 는 .gitignore/
#   .vercelignore 를 무시하고** 로컬 data/ 의 대형 캐시·백업(molit_trades.db 130M, data/backup/*.pre-* 84M×3,
#   naver_cache 36M …)까지 통째로 번들에 밀어넣는다 → 함수 225MB 한도 초과로 배포 실패.
#   (GitHub 자동배포는 git에 없는 대형 파일을 안 올려 무관하지만, 현재 GitHub 자동배포는 멈춰 있어 CLI로 배포함.)
#
# 이 스크립트는 배포 동안만 대형 로컬 파일을 stash 로 옮겼다가 되돌린다. 서빙은 Supabase 를 읽으므로
# 이 파일들 없이도 프로덕션은 정상 동작한다(로컬 크롤에만 필요).
#
# 사용: pwsh scripts/deploy.ps1        (레포 루트에서)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)   # 레포 루트로

$stash = Join-Path $env:TEMP "auction_deploy_stash"
New-Item -ItemType Directory -Force $stash | Out-Null

# includeFiles(data/**)가 삼키는 대형 로컬 파일 — gitignore돼 있어 GitHub 배포엔 없지만 로컬엔 있다.
$bigFiles = @(
  "data\molit_trades.db",
  "data\naver_cache.json",
  "data\courtauction_full_cache.json",
  "data\courtauction_cache.json",
  "data\courtauction_cache.json.dryrun.json"
)

Write-Host "[deploy] 대형 로컬 파일 임시 이동..." -ForegroundColor Cyan
foreach ($f in $bigFiles) {
  if (Test-Path $f) { Move-Item $f $stash -Force; Write-Host "  moved: $f" }
}
if (Test-Path "data\backup") { Move-Item "data\backup" (Join-Path $stash "backup") -Force; Write-Host "  moved: data\backup" }

$sz = (Get-ChildItem data -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("[deploy] data/ 현재 {0:N1} MB (한도 대비 안전)" -f $sz) -ForegroundColor Green

try {
  Write-Host "[deploy] vercel deploy --prod ..." -ForegroundColor Cyan
  vercel deploy --prod --yes
}
finally {
  Write-Host "[deploy] 대형 파일 원위치..." -ForegroundColor Cyan
  foreach ($f in $bigFiles) {
    $n = Split-Path $f -Leaf
    $s = Join-Path $stash $n
    if (Test-Path $s) { Move-Item $s $f -Force; Write-Host "  restored: $f" }
  }
  $b = Join-Path $stash "backup"
  if (Test-Path $b) { Move-Item $b "data\backup" -Force; Write-Host "  restored: data\backup" }
  Write-Host "[deploy] 완료 — 로컬 data/ 복원됨." -ForegroundColor Green
}
