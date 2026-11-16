# QLever endpoint for the corpus graph — runbook

Stands up a public, read-only SPARQL endpoint on `tei.dh.unibe.ch` over the
corpus graph built by KG-2 (#328) and KG-3 (#329). Issue: #330.

> **Since phase 2 of #325 (16.11.2026) QLever runs on idhefix and serves the
> Bestandsgraph** — Dodis, Königsfelden, SSRQ, MLS, SIKART, Bullinger, Escher
> and, when given, the corpus graph. tei only proxies `/sparql/` through an SSH
> tunnel. That runbook is the next section; the tei section further down is
> phase 1 and stays as the record of how the corpus-only endpoint was meant to
> run (it never did, #330 comment of 14.10.2026).

## Phase 2: the Bestandsgraph on idhefix, `/sparql/` on tei

Why idhefix: tei has 3.9 GB RAM and one core; idhefix 257 GB and 48, and the
holdings' databases lie there (`docs/KG_BESTANDSGRAPH_PLAN.md`, decision 1).

| Piece | Where | What |
|---|---|---|
| `knowledge_hub/bestand_build.py` | idhefix | runs the seven exporters, one sorted `.nt` per holding under `~/kg/parts/`, one manifest; rebuilds only when an input or the commit changed; a failed holding, or one that shrank below half its last build, swaps nothing in |
| `refresh_bestand.sh` + `docker-compose.bestand.yml` | idhefix | indexes `parts/*.nt` into `index.new/`, swaps it for `index/`, starts QLever on `127.0.0.1:7019`, checks the served count against the manifest |
| `systemd/qlever-refresh.{service,timer}` | idhefix, user `tobias` | nightly 03:17 |
| `systemd/sparql-tunnel.service` | tei, user `dh` | `127.0.0.1:7019` on tei → idhefix |
| `nginx-qlever.conf` | tei | `/sparql/` → the tunnel; refuses `access-token`/`cmd` parameters, strips `Authorization` |

**The access token is not in the repository.** Phase 1's compose file defaults
it to `ah-corpus-admin`, and nginx forwards query strings — a token anyone can
read would reach QLever's admin commands. Phase 2 requires
`QLEVER_ACCESS_TOKEN` from `~/.config/qlever/env` on idhefix and fails without it.

Human Lineage (HL-9) is not a source until its licence is decided (#830).

### On idhefix, as `tobias`

1. A checkout with a venv: `git clone` into `~/agentic_historian`, then
   `python3 -m venv agentic_historian/.venv` and install the package's
   requirements into it.
2. `~/.config/qlever/env`, mode 0600, with `KG_DIR`, `KG_SOURCE_DB_DIR`,
   `KG_SSRQ_REGISTER`, `KG_SSRQ_TEI` and `QLEVER_ACCESS_TOKEN` (generated once
   with `openssl rand -hex 24`, never printed).
3. The first build by hand, to read what it says:
   `set -a; . ~/.config/qlever/env; set +a; bash deploy/qlever/refresh_bestand.sh`.
   Expected tail: `serves N triples = build …` and the per-holding table.
4. The timer: copy `systemd/qlever-refresh.{service,timer}` to
   `~/.config/systemd/user/`, `systemctl --user daemon-reload`,
   `systemctl --user enable --now qlever-refresh.timer`;
   `loginctl show-user tobias -p Linger` must say `yes`.

### On tei, as `dh`

1. `git pull --ff-only` in `/home/dh/agentic_historian`.
2. The tunnel: copy `systemd/sparql-tunnel.service` to
   `~/.config/systemd/user/`, `daemon-reload`, `enable --now sparql-tunnel`;
   a `COUNT(*)` against `http://127.0.0.1:7019/` gives the number idhefix gives.
3. nginx: `nginx-qlever.conf` to `/etc/nginx/snippets/qlever.conf`, one
   `include` line in the `tei.dh.unibe.ch` server block, `nginx -t` before
   every reload. From outside: the same count at `https://tei.dh.unibe.ch/sparql/`,
   and `?cmd=clear-cache` answers 403.
4. The bot asks the endpoint once `KG_SPARQL_ENDPOINT=http://127.0.0.1:7019/`
   is in `/etc/agentic_historian.env` (KG-11, `/sparql`).

### Health, any time

`python -m knowledge_hub.bestand_build --out ~/kg --status` shows what is
built, from when, per holding. `--check http://127.0.0.1:7019/` compares the
served triple count with the build (exit 1 = stale or down). Every exporter
also stamps its build into the graph as a `prov:Activity` with
`prov:endedAtTime`, so the age of each holding is a SPARQL question as well.


Everything before "On tei" is already tested offline. The host steps are the
ones that need you — work through them in order and stop at the first check
that fails.

## Publication scope (owner-approved)

Approved by the project owner (T. Hodel) on **2026-07-30**:

| Decision | Setting |
|---|---|
| Endpoint | public, read-only SELECT |
| Care-flagged documents | **included** |
| Unclassified documents | included, and marked as unclassified (`shb:careClassified false`, #631) |
| Person nodes | **all published**, authority id not required |

Rationale: the processed corpus is 14th–16th c. administrative material, so the
"recent enough to matter" concern behind the issue's privacy note does not bite.

The scope is not hard-coded. To narrow it later, set any of these and re-run
`refresh.sh` — the build reports what each one withheld, so an omission is never
silent:

```bash
KG_PUBLISH_CARE_FLAGGED=false          # drop care-flagged AND unclassified documents
KG_PUBLISH_PERSON_NODES=false          # drop PERSON / CARE_ACTOR nodes
KG_PUBLISH_REQUIRE_AUTHORITY_ID=true   # keep only persons with a GND/HLS/Wikidata id
```

## Build the graph (works anywhere, no Docker)

```bash
python -m knowledge_hub.corpus_build --out build/corpus.ttl
```

Prints documents / readings / mentions / entities / authority links, and writes
`build/corpus.ttl.manifest.json` with the triple count and build time. Re-running
on unchanged inputs is byte-identical, which is what makes rebuild-and-swap safe.

Check what is currently built at any time:

```bash
python -m knowledge_hub.corpus_build --out build/corpus.ttl --status
```

## Phase 1: on tei (corpus graph only, superseded)

Prerequisite: Docker with the compose plugin. Verify before starting —

```bash
docker compose version
```

If that fails, stop here and install it; nothing below will work without it.

### 1. First-time setup

```bash
cd /home/dh/agentic_historian
git pull origin main
mkdir -p deploy/qlever/data
```

### 2. First build + index

```bash
bash deploy/qlever/refresh.sh
```

The script builds the graph, writes `deploy/qlever/data/corpus.ttl`, builds the
QLever index, starts the server on `127.0.0.1:7019`, and polls until it answers.
It refuses to swap on an empty build and leaves the previous index in place if
the index build fails, so a bad run cannot take the endpoint down.

Expected tail: `» Endpoint healthy` followed by the manifest JSON.

### 3. Check it locally, before exposing it

```bash
curl -s 'http://127.0.0.1:7019/?query=SELECT%20(COUNT(*)%20AS%20%3Fn)%20WHERE%20%7B%3Fs%20%3Fp%20%3Fo%7D'
```

The count must match `triples` in the manifest. If it does not, the served index
is stale — re-run step 2 rather than exposing it.

### 4. Expose via nginx

```bash
sudo cp deploy/qlever/nginx-qlever.conf /etc/nginx/snippets/qlever.conf
```

Add one line inside the existing `tei.dh.unibe.ch` server block:

```nginx
include /etc/nginx/snippets/qlever.conf;
```

Then — **always test before reloading**, a bad config takes the whole site down:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### 5. Verify from outside

```bash
curl -s 'https://tei.dh.unibe.ch/sparql/?query=SELECT%20(COUNT(*)%20AS%20%3Fn)%20WHERE%20%7B%3Fs%20%3Fp%20%3Fo%7D'
```

Same number as step 3. If step 3 works and step 5 does not, the problem is
nginx, not QLever.

## Refresh after new documents

```bash
bash deploy/qlever/refresh.sh
```

Safe to run on a cron or after a batch. If the graph is unchanged it exits
without touching the index.

## The canned queries

The questions a historian actually asks do not get typed into this endpoint by
hand — they are named, parameterised and tested in
`agentic_historian/knowledge_hub/queries.py` and reachable from Discord as
`/sparql`. See [`docs/KG_QUERIES.md`](../../docs/KG_QUERIES.md) (KG-5, #331).
`/sparql frage:<name> zeige_sparql:true` prints the query text for pasting into
the endpoint's own UI, with its parameters already bound.

## A real query

"Which readings of a page exist, what produced each, and which did a historian
prefer?" — the question the graph was built to answer:

```sparql
PREFIX crm:  <http://www.cidoc-crm.org/cidoc-crm/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX shb:  <https://tei.dh.unibe.ch/ontology/>

SELECT ?reading ?producer ?confidence ?preferred WHERE {
  ?doc crm:P128_carries ?reading .
  OPTIONAL { ?reading prov:wasGeneratedBy ?run .
             ?run prov:used ?producer .
             OPTIONAL { ?run shb:confidence ?confidence } }
  OPTIONAL { ?reading shb:closestReading ?preferred }
}
```

`shb:` is our own vocabulary (KG-12, #659): what the pipeline says about a
reading, a run or a decision, registered with domain and range in
`knowledge_hub/ontology/registry.py`. Everything else is CIDOC CRM 7.1.3,
SDHSS or PROV, and `corpus_build` refuses to write a graph that uses a term
outside that register — so a failed `refresh.sh` with «vocabulary gate refused»
in its output is the gate working, not the index.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `docker compose version` fails | compose plugin missing — install it first |
| Index build fails | check `docker compose logs index`; the old index still serves |
| Endpoint never comes up | `docker compose logs qlever`; usually port 7019 already taken |
| `nginx -t` fails | fix before reloading — a reload with a bad config drops the site |
| Counts differ between step 3 and the manifest | index is stale; re-run `refresh.sh` |
| Public 502, local works | nginx proxy or the `include` line; QLever is fine |

## What is deliberately not here

- **No live incremental updates.** Rebuild-and-swap only (#330 says so): the
  build is deterministic, so a rebuild is cheap and reproducible, whereas partial
  updates against a served index risk a half-written graph.
- **No write endpoint.** QLever's update endpoints need the access token, which
  stays on the host and is never proxied.
