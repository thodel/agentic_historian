#!/usr/bin/env bash
#
# update.sh — update the Agentic Historian prod bot (tei) to the latest main.
#
# Idempotent and safe to re-run. Run from anywhere:
#     bash /home/dh/agentic_historian/update.sh
#
# Flow: pre-flight checks + fetch (bot still running) → stop service → fast-forward
# main (hard-reset fallback if history was rewritten) → install pinned deps →
# import smoke test → restart → verify. A failed pre-flight check leaves the bot
# running; anything later aborts BEFORE restarting. The untracked .env.gpustack at the
# repo root is never touched (it is gitignored, so reset --hard leaves it alone).
#
# Overridable via env vars: AH_REPO, AH_SERVICE, AH_BRANCH, AH_MCP_PY,
# AH_MCP_SERVICE, AH_RESTART_MCP (0 = leave the MCP running as it is).
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

cd "$REPO"

# Checks first, while the bot still runs: a refusal here leaves it up. On
# 26.10. a WIP check stopped the service and left it stopped.
c "Pre-flight checks"
b="$(git branch --show-current || true)"
[ "$b" = "$BRANCH" ] || die "on branch '$b', expected '$BRANCH'. Resolve manually, then re-run."
[ -z "$(git status --porcelain --untracked-files=no)" ] \
    || die "tracked changes on prod (WIP left behind?). Inspect 'git status'; stash or commit, then re-run."

c "Fetching origin"
git fetch --prune origin

c "Stopping $SERVICE"
sudo systemctl stop "$SERVICE" || true

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

# The ATR MCP's jobs run on the repo root's venv (atr-mcp.service), not the
# bot's. It was kept by hand and fell behind twice: atr-client on 25.10.,
# huggingface_hub on 27.10. Runtime requirements only; a failure here does not
# stop the bot's deploy, it is named.
MCP_PY="${AH_MCP_PY:-$REPO/.venv/bin/python}"
if [ -x "$MCP_PY" ] && [ "$MCP_PY" != "$PY" ]; then
    c "Installing runtime dependencies into the MCP venv ($MCP_PY)"
    "$MCP_PY" -m pip install -q -r "$PKG/requirements.txt" \
        || printf '\n\033[1;33m! MCP venv install failed — the bot deploy goes on; fix it by hand\033[0m\n'
fi

c "Import smoke test"
SMOKE=("$PY" -c "import bot, config, orchestrator, ingest, agent_tools, nl_orchestrator, semantic; from utils import publish_github, mcp_probe; from knowledge_hub import store; from eval import harness; print('imports OK')")
# Import as the service does. Since #567 the bot runs as its own user, owns its
# data directories and reads its secrets from AH_ENV_FILE; run as dh, importing
# knowledge_hub cannot create its lock file there, and on 23.10.2026 that left
# the bot stopped after a deploy whose code was fine.
RUN_AS="$(systemctl show -p User --value "$SERVICE" 2>/dev/null || true)"
if [ -n "$RUN_AS" ] && [ "$RUN_AS" != "$(id -un)" ]; then
    ENV_FILE="$(systemctl show -p Environment --value "$SERVICE" 2>/dev/null \
        | tr ' ' '\n' | sed -n 's/^AH_ENV_FILE=//p')"
    printf '  as %s%s\n' "$RUN_AS" "${ENV_FILE:+, AH_ENV_FILE=$ENV_FILE}"
    ( cd "$PKG" && sudo -u "$RUN_AS" env ${ENV_FILE:+AH_ENV_FILE="$ENV_FILE"} "${SMOKE[@]}" )
else
    ( cd "$PKG" && "${SMOKE[@]}" )
fi || die "import smoke test failed — NOT restarting. Fix the error above, then re-run."

c "Starting $SERVICE"
sudo systemctl start "$SERVICE"
sleep 3
systemctl is-active --quiet "$SERVICE" || die "$SERVICE failed to start — see: journalctl -u $SERVICE -n 40"
c "✓ $SERVICE active on $(git rev-parse --short @)"

# The MCP serves the code it was started with; its jobs survive a restart
# (KillMode=process). AH_RESTART_MCP=0 leaves it alone.
MCP_SERVICE="${AH_MCP_SERVICE:-atr-mcp.service}"
if [ "${AH_RESTART_MCP:-1}" = 1 ] && systemctl is-active --quiet "$MCP_SERVICE" 2>/dev/null; then
    c "Restarting $MCP_SERVICE"
    sudo systemctl restart "$MCP_SERVICE" \
        && systemctl is-active --quiet "$MCP_SERVICE" \
        || printf '\n\033[1;33m! %s did not come back — see: journalctl -u %s -n 40\033[0m\n' "$MCP_SERVICE" "$MCP_SERVICE"
fi

c "Recent journal"
journalctl -u "$SERVICE" -n 12 --no-pager 2>/dev/null \
    | grep -iE "ready|connected|gateway|error|traceback" || true

echo
c "Update complete."
