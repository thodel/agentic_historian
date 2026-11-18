#!/usr/bin/env bash
#
# index_parts.sh — index $PWD/parts/*.nt into $PWD/index.new/ (KG-4, #330).
#
# Runs inside the QLever image, with the build directory as the working
# directory; docker-compose.bestand.yml (Docker) and refresh_bestand.sh
# (Podman) both call it, so the two engines index the same way.
#
# Named graphs (#830): a part with a sidecar `<part>.nt.graph` goes into the
# graph that file names (the Human Lineage layer, CC BY-SA 4.0); every other
# part into the default graph. A query without GRAPH sees the union.
set -euo pipefail

INDEX="${QLEVER_INDEX:-ah-bestand}"
rm -rf index.new && mkdir index.new
args=()
for f in parts/*.nt; do
    g=-
    if [ -s "$f.graph" ]; then g=$(head -n1 "$f.graph"); fi
    args+=(-f "$f" -g "$g")
done
[ "${#args[@]}" -gt 0 ] || { echo "no parts/*.nt to index" >&2; exit 1; }
qlever-index -i "index.new/$INDEX" -F nt "${args[@]}" -s "$INDEX.settings.json"
