#!/bin/bash
# scripts/pr.sh — Create a PR for the current branch against master
# Usage: ./scripts/pr.sh "feat: my feature" "Closes #123"
#         ./scripts/pr.sh "WIP: work in progress"

set -e

BRANCH=$(git symbolic-ref --short HEAD)
TITLE="${1:-WIP: $BRANCH}"
BODY="${2:-TODO: describe changes}"
REPO="${REPO:-thodel/agentic_historian}"
BASE="${BASE:-master}"

echo "Creating PR: $TITLE"
echo "From branch: $BRANCH → $REPO ($BASE)"

gh pr create \
  --repo "$REPO" \
  --title "$TITLE" \
  --body "$BODY" \
  --base "$BASE" \
  --draft

echo "✅ PR created"