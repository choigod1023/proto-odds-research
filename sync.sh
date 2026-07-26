#!/usr/bin/env bash
# 연구 문서·산출물을 레포에 반영하고 푸시한다.
# 사용: ./sync.sh "커밋 메시지"
set -euo pipefail
cd "$(dirname "$0")"

MSG="${1:-docs: 연구 문서 갱신 $(date -u +%Y-%m-%dT%H:%MZ)}"

if [[ -z "$(git status --porcelain)" ]]; then
  echo "변경 없음 — 푸시 생략"
  exit 0
fi

git add -A
git status --short
git commit -q -m "$MSG"
git push -q origin HEAD
echo "푸시 완료: $MSG"
