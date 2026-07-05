# Knowledge Hub — MCP Federation & How to Add a Source

**Status:** the Knowledge Hub is realised as a **federation of MCP servers**, one
per authority source. There is **no local authority store**. Adding a new hub is
a declarative operation — append one entry to the registry and verify — with **no
schema migration and no changes to the agents**.

- Registry (single source of truth): [`knowledge_hub/mcp_registry.py`](../knowledge_hub/mcp_registry.py)
- Host base + timeout: `config.MCP_BASE_URL`, `config.MCP_TIMEOUT` (env-overridable)
- **Consumers (built, epic #129):** `utils/mcp_client.py` (async client + `PersonResult`),
  `utils/entity_resolver.py` (cross-source merge), `agents/search_agent.py` (`/search`),
  and Agent C linking (`ENABLE_MCP_LINKING`). Adding a source auto-includes it in all of them.
- The retired local approach (KNEX/HBLS loaders + SQLite) is superseded — see the
  closed AH-80 epic (#58–#68).

---

## Current sources (live 2026-07-03)

| name | URL | kinds | authority ids | notes |
|---|---|---|---|---|
| `eos` | `…/mcp/eos` | person, place, org, fulltext | – | 75,447 docs / 893,303 spans |
| `hbls` | `…/mcp/hbls` | person | GND, Wikidata | ~137k person records |
| `hls` | `…/mcp/hls` | person, place, org | HLS, GND | replaces the local `hls.json` dump |
| `kf` | `…/mcp/kf` | person, place | – | Königsfelden register |
| `wikidata` | gateway `wikidata` MCP | person, place, org | Wikidata, GND | reconciliation (external) |

`…` = `config.MCP_BASE_URL` (default `https://tei.dh.unibe.ch/mcp`, VPN-gated).

---

## The common contract

Every source, natively or through an adapter, must present records in this shape
so consumers (federated search, Agent C linking) can merge across sources:

```python
class PersonResult(BaseModel):
    source: str                 # registry name: "hls" | "hbls" | "kf" | "eos" | ...
    pid: str                    # source-local id
    name: str
    forename: str | None
    surname: str | None
    life_dates: str | None      # "1300–1370" or "fl. 1348" — ranges ok, don't force precision
    occupation: str | None
    hls_id: int | None
    gnd_id: str | None
    wikidata_id: str | None
    variants: list[str] = []    # name variants / aliases
    mention_count: int = 0
    entries: list[str] = []     # register/entry references
    notes: str | None = None
```

`PersonRecord(PersonResult)` adds `relationships`, `geo (lat, lon)`, and
`all_entries` for a full single-entity fetch.

Expected MCP tools per source (declared in each registry entry's `tools`):
`search_persons(query, limit)`, `get_person(pid)`, `search_fulltext(query, limit)`.
A source that exposes different tool names or a different record shape supplies an
`adapter` (a callable mapping its native record → `PersonResult`); `adapter=None`
means it already conforms.

---

## Methodology — adding a new knowledge hub

**Precondition:** the source is already reachable as an MCP server (someone stood
up the MCP; standing up the server itself is out of scope here — see the HBLS
build notes in `IMPLEMENTATION_PLAN.md`).

1. **Confirm the MCP is live and speaks the contract.** From inside the VPN:
   ```bash
   curl -sf "$MCP_BASE_URL/<name>/health"          # or the source's discovery route
   ```
   Inspect one `search_persons` / `search_fulltext` response. Note the record
   field names and the tool names it actually exposes.

2. **Register it** — append one entry to `SOURCES` in
   [`knowledge_hub/mcp_registry.py`](../knowledge_hub/mcp_registry.py):
   ```python
   MCPSource(
       name="<name>",                 # stable key; also PersonResult.source
       title="<human name>",
       kinds=("person", "place"),     # what it can resolve (subset of KINDS)
       path="<name>",                 # URL suffix under MCP_BASE_URL
       tools=("search_persons", "get_person", "search_fulltext"),
       authority=<True if it yields GND/HLS/Wikidata ids>,
       notes="<volume / provenance / caveats>",
   )
   ```
   - External MCPs (registered via the gateway, not the tei base): set
     `external=True` (and `full_url=...` if it has a fixed URL).
   - If step 1 showed a non-conforming record shape or tool names: write a small
     adapter and set `adapter=<fn>`. Keep the adapter in
     `knowledge_hub/adapters/<name>.py`.

3. **Verify the registry** stays well-formed and the source is picked up:
   ```bash
   pytest agentic_historian/tests/test_kh_mcp_registry.py
   ```
   Add one assertion to that test naming your source (so its presence is pinned).

4. **Smoke-test end-to-end** (inside VPN): confirm a federated query returns
   source-attributed hits from the new source and that the resolver merges them
   with existing sources by shared authority id (GND/HLS) or name+dates.

5. **Document** the source in the table above and in the `IMPLEMENTATION_PLAN.md`
   data-sources table.

### Acceptance checklist

- [ ] MCP reachable; `search_*` returns records inside the VPN
- [ ] one `MCPSource(...)` entry added to `SOURCES`
- [ ] adapter written **iff** the native shape/tool names differ (else `adapter=None`)
- [ ] `test_kh_mcp_registry.py` green, with an assertion naming the new source
- [ ] federated smoke test returns attributed hits and merges by authority id
- [ ] source row added to this doc + `IMPLEMENTATION_PLAN.md`

That is the whole procedure. Because every consumer iterates `list_sources()` /
`sources_for_kind()`, a registered source is automatically included — nothing
else in the app changes.

---

## Design invariants (why this stays simple)

- **One registry, iterated everywhere.** No consumer hard-codes a source list.
- **URLs derived from one base.** `config.MCP_BASE_URL` repoints the whole
  federation (staging/prod) with one env var; only external sources are exempt.
- **Authority over precision.** Merge on stable ids (GND/HLS/Wikidata) first, then
  name + overlapping life dates; keep date ranges fuzzy (`fl. YYYY`).
- **Partial failure is normal.** A down source is skipped with a flag, not a hard
  error — federated queries return partial, attributed results.
