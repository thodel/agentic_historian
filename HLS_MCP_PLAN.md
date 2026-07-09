# HLS MCP Server — Implementation Plan

## Data Sources
- **Primary**: `~/hls/HLS/hls_articles.json` (33,506 articles, ~145 MB)
  - Fields: `id` (6-digit zero-padded string), `title`, `bio` ({birth_date, death_date, family_name, first_name}), `content_text`, `category`, `geo` ({lat, lon}), `lexical_class`, `time_spanes`
- **Enrichment**: dhs-nerd gh-pages (72,718 articles) at `https://raw.githubusercontent.com/dddpt-epfl-phd/dhs-nerd/gh-pages/docs/data/{lang}/{id}.json`
  - Adds: GND ID (`gndid`), Wikidata Q-ID (`wikidata_id` / `wikidata_entity_id`), Wikipedia links, `wiki_links[]` with cross-DHS entity resolution
- **Mapping**: direct — `hls_articles.json` ID `025265` → `docs/data/fr/025265.json`

## Design Decisions
1. **On-demand GitHub fetch for enrichment** — don't mirror 72k JSON files locally; fetch per-article at load time with concurrency
2. **Language: FR primary** — FR has richer content; fall back to DE if FR article missing
3. **SQLite + FTS5** — same pattern as SSRQ/KF MCP servers
4. **HBLS join** — skip for now; dhs-nerd `wiki_links` already provides GND/Wikidata via entity-fishing
5. **No hls_articles_extended 2.csv** — too wide (800+ columns); use structured JSON

## Schema
```sql
CREATE TABLE persons (
  id          TEXT PRIMARY KEY,   -- "025265"
  title       TEXT,
  family_name TEXT,
  first_name  TEXT,
  birth_date  TEXT,               -- "1599-08-08" or ""
  death_date  TEXT,
  content_text TEXT,              -- full article text (truncated to 50k chars)
  category    TEXT,
  lexical_class TEXT,
  gnd_id      TEXT,               -- "1060078279" from dhs-nerd
  wikidata_id TEXT,               -- "Q96254743" from dhs-nerd
  wikipedia_url TEXT,
  hls_url     TEXT,
  geo_lat     REAL,
  geo_lon     REAL
);

CREATE VIRTUAL TABLE persons_fts USING fts5(
  title, family_name, first_name, content_text, category,
  content='persons', content_rowid='rowid'
);

CREATE TABLE errors (
  id TEXT PRIMARY KEY,
  error TEXT
);
```

## File Layout
```
~/mcp/hls-persons/
├── load_data.py        # Parse hls_articles.json + fetch dhs-nerd enrichment → SQLite
├── server.py           # FastMCP server (search, get_person, get_place, health)
├── requirements.txt    # fastmcp, httpx, aiosqlite
├── data/
│   └── hls.db          # Built by load_data.py
└── README.md
```

## Tool Interface
```python
# Search persons
hls_search_persons(query: str, limit: int = 20)
  → [{id, title, family_name, first_name, birth_date, death_date, gnd_id, wikidata_id}]

# Get person by HLS ID
hls_get_person(id: str)  # e.g. "025265"
  → {full person record with content_text, gnd_id, wikidata_id, wikipedia_url}

# Search places (geo-tagged persons → places)
hls_search_places(query: str, limit: int = 20)
  → [{place_name, id, geo_lat, geo_lon}]

# Full-text search
hls_search(query: str, limit: int = 20)
  → [{id, title, snippet(content_text)}]

# Stats
hls_corpus_stats()
  → {total_persons, with_gnd, with_wikidata, with_geo}

# Health
hls_health()
  → {status: "ok", db_version, article_count}
```

## Load Strategy
1. Parse `hls_articles.json` → insert into `persons` table (no enrichment yet)
2. Fetch dhs-nerd index to find available FR/DE articles (just a HEAD check per ID is too slow; use batch approach)
3. Fetch dhs-nerd articles in batches of 50 via `httpx.AsyncClient` with semaphore(20)
4. Extract `wiki_links[0].gndid`, `wiki_links[0].item` (Wikidata Q-ID), `wiki_links[0].articlefr` (Wikipedia)
5. Update `persons` table with enrichment data
6. Log failures to `errors` table

## Performance
- Load time estimate: ~30 min (33,506 articles × 2 lang lookups, 20 concurrent)
- DB size estimate: ~200-300 MB (content_text is large)
- FR content preferred; fall back to DE if FR missing

## OpenClaw Integration
- Port: 8004
- MCP config: `mcp_servers.yaml`