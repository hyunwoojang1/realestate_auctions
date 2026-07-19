#!/usr/bin/env bash
# 프로덕션 배포 원커맨드 — push 후 반드시 실행 (CLAUDE.md ##0.5 규칙).
#
# 워킹트리에 타 세션 미완성 변경이 섞여 있어도 안전하도록, 지정 커밋(기본 HEAD)의
# 깨끗한 worktree 를 임시로 만들어 그 안에서 `vercel deploy --prod` 한다.
#
# 사용:  bash scripts/deploy_prod.sh [commit-ish]   (기본: HEAD)
# 검증:  배포 후 프로덕션 /health 가 {"status":"ok",...} 인지 자동 확인.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COMMIT="${1:-HEAD}"
TMP="${TMPDIR:-/tmp}/auction_deploy_$$"
PROD_URL="https://auction-arbitrage-hyunwoo-jang-s-projects.vercel.app"

cd "$REPO_DIR"
SHA="$(git rev-parse --short "$COMMIT")"
echo "▶ 배포 대상 커밋: $SHA ($COMMIT)"

cleanup(){ git -C "$REPO_DIR" worktree remove --force "$TMP" 2>/dev/null || true; }
trap cleanup EXIT

git worktree add --detach "$TMP" "$COMMIT" >/dev/null
cp -r "$REPO_DIR/.vercel" "$TMP/.vercel"   # 프로젝트 링크(빌드 설정은 vercel.json이 커밋에 있음)

cd "$TMP"
echo "▶ vercel deploy --prod 실행 중…"
vercel deploy --prod --yes >/dev/null 2>&1 || { echo "✖ vercel deploy 실패"; exit 1; }

echo "▶ 프로덕션 헬스체크…"
sleep 3
HEALTH="$(curl -s --max-time 20 "$PROD_URL/health" || true)"
echo "  $PROD_URL/health → $HEALTH"
case "$HEALTH" in
  *'"status":"ok"'*) echo "✔ 배포 완료·라이브 확인 ($SHA)";;
  *) echo "✖ 헬스체크 실패 — vercel ls 로 배포 상태 확인 필요"; exit 1;;
esac
