# Agentic Historian — Implementation Plan

**Version:** 1.0  
**Date:** 2026-07-02  
**Status:** Draft for review

---

## 1. Overview & Vision

The Agentic Historian is a federated search and entity-resolution system for Swiss historical persons. It allows a researcher to enter a single name — "Johannes von Hallwyl", for example — and receive in return a unified, de-duplicated person entity that aggregates everything known about that individual across all available historical source databases.

The system sits as an orchestration layer above existing MCP (Model Context Protocol) servers that wrap individual data sources. It does not replace those sources; it harmonises them.

**Why this matters for Digital Humanities:**

Swiss historical research is fragmented across at least four major corpora — the *Sammlung schweizerischer Rechtsquellen* (SSRQ), the Königsfelden registers (KF), the existing Epoch Store person records (EOS), and the newly available HBLS/HGB Basel person dataset. A researcher investigating a medieval or early-modern figure must currently visit each source manually, then mentally reconcile name variants, conflicting dates, and ambiguous identifiers. The Agentic Historian automates that reconciliation, surfacing consensus and conflict across sources with explicit provenance.

The long-term vision is a system that can answer: *"Who was this person, what do we know, where does our knowledge come from, and how certain are we?"*

---

## 2. Current State

### 2.1 Deployed MCP Servers

| Server | Port | Data | Status |
|--------|------|------|--------|
| SSRQ MCP | 8002 | 23,674 persons, 7,047 orgs, 138,000+ name index entries | ✅ Deployed today (2026-07-02) |
| KF MCP | 8001 | Königsfelden register — `kf__search_persons`, `kf__get_person`, `kf__search_fulltext` | ✅ Exists |
| EOS MCP | 8000 | Epoch Store person records | ✅ Existing |
| HBLS MCP | — | HBLS/HGB Basel persons (`persons_resolved.json`, 137,038 records) | ❌ Not yet built |

### 2.2 Source Data Summary

| Source | Approx. Records | Key Fields | Notes |
|--------|----------------|------------|-------|
| SSRQ | 23,674 persons | Name, dates, roles, source document, GND | Rich metadata, source-critical |
| KF (Königsfelden) | ~thousands | Person name, register entry, role in abbey records | Monastic context; overlaps with SSRQ temporally |
| EOS | Unknown (existing) | Person records | Already integrated via port 8000 |
| HBLS | 137,038 merged person records | HGB Basel data; HLS-linked; GND-linked | Largest single source; requires new MCP server |

### 2.3 What Needs to Be Built

1. **HBLS MCP Server** — MCP wrapper around `persons_resolved.json` (137,038 records)
2. **Orchestration Layer** — OpenClaw-side logic to query all four MCP servers in parallel
3. **Entity Resolution Engine** — matching and merging logic across heterogeneous schemas
4. **Unified Response Layer** — confidence scoring, source attribution, external ID links

---

## 3. Target Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         User Query                               │
│              ("Johannes von Hallwyl", ~1400–1471)                │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                   OpenClaw Orchestration Layer                    │
│     (parallel fan-out, result aggregation, entity resolution)    │
└────┬──────────────┬──────────────┬──────────────┬────────────────┘
     │              │              │              │
     ▼              ▼              ▼              ▼
┌─────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────┐
│ SSRQ    │  │ KF MCP   │  │ EOS MCP  │  │ HBLS MCP       │
│ MCP     │  │ Port     │  │ Port     │  │ (NEW)          │
│ Port    │  │ 8001     │  │ 8000     │  │ Port TBD       │
│ 8002    │  │          │  │          │  │                │
│         │  │          │  │          │  │ 137,038 records│
└────┬────┘  └────┬─────┘  └────┬─────┘  └───────┬────────┘
     │             │             │                 │
     └─────────────┴─────────────┴─────────────────┘
                             │
                             ▼
              ┌──────────────────────────┐
              │  Entity Resolver + Merger │
              │  • ID deduplication       │
              │  • Fuzzy name + date match │
              │  • Confidence scoring     │
              └────────────┬─────────────┘
                           │
                           ▼
              ┌──────────────────────────┐
              │  Unified Person Record   │
              │  • Canonical name        │
              │  • Alternate names       │
              │  • Lifespan range        │
              │  • Source evidence       │
              │  • External links        │
              │    (HLS, HBLS, GND)      │
              │  • Confidence score      │
              └──────────────────────────┘
```

### Data Flow

1. User submits a person name query.
2. OpenClaw orchestration layer fans out **parallel** search requests to all four MCP servers simultaneously.
3. Each MCP server returns a list of candidate records with whatever schema it exposes.
4. The entity resolver ingests all candidates, applies matching rules (see §4), and produces merged clusters.
5. Each cluster is rendered as a unified person record with provenance and confidence scores.
6. The ranked list of unified records is returned to the user.

---

## 4. Entity Resolution Strategy

Entity resolution is the core algorithmic challenge. Records referring to the same real person may differ in spelling, date precision, and schema. The strategy uses a **tiered cascade**: cheap checks first, expensive ones only if needed.

### 4.1 Tier 1 — Exact ID Match (highest confidence: 0.95–1.0)

If two records share the same unambiguous external identifier, they refer to the same person:

- **GND** (Gemeinsame Normdatei) — German national authority file
- **HLS ID** (Historisches Lexikon der Schweiz)
- **HBLS ID** (Historischer Berner Lexikon)
- Internal stable IDs within a source (SSRQ ID, KF entry ID, EOS ID)

This tier is deterministic and requires no fuzzy matching.

### 4.2 Tier 2 — Name Normalisation + Date Overlap (confidence: 0.75–0.94)

If no shared exact ID exists, compare:

1. **Normalised name strings** — lowercase, strip punctuation, expand standard abbreviations (e.g. "Joh." → "Johannes"), remove patronymics/matronymics that vary across sources.
2. **Date overlap** — lifespans (or activity periods) must overlap by at least 5 years. Allow ±5-year fuzziness on year boundaries.
3. **Geographic anchor** — if both records mention the same place (city, canton, diocese), boost confidence.

**Name normaliser requirements:**
- Strip diacritics for comparison (ä → a, ü → u, etc.) while preserving them in output.
- Split "von Hallwyl" → discard "von" prefix for surname matching.
- Recognise Latinised forms ("Johannes" = "Hans", "Wilhelm" = "Guillelmus").
- Handle patronymic suffixes and genitive forms (-s, -es).

### 4.3 Tier 3 — Relationship-Assisted Matching (confidence: 0.60–0.74)

If a record mentions relationships (witness, parent, spouse, lord/vassal), and those related persons can be independently matched, use shared relationships as evidence for the anchor record.

This tier requires a second-pass resolution: once Tier-1 and Tier-2 matching identifies related persons, re-examine previously unresolved candidates.

### 4.4 Tier 4 — Residual Clusters (confidence: < 0.60)

Records that cannot be matched above Tier 3 remain as separate candidates. The response should indicate these are unmerged and may warrant manual review.

### 4.5 Confidence Score Calculation

```
base_score = max(tier1, tier2, tier3)   # if any tier matches
if shared_place:       base_score += 0.05
if shared_occupation:  base_score += 0.03
if relationship_match: base_score += 0.05

# Clamp to [0.0, 1.0]
confidence = min(1.0, base_score)
```

| Score Range | Meaning |
|-------------|---------|
| 0.90–1.00 | Confident match — GND/HLS ID or near-identical metadata |
| 0.75–0.89 | Probable match — name normalised, date overlap, same place |
| 0.60–0.74 | Possible match — partial evidence, manual review recommended |
| < 0.60 | Separate records — cannot confidently merge |

---

## 5. Milestones

### Phase 1 — Parallel Search: SSRQ + KF Only
**Goal:** Get the orchestration layer working with the two most mature, already-deployed MCP servers.

**Deliverables:**
- [ ] OpenClaw tool that calls `ssrq__search_persons` (port 8002) and `kf__search_persons` (port 8001) in parallel
- [ ] Naive combined results list (no entity resolution yet) sorted by name relevance
- [ ] Basic provenance tagging on each result ("Source: SSRQ", "Source: KF")
- [ ] Error handling: if one server fails, return results from the other with a warning

**Exit criterion:** Searching "Johannes von Hallwyl" returns results from both SSRQ and KF in a single response.

---

### Phase 2 — Add EOS to Parallel Search
**Goal:** Extend the parallel fan-out to include EOS MCP (port 8000).

**Deliverables:**
- [ ] Integrate `eos__search_persons` (or equivalent) into the parallel orchestration
- [ ] Unified result schema — each result regardless of source has: `name`, `dates`, `roles`, `source`, `source_id`
- [ ] Deduplication within a single source (SSRQ may return the same person twice; collapse by `source_id`)
- [ ] Timeout handling: if EOS does not respond within 5 seconds, return SSRQ + KF results only

---

### Phase 3 — Build HBLS MCP Server
**Goal:** Create a new MCP server that wraps `persons_resolved.json` (137,038 HBLS/HGB Basel merged person records).

**Deliverables:**
- [ ] Ingest `persons_resolved.json` — inspect schema, document field mapping
- [ ] Build MCP server on a dedicated port (e.g. port 8003), name: `hb ls-mcp`
- [ ] Expose at minimum: `hbls__search_persons`, `hbls__get_person`, `hbls__search_fulltext`
- [ ] Ensure GND IDs and HLS IDs from the dataset are exposed as queryable fields
- [ ] Load test with 137,038 records — query latency should remain under 500ms for name searches
- [ ] Error handling: missing file, malformed records, indexing failures

**Notes on `persons_resolved.json`:**
- The file contains merged records from HGB Basel with HLS and GND cross-links
- 137,038 records is large; consider SQLite or a similar indexed store rather than raw JSON scan on every query
- A pragmatic approach: load into SQLite, expose MCP tools that query SQLite

---

### Phase 4 — Cross-Source Entity Resolution Engine
**Goal:** Replace naive result concatenation with proper matching and merging across sources.

**Deliverables:**
- [ ] Implement the name normaliser (Tier 2) — handle Swiss/German name conventions (von, zu, gen.), Latinised forms, diacritic stripping
- [ ] Implement GND/HLS/HBLS ID extraction from all four sources (Tier 1)
- [ ] Implement date overlap detection with configurable tolerance
- [ ] Implement place normalisation (standardise canton names, Latin/German place name variants)
- [ ] Implement confidence scoring (see §4.5)
- [ ] Produce merged person clusters — each cluster has one canonical record plus alternates from other sources
- [ ] Logging/audit trail: which rule matched between which sources, for debugging

**Exit criterion:** Searching "Johannes von Hallwyl" produces one unified record with evidence from all three matching sources, rather than three separate result entries.

---

### Phase 5 — Unified Person Record with Confidence Scores
**Goal:** Surface the full system as a polished user-facing feature.

**Deliverables:**
- [ ] Unified record schema with all required fields (canonical name, alternates, lifespan, sources, external links, confidence)
- [ ] Render external links to HLS (`https://hls-dhs-dss.ch/`) and GND (`https://d-nb.info/gnd/`) where available
- [ ] Show provenance breakdown: " KF: mentioned in register fol. 42r as witness. SSRQ: dated 1431 in Amt Ac."
- [ ] Surface uncertainty: "Date of birth: unknown (SSRQ); estimated 1390–1400 (HBLS)"
- [ ] Return ranked list when multiple distinct persons match the query
- [ ] API documentation for the unified record schema
- [ ] End-to-end test corpus: 10 known historical persons with expected match sets

---

## 6. Next Step — Tomorrow Morning

**The single most important action is to build the HBLS MCP server (Phase 3).**

Rationale: HBLS is the largest single source (137,038 records), is not currently represented in the system at all, and requires both schema analysis of `persons_resolved.json` and a new MCP server deployment. Without HBLS, the Agentic Historian covers only a fraction of the available data.

**Concrete tomorrow action:**

1. **Inspect `persons_resolved.json`** — read the first 50 records to understand the schema (field names, nested structures, which fields hold GND IDs, HLS IDs, dates, alternate names).
2. **Decide storage backend** — if `persons_resolved.json` is raw flat JSON, evaluate whether to keep it as JSON or load into SQLite for indexed queries. For 137k records with text search needs, SQLite FTS5 is likely necessary.
3. **Scaffold the HBLS MCP server** — start from the existing SSRQ MCP server as a template (similar domain: Swiss historical persons), adapt the schema mapping, run on port 8003.
4. **Smoke test** — confirm `hbls__search_persons("Hallwyl")` returns results.

Everything else in the plan depends on having HBLS in the system.

---

## 7. Risks & Challenges

### 7.1 Name Variants
Swiss historical names are volatile. "Johannes von Hallwyl" may appear as:
- "Johann von Hallwyl"
- "Joh. v. Hallwyl"
- "Hans von Hallwyl"
- "Johannes de Hallwyl" (Latin)
- "Johannes Hallwyler"

**Mitigation:** Build a comprehensive name variant table and name normaliser. Phase 4 must invest heavily in normalisation before entity resolution can work. Consider using the 138,000-entry SSRQ name_index as a reference for variant patterns.

### 7.2 Date Format Heterogeneity
Dates arrive in many forms:
- Exact: `1403-04-15`
- Year only: `1403`
- Range: `1390–1430`
- Approximate: `um 1400`, `ca. 1400`
- Uncertain: `1403?`, `nachs 1403`
- Regnal years, feast days, and Latin date strings

**Mitigation:** Parse dates into a canonical internal representation (year as integer, with uncertainty flags). Implement a Swiss-specific date parser that handles regnal years and monastic feast calendars. Use ISO 8601 internally; accept all common formats on input.

### 7.3 Partial Failures
Any MCP server may be slow, unavailable, or return an error. The system must:
- Not block the full response if one source fails
- Surface partial results with a warning (e.g., "HBLS timed out — results below are from SSRQ, KF, EOS only")
- Retry once on transient failures (500ms back-off)

**Mitigation:** All MCP calls must have individual timeouts (recommended: 5s per source). Use asyncio.gather with `return_exceptions=True` to prevent one failure from cancelling others.

### 7.4 False Positive Merges
Aggressive entity resolution will sometimes merge two distinct people who share a name and lived at the same time. For a figure like "Heinrich" (common medieval name), this is a serious risk.

**Mitigation:** Require multiple independent signals before high-confidence merges. GND ID match alone is sufficient. Name+date match without place corroboration should be capped at confidence 0.75. Always surface the evidence chain so researchers can evaluate a merge themselves.

### 7.5 HBLS Data Quality
The 137,038 records in HBLS are "merged" records. The merge logic is external and may have errors. Some records may reference the same person that were not merged, or conversely may incorrectly conflate two people.

**Mitigation:** Treat HBLS as one authoritative source, not as ground truth. The entity resolution engine should treat HBLS records as candidates equal to other sources, not as a gold standard. Expose HBLS internal merge provenance if available.

### 7.6 Scale
137,038 HBLS records × 4 sources = entity resolution could involve large candidate sets for common names.

**Mitigation:** Pre-index each source by normalised name. Use blocking (group records by surname first, then compare within block) to avoid O(n²) comparison across full datasets.

---

*End of plan. Questions, revisions, or additions welcome — update this document as implementation proceeds.*