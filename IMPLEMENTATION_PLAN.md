# Agentic Historian — Implementation Plan

**Status:** 2026-07-24 (updated)
**Version:** 4 · supersedes v3 (2026-07-16)

---

## Architecture

```
[User query: "Johann von Bern, 14. Jh."]
         │
         ▼
┌────────────────────────────────────────────────────────┐
│           OpenClaw orchestrator (sessions_spawn)        │
│  parallel: one subagent per MCP source                 │
└──────────┬──────────────┬──────────────┬──────────────┘
           │              │              │              │
    ┌──────▼──┐    ┌──────▼──┐    ┌──────▼──┐    ┌──────▼──┐
    │  SSRQ   │    │   KF    │    │  EOS/   │    │  HBLS   │
    │ :8002   │    │  :8001  │    │  HGB    │    │  :8003  │
    │ 23 674  │    │  5 260  │    │ :8000   │    │   NEW   │
    │ persons │    │ persons │    │ 137 038 │    │  eos_   │
    │ +7 047  │    │         │    │ persons │    │ persons │
    │   orgs  │    │         │    │         │    │         │
    └──────┬──┘    └──────┬──┘    └──────┬──┘    └──────┬──┘
           │              │              │              │
           └──────────────┴──────┬───────┴──────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  Entity resolver        │
                    │  (parallel results →    │
                    │   merged PersonResult)  │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  Unified response       │
                    │  source attribution     │
                    │  confidence scores      │
                    └─────────────────────────┘
```

---

## Current MCP Federation (2026-07-24)

| Port | Source | Local DB | Persons | Transport | Endpoint | Status |
|------|--------|----------|---------|-----------|---------|--------|
| 8000 | EOS/HGB | `/home/dh/eos_data/hgb.db` | 137 038 | SSE | `https://tei.dh.unibe.ch/mcp/eos/` | existed |
| 8001 | KF | `/home/dh/kf_data/kf.db` | 5 260 | HTTP | `https://tei.dh.unibe.ch/mcp/kf/` | existed |
| 8002 | SSRQ | `/home/dh/.openclaw/tmp/ssrq_v6.db` | 23 674 + 7 047 orgs | HTTP | `https://tei.dh.unibe.ch/mcp/ssrq/` | deployed today |
| 8003 | HBLS | github.com/thodel/eos_persons | ? | — | — | NOT YET BUILT |
| — | HLS | separate HLS service | — | — | — | deferred |

**What changed today:** SSRQ MCP (port 8002) deployed and functional. KF and EOS/HGB MCPs already existed. HBLS MCP from `github.com/thodel/eos_persons` is the primary gap.

---

## Milestones

### M1 · Parallel search across SSRQ + KF (ready to implement)
1. Add SSRQ and KF to the MCP registry
2. Write `knowledge_hub/search_parallel.py`:
   - `search_all(query: str, kind: str) -> list[PersonResult]`
   - `sessions_spawn` 2 subagents: one calls SSRQ, one calls KF
   - Deduplicate by GND/HLS/Wikidata ID match, then by name+date fuzzy match
   - Rank by: exact authority ID match > exact name + date overlap > fuzzy name
3. Wire into `entity_agent.py` / `search_agent.py`
4. **Deliverable:** single query returns results from both SSRQ and KF with source tags and confidence flags.

**Risks:** KF SSE transport; name variant mismatches. Both manageable.

---

### M2 · Add EOS/HGB (port 8000) to parallel search
1. Probe EOS/HGB tool contract (`/mcp/eos/`)
2. Add EOS/HGB `MCPSource` to `mcp_registry.py`
3. Extend `search_parallel.py` to spawn 3 subagents
4. EOS/HGB persons have Wikidata/GND links — use those as primary merge key

**Risks:** EOS/HGB SSE transport; 137k corpus means more noise — aggressive date-range filtering essential.

---

### M3 · Build and expose HBLS MCP (port 8003) — NEW PRIORITY
1. Clone and analyse: `git clone https://github.com/thodel/eos_persons.git ~/eos_persons`
2. Build MCP server (FastAPI/HTTP, same pattern as SSRQ)
3. Expose via nginx (coordinate with Tobias for sudo)
4. Add to parallel search as 4th source

---

### M4 · Cross-source entity resolution engine
Merge strategy (priority order):
1. Authority ID merge (GND, HLS, Wikidata) → high confidence
2. Exact name + date overlap → medium confidence
3. Fuzzy name + geo + time → likely same
4. First-name alias map (Johann ~ Hans, Maria ~ Marie)

---

### M5 · Agent C integration — use parallel search for entity linking
- Replace single-source `search_persons` with `search_all` in `entity_agent.py`
- Entity linking UI shows candidates from all 4 sources ranked by confidence
- "kein Link" option preserved

---

### M6 · Unresolved (deferred)
- HLS direct (port 8004): expose alongside HBLS?
- Write-back path: write new persons to hub?
- SSRQ orgs: include org path in parallel search?
- Performance budget: 8s timeout per source, partial results OK

---

## Next concrete step (M1)

Write `knowledge_hub/search_parallel.py`, add mocked tests, then wire into `entity_agent.py`.

Branch: `feat/parallel-search` off `main`.

---

## Key challenges

| Challenge | Mitigation |
|-----------|-----------|
| Entity resolution across ID schemes | GND/Wikidata as primary merge key |
| Name variants (Johann ~ Hans) | Name-alias map + soundex fallback |
| HBLS data format unknown | Analyse eos_persons repo first |
| nginx sudo needed | Coordinate with Tobias |
| Partial MCP failure | Graceful degradation, log failed sources |
