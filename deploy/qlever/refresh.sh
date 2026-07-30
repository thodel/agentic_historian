#!/usr/bin/env bash
#
# refresh.sh — rebuild the corpus graph and swap the QLever index (KG-4, #330).
#
#     bash deploy/qlever/refresh.sh
#
# Rebuild-and-swap, never live incremental updates: the build is deterministic,
# so a rebuild either reproduces the current graph exactly or reflects real new
# data. The old index is kept until the new one is serving, so a failed build
# leaves the endpoint up on the previous graph.
#
# Overridable: AH_REPO, QLEVER_DIR.
set -euo pipefail

REPO="${AH_REPO:-/home/dh/agentic_historian}"
PKG="$REPO/agentic_historian"
PY="$PKG/.venv/bin/python"
QDIR="${QLEVER_DIR:-$REPO/deploy/qlever}"
DATA="$QDIR/data"

c()   { printf '\n\033[1;36m» %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ -x "$PY" ] || die "venv python not found: $PY"
mkdir -p "$DATA"

c "Building corpus graph"
cd "$PKG"
"$PY" -m knowledge_hub.corpus_build --out "$DATA/corpus.new.ttl"

[ -s "$DATA/corpus.new.ttl" ] || die "build produced an empty graph — refusing to swap"

# Nothing changed? Then there is nothing to reindex; the endpoint is current.
if [ -f "$DATA/corpus.ttl" ] && cmp -s "$DATA/corpus.new.ttl" "$DATA/corpus.ttl"; then
    rm -f "$DATA/corpus.new.ttl" "$DATA/corpus.new.ttl.manifest.json"
    c "Graph unchanged — index left as is"
    exit 0
fi

c "Graph changed — reindexing"
mv "$DATA/corpus.new.ttl" "$DATA/corpus.ttl"
[ -f "$DATA/corpus.new.ttl.manifest.json" ] \
    && mv "$DATA/corpus.new.ttl.manifest.json" "$DATA/corpus.ttl.manifest.json"

cd "$QDIR"
printf '{ "ascii-prefixes-only": false, "num-triples-per-batch": 100000 }\n' \
    > "$DATA/ah-corpus.settings.json"

docker compose stop qlever || true
docker compose run --rm index || die "index build failed — endpoint still on the previous index"
docker compose up -d qlever

c "Verifying endpoint"
for _ in $(seq 1 30); do
    if curl -sf 'http://127.0.0.1:7019/?query=SELECT%20(COUNT(*)%20AS%20%3Fn)%20WHERE%20%7B%3Fs%20%3Fp%20%3Fo%7D' >/dev/null; then
        c "Endpoint healthy"
        "$PY" -m knowledge_hub.corpus_build --out "$DATA/corpus.ttl" --status
        exit 0
    fi
    sleep 2
done
die "endpoint did not come up — check: docker compose logs qlever"
