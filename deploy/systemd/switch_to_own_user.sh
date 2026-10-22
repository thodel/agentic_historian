#!/usr/bin/env bash
# Put #567 into force on the host: the bot runs as its own user
# `agentic_historian`, reads only its own secrets file, and is confined to its
# two data directories.
#
# Today it runs as `dh` — the user the OpenClaw agent runs as — so each process
# can read the other's secrets, and no file mode helps while the reader owns the
# file. This script does the switch in the one order that works and then
# *measures* whether it took, rather than reporting its own intentions.
#
#   sudo deploy/systemd/switch_to_own_user.sh --dry-run    # measures, changes nothing
#   sudo deploy/systemd/switch_to_own_user.sh
#
# It stops before the restart whenever a measurement says the service would
# come up as a user that cannot read its secrets or its code:
#
#   exit 2  not root (a real run)
#   exit 3  the data directories could not be read from the configuration
#   exit 4  the secrets file does not exist — the restarted bot would have no
#           token at all, because .env.gpustack is 0600 dh:dh and unreadable
#           for the new user, and that is the point
#   exit 5  the new user cannot reach the checkout or the venv (a stock
#           /home/dh is 0750) and the traverse right could not be granted
#   exit 6  hardening.conf does not list a directory the configuration writes —
#           with ProtectSystem=strict that is a write that fails on the first job
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
DROPINS="$REPO/deploy/systemd/$UNIT.service.d"
ENV_FILE="${AH_ENV_FILE:-/etc/agentic_historian.env}"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

say() { printf '%s\n' "$*"; }
run() {
  if [ "$DRY" = 1 ]; then say "  would run: $*"; else say "  $*"; "$@"; fi
}
stop() {  # <code> <message…>: the same words in both modes, so the dry run is honest
  local code=$1; shift
  if [ "$DRY" = 1 ]; then say "  would stop here (exit $code): $*" >&2; else say "  stop (exit $code): $*" >&2; fi
  exit "$code"
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
say "0. does hardening.conf grant what the configuration writes?"
# hardening.conf carries the paths as measured on tei. If a *_DIR setting moved
# since, the unit confines the bot away from a directory it needs, and the
# failure arrives later, as a denied write that does not look like this.
missing_grant=0
for dir in "${DATA_DIRS[@]}"; do
  granted=0
  while IFS= read -r line; do
    granted_path="${line#ReadWritePaths=}"
    case "$dir" in "$granted_path"|"$granted_path"/*) granted=1 ;; esac
  done < <(grep -E '^ReadWritePaths=' "$DROPINS/hardening.conf")
  if [ "$granted" = 1 ]; then say "  granted: $dir"; else say "  NOT in hardening.conf: $dir"; missing_grant=1; fi
done
if [ "$missing_grant" = 1 ]; then
  stop 6 "re-run writable_paths.py in the bot's venv and put its ReadWritePaths= lines into hardening.conf first"
fi

say ""
say "1. the user (system account, no login, no home of its own)"
if id "$BOT_USER" >/dev/null 2>&1; then
  say "  $BOT_USER exists — left as it is"
else
  run useradd --system --no-create-home --shell /usr/sbin/nologin "$BOT_USER"
fi

say ""
say "2. can $BOT_USER reach the code at all?"
# A stock Ubuntu home is 0750, so the new user cannot traverse /home/dh: the
# service would start and die on its first import with 'permission denied' on
# a path that plainly exists. Traverse (x) only, for this one user, on each
# ancestor that lacks it — not o+rx for everybody, which would open the rest of
# that home to every user on the host.
traversable() {  # <dir>: can a user who is not the owner and not in the group pass through?
  local mode owner
  read -r mode owner < <(stat -c '%a %U' "$1")
  [ "$owner" = "$BOT_USER" ] && return 0
  (( (8#$mode & 1) != 0 ))
}
NEEDS_TRAVERSE=()
TARGETS=("$REPO")
case "$VENV_PY" in /*) TARGETS+=("$(dirname "$VENV_PY")") ;; esac  # python3 from PATH has no venv to walk
for target in "${TARGETS[@]}"; do
  d="$target"
  while [ "$d" != "/" ] && [ "$d" != "." ]; do
    if ! traversable "$d"; then
      case " ${NEEDS_TRAVERSE[*]-} " in *" $d "*) ;; *) NEEDS_TRAVERSE+=("$d") ;; esac
    fi
    d="$(dirname "$d")"
  done
done
if [ "${#NEEDS_TRAVERSE[@]}" -eq 0 ]; then
  say "  every directory above the checkout and the venv is traversable — nothing to grant"
else
  for d in "${NEEDS_TRAVERSE[@]}"; do
    say "  $(stat -c '%A %U' "$d") $d — $BOT_USER cannot pass through"
  done
  # Measured in both modes: a dry run that stays quiet about a missing tool and
  # a real run that stops on it are two different scripts (tei had no `acl`
  # package on 22.10.2026, and the dry run said nothing).
  if ! command -v setfacl >/dev/null 2>&1; then
    stop 5 "setfacl is not installed (package acl); without it the only alternative is chmod o+x on those directories, which opens them to every user — your call, not this script's"
  fi
  for d in "${NEEDS_TRAVERSE[@]}"; do
    run setfacl -m "u:$BOT_USER:--x" "$d"
  done
fi
if [ "$DRY" = 0 ]; then
  if runuser -u "$BOT_USER" -- test -r "$VENV_PY" -a -r "$REPO/agentic_historian/bot.py"; then
    say "  measured: $BOT_USER reads the venv python and bot.py"
  else
    stop 5 "$BOT_USER still cannot read $VENV_PY or $REPO/agentic_historian/bot.py — 'namei -m' on either path names the directory"
  fi
else
  say "  (a real run then checks, as $BOT_USER, that the venv python and bot.py are readable)"
fi

say ""
say "3. the env file: owned by root, readable by the bot's group only"
if [ -f "$ENV_FILE" ]; then
  run chown "root:$BOT_USER" "$ENV_FILE"
  run chmod 640 "$ENV_FILE"
else
  say "  $ENV_FILE does not exist. Fill it from deploy/systemd/agentic_historian.env.example"
  say "  (copy ONLY the bot's keys out of .env.gpustack), install it 0640 root:$BOT_USER, re-run."
  stop 4 "without it the restarted bot has no secrets at all: .env.gpustack is 0600 dh:dh and $BOT_USER cannot read it — which is the point of the switch"
fi

say ""
say "4. ownership of the data directories"
for dir in "${DATA_DIRS[@]}"; do
  [ -d "$dir" ] || run mkdir -p "$dir"
  run chown -R "$BOT_USER:$BOT_USER" "$dir"
  run chmod -R u+rwX "$dir"
done

say ""
say "5. the three drop-ins: secrets (item 1), hardening (item 2), user (item 3)"
for conf in secrets.conf hardening.conf user.conf; do
  run install -m 644 -D "$DROPINS/$conf" "/etc/systemd/system/$UNIT.service.d/$conf"
done
run systemctl daemon-reload
run systemctl restart "$UNIT"

say ""
say "6. what actually took — measured, not claimed"
if [ "$DRY" = 1 ]; then
  say "  would run: deploy/systemd/check_separation.py --secrets $ENV_FILE"
  exit 0
fi
sleep 3
if systemctl is-active --quiet "$UNIT"; then
  say "  $UNIT is active as $(systemctl show "$UNIT" -pUser --value)"
else
  say "  $UNIT is NOT active — 'journalctl -u $UNIT -n 50' says why; a 'permission denied'" >&2
  say "  names a path the new user cannot read or write." >&2
fi
"$REPO/deploy/systemd/check_separation.py" --secrets "$ENV_FILE" || true
say ""
say "Name the OpenClaw credentials to answer the Definition of Done:"
say "  deploy/systemd/check_separation.py --openclaw-secrets <path>"
say ""
say "Then rotate what both processes handled while they shared a user — the keys"
say "copied out of .env.gpustack are still readable there by the OpenClaw agent"
say "until they are replaced (SEPARATION.md, #567 item 4)."
