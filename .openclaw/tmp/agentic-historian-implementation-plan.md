# Agentic Historian — Implementation Plan
**Author:** dh-bot  
**Date:** 2026-06-30  
**Status:** Draft for review

---

## 1. Current Stack (as of 2026-06-30)

| Service | Port | Status | Data |
|---|---|---|---|
| SSRQ MCP | 8002 | ✅ Deployed today | 23,674 persons, 7,047 orgs, 138k name variants |
| KF MCP | 8001 | ✅ Already existed | Königsfelden register (search_persons, get_person, search_fulltext) |
| EOS MCP | 8000 | ✅ Already existed | HGB Basel corpus — 75,447 documents, 203,734 person spans |
| HBLS MCP | — | ❌ Not yet built | `github.com/thodel/eos_persons` — to be cloned and built |

> **Note:** Only SSRQ was built today. KF and EOS MCPs pre-existed. HBLS/eos_persons is the missing component.

---

## 2. Architecture

```
                        [User query]
                              │
                     [OpenClaw orchestration]
                              │
              ┌───────────────┼───────────────┬───────────────┐
              ↓               ↓               ↓               ↓
        [SSRQ MCP]     [KF MCP]        [EOS MCP]       [HBLS MCP]
        port 8002      port 8001       port 8000       port TBD
        23,674 pers    5,260 pers      203,734 spans   TBD
              │               │               │               │
              └───────────────┴───────────────┴───────────────┘
                              │
                  [Entity resolver + merger]
                  (ID match → name fuzzy → geo+time)
                              │
                     [Unified response]
                     (ranked by confidence,
                      source attribution)
```

### Source-to-source cross-references already established

| Bridge | Method | Coverage |
|---|---|---|
| KF → HLS | Via SSRQ TTL `pers:type "HLS"` name provenance | 162 persons (confirmed 2026-06-30) |
| KF → SSRQ | ID crosswalk (perXXXXXX → numeric) | 3,996 clean matches, 1,264 excluded |
| KF → EOS | Text match + year overlap (±20 yr) | 10 confirmed matches |
| HLS → EOS | HLS article IDs | 10 confirmed via KF bridge |

---

## 3. Entity Resolution Strategy

Three-tier matching, applied in order:

**Tier 1 — Exact ID match** (highest confidence)
- Shared GND ID, HLS ID, or stable cross-source ID
- E.g., KF `per012269` ↔ HLS `012788` via SSRQ TTL

**Tier 2 — Name fuzzy match** (medium confidence)
- Normalized name (strip diacritics, standardize first/last)
- Overlapping date ranges (lifetime ± tolerance)
- Source: KF→SSRQ name-provenance chain

**Tier 3 — Place + time overlap** (lower confidence)
- Same geographic location + same era (± 20 yr)
- Used when names differ significantly

**Confidence levels:**
- `high` — Tier 1 match
- `medium` — Tier 2 match, verified
- `low` — Tier 3 match, needs review

---

## 4. Milestones

### Milestone 1 — Parallel search across SSRQ + KF *(immediate)*
- [ ] Use `sessions_spawn` with `runtime=subagent` to query SSRQ and KF MCPs simultaneously
- [ ] Merge results by name fuzzy match (Tier 2)
- [ ] Return unified person record with source attribution
- [ ] **Reasoning:** KF and SSRQ are both running today; this is the fastest path to a working parallel search

### Milestone 2 — Add EOS to parallel search *(short-term)*
- [ ] Understand what EOS MCP (port 8000) exposes — check `spans` table structure, `class`, `element` fields
- [ ] Extend spawn to 3 parallel subagents (SSRQ + KF + EOS)
- [ ] Apply KF→EOS matching (text + year overlap, already demonstrated: 10/162 matches)
- [ ] Merge EOS results into unified record

### Milestone 3 — Build HBLS MCP *(short-term, blocking entity resolution)*
- [ ] Clone `github.com/thodel/eos_persons`
- [ ] Analyse data format (SQLite? CSV? TTL? existing schema?)
- [ ] Build MCP server in Python using FastAPI (following SSRQ pattern)
- [ ] Expose standard endpoints: `GET /person/{id}`, `GET /search?q=`, `GET /` (health)
- [ ] Use SSE or streamable-http transport
- [ ] Deploy on port 8003+ (check free ports)
- [ ] Register in OpenClaw as new tool namespace

### Milestone 4 — Cross-source entity resolution *(medium-term)*
- [ ] Build unified entity record:
  - Aggregate all matches across SSRQ + KF + EOS + HBLS
  - Attach confidence score and match tier per source
  - Track which source provided the authoritative ID
- [ ] Implement Tier 3 (place + time) matching for remaining unresolved entities
- [ ] Handle partial failures gracefully (one source down ≠ total failure)

### Milestone 5 — Unified person record with UI *(medium-term)*
- [ ] Design unified record schema: names[], dates[], places[], sources[], external_ids{}, confidence
- [ ] Build OpenClaw tool that returns the unified record
- [ ] Consider a lightweight web UI for browsing resolved entities

---

## 5. MCP Server Pattern (for consistency)

All four MCPs should follow the same interface contract:

```
GET /              → {"status": "ok", "name": "...", "version": "..."}
GET /person/{id}   → {"id": "...", "name": "...", "dates": {...}, "places": [...], "external_ids": {}, "sources": [...]}
GET /search?q=     → [{"id": "...", "name": "...", "dates": {...}, "type": "person|org|place"}, ...]
GET /org/{id}      → (optional, for org resolution)
```

**Transport:** HTTP with SSE or streamable-http (not stdio, for deployment consistency)

**Recommended stack:** FastAPI + `mcp` Python SDK (or manually implemented SSE endpoint following MCP protocol)

---

## 6. Key Challenges

| Challenge | Mitigation |
|---|---|
| Entity resolution across different ID schemes | Three-tier matching (ID → name fuzzy → geo+time) |
| Name variants (Johann/Hans, Maria/Marie) | Unicode NFD normalization + forename/surname decomposition |
| Date formats (Julian/Gregorian, ranges, uncertainty) | Per-source parsing, store raw + normalized |
| Parallel search latency | Spawn subagents in parallel, set timeout per source |
| Partial source failure | Aggregate available results, flag missing sources |
| HBLS data format unknown | Clone and analyse before building MCP |
| 10,000+ co-PI edges | Network analysis uses keyword co-occurrence as proxy (already built) |

---

## 7. Next Steps (in order)

1. **Today/Tomorrow:** Write the parallel search orchestrator — spawn SSRQ + KF subagents, merge by name fuzzy match, test against known persons (e.g., per000519 Heinrich Chur/Hewen)
2. **This week:** Document EOS MCP interface (what does port 8000 actually expose?)
3. **This week:** Clone and analyse `github.com/thodel/eos_persons` — understand HBLS data format
4. **Next week:** Build HBLS MCP (port 8003 or next free), following SSRQ FastAPI pattern
5. **Next week:** Extend parallel search to 3 sources (SSRQ + KF + EOS), then 4 (add HBLS)
6. **Ongoing:** Entity resolution refinement as data quality improves

---

## 8. Already-Demonstrated Components

The following are already built and working — re-usable patterns:

| Component | Location | Notes |
|---|---|---|
| SSRQ MCP server | `/home/dh/.openclaw/workspace/ssrq_project/api/server.py` | FastAPI, port 8002 |
| SSRQ skill | `skills/ssrq/SKILL.md` | Binary: `~/.local/bin/ssrq` |
| KF↔SSRQ crossref | `.openclaw/tmp/kf_ssrq_clean.csv` | 3,996 safe matches |
| KF→HLS ID map | `.openclaw/tmp/kf_hls_id_map.json` | 162 persons |
| KF→EOS matching | `.openclaw/tmp/kf_hls_eos_crossref.json` | 10 confirmed |
| Co-PI network | `reports/.../data/network_pi_pi_edges.csv` | 10,095 edges |
| SSRQ setup guide | `.openclaw/tmp/SSRQ_SETUP_GUIDE.md` | Deployment reference |