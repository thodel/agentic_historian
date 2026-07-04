# Knowledge Hub — HLS & HBLS Data Integration Plan

Status: **proposal** · Author: planning session 2026-06-26 · Tracking epic: **AH-80**

## 1. Goal

Replace the seed-only Knowledge Hub (`knowledge_hub/hub.py`, currently two example
records) with a **real authority-controlled person/place register** built from two
Swiss biographical corpora, so that Agent C (`agents/entity_agent.py`) can link
document mentions to durable identities (HLS / GND / Wikidata / VIAF) instead of
finding nothing.

Two data sources:

| Source | Location | What it is | Volume |
|---|---|---|---|
| **HLS / KNEX** | `/Users/TH_1/Documents/HLS/knex_poc_hls_extract/` | KNEX proof-of-concept extraction: per person a CIDOC-CRM `graph.csv` + `log.txt` (source HLS article + structured birth/activities). Backed by the full HLS dump (`hls_articles.json`, ~60k bios) in the HLS root. | 140 persons (PoC); ~60k (full) |
| **HBLS** | `Repo/eos_persons/hbls-extraction/` (the **data folder**) | Already-extracted *Historisch-Biographisches Lexikon der Schweiz* (1921–34): `hbls_persons.json` (27,838 records: surname, given, life years, bio, volume/page, backlink) + `gnd_enrichment.json` (1,813 GND-enriched: roles, sameAs, publications) + `hbls_persons_basel.json` (4,932 Basel slice). | 27,838 persons |

This directly addresses **#39** (HLS-Linking missing) and feeds **#26** (swappable
QLEVER backend) and **#43** (embedding/reranker for entity linking).

## 2. Reuse from `eos_persons` — don't reinvent

`eos_persons` already solved the hard parts. The plan is to **port**, not rebuild:

| eos_persons module | What it gives us | Where it lands in agentic_historian |
|---|---|---|
| `link_hls.py` | name normalisation (particle stripping, given-name canonicalisation, accent folding), `SequenceMatcher` surname/given ratios, life-span temporal agreement, `score = 0.4·surname + 0.3·given + 0.3·date`, `n_candidates` ambiguity flag | `knowledge_hub/linking.py` (shared matcher) |
| `build_persons.py` | font-aware HBLS small-caps parser (already run → `hbls_persons.json`) | reuse output; no re-parse needed |
| `build_identity_clusters.py` | cross-corpus identity graph: nodes `(corpus, local_id)` + authority ids `gnd:`/`wd:`; connected components = one person; over-merge guards (intra-corpus, birth-spread, ambiguous edges) | `knowledge_hub/clustering.py` |
| `link_hbls_gnd.py` + `enrich_wikidata.py` | Tier-0 transitive GND via HLS→Wikidata (P902→P227), date cross-check | `knowledge_hub/enrich/gnd_wikidata.py` |
| `link_hbls_gnd_lobid.py` (+ `.lobid_cache/`) | Tier-1 direct lobid GND lookup with on-disk cache | `knowledge_hub/enrich/gnd_lobid.py` |
| `DEDUP_PLAN.md`, `GND_LINKING_PLAN.md` | the tested matching thresholds, tier strategy, Basel-first sequencing | design reference for the issues below |

Sequencing principle from eos_persons: **Basel slice first** (densest, reviewable),
tune thresholds, then `--all`.

## 3. Target hub schema (v2)

Current person dict: `id, name, variants, role, active_period, location, wikidata,
gnd, hls, notes`. Extend (backward-compatible — additive keys) to carry merged
provenance, matching the eos_persons merged-person model:

```jsonc
{
  "id": "hub_p_000123",
  "name": "Albert Alder",                // preferred (HLS form if present, else HBLS)
  "variants": ["Albert Alder"],
  "given": "Albert", "surname": "Alder",
  "birth_year": 1888, "death_year": 1980, "floruit_years": null,
  "roles": ["Arzt", "Hämatologe"],       // HLS occupation + GND professionOrOccupation
  "location": "Aarau", "region": "AG", "coordinates": null,
  "wikidata": "Q...", "gnd": "...", "hls": "014267", "viaf": "...",
  "sources": [                            // full provenance, one per contributing corpus
    {"corpus": "hls",  "id": "014267",     "url": "https://hls-dhs-dss.ch/de/articles/014267/"},
    {"corpus": "hbls", "id": "hbls:1:51",  "backlink": "file://.../HBLS_band_01.pdf#page=70"}
  ],
  "notes": ""
}
```

Places/orgs get the analogous treatment (the KNEX graph already yields
`Geographical Place` and `Group` entities with appellations).

The **stable interface stays untouched** (`search_person/find_person/search_place/
find_place/add_*`), so Agent C and the future QLEVER backend (#26) keep working.

## 4. Architecture

```
knowledge_hub/
  hub.py                 # unchanged public API; gains an index-backed store
  linking.py             # PORT of link_hls.py — shared fuzzy matcher
  clustering.py          # PORT of build_identity_clusters.py — cross-corpus dedup
  loaders/
    hls_knex.py          # KNEX graph.csv + log.txt  -> hub records
    hbls.py              # hbls_persons.json + gnd_enrichment.json -> hub records
  enrich/
    gnd_wikidata.py      # Tier-0 transitive (HLS->WD->GND)
    gnd_lobid.py         # Tier-1 lobid lookup (cached)
  build_hub.py           # orchestrates: load -> link -> cluster -> enrich -> emit hub.json
  data/
    hub.json             # current store (seed); replaced by built register
    hub.sqlite           # index for ~30k+ persons (see AH-87)
```

## 5. Phased work (→ issues)

1. **Schema v2 + store index** — extend dicts; back substring search with an index
   so ~30k persons stay fast; keep the public API stable. (AH-81, AH-87)
2. **HLS KNEX loader** — parse the 140 CIDOC-CRM `graph.csv` into person/place/org/
   role records; pull the HLS article id from `log.txt`. (AH-82)
3. **HBLS loader** — ingest `hbls_persons.json` + `gnd_enrichment.json`; Basel slice
   first. (AH-83)
4. **Shared matcher** — port `link_hls.py` to `knowledge_hub/linking.py`. (AH-84)
5. **Cross-corpus clustering** — port `build_identity_clusters.py`; merge HLS↔HBLS on
   `gnd:`/`wd:` + name+date with over-merge guards. (AH-85)
6. **GND/Wikidata enrichment** — Tier-0 transitive + Tier-1 lobid, cached. (AH-86)
7. **Wire into Agent C** — entity_agent links PERSON/PLACE against the real register
   and writes `hls`/`gnd`/`wikidata`/`viaf` to output. Closes the core of **#39**. (AH-88)
8. **Embedding shortlist (optional)** — qwen3-embedding + jina-reranker to pre-rank
   hub candidates before LLM disambiguation; ties into **#43**. (AH-89)
9. **Build pipeline + docs** — `build_hub.py` one-shot refresh + README. (AH-90)

## 6. Open questions

- **Full HLS vs KNEX PoC**: start with the 140-person KNEX export (rich, structured),
  then scale to the full `hls_articles.json` (~60k) once the loader is proven.
- **Where the data lives**: read eos_persons / HLS paths directly for the build, but
  commit the *built* `hub.json` (and/or `hub.sqlite`) so the bot runs without those
  external trees mounted.
- **Backend**: SQLite now (AH-87) keeps the door open for QLEVER/RDF later (#26); the
  KNEX CIDOC-CRM triples are already RDF-shaped and map cleanly to a triple store.
