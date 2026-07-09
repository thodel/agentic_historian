---
name: ssrq
description: Search the Swiss Summary of Roman Law Queries (SSRQ) database — 23,674 persons, 138k name variants. Local SQLite at /home/dh/.openclaw/tmp/ssrq_v6.db.
user-invocable: true
---

# SSRQ — Swiss Summary of Roman Law Queries

Search the SSRQ person/organisation database via the `ssrq` CLI tool on tei.

## Quick Start

```bash
ssrq ping                 # Show database stats
ssrq search <name>        # Fuzzy search by name (LIKE %name%)
ssrq person <ssrq_id>     # Full person record (e.g. per000001)
ssrq org <ssrq_id>        # Full org record (e.g. org000001)
ssrq names <ssrq_id>      # All name variants for a person/org
ssrq related <ssrq_id>    # Spouse, mother, father, location links
```

## ID Formats

- Persons: `per000001` or just `1` / `000001`
- Organisations: `org000001` or just `1` / `000001`

## Search Tips

- Search is case-insensitive SQL LIKE — no wildcards needed
- Works on: label, std_name, surname, forename, orig_names, std_names
- Returns up to 40 results, sorted by surname/forename
- Name variants shown inline (up to 10, pipe-separated)

## Database

- **Path:** `/home/dh/.openclaw/tmp/ssrq_v6.db`
- **Persons:** 23,674 | **Orgs:** 7,047 | **Name index:** 138,298
- **Script location:** `/home/dh/.local/bin/ssrq` (also at `/usr/local/bin/ssrq`)

## DB Schema

```sql
persons(id, uri, etype, label, label_lang, std_name, forename, surname, sex,
        first_year, last_year, years, org_ids, spouse_ids, mother_ids,
        father_ids, loc_ids, orig_names, std_names)

orgs(id, uri, etable, label, std_name, surname, alias_of, org_type)

name_index(name_text, ssrq_id, is_orig)
  -- is_orig=1: original/formal name; is_orig=0: variant/alias
```

## Cross-Reference

- SSRQ persons link to Königsfelden (kf) via numeric ID in `.openclaw/tmp/kf_ssrq_hls_crossref.json`
- SSRQ TTL at `/home/dh/resources/ssrq__fuseki_042810.ttl` has HLS IDs for ~162 persons
- See `kf__search_persons` for Königsfelden lookups
- MCP server also available at `https://tei.dh.unibe.ch/mcp/ssrq/` for programmatic access