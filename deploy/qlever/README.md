# QLever endpoint for the corpus graph — runbook

Stands up a public, read-only SPARQL endpoint on `tei.dh.unibe.ch` over the
corpus graph built by KG-2 (#328) and KG-3 (#329). Issue: #330.

Everything before "On tei" is already tested offline. The host steps are the
ones that need you — work through them in order and stop at the first check
that fails.

## Publication scope (owner-approved)

Approved by the project owner (T. Hodel) on **2026-07-30**:

| Decision | Setting |
|---|---|
| Endpoint | public, read-only SELECT |
| Care-flagged documents | **included** |
| Person nodes | **all published**, authority id not required |

Rationale: the processed corpus is 14th–16th c. administrative material, so the
"recent enough to matter" concern behind the issue's privacy note does not bite.

The scope is not hard-coded. To narrow it later, set any of these and re-run
`refresh.sh` — the build reports what each one withheld, so an omission is never
silent:

```bash
KG_PUBLISH_CARE_FLAGGED=false          # drop care-flagged documents
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

## On tei

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

## A real query

"Which readings of a page exist, what produced each, and which did a historian
prefer?" — the question the graph was built to answer:

```sparql
PREFIX crm:   <http://www.cidoc-crm.org/cidoc-crm/>
PREFIX prov:  <http://www.w3.org/ns/prov#>
PREFIX sdhss: <https://sdhss.org/ontology/>

SELECT ?reading ?producer ?confidence ?preferred WHERE {
  ?doc crm:P128_carries ?reading .
  OPTIONAL { ?reading prov:wasGeneratedBy ?run .
             ?run prov:used ?producer .
             OPTIONAL { ?run sdhss:confidence ?confidence } }
  OPTIONAL { ?reading sdhss:closestReading ?preferred }
}
```

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
