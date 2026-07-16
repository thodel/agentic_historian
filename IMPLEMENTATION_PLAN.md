# Agentic Historian — Implementation Plan

**Status:** 2026-07-16
**Version:** 3 · supersedes earlier drafts

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
    │  SSRQ   │    │   KF    │    │  HGB    │    │  HBLS   │
    │ :8002   │    │  :8001  │    │  :8000  │    │  :8003  │
    │ 23 674  │    │  5 260  │    │ 137 038 │    │  ? pers │
    │ persons │    │ persons │    │ persons │    │         │
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

## Current MCP Federation (2026-07-16)

| Port | Source | DB | Persons | Transport | Endpoint |
|------|--------|----|---------|-----------|---------|
| 8000 | HGB (EOS) | `/data/hgb.db` | 137 038 | SSE | `https://tei.dh.unibe.ch/mcp/eos/` |
| 8001 | KF | `/data/kf.db` | 5 260 | HTTP | `https://tei.dh.unibe.ch/mcp/kf/` |
| 8002 | SSRQ | local (`ssrq_v6.db`) | 23 674 | HTTP | `https://tei.dh.unibe.ch/mcp/ssrq/` |
| 8003 | HBLS | `/home/dh/eos_persons/hbls_mcp/hbls.db` | ? | SSE+HTTP | local only |
| 8004 | HLS | `/data/hls.db` | ? | SSE | local only |

**Note:** KF (8001) and HGB (8000) are legacy SSE servers. SSRQ (8002) is streamable-HTTP.
HBLS (8003) and HLS (8004) are local-only; they must be exposed via nginx before
remote agents can reach them, or consumed via the tei MCP gateway.

---

## Milestones

### M1 · Parallel search across SSRQ + KF (trivial win)
**Goal:** demonstrate parallel querying with result merging.

1. Add `ssrq` and `kf` to the MCP registry (SSRQ already has an entry; check KF)
2. Write `knowledge_hub/search_parallel.py`:
   - `search_all(query: str, kind: str) -> list[PersonResult]`
   - `sessions_spawn` 2 subagents: one calls SSRQ, one calls KF
   - Collect both results, deduplicate by GND/HLS/Wikidata ID match, then by
     name+date fuzzy match
   - Rank by: exact authority ID match > exact name + date overlap > fuzzy name
3. Wire into `entity_agent.py` / `search_agent.py`
4. **Deliverable:** single query returns results from both SSRQ and KF with
   source tags and confidence flags.

**Risks:** KF SSE transport; name variant mismatches. Both manageable.

---

### M2 · Add HGB (EOS port 8000) to parallel search
**Goal:** include the 137k HGB corpus.

1. Probe HGB's tool contract (`/mcp/eos/`)
2. Add HGB `MCPSource` to `mcp_registry.py` with appropriate `tool_map`
3. Extend `search_parallel.py` to spawn 3 subagents
4. Entity resolution: HGB persons have Wikidata/GND links; use those as primary
   merge key

**Risks:** HGB's SSE transport is less reliable than HTTP; 137k corpus means
more candidate noise — aggressive filtering by date range essential.

---

### M3 · Expose HBLS (port 8003) via nginx at `https://tei.dh.unibe.ch/mcp/hbls/`
**Goal:** make HBLS accessible to remote subagents.

1. Write nginx config snippet for `/mcp/hbls/ → localhost:8003`
2. Test: `curl https://tei.dh.unibe.ch/mcp/hbls/mcp`
3. Add `MCPSource` for `hbls` with `transport="sse"` or `"http"` as appropriate
4. Verify tool contract (search_persons, get_person, get_by_hls, etc.)

**Risks:** nginx config needs `sudo`; coordinate with Tobias.

---

### M4 · Cross-source entity resolution engine
**Goal:** build the unified `PersonResult` from multiple sources.

**Merge strategy (in priority order):**

```
1. Authority ID merge
   GND ID match  →  same person (high confidence)
   HLS ID match  →  same person (high confidence)
   Wikidata QID  →  same person (high confidence)

2. Exact name + date overlap
   Same normalised name + year range overlaps ≥ 1 year  →  same person (medium)

3. Fuzzy name + geo + time
   Soundex/Metaphone match + same location + ±10 year overlap  →  likely same

4. Same first name + surname token set (Johann ~ Hans variant)
   via configurable name-alias map
```

**Conflict resolution:** prefer the source with the most fields populated;
authority IDs from the source with `authority=True` in the registry.

**Data structures:**
```python
@dataclass
class MergedPerson:
    canonical_name: str
    all_names: list[str]           # variants from all sources
    authority_ids: dict[str, str]  # {gnd, hls, wikidata, ssrq, kf, hgb, hbls}
    year_from: int | None
    year_to: int | None
    occupations: list[str]
    locations: list[str]
    sources: list[str]             # which MCPs had this person
    confidence: float              # 0.0–1.0
    raw_records: dict[str, dict]   # source → raw record for audit
```

---

### M5 · Agent C integration — use parallel search for entity linking
**Goal:** Gate 3 (entity-link review) draws from all 4 sources.

- Replace single-source `search_persons` with `search_all` in `entity_agent.py`
- For each entity mention, present top candidates from each source ranked by
  confidence
- The "kein Link" option stays; clicking a candidate writes the link to the hub

**Deliverable:** entity linking UI shows candidates from SSRQ + KF + HGB + HBLS
in one compact select list.

---

### M6 · Unresolved questions (deferred)

- **Port 8004 (HLS direct):** worth exposing alongside HBLS, or is HBLS sufficient?
- **Write-back path:** when a new person is discovered in one source, should it
  be written to the hub? Under what conditions?
- **SSRQ orgs:** SSRQ has 7,047 orgs — does the parallel search need an org path too?
- **Performance budget:** parallel spawning has latency cost; set a timeout
  per source (default 8s) and return partial results on timeout.

---

## Concurrency model

```
User query
    │
    ▼
sessions_spawn (runtime="subagent", mode="run")  ← fire all 4 concurrently
    │
    ├─ subagent-SSRQ  → MCP call → results
    ├─ subagent-KF    → MCP call → results
    ├─ subagent-HGB   → MCP call → results
    └─ subagent-HBLS  → MCP call → results
    │
    ▼ (all results collected or timeout)
entity_resolver.merge(results)
    │
    ▼
unified response
```

**Timeout per subagent:** 8 000 ms; on timeout return empty list for that source.
**On partial failure:** return results from successful sources; log which failed.

---

## Registry integration checklist

Each new source = one `MCPSource(...)` entry in `knowledge_hub/mcp_registry.py`:

```python
MCPSource(
    name="ssrq",
    title="SSRQ — Summary of Swiss Roman Law Queries",
    kinds=("person", "org"),           # SSRQ also has orgs
    path="ssrq",
    tools=("search_persons", "get_person", "search_orgs"),
    authority=True,                    # SSRQ has HLS IDs
    transport="http",
    tool_map={"search_orgs": "org"},  # tool name differs
),
```

---

## Next concrete step (M1)

Write `knowledge_hub/search_parallel.py` and add a test that mocks two MCP
sources. Then wire it into `entity_agent.py` as a drop-in for the current
single-source `search_persons` call.

Branch: `feat/ah-287-progress-rendering` is open as PR #291.
Next branch: `feat/parallel-search` off `main`.
