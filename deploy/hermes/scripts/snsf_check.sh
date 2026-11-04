#!/usr/bin/env bash
# snsf_check.sh — has data.snf.ch republished grants_with_abstracts.csv? (#799)
#
# A Hermes cron job without an agent (`--no-agent`): whatever this prints is
# delivered; an empty output is a silent tick. So it prints nothing when the
# headers and the header line of the CSV match the baseline, and a short
# report when they do not — then it updates the baseline.
#
# The CSV is ~426 MB; only the headers and the first 64 KiB are fetched.
#
#   SNSF_URL       the dataset (default: the SNSF grants with abstracts)
#   SNSF_BASELINE  where the baseline JSON lives (default: next to this script)
set -euo pipefail

URL="${SNSF_URL:-https://data.snf.ch/datasets/grants_with_abstracts.csv}"
BASE="${SNSF_BASELINE:-$(dirname "$(readlink -f "$0")")/snsf_grants_baseline_meta.json}"

headers=$(curl -sSIL --max-time 60 "$URL" | tr -d '\r')
last_modified=$(printf '%s\n' "$headers" | awk -F': ' 'tolower($1)=="last-modified"{v=$2} END{print v}')
content_length=$(printf '%s\n' "$headers" | awk -F': ' 'tolower($1)=="content-length"{v=$2} END{print v}')
# Into a file, not a pipe: `head -2` would close the pipe while curl still
# writes the 64 KiB block, and curl exits 23 (EPIPE) — the first live run.
head_file=$(mktemp)
trap 'rm -f "$head_file"' EXIT
curl -sSL --max-time 120 -r 0-65535 "$URL" -o "$head_file"
header_hash=$(head -2 "$head_file" | sha256sum | cut -d' ' -f1)

python3 - "$BASE" "$URL" "$last_modified" "$content_length" "$header_hash" <<'EOF'
import json, sys
from datetime import datetime, timezone
from pathlib import Path

base, url, last_modified, content_length, header_hash = sys.argv[1:]
path = Path(base)
now = datetime.now(timezone.utc).isoformat(timespec="seconds")
current = {"url": url, "last_modified": last_modified,
           "content_length": content_length, "header_hash": header_hash}

if not path.is_file():
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**current, "checked_at": now}, indent=2) + "\n")
    print(f"SNSF grants_with_abstracts.csv: Baseline angelegt ({last_modified or '?'}, "
          f"{content_length or '?'} Bytes). Nächster Lauf vergleicht dagegen.")
    sys.exit(0)

old = json.loads(path.read_text())
changed = {k: (old.get(k), v) for k, v in current.items() if k != "url" and old.get(k) != v}
path.write_text(json.dumps({**current, "checked_at": now}, indent=2) + "\n")
if not changed:
    sys.exit(0)                      # silent: nothing to deliver
lines = ["SNSF grants_with_abstracts.csv hat sich geändert:"]
for k, (before, after) in changed.items():
    lines.append(f"- {k}: {before or '?'} → {after or '?'}")
if "content_length" in changed:
    try:
        delta = int(current["content_length"]) - int(old.get("content_length") or 0)
        lines.append(f"- Grösse: {delta:+,} Bytes")
    except ValueError:
        pass
lines.append(f"Quelle: {url} — Baseline aktualisiert.")
print("\n".join(lines))
EOF
