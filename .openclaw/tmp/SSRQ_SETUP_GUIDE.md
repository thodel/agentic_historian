# SSRQ OpenClaw Skill — Setup Guide

How to add the SSRQ (Sammlung schweizerischer Rechtsquellen) SQLite search
tool as an OpenClaw skill that `dh-bot` can use autonomously.

---

## Overview

- **Database:** `/home/dh/.openclaw/tmp/ssrq_v6.db` (23,674 persons, 7,047 orgs, 138k name variants)
- **Binary:** `~/.local/bin/ssrq` (Python script — no `sqlite3` CLI needed)
- **Skill file:** `skills/ssrq/SKILL.md`
- **Running at:** `https://tei.dh.unibe.ch/mcp/ssrq/` (23,674 persons, 138k name_index rows)

---

## Step-by-Step

### 1. Create the `ssrq` binary

Since the tei server has no `sqlite3` CLI, the script is written in Python.

**Location:** `~/.local/bin/ssrq`

The script wraps a Python `sqlite3` connection and exposes these commands:

| Command | Description |
|---|---|
| `ssrq ping` | DB stats |
| `ssrq search <name>` | Fuzzy search persons + orgs |
| `ssrq person <ssrq_id>` | Full person record |
| `ssrq org <ssrq_id>` | Full org record |
| `ssrq names <ssrq_id>` | All name variants |
| `ssrq related <ssrq_id>` | Related entities |

```python
#!/usr/bin/env python3
import sys, sqlite3, os, json

DB = "/home/dh/.openclaw/tmp/ssrq_v6.db"
# ... (full script in .openclaw/tmp/ssrq)
```

Copy the script to `~/.local/bin/ssrq` and `chmod +x` it.

**Test it:**
```bash
~/.local/bin/ssrq ping
~/.local/bin/ssrq search Johann | head -10
```

---

### 2. Create the OpenClaw skill

Create `skills/ssrq/SKILL.md` in the workspace with frontmatter:

```markdown
---
name: "ssrq"
description: "Search the Swiss SSRQ (Sammlung schweizerischer Rechtsquellen) SQLite database — 23k persons, 7k orgs, 138k name variants."
---

# Skill: ssrq

[...commands, examples, DB schema...]
```

SKILL.md should be committed to the workspace repo so it persists.

---

### 3. Write a guide

Document the setup process in `.openclaw/tmp/SSRQ_SETUP_GUIDE.md` (this file).

---

### 4. Register in OpenClaw (if needed)

Skills in `skills/<name>/SKILL.md` are auto-discovered. No manual registration required
if OpenClaw is configured to scan the workspace skills directory.

If not, use `openclaw skills register` or the Gateway web UI.

---

### 5. Restart the Gateway

```bash
openclaw gateway restart
```

Or via the Gateway web UI at `https://tei.dh.unibe.ch:18789`.

The new skill should be picked up and visible in the agent's available skills.

---

### 6. Verify

Ask `dh-bot`:

> Search for "Johann von Werdenberg"

It should call `~/.local/bin/ssrq search Johann von Werdenberg` and return results.

---

## Database Schema

```sql
persons(id, uri, etype, label, label_lang, std_name, forename, surname, sex,
        first_year, last_year, years, org_ids, spouse_ids, mother_ids,
        father_ids, loc_ids, orig_names, std_names)

orgs(id, uri, etype, label, std_name, surname, alias_of, org_type)

name_index(name_text, ssrq_id, is_orig)
  -- is_orig=1: original/formal name; is_orig=0: variant/alias
```

The `name_index` table is best for variant/alternate spelling lookups.
Search uses SQL `LIKE %term%` — works well for partial names.

`ssrq_id` format: `perNNNNNN` (persons) or `orgNNNNNN` (orgs), zero-padded to 6 digits.

---

## Troubleshooting

**`ssrq: command not found`**
→ Binary is at `~/.local/bin/ssrq`, make sure that's in your `$PATH`, or use the full path.

**`Database not found`**
→ DB is at `/home/dh/.openclaw/tmp/ssrq_v6.db`. If the path changes, update the `DB` variable at the top of the script.

**`sqlite3.OperationalError`**
→ Table doesn't exist in this DB version. Check `ssrq ping` to confirm which DB version is loaded.

---

## Notes

- No `sqlite3` CLI on tei — script uses Python's built-in `sqlite3` module.
- The `ssrq` script is a CLI wrapper, not an MCP server. It runs as a subprocess.
- If the MCP server at `https://tei.dh.unibe.ch/mcp/ssrq/` changes, update the SKILL.md accordingly.