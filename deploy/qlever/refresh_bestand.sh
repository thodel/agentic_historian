#!/usr/bin/env bash
#
# refresh_bestand.sh — build the Bestandsgraph and swap the QLever index on
# idhefix (KG-4, #330; phase 2 of #325).
#
#     bash deploy/qlever/refresh_bestand.sh            # build if inputs changed, then reindex
#     bash deploy/qlever/refresh_bestand.sh --force    # rebuild and reindex regardless
#
# The graph comes from knowledge_hub.bestand_build: one sorted N-Triples part
# per holding under $KG_DIR/parts/. Unchanged inputs (path, size, mtime of every
# database, and the commit) leave the index alone — the nightly timer is free.
# The index is built into $KG_DIR/index.new/ and swapped for index/ only when
# complete; a failed build or index leaves the previous index serving.
#
# Configured by the environment (qlever-refresh.service reads
# ~/.config/qlever/env; see README):
#   AH_REPO              checkout of agentic_historian   (default $HOME/agentic_historian)
#   AH_PYTHON            python with the package's deps  (default $AH_REPO/agentic_historian/.venv/bin/python)
#   KG_DIR               build directory                 (default $HOME/kg)
#   KG_SOURCE_DB_DIR     the MCP servers' databases      (default /home/tobias/mcp/data)
#   KG_SSRQ_REGISTER, KG_SSRQ_TEI, KG_CORPUS_TTL         optional sources
#   QLEVER_ACCESS_TOKEN  required, never in the repository
#   QLEVER_MEMORY, QLEVER_CACHE                          (defaults 16G, 8G)
set -euo pipefail

REPO="${AH_REPO:-$HOME/agentic_historian}"
PKG="$REPO/agentic_historian"
PY="${AH_PYTHON:-$PKG/.venv/bin/python}"
COMPOSE=(docker compose -f "$REPO/deploy/qlever/docker-compose.bestand.yml")
KG_DIR="${KG_DIR:-$HOME/kg}"
export QLEVER_DATA="$KG_DIR"
export QLEVER_INDEX="ah-bestand"
export QLEVER_MEMORY="${QLEVER_MEMORY:-16G}"
export QLEVER_CACHE="${QLEVER_CACHE:-8G}"

c()   { printf '\n\033[1;36m» %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ -x "$PY" ] || die "python not found: $PY (set AH_PYTHON)"
[ -n "${QLEVER_ACCESS_TOKEN:-}" ] || die "QLEVER_ACCESS_TOKEN not set (~/.config/qlever/env)"
docker compose version >/dev/null 2>&1 || die "docker compose plugin missing"
mkdir -p "$KG_DIR"

c "Building the Bestandsgraph into $KG_DIR"
cd "$PKG"
set +e
"$PY" -m knowledge_hub.bestand_build --out "$KG_DIR" ${1:+"$1"}
code=$?
set -e
case "$code" in
    0) ;;
    3) # Inputs unchanged. But an earlier run may have swapped the parts and then
       # failed to index them: the endpoint is the judge, not the manifest.
       if "$PY" -m knowledge_hub.bestand_build --out "$KG_DIR" --check http://127.0.0.1:7019/ ; then
           c "Inputs unchanged, endpoint serves the build — nothing to do"; exit 0
       fi
       c "Inputs unchanged, but the endpoint does not serve the build — reindexing" ;;
    *) die "build failed — the previous index still serves" ;;
esac

c "Graph changed — indexing into index.new/"
printf '{ "ascii-prefixes-only": false, "num-triples-per-batch": 1000000 }\n' \
    > "$KG_DIR/$QLEVER_INDEX.settings.json"
"${COMPOSE[@]}" run --rm index || die "index build failed — the previous index still serves"
[ -n "$(ls -A "$KG_DIR/index.new" 2>/dev/null)" ] || die "index.new is empty — not swapping"

c "Swapping index.new/ for index/"
"${COMPOSE[@]}" stop qlever || true
rm -rf "$KG_DIR/index.old"
[ -d "$KG_DIR/index" ] && mv "$KG_DIR/index" "$KG_DIR/index.old"
mv "$KG_DIR/index.new" "$KG_DIR/index"
"${COMPOSE[@]}" up -d qlever

c "Verifying the endpoint against the build"
for _ in $(seq 1 60); do
    if "$PY" -m knowledge_hub.bestand_build --out "$KG_DIR" --check http://127.0.0.1:7019/ ; then
        rm -rf "$KG_DIR/index.old"
        "$PY" -m knowledge_hub.bestand_build --out "$KG_DIR" --status
        exit 0
    fi
    sleep 5
done
die "endpoint does not serve the new build; the previous index is in $KG_DIR/index.old — logs: ${COMPOSE[*]} logs qlever"
