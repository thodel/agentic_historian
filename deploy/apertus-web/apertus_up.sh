#!/usr/bin/env bash
# Bring Apertus 1.5 back up, on tei, in one command:
#
#   sudo bash deploy/apertus-web/apertus_up.sh            # page + warm model
#   sudo bash deploy/apertus-web/apertus_up.sh --no-warm  # page only
#
# 1. The test page: install_on_tei.sh, which is idempotent — it keeps the
#    existing password, (re)writes and starts the unit, puts the nginx include
#    back and checks it is loaded.
# 2. The model: one tiny request through the gateway, so the cold start on
#    idhefix (~6 min: 16 GB off the research share, then compilation) happens
#    now, not in front of the first tester. Waits until Apertus answers.
#
# The page runs from the checkout this script lives in. Take it down again with
# apertus_down.sh. Freeing card 1 on idhefix needs nothing from here: the
# gateway evicts Apertus as soon as another model needs the room.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECKOUT="$(cd "$HERE/../.." && pwd)"
ENV_FILE=/etc/apertus-web.env
MODEL=apertus-1.5-8b
WARM=1
[ "${1:-}" = "--no-warm" ] && WARM=0

[ "$(id -u)" -eq 0 ] || { echo "ERROR: run with sudo" >&2; exit 1; }

echo "== 1/2 Testseite =="
bash "$HERE/install_on_tei.sh" "$CHECKOUT"

[ "$WARM" -eq 1 ] || { echo "Modell nicht vorgewärmt (--no-warm)."; exit 0; }

echo
echo "== 2/2 Modell auf idhefix vorwärmen =="
GW="$(grep '^ATR_GATEWAY_URL=' "$ENV_FILE" | cut -d= -f2-)"
KEY="$(grep '^ATR_API_KEY=' "$ENV_FILE" | cut -d= -f2-)"

resident() {
  curl -s --max-time 10 -H "X-API-Key: $KEY" "$GW/health" \
    | python3 -c "import json,sys; sys.exit(0 if '$MODEL' in (json.load(sys.stdin).get('resident_models') or []) else 1)"
}

if resident; then
  echo "$MODEL ist bereits geladen."
else
  echo "$MODEL ist nicht geladen; Kaltstart läuft (ca. 6 min, höchstens 15)..."
  # A 256x256 grey PNG, built with the standard library: the request only has to
  # reach the model, and it must be an image because this is a vision model.
  BODY="$(python3 - "$MODEL" <<'EOF'
import base64, json, struct, sys, zlib
w = h = 256
raw = b"".join(b"\x00" + b"\x80\x80\x80" * w for _ in range(h))
def chunk(t, d):
    return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
       + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
print(json.dumps({"model": sys.argv[1], "max_tokens": 16, "temperature": 0.0,
  "messages": [{"role": "user", "content": [
    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
    {"type": "text", "text": "Welche Farbe hat dieses Bild? Ein Wort."}]}]}))
EOF
)"
  T0=$(date +%s)
  OUT="$(curl -s --max-time 1200 -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
         -d "$BODY" "$GW/v1/chat/completions" || true)"
  T1=$(date +%s)
  if ! printf '%s' "$OUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print('Antwort:', d['choices'][0]['message']['content'].strip())" 2>/dev/null; then
    echo "ERROR: kein Modell-Antwort nach $((T1 - T0)) s:" >&2
    printf '%s\n' "$OUT" >&2
    echo "Auf idhefix: journalctl --user -u atr-gateway --since '20 min ago' | grep -iE 'Launching vLLM|Error|Traceback|healthy'" >&2
    exit 1
  fi
  echo "Kaltstart und erste Antwort: $((T1 - T0)) s"
fi

PW="$(grep '^APERTUS_WEB_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
echo
echo "Apertus ist bereit: https://tei.dh.unibe.ch/apertus/"
echo "Passwort (Benutzername beliebig): $PW"
