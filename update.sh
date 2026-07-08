#!/usr/bin/env bash
#
# update.sh — update the Agentic Historian prod bot (tei) to the latest main.
#
# Idempotent and safe to re-run. Run from anywhere:
#     bash /home/dh/agentic_historian/update.sh
#
# Flow: stop service → fast-forward main (hard-reset fallback if history was
# rewritten) → install pinned deps → import smoke test → restart → verify.
# Aborts BEFORE restarting if anything is off. The untracked .env.gpustack at the
# repo root is never touched (it is gitignored, so reset --hard leaves it alone).
#
# Overridable via env vars: AH_REPO, AH_SERVICE, AH_BRANCH.
#
set -euo pipefail

REPO="${AH_REPO:-/home/dh/agentic_historian}"
PKG="$REPO/agentic_historian"
PY="$PKG/.venv/bin/python"
SERVICE="${AH_SERVICE:-agentic-historian.service}"
BRANCH="${AH_BRANCH:-main}"

c()   { printf '\n\033[1;36m» %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ -d "$REPO/.git" ] || die "not a git repo: $REPO"
[ -x "$PY" ]        || die "venv python not found: $PY (create the venv first)"

c "Stopping $SERVICE"
sudo systemctl stop "$SERVICE" || true

cd "$REPO"

c "Pre-flight checks"
b="$(git branch --show-current || true)"
[ "$b" = "$BRANCH" ] || die "on branch '$b', expected '$BRANCH'. Resolve manually, then re-run."
[ -z "$(git status --porcelain --untracked-files=no)" ] \
    || die "tracked changes on prod (WIP left behind?). Inspect 'git status'; stash or commit, then re-run."

c "Fetching origin"
git fetch --prune origin

if [ "$(git rev-parse @)" = "$(git rev-parse "origin/$BRANCH")" ]; then
    c "Already at latest ($(git rev-parse --short @))"
else
    c "Updating to origin/$BRANCH"
    # Fast-forward if possible; if history diverged (e.g. a force-push), hard-reset.
    if ! git merge --ff-only "origin/$BRANCH"; then
        c "History diverged (force-push?) — hard-reset to origin/$BRANCH (.env.gpustack survives)"
        git reset --hard "origin/$BRANCH"
    fi
fi
git log --oneline -1

c "Installing pinned dependencies"
# NB: use `python -m pip`, not `.venv/bin/pip` — the venv's pip shebang is stale
# (the venv was relocated at some point). requirements-dev.txt is the full pinned
# set CI validates against, so it guarantees every runtime dep is present.
"$PY" -m pip install -q -r "$REPO/requirements-dev.txt"

c "Import smoke test"
( cd "$PKG" && "$PY" -c "import bot, config, orchestrator, ingest, agent_tools, nl_orchestrator, semantic; from utils import publish_github, mcp_probe; from knowledge_hub import store; from eval import harness; print('imports OK')" ) \
    || die "import smoke test failed — NOT restarting. Fix the error above, then re-run."

c "Starting $SERVICE"
sudo systemctl start "$SERVICE"
sleep 3
systemctl is-active --quiet "$SERVICE" || die "$SERVICE failed to start — see: journalctl -u $SERVICE -n 40"
c "✓ $SERVICE active on $(git rev-parse --short @)"

c "Recent journal"
journalctl -u "$SERVICE" -n 12 --no-pager 2>/dev/null \
    | grep -iE "ready|connected|gateway|error|traceback" || true

echo
c "Update complete."
