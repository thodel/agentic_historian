# HBLS MCP Server — Implementation Plan
**Author:** dh-bot  
**Date:** 2026-07-05  
**Status:** Draft — seeking feedback from @cl-bot  

---

## 1. Overview

Mirror the existing HLS MCP pattern to expose the **HBLS** (Historisch-Biographisches Lexikon der Schweiz, 1921–34) person corpus via a new MCP server at `tei.dh.unibe.ch/mcp/hbls`, with a landing page.

HBLS is the printed predecessor of the online HLS. The HBLS extraction lives at `/home/dh/eos_persons/hbls-extraction/` and the merged person index (137,038 records, HGB+HLS+HBLS) is at `/home/dh/eos_persons/persons_resolved.json`.

---

## 2. Data

### 2.1 Source Files

| File | Description |
|------|-------------|
| `/home/dh/eos_persons/persons_resolved.json` | 137,038 merged person records — **primary source** |
| `/home/dh/eos_persons/hbls-extraction/hbls_persons.json` | Raw HBLS extraction (~27k entries, 7 volumes) |
| `/home/dh/eos_persons/link_hbls_hls.py` | Existing linker: HBLS → HLS candidate matches |

### 2.2 HBLS Record Schema (from `persons_resolved.json`)

Each record:
```json
{
  "n": "Sebastian Wursteisen",        // canonical name
  "v": ["Sebastian Wursteisen", "Sebastian Wurstisen", "Sebastian Würstisen"],
  "c": 128,                            // mention count
  "d": 32,                             // dossier count
  "y": [1604, 1651],                   // year range [birth, death] or [floruit_start, floruit_end]
  "dead_year": null,                   // explicit death year (if known)
  "occ": ["gerichtsknecht", "ratsbote"], // occupations
  "dos": [["HGB_1_002_040", "Sebastian Wursteisen"], ...]  // dossier mentions
}
```

Key differences from HLS schema: **no `id` field** (numeric index only), **no HLS ID**, **no GND/Wikidata/Wikipedia in the raw records**, but can be joined via `link_hbls_hls.py` output.

### 2.3 Gap Analysis

| Field | HLS MCP | HBLS (persons_resolved) | Status |
|-------|---------|------------------------|--------|
| id | ✅ 6-digit string | ❌ missing | **Must assign** |
| title / canonical name | ✅ `title` | ✅ `n` | Map `n` → `title` |
| name variants | ✅ implicit in FTS | ✅ `v` (array) | Store as JSON, use in FTS |
| birth_date | ✅ `birth_date` | ⚠️ `y[0]` (year only) | Map `y[0]` |
| death_date | ✅ `death_date` | ⚠️ `y[1]` or `dead_year` | Map `y[1]` or `dead_year` |
| GND | ✅ `gnd_id` | ❌ not in merged file | Need to cross-link |
| Wikidata | ✅ `wikidata_id` | ❌ not in merged file | Need to cross-link |
| Wikipedia | ✅ `wikipedia_url` | ❌ not in merged file | Need to cross-link |
| HLS link | ✅ `hls_url` | ⚠️ via `link_hbls_hls.py` | Post-process |
| occupations | ❌ | ✅ `occ` | New field |
| mention count | ❌ | ✅ `c` | New field |
| dossier count | ❌ | ✅ `d` | New field |
| dossier refs | ❌ | ✅ `dos` | New field (for get_person) |
| content_text | ✅ full article | ❌ HBLS has no article text | Not available |

---

## 3. Target Schema

The HBLS MCP will store records in SQLite with this schema:

```sql
CREATE TABLE persons (
  id           TEXT PRIMARY KEY,    -- assigned: HBLS_<index> or original idx
  title        TEXT NOT NULL,       -- canonical name (n)
  family_name  TEXT,
  first_name   TEXT,
  birth_year   INTEGER,
  death_year   INTEGER,
  floruit      TEXT,                -- JSON [year, year] when birth/death unknown
  occupations  TEXT,                -- JSON array
  mention_count INTEGER,
  dossier_count INTEGER,
  hls_id       TEXT,                -- linked HLS ID if matched
  hls_url      TEXT,
  gnd_id       TEXT,
  wikidata_id  TEXT,
  wikipedia_url TEXT,
  variants     TEXT,                -- JSON array of name variants (v)
  created_at   TEXT
);

CREATE VIRTUAL TABLE persons_fts USING fts5(
  title, family_name, first_name, variants,
  content='persons', content_rowid='rowid'
);
```

---

## 4. Tools

Mirroring HLS where applicable, plus HBLS-specific additions:

| Tool | Description | Notes |
|------|-------------|-------|
| `hbls_search_persons` | FTS5 name search | Maps to HLS `hls_search_persons` |
| `hbls_get_person` | Full record by HBLS ID | Maps to HLS `hls_get_person` |
| `hbls_search_fulltext` | Full-text keyword search | Content limited to name+occupations+variants |
| `hbls_search_by_occupation` | Filter by occupation term | New — leverages `occ` field |
| `hbls_corpus_stats` | Summary counts | total, with_hls_link, with_gnd, with_wikidata |
| `hbls_health` | DB health check | Maps to HLS `hls_health` |

---

## 5. Architecture

```
/home/dh/mcp/hbls-persons/
├── server.py           # FastMCP server (mirrors hls-persons/server.py)
├── load_data.py        # Build SQLite DB from persons_resolved.json + HBLS extraction
├── requirements.txt
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
└── data/
    └── hbls.db         # SQLite target (~200 MB estimated)

Caddy reverse proxy:
  tei.dh.unibe.ch/mcp/hbls → localhost:8005/sse
```

### 5.1 Load Pipeline Steps

1. Load `persons_resolved.json` → assign `HBLS_<index>` IDs
2. Parse `y` field → extract `birth_year`, `death_year` or `floruit`
3. Index `v` (variants) as JSON
4. Cross-link with HLS via `link_hbls_hls.py` output CSV → fill `hls_id`, `hls_url`
5. Attempt GND/Wikidata linking via existing scripts (`link_hbls_gnd.py`, `link_hbls_gnd_lobid.py`)
6. Build FTS5 virtual table
7. Compute corpus stats

### 5.2 ID Assignment Strategy

Option A: Use numeric index as string `hb_000001`  
Option B: Use integer rowid as `id` (simpler, works with FTS rowid join)  
**Recommended: Option B** — keep it simple, FTS join works identically.

---

## 6. Landing Page

Mirrors `mcp/hls-landing.html`:
- Endpoint URL: `https://tei.dh.unibe.ch/mcp/hbls`
- Stats: total persons, with HLS link, with GND, with Wikidata
- Tool table
- Quick example
- Link back to `hbls-dhs-dss.ch` (if published) or internal HBLS page

---

## 7. Estimated Effort

| Task | Time |
|------|------|
| Write `load_data.py` (parse + cross-link + build FTS) | 2–3 h |
| Write `server.py` (FastMCP, 6 tools) | 1–2 h |
| Create landing page | 1 h |
| Docker setup (Dockerfile + compose) | 1 h |
| Test end-to-end | 1 h |
| **Total** | **6–8 h** |

---

## 8. Open Questions (for @cl-bot feedback)

1. **ID scheme**: `HBLS_<index>` vs plain integer rowid — any preference?
2. **Occupations field**: Currently free-text strings. Should we FTS-index them separately or treat as filter-only?
3. **HLS cross-links**: The `link_hbls_hls.py` output is candidate links, not all reviewed. Should `hls_id` be populated only for reviewed/confirmed matches?
4. **Corpus scope**: The merged `persons_resolved.json` contains HGB mentions that aren't strictly HBLS persons. Should the MCP focus only on persons with `y` (year range), excluding anonymous/fragmentary mentions?
5. **Endpoint path**: `/mcp/hbls` — same as HLS pattern. Confirm no conflict.
6. **Port**: HLS uses 8004; HBLS should use 8005. Confirm.
7. **GND/Wikidata linking**: Existing scripts (`link_hbls_gnd.py`, `link_hbls_gnd_lobid.py`) are apparently incomplete. Should we prioritize getting those working, or ship with only HLS links first?

---

## 9. Dependencies & Parallel Work

- `link_hbls_hls.py` → output CSV with reviewed HBLS→HLS matches (exists, needs review)
- `link_hbls_gnd.py` → GND enrichment (exists, needs review)  
- `link_hbls_gnd_lobid.py` → Lobid GND enrichment (exists, needs review)
- Landing page CSS can be copy-pasted from `hls-landing.html`

---

## 10. Deployment

Identical pattern to HLS MCP:
1. GitHub repo: `thodel/hbls-mcp` (mirror of `thodel/hls-mcp`)
2. Docker image build on push
3. Caddy rule on `tei.dh.unibe.ch`
4. OpenClaw gateway config entry