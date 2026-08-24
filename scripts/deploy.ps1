# 프로덕션 배포 래퍼 — deploy_prod.sh(git worktree 배포)로 위임.
#
# (2026-08-24 감사 H-2) 종전의 'stash 이동 후 복원' 방식은 **폐기**했다:
#   - Move-Item 으로 대체 불가 파일(molit_trades.db 214MB)을 옮겼다가 finally 가 끊기면
#     영구 소실되는 구조였고, 실제로 한 번 잃었다(CLAUDE.md §0.6 사고 기록).
#   - deploy_prod.sh 는 지정 커밋의 **깨끗한 git worktree**에서 배포한다 — gitignore 된
#     대형 캐시가 워크트리에 애초에 존재하지 않아 stash 자체가 불필요하다.
#   위험 경로를 없애는 것이 가드를 더 다는 것보다 확실하다(아키텍처 감사 권고 그대로).
#
# 사용: pwsh scripts/deploy.ps1 [commit-ish]   (기본 HEAD — deploy_prod.sh 와 동일)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path $PSScriptRoot -Parent
Write-Host "[deploy.ps1] stash 방식은 폐기됨(2026-08-24) — deploy_prod.sh(worktree 배포)로 위임합니다."
& bash (Join-Path $RepoRoot "scripts/deploy_prod.sh") @args
exit $LASTEXITCODE
