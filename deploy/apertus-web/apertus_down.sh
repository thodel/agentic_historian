#!/usr/bin/env bash
# Take the Apertus test page down again, on tei:
#
#   sudo bash deploy/apertus-web/apertus_down.sh
#
# Stops and disables the unit and removes the nginx include from the file nginx
# actually loads (on tei a copy in sites-enabled/, not a symlink). Keeps the
# unit, the snippet and /etc/apertus-web.env, so apertus_up.sh brings the page
# back with the same password. The model on idhefix is not touched: the gateway
# evicts it as soon as another model needs card 1.
set -euo pipefail

SITE="$(readlink -f /etc/nginx/sites-enabled/tei.dh.unibe.ch)"
[ "$(id -u)" -eq 0 ] || { echo "ERROR: run with sudo" >&2; exit 1; }

systemctl disable --now apertus-web 2>/dev/null || true
echo "apertus-web: gestoppt und deaktiviert"

if grep -q 'snippets/apertus.conf' "$SITE"; then
  mkdir -p /etc/nginx/backup              # never inside sites-enabled/
  BACKUP="/etc/nginx/backup/$(basename "$SITE").$(date +%Y%m%d-%H%M%S)"
  cp "$SITE" "$BACKUP"
  sed -i '/snippets\/apertus.conf/{N;/\n[[:space:]]*$/d;s/.*snippets\/apertus.conf;\n//}' "$SITE"
  if nginx -t 2>/dev/null; then
    systemctl reload nginx
    echo "nginx: Include entfernt (Backup: $BACKUP)"
  else
    cp "$BACKUP" "$SITE"
    nginx -t || true
    echo "ERROR: nginx -t failed; $SITE restored" >&2
    exit 1
  fi
else
  echo "nginx: kein Include vorhanden"
fi
echo "Wieder hochfahren: sudo bash $(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/apertus_up.sh"
