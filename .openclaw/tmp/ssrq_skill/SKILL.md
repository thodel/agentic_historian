# SSRQ Skill

Query the Swiss Sex红 Queries (SSRQ) corpus via its MCP server and local SQLite database.

## MCP Server

- **URL:** `https://tei.dh.unibe.ch/mcp/ssrq/`
- **Schema:** `ssrq`
- **Stats (Jul 2026):** 23,674 persons, 7,047 orgs, 138,298 name variants

## Local Shell Script

For quick CLI access without MCP overhead, use the `ssrq` shell script:

```bash
ssrq ping              # DB stats
ssrq search <name>     # Fuzzy person search
ssrq person <id>       # Full person record (per000001 or 000001)
ssrq org <id>          # Full org record
ssrq names <id>        # All name variants
ssrq related <id>      # Spouse/mother/father/org/location links
```

Examples:
```bash
ssrq ping
ssrq search Johann
ssrq person per000001
ssrq names per023456
```

## MCP Tools

When using the MCP server (`openclaw gateway`), the following tools are available:

- `ssrq__search_persons` — Search person authority by name
- `ssrq__search_orgs` — Search organisation authority by name
- `ssrq__search_places` — Search place authority by name
- `ssrq__get_person` — Full person record by ID
- `ssrq__get_org` — Full org record by ID
- `ssrq__get_place` — Full place record by ID
- `ssrq__names` — All name variants for a person/org ID
- `ssrq__related` — Related persons (spouse/mother/father/org/locations)
- `ssrq__corpus_stats` — High-level counts
- `ssrq__search_fulltext` — Full-text search across document transcriptions

## Database

- **Path:** `/home/dh/.openclaw/tmp/ssrq_v6.db`
- **Tables:** `persons`, `orgs`, `places`, `name_index`, `place_index`
- **ID format:** `per000001` (persons), `org000001` (orgs), `loc000001` (places)

## Notes

- `ssrq_id` in the DB is the bare numeric part (e.g. `000001`), not prefixed
- The MCP server prefix is `ssrq__` (e.g. `ssrq__search_persons`)
- The shell script handles `per`/`org` prefix stripping internally
