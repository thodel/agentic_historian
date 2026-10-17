#!/usr/bin/env bash
# Move the bot to its own user `agentic_historian` (#567, item 3).
#
# Today the bot runs as `dh` — the user the OpenClaw agent runs as — so each
# process can read the other's secrets, and no file mode helps while the reader
# owns the file. This script does the switch in the one order that works and
# then *measures* whether it took, rather than reporting its own intentions.
#
#   sudo deploy/systemd/switch_to_own_user.sh --dry-run
#   sudo deploy/systemd/switch_to_own_user.sh
#
# **Not named dh-bot.** That is the OpenClaw agent's Discord identity
# (SEPARATION.md, S1). The bot's name is `agentic_historian`.
#
# What it does NOT do, on purpose:
#   * it does not touch the OpenClaw agent, its files or its service;
#   * it does not make the checkout or the venv writable for the new user —
#     `/update` reports and no longer pulls (#567), so the deploy runs from
#     outside as whoever owns the checkout;
#   * it does not create or fill the env file. Secrets are not moved by a
#     script that prints what it does.
set -euo pipefail

BOT_USER=agentic_historian
UNIT=agentic-historian
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE=/etc/agentic_historian.env
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

say() { printf '%s\n' "$*"; }
run() {
  if [ "$DRY" = 1 ]; then say "  would run: $*"; else say "  $*"; "$@"; fi
}

if [ "$DRY" = 0 ] && [ "$(id -u)" != 0 ]; then
  say "needs root: it creates a user and changes ownership." >&2
  exit 2
fi

# The writable paths come from the running configuration, not from this script.
# A list typed here drifts from the unit, and a path that is missing under
# ProtectSystem=strict is a write that fails at the first job (#567, #643).
VENV_PY="$REPO/agentic_historian/.venv/bin/python"
[ -x "$VENV_PY" ] || VENV_PY=python3
say "data directories, from $($VENV_PY -c 'import sys; print(sys.executable)'):"
mapfile -t DATA_DIRS < <("$VENV_PY" "$REPO/deploy/systemd/writable_paths.py" --data-only 2>/dev/null || true)
if [ "${#DATA_DIRS[@]}" -eq 0 ]; then
  say "could not read the data directories from the configuration." >&2
  say "Run writable_paths.py in the bot's venv and fix what it says first —" >&2
  say "guessing them here is how item 2 shipped a path that did not exist." >&2
  exit 3
fi
printf '  %s\n' "${DATA_DIRS[@]}"

say ""
say "1. the user (system account, no login, no home of its own)"
if id "$BOT_USER" >/dev/null 2>&1; then
  say "  $BOT_USER exists — left as it is"
else
  run useradd --system --no-create-home --shell /usr/sbin/nologin "$BOT_USER"
fi

say ""
say "2. ownership of the data directories"
for dir in "${DATA_DIRS[@]}"; do
  [ -d "$dir" ] || run mkdir -p "$dir"
  run chown -R "$BOT_USER:$BOT_USER" "$dir"
  run chmod -R u+rwX "$dir"
done

say ""
say "3. the env file: owned by root, readable by the bot's group only"
if [ -f "$ENV_FILE" ]; then
  run chown "root:$BOT_USER" "$ENV_FILE"
  run chmod 640 "$ENV_FILE"
else
  say "  $ENV_FILE does not exist — fill it from"
  say "  deploy/systemd/agentic_historian.env.example first, then re-run."
fi

say ""
say "4. the unit drop-in"
run install -m 644 -D "$REPO/deploy/systemd/agentic-historian.service.d/user.conf" \
    "/etc/systemd/system/$UNIT.service.d/user.conf"
run systemctl daemon-reload
run systemctl restart "$UNIT"

say ""
say "5. what actually took — measured, not claimed"
if [ "$DRY" = 1 ]; then
  say "  would run: deploy/systemd/check_separation.py"
  exit 0
fi
"$REPO/deploy/systemd/check_separation.py" --secrets "$ENV_FILE" || true
say ""
say "Name the OpenClaw credentials to answer the Definition of Done:"
say "  deploy/systemd/check_separation.py --openclaw-secrets <path>"
say ""
say "If the service is now failing: it is almost certainly a path the new user"
say "cannot write. 'journalctl -u $UNIT -n 50' names it, and writable_paths.py"
say "says which ones the configuration expects."
