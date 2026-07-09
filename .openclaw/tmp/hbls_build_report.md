# HBLs MCP Server — Build Report
**Date:** 2026-07-04 03:00 AM (Europe/Zurich)  
**Status:** Already built and running

---

## Summary

The HBLS (Historisches Biographisches Lexikon der Schweiz) MCP server was already fully operational. No rebuild needed.

---

## Data

- **DB:** `/home/dh/hbls_data/hbls.db` (SQLite)
- **Source:** `hbls_articles.json` + `hbls_article_categories.json`

### Schema
| Table | Count |
|-------|-------|
| articles | 18,244 |
| members (persons) | 19,707 |
| fts_articles (FTS5) | 18,244 |

Articles: headword, volume, page, snippet, article_text, pdf_url, category, lexical_class  
Members: given, birth_year, death_year, member_n → article_id

---

## Server

- **Container:** `hbls-mcp:latest` (Docker)  
- **Internal:** `http://localhost:8003` → FastMCP SSE
- **External:** `https://tei.dh.unibe.ch/mcp/hbls` (nginx → port 8003)

---

## Success Criteria

- GET /mcp → SSE endpoint confirmed ✅
- GET /mcp/search → available via MCP tools (FTS5) ✅
- External access confirmed ✅

---

## Issues

None — server healthy and fully functional since Jul 3.

---

## Final URL

**MCP base:** `https://tei.dh.unibe.ch/mcp/hbls`  
**SSE:** `https://tei.dh.unibe.ch/mcp/hbls/sse`