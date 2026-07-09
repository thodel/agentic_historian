# Agentic Historian — Implementation Plan
**Status:** 2026-06-29 | **Author:** dh-bot

---

## 1. Current System State

Four data sources, two already served as MCP:

### MCP Servers (live)

| Port | Source | Database | Size | Key content |
|------|--------|----------|------|-------------|
| **8000** | EOS/HGB (Basel) | `/home/dh/eos_data/hgb.db` | 215 MB | 75,447 documents, 893,303 spans, 360,709 events, FTS5 indexed |
| **8001** | KF (Königsfelden) | `/home/dh/kf_data/kf.db` | 25 MB | 1,557 entries, 5,260 persons, 2,270 orgs, 1,333 places |
| **8002** | SSRQ | `/home/dh/.openclaw/tmp/ssrq_v6.db` | ~50 MB | 23,674 persons, 7,047 orgs, 138,298 name variants |

### Data Not Yet Served via MCP

| Source | Location | Records | Notes |
|--------|----------|---------|-------|
| **HBLS** (HLS) | `/home/dh/.openclaw/workspace/eos_persons/hbls_web.json` | 18,244 entries | Swiss historical lexicon — places, families, events, not persons |
| **HLS biographies** | `/home/dh/.openclaw/workspace/eos_persons/hls_articles.csv` | ~ | Separate CSV with person biographies |
| **persons_resolved** | `/home/dh/.openclaw/workspace/eos_persons/persons_resolved.json` | ~ | HGB persons resolved/merged with external IDs |

### MCP Tool Overview (each server)

**EOS/HGB (port 8000)** — FastMCP/SSE transport:
- `search_persons(query, limit)` — FTS over person span text
- `get_document(doc_id)` — full doc with NLP spans
- `get_dossier(dossier_id)` — all docs in a property dossier
- `search_text(query, limit)` — FTS over raw transcription text
- `get_persons_in_year_range(year_from, year_to, limit)`
- `get_cooccurrences(person_name, limit)` — co-occurrence network
- `corpus_stats()`, `list_dossiers(limit)`

**KF (port 8001):**
- `kf__search_persons(query, limit)` — search persons
- `kf__get_person(id)` — person by ID
- `kf__search_fulltext(query, limit)` — register fulltext search

**SSRQ (port 8002):**
- Persons: 23,674 with name variants
- Orgs: 7,047
- `std_name`, `forename`, `surname`, `orig_names`, `std_names` fields
- `name_index` table: 138,298 variant name → person mappings

**HBLS (port 8003) — TO BUILD:**
- 18,244 HBLS entries: `{k: id, v: volume, p: page, s: text, m: mentions, url}`
- Types: places, families, events, guilds, institutions (NOT person biographies)
- HLS biographies: CSV with `category == "bio"` entries

---

## 2. Architecture

```
[User query]
       ↓
[OpenClaw orchestration — sessions_spawn with runtime=subagent]
       ↓
   ┌───┴───┬──────────────┐
   ↓       ↓              ↓
[SSRQ]  [KF]          [EOS/HGB]      [HBLS MCP]
8002    8001           8000           8003 (to build)
   └───────┴──────────────┘
              ↓
     [Entity resolver + deduplicator]
              ↓
    [Unified response + source attribution]
```

**Key design principles:**
- Parallel subagent queries via `sessions_spawn` (one per MCP)
- Merge by: exact GND/ID match → fuzzy name + date overlap → place + era match
- Confidence scoring: high (>0.9 GND), medium (name+date), low (name-only)
- Partial failure: if one MCP is down, return what was found; don't block

---

## 3. Milestones

### Milestone 1 — Parallel search across SSRQ + KF ✅
**Status:** SSRQ MCP at port 8002 is built and live.

**Next step:** Extend OpenClaw orchestration to call both in parallel.
```python
async def search_persons(name: str):
    async def ssrq_search():
        # call SSRQ MCP port 8002
    async def kf_search():
        # call KF MCP port 8001
    results = await asyncio.gather(ssrq_search(), kf_search())
    return merge_results(results)
```

### Milestone 2 — Integrate EOS/HGB into parallel search
**Status:** Server running at port 8000.
**Next step:** Add EOS/HGB as third parallel source.
- Tool: `search_persons(query, limit)` → returns doc_id, year, location, confidence
- Challenge: EOS returns *mentions* (a person mentioned in a document), not *entities* (a biographical record)
- Requires: disambiguation — multiple mention rows for the same real person

### Milestone 3 — Build HBLS MCP server (port 8003)
**Status:** HBLS data exists at `eos_persons/hbls_web.json` (18,244 entries).
**Data format:**
```json
{"k": "AAALP", "v": 1, "p": 13,
 "s": "(Kt. Obwalden...). Hochalp am S.-Ende...",
 "m": [], "url": "https://.../HBLS_band_01.pdf#page=13"}
```
Each entry is a place/family/event, NOT a person. For person biographies, use `hls_articles.csv` (category == "bio").

**Steps:**
1. Clone `eos_persons` repo
2. Analyse `hls_articles.csv` for biography entries
3. Build FastMCP server exposing:
   - `search_hbls(query, limit)` — search HBLS entries
   - `get_hbls_entry(id)` — get entry by HBLS key
   - `search_hls_biographies(name, limit)` — search HLS bio articles
4. Serve on port 8003 (free)

### Milestone 4 — Cross-source entity resolution
**Matching strategy:**
1. **GND/ID exact match** — if two sources share a GND ID or stable ID → same person (confidence: high)
2. **Name + date overlap** — same name (fuzzy match) + overlapping birth/death → likely same (confidence: medium)
3. **Place + era** — same location + same time window → candidate (confidence: low)

**Name normalization:**
- Lowercase, strip diacritics (ä→a, ö→o, ü→u, ß→ss)
- Expand abbreviations (Hans↔Johann, Maria↔Marie)
- Strip prefixes/suffixes (von, de, le, -Österreich)

**Data to resolve across:**
- SSRQ: `per000001`… `forename`, `surname`, `birth/death years`, `orig_names`
- KF: `per000001`… `forename`, `surname`, `birth/death`, `gnd_id` (if present)
- EOS/HGB: span mentions — no stable person ID, just name + doc + year
- HBLS: GND-linked entries via `link_hbls_gnd_lobid.py`

### Milestone 5 — Unified person record
**Schema:**
```json
{
  "canonical_name": "Hans von Root",
  "sources": [
    {"source": "SSRQ", "id": "per000042", "confidence": 0.95},
    {"source": "KF",   "id": "per000987", "confidence": 0.87}
  ],
  "life_span": {"from": 1450, "to": 1512},
  "name_variants": ["Hans von Root", "Hanns Rot", "Johannes de Radice"],
  "places": ["Basel", "Liestal"],
  "occupations": ["Bürger", "Gerber"],
  "gnd_ids": ["123456789"],
  "notes": "Appears in SSRQ in 1450–1512, KF register 1455"
}
```

---

## 4. Concrete Next Steps (Priority Order)

1. **[Tobias]** Test `!pull_folder test` with the SwitchDrive fix to confirm it works in production
2. **[Tobias]** Create MCP for HBLS at port 8003 — follow the `eos_persons/mcp_server/` pattern
3. **[dh-bot]** Implement parallel search subagent orchestration calling SSRQ + KF simultaneously
4. **[dh-bot]** Add EOS/HGB as third source; handle mention-to-entity disambiguation
5. **[dh-bot]** Integrate HBLS as fourth source; implement cross-source GND deduplication
6. **[Tobias]** Build the unified person record schema; define confidence scoring

---

## 5. Open Questions

- **KF Gnd IDs:** Does the KF `persons` table have GND/IDSRG fields? (currently unknown — needs inspection)
- **EOS mention deduplication:** How many unique persons are in 893k spans? Needs entity clustering.
- **HBLS biography CSV:** Is `hls_articles.csv` available locally or does it need to be sourced?
- **Parallel failure handling:** If one MCP is down, should results from other sources still be returned?
- **Command prefix:** Currently `!` — does Tobias want Discord slash commands instead?

---

## 6. Pending PRs

| PR | Branch | Description | Status |
|----|--------|-------------|--------|
| #77 | `fix/ah-52-voyant-local` | Use local Voyant server | OPEN |
| #81 | `fix/ah-73-kraken-client-config` | Fix kraken config import | MERGED ✅ |
| #84 | `fix/switchdrive-path-resolution` | `/pull test` path fix | MERGED ✅ |