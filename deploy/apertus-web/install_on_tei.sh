#!/usr/bin/env bash
# Install the Apertus test page on tei: https://tei.dh.unibe.ch/apertus/
#
#   sudo bash deploy/apertus-web/install_on_tei.sh <checkout>
#
# <checkout> is the agentic_historian checkout the page runs from (a worktree of
# the branch that carries apertus_web/, e.g. /home/dh/ah-apertus). Everything
# else is taken from the atr-mcp unit that already runs on this host — its user
# and its venv — and from /etc/atr-mcp.env, which holds the gateway URL and key.
# Nothing is typed in by hand, so nothing can be typed in wrong.
#
# Idempotent: a second run keeps the existing password, rewrites the unit and
# the nginx snippet, and does not add the include line twice. Every nginx change
# is backed up and rolled back if `nginx -t` fails, because a bad config takes
# the whole site down.
set -euo pipefail

CHECKOUT="${1:?usage: sudo bash $0 <agentic_historian checkout with apertus_web/>}"
CHECKOUT="$(cd "$CHECKOUT" && pwd)"
PKG="$CHECKOUT/agentic_historian"
HERE="$CHECKOUT/deploy/apertus-web"
ENV_FILE=/etc/apertus-web.env
UNIT=/etc/systemd/system/apertus-web.service
SNIPPET=/etc/nginx/snippets/apertus.conf
SITE=/etc/nginx/sites-available/tei.dh.unibe.ch
PORT=8310

die() { echo "ERROR: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "run with sudo"
[ -f "$PKG/apertus_web/app.py" ] || die "$PKG/apertus_web/app.py not found — wrong checkout or branch"
[ -f "$SITE" ] || die "$SITE not found"

# ── 1. User and venv: the same as the MCP server's ──────────────────────────────
RUN_USER="$(systemctl show -p User --value atr-mcp)"
[ -n "$RUN_USER" ] || die "atr-mcp has no User= — cannot tell which account to run as"
MCP_EXEC="$(systemctl cat atr-mcp | sed -n 's/^ExecStart=\([^ ]*\).*/\1/p' | head -1)"
VENV_BIN="$(dirname "$MCP_EXEC")"
UVICORN="$VENV_BIN/uvicorn"
[ -x "$UVICORN" ] || die "no uvicorn at $UVICORN (from atr-mcp's ExecStart)"
sudo -u "$RUN_USER" test -r "$PKG/apertus_web/app.py" \
  || die "$RUN_USER cannot read $PKG — give it access (as the MCP checkout has)"
echo "user: $RUN_USER   venv: $VENV_BIN   code: $PKG"

# The venv must import the page with only what it already has.
sudo -u "$RUN_USER" env -C "$PKG" APERTUS_WEB_PASSWORD=import-check-only-xxxxxxxx \
  ATR_GATEWAY_URL=http://127.0.0.1:1 AH_ENV_FILE=/dev/null \
  "$VENV_BIN/python" -c 'import apertus_web.app as w; assert w.app is not None' \
  || die "the MCP venv cannot import apertus_web (see the traceback above)"

# ── 2. Secrets: a password of its own; gateway URL and key from atr-mcp ────────
if [ ! -f "$ENV_FILE" ]; then
  GW_URL="$(grep -h '^ATR_GATEWAY_URL=' /etc/atr-mcp.env | tail -1 | cut -d= -f2-)"
  GW_KEY="$(grep -h '^ATR_API_KEY=' /etc/atr-mcp.env | tail -1 | cut -d= -f2-)"
  [ -n "$GW_URL" ] && [ -n "$GW_KEY" ] || die "ATR_GATEWAY_URL/ATR_API_KEY missing in /etc/atr-mcp.env"
  install -m 0600 -o root -g root /dev/null "$ENV_FILE"
  {
    echo "APERTUS_WEB_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
    echo "ATR_GATEWAY_URL=$GW_URL"
    echo "ATR_API_KEY=$GW_KEY"
  } > "$ENV_FILE"
  echo "wrote $ENV_FILE (new password)"
else
  echo "kept $ENV_FILE (existing password)"
fi

# ── 3. The unit, from the template with this host's values ─────────────────────
sed -e "s|^User=.*|User=$RUN_USER|" \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=$PKG|" \
    -e "s|^ExecStart=.*|ExecStart=$UVICORN apertus_web.app:app \\\\|" \
    "$HERE/apertus-web.service" > "$UNIT"
systemctl daemon-reload
systemctl enable apertus-web >/dev/null
systemctl restart apertus-web
for _ in $(seq 1 20); do
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" || true)"
  [ "$code" = "401" ] && break
  sleep 1
done
[ "$code" = "401" ] || { journalctl -u apertus-web -n 30 --no-pager; die "service answers $code, expected 401"; }
echo "service: 401 without password (as it must)"

# ── 4. nginx: snippet plus one include line, with backup and rollback ──────────
install -m 0644 "$HERE/nginx-apertus.conf" "$SNIPPET"
if ! grep -q 'snippets/apertus.conf' "$SITE"; then
  BACKUP="$SITE.bak-$(date +%Y%m%d-%H%M%S)"
  cp "$SITE" "$BACKUP"
  # First `location /mcp/` lies inside the TLS server block (see deploy/mcp-atr/).
  sed -i '0,/^[[:space:]]*location \/mcp\//s//    include \/etc\/nginx\/snippets\/apertus.conf;\n\n&/' "$SITE"
  grep -q 'snippets/apertus.conf' "$SITE" || { cp "$BACKUP" "$SITE"; die "no 'location /mcp/' to insert before; site unchanged"; }
  echo "added include to $SITE (backup: $BACKUP)"
fi
if nginx -t 2>/dev/null; then
  systemctl reload nginx
else
  nginx -t || true
  [ -n "${BACKUP:-}" ] && cp "$BACKUP" "$SITE" && echo "ROLLBACK: $SITE restored"
  die "nginx -t failed"
fi

# ── 5. From outside ────────────────────────────────────────────────────────────
PW="$(grep '^APERTUS_WEB_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
code="$(curl -s -o /dev/null -w '%{http_code}' https://tei.dh.unibe.ch/apertus/ || true)"
echo "https://tei.dh.unibe.ch/apertus/ without password: $code (expect 401)"
echo "status with password: $(curl -s -u "test:$PW" https://tei.dh.unibe.ch/apertus/api/status || true)"
echo
echo "Done. Password for testers (user name: anything):"
echo "  $PW"
echo "Take down after the test: sudo systemctl disable --now apertus-web, remove the"
echo "include line from $SITE, sudo systemctl reload nginx."
