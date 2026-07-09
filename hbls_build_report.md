# HBLS MCP Server — Build Report
**Date:** 2026-07-08 | **Time:** 03:00 UTC | **Status:** ✅ Running and accessible

---

## Summary

The HBLS MCP server is already built and running (from a prior session). My job was to verify, test, and document it. The server is functional via MCP/SSE protocol from outside.

**Container:** `hbls-mcp-test` (port 8003)  
**Data:** `/home/dh/hbls_data/hbls.db` (SQLite + FTS5)  
**Code:** `/home/dh/hbls_mcp/server.py`

---

## Data Schema

SQLite database (`hbls.db`) with two main tables:

**`articles`** — one row per HBLS encyclopedia article  
Fields: `id`, `headword`, `volume`, `page`, `snippet`, `article_text`, `pdf_url`, `category`, `lexical_class`

**`members`** — family/article members with biographical data  
Fields: `id`, `article_id` (FK), `given`, `birth_year`, `death_year`, `member_n`

**`fts_articles`** — FTS5 virtual table for full-text search

**`hbls_article_categories.json`** — maps (volume|id|headword) → {category, lexical_class}

---

## Record Counts

- **18,244** articles across 8 volumes
- **19,707** named members/persons
- **~37 MB** of article text

---

## Test Results

### Local (all passing)
```
GET /mcp                          → 200 OK  name=HBLS, 10 tools
GET /mcp/search?q=Keller&limit=2  → 200 OK  count=2 [VISCHER, KELLERMÜLLER]
GET /mcp/stats                    → 200 OK  articles=18244, members=19707
```

### External via nginx
```
GET /mcp/hbls/sse     → 200 OK  event:endpoint  session_id=<uuid> ✅
GET /mcp/hbls/landing → 200 OK  HTML landing page ✅
GET /mcp/hbls/mcp     → broken (nginx rewrite issue) ⚠️
GET /mcp/hbls/search  → broken (nginx rewrite issue) ⚠️
```

---

## Working Endpoints

| Endpoint | Status |
|----------|--------|
| MCP SSE (primary) | https://tei.dh.unibe.ch/mcp/hbls ✅ |
| Landing page | https://tei.dh.unibe.ch/mcp/hbls/landing ✅ |
| Local REST manifest | http://localhost:8003/mcp ✅ |
| Local REST search | http://localhost:8003/mcp/search?q=... ✅ |
| External REST | needs nginx fix ⚠️ |

---

## Nginx Fix Needed (root required)

Change line 197 in `/etc/nginx/sites-enabled/tei.dh.unibe.ch`:
- From: `location /mcp/hbls {` (prefix match)
- To: `location = /mcp/hbls {` (exact match)

Then: `sudo nginx -t && sudo nginx -s reload`

---

## MCP Tools

corpus_stats, search, get_article, get_article_by_page, list_volume, get_family_members, search_persons, search_bio, get_pdf_url, get_articles_by_category, get_category_stats

---

## Issues Encountered

1. **Nginx prefix match** — `location /mcp/hbls` intercepts all child paths; fix requires root access.
2. **External REST** — broken due to nginx issue above; MCP/SSE protocol works fine.
3. Server was pre-built in a prior session; my work was verification + documentation.
