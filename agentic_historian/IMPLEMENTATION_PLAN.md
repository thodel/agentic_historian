# Agentic Historian — Implementation Plan

**Status:** draft · **Updated:** 2026-07-01 · **Companion repos:** `serving-atr-inference`

## Overview

The Agentic Historian is an autonomous pipeline for transcribing, describing, and analysing historical handwritten documents (14th–16th century, Swiss/German administrative sources). The next phase extends it with **parallel multi-source entity search** across independent data sources via MCP.

---

## Knowledge Hub Architecture — MCP-federated (core principle)

**Every authority/register source in the Knowledge Hub is provided over MCP — not loaded into a local store.** Persons, places, organisations and cross-references (HLS, HBLS, SSRQ, KF, EOS) and external authorities (GND, Wikidata) are each an **MCP server** that the pipeline queries at request time. The local `knowledge_hub/hub.py` holds only the **controlled vocabulary** (Taxonomien / care terms) and an optional thin cache — it is **not** the source of authority data.

Consequences:
- **Agent C entity linking** resolves persons/places by querying the MCP federation in parallel — not a local JSON/SQLite dump.
- The previous **AH-80 epic (#58–#68: local HLS-KNEX / HBLS loaders + SQLite store)** is **superseded**. Each of those sources becomes (or is fronted by) an MCP server; re-scope those issues to "build/point at the `<source>` MCP" rather than "load into a local hub."
- The offline HLS dump (`ENABLE_HLS_LOOKUP` / `hls.json`) survives only as an **offline fallback**; the primary path is the HLS/SSRQ MCP.
- Adding new authority data = stand up an MCP server + register it. **No app-side schema migration.**
- One shared client (`utils/mcp_client.py`) + a common `PersonResult`/`PersonRecord` contract (below) normalises every source.

---

## Data Sources & MCP Servers

| Source | Content | MCP Port | Status |
|---|---|---|---|
| **EOS** | 75,447 documents, 893,303 spans; HGB Basel | 8000 | ✅ Running |
| **KF** | Königsfelden register; persons, places, entries | 8001 | ✅ Running |
| **SSRQ** | 23,674 persons, 7,047 orgs, 138k name variants; Swiss legal history | 8002 | ✅ Running |
| **HBLS** | 137,038 merged person records; Historical Language Services | TBD | ⬜ Needs building |
| **HLS** | Historisches Lexikon der Schweiz — person/place authority | TBD | ⬜ MCP (replaces the local `hls.json` dump) |
| **GND / Wikidata** | External authority reconciliation | (existing MCP) | ✅ Available (`wikidata` MCP) |

**SSRQ MCP** (`https://tei.dh.unibe.ch/mcp/ssrq/`) is the newest addition (2026-07-01). All hub sources — including HLS and GND/Wikidata — are consumed the same way: as MCP servers behind the common `PersonResult` contract.

---

## Goal

Answer researcher queries by searching **all four sources in parallel**, resolving entity identity across them, and returning a unified, source-attributed response.

Example query: *"What records exist for Friedrich von Erdingen?"* → federated search across SSRQ, KF, EOS, HBLS with deduplication and confidence ranking.

---

## Architecture

```
                          [User query]
                                │
                    [Orchestrator / Router]
                   (spawns 4 parallel subagents)
                    /       |       \        \
           [KF MCP]  [SSRQ MCP]  [EOS MCP]  [HBLS MCP]
           port 8001  port 8002   port 8000   port TBD
               \        |         /          /
                \       |        /          /
                 [Entity Resolver + Merger]
                 (deduplicate by ID / name / time / place)
                                │
                    [Unified response + source attribution]
```

### Subagent Pattern

Each subagent is spawned with `runtime=subagent`, calls its respective MCP tool directly, and returns structured JSON. The orchestrator collects and merges.

```python
# Pseudocode
async def search_all(query: str) -> list[Entity]:
    tasks = [
        spawn_subagent(mcp="kf",     tool="search_persons", args={"query": query}),
        spawn_subagent(mcp="ssrq",   tool="search",         args={"query": query}),
        spawn_subagent(mcp="eos",    tool="search",         args={"query": query}),
        spawn_subagent(mcp="hbls",   tool="search_persons", args={"query": query}),
    ]
    results = await asyncio.gather(*tasks)
    return merge(results)
```

### Entity Resolution Strategy

| Signal | Confidence | Action |
|---|---|---|
| Exact ID match (GND, HLS ID, stable ID across sources) | High | Merge |
| Exact name match + overlapping life dates (±25 yr) | High | Merge |
| Forename + surname in variant lists + overlapping dates | Medium | Merge, flag for review |
| Same location + same era + surname match only | Low | Separate entries |
| No signal | — | Keep separate |

Confidence levels map to `high`, `medium`, `low`, `unresolved`.

---

## MCP Server Interface Standard

All four MCP servers should expose compatible interfaces:

```python
# Person search
search_persons(query: str, limit: int = 20) -> list[PersonResult]
# Person by ID
get_person(pid: str) -> PersonRecord
# Full-text / register search
search_fulltext(query: str, limit: int = 20) -> list[TextHit]
```

```python
class PersonResult(BaseModel):
    source: Literal["kf", "ssrq", "eos", "hbls"]
    pid: str                          # source-local ID
    name: str
    forename: str | None
    surname: str | None
    life_dates: str | None            # "1300–1370" or "fl. 1348"
    occupation: str | None
    hls_id: int | None                # Historical Language Services ID
    gnd_id: str | None
    wikidata_id: str | None
    notes: str | None
    # Variant names (alias_index for SSRQ)
    variants: list[str] = []
    # Mention count / record count
    mention_count: int = 0
    # Entry/register references
    entries: list[str] = []

class PersonRecord(PersonResult):
    """Full authority record for a single person."""
    relationships: list[Relationship] = []
    geo: tuple[float, float] | None   # (lat, lon) if place
    all_entries: list[RegisterEntry] = []
```

---

## HBLS MCP — Build Plan

**Priority:** 2nd milestone (after confirming parallel search works with 3 sources).

### 1. Clone and analyse

```bash
git clone https://github.com/thodel/eos_persons.git /home/dh/eos_persons
```

Data format TBD — likely JSON or SQLite. Key fields: `n` (canonical name), `v` (variants), `y` (year range), `c` (mention count), `d` (dossier count), `occ`, `loc`, `dos`, `hls`, `wd`.

### 2. Build MCP server

Pattern: FastAPI + SSE/streamable-http, same structure as SSRQ MCP. Expose:

- `GET /health` → `{status, hbls_version, person_count}`
- `GET /persons/search?q=<name>&limit=20` → list of `PersonResult`
- `GET /persons/<hbls_id>` → full `PersonRecord`
- `GET /search?q=<name>` → alias for search

### 3. Register MCP

```bash
openclaw mcp add hbls --url https://tei.dh.unibe.ch/mcp/hbls/ --port TBD
openclaw gateway restart
```

### 4. Verify

```bash
ssrq ping  # already works
# Once HBLS MCP is up:
curl https://tei.dh.unibe.ch/mcp/hbls/health
```

---

## Milestones

### Milestone 1 — Parallel search (KF + SSRQ + EOS) ✅
**Status:** Ready to implement.

1. Extend `knowledge_hub/hub.py` or create `agents/search_agent.py`
2. Use `sessions_spawn` with `runtime=subagent` for parallel MCP calls
3. Implement entity resolver (ID match → high confidence; name+date → medium)
4. Return unified results with source attribution

**Verify:** Query *"Johann"* across all three sources; confirm deduplicated results.

### Milestone 2 — HBLS MCP 🏗️
1. Clone `github.com/thodel/eos_persons`
2. Analyse data format and schema
3. Build FastAPI MCP server (HTTP/SSE)
4. Deploy on tei (port TBD, check 8003+)
5. Register with OpenClaw MCP

**Verify:** `curl https://tei.dh.unibe.ch/mcp/hbls/health`

### Milestone 3 — Four-source merge
1. Add HBLS subagent to parallel search
2. Cross-match HBLS ↔ SSRQ via HLS ID (`pers:refs` in SSRQ TTL)
3. Cross-match HBLS ↔ KF via name+date (existing `kf_ssrq_hls_crossref.json` has 62 triple-linked records)
4. Confidence-ranked unified response

**Key crosswalk files (already built):**
- `~/.openclaw/tmp/kf_hls_id_map.json` — 162 KF→HLS IDs (155 high confidence, 7 medium)
- `~/.openclaw/tmp/kf_ssrq_hls_crossref.json` — full 5,260-person crossref
- `~/.openclaw/tmp/kf_ssrq_exclusions.json` — persons excluded from safe crosswalk

### Milestone 4 — API / Discord interface
1. `/search <name>` slash command → federated search
2. `/entity <id>` → unified entity record across all sources
3. `/compare <name>` → side-by-side comparison from each source
4. Progress reports to #allgemein

### Milestone 5 — Corpus integration
1. Entity resolution on extracted persons (Phase 5 / Agent C output)
2. **Agent C links via the same MCP federation** (KF register, SSRQ authority, HLS, HBLS) — one shared client + resolver as `/search`, no local authority store
3. GND/Wikidata reconciliation via the `wikidata` MCP

---

## Key Challenges

| Challenge | Mitigation |
|---|---|
| Different ID schemes (GND, HLS, local) | Use name+date+place as fallback; prefer ID matches |
| Name variants (Johann ↔ Hans, Maria ↔ Marie) | Cross-reference variant lists; SSRQ has `alias_index` |
| Julian/Gregorian date ambiguity | Store ranges as `fl. YYYY` or `YYYY–YYYY`; don't require precision |
| Partial MCP failures | Timeout + partial results; flag failed sources |
| HBLS data quality (0.6% HLS-link rate) | Supplement with Wikidata/GND where available |

---

## Existing Components

| File | Role |
|---|---|
| `bot.py` | Discord slash commands |
| `orchestrator.py` | A→B→C pipeline wiring |
| `agent_a/` | HTR pipeline |
| `agent_b/` | Source description |
| `agent_c/` | Entity extraction (NER) |
| `knowledge_hub/hub.py` | Controlled vocabulary + thin cache — **authority data now via MCP federation**, not stored here |
| `utils/mcp_client.py` | (to build) shared async client for all knowledge-hub MCP servers |
| `serving-atr-inference/config/models.yaml` | ATR model registry (kraken/VLM/TrOCR/party) |
| `serving-atr-inference/` | Inference server the bot's `KRAKEN_SERVICE_URL` points at |

---

## Phases (updated)

- Phase 0 — GitHub setup & exec approvals ✅
- Phase 1 — Scaffold & Discord bot ✅
- Phase 2 — Knowledge hub ✅
- Phase 3 — OCR (HTR) pipeline ✅
- Phase 4 — Source description ✅
- Phase 5 — Entity extraction (NER) ✅
- Phase 6 — Corpus analysis ✅
- Phase 7 — Meta agent ✅
- Phase 8 — Hot folder integration 🔄
- **Phase 9 — Multi-source federated search** 🔄 *(was Phase 9: Testing & tuning)*
- Phase 10 — HBLS MCP integration ⬜
- Phase 11 — Unified entity resolution & API ⬜