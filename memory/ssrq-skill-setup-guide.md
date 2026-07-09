# SSRQ Skill Setup — Step-by-Step Guide

Created: 2026-06-29
Skill: `ssrq` — Search the Swiss SSRQ SQLite database

---

## Overview

This guide explains how to set up a new CLI-based skill for OpenClaw on tei.dh.unibe.ch. The skill consists of:
1. A **shell script** installed somewhere in PATH (here: `~/.local/bin/ssrq`)
2. A **SKILL.md** file (here: `skills/ssrq/SKILL.md`)

---

## Steps

### 1. Build the shell script

The script (`tmp/ssrq`) wraps Python code in bash heredocs. Key pattern:

```bash
#!/bin/bash
DB="/home/dh/.openclaw/tmp/ssrq_v6.db"

cmd="${1:-}"; shift || true
case "$cmd" in
    ping)
        python3 - <<'PY'
# Python code here
PY
        ;;
    search)
        python3 - <<PY
# Note: Use <<'PY' (quoted) when Python code contains ${...} or backticks
# Use <<PY (unquoted) for simpler cases
PY
        ;;
esac
```

**Important escaping rules:**
- Use `<<'PY'` (single-quoted heredoc) when Python code contains `${var}`, backticks, or f-strings with `{field}` where `field` is a Python variable
- Use `<<PY` (unquoted) for Python code without bash-interpolated variables

### 2. Write SKILL.md

Create `skills/<skillname>/SKILL.md` in the workspace with frontmatter:

```markdown
---
name: "ssrq"
description: "..."
---

# Skill: ssrq

...
```

### 3. Install the script to PATH

**Option A: `~/.local/bin/`** (no sudo needed, but must exist)
```bash
mkdir -p ~/.local/bin
cp tmp/ssrq ~/.local/bin/ssrq
chmod +x ~/.local/bin/ssrq
```

**Option B: `/usr/local/bin/`** (requires sudo)
```bash
sudo cp tmp/ssrq /usr/local/bin/ssrq
sudo chmod +x /usr/local/bin/ssrq
```

Check it's in PATH and works:
```bash
which ssrq
ssrq ping
ssrq search Johann
ssrq person per000001
```

### 4. Write skill documentation

Store at `skills/<skillname>/SKILL.md`. Include:
- What the skill does
- CLI commands and examples
- Database schema if applicable
- Tips and gotchas

### 5. OpenClaw Gateway Restart

After creating a new skill, restart the gateway to pick it up:

```
openclaw gateway restart
```

(From TOOLS.md: use `gateway` tool with `restart` action)

---

## Troubleshooting

### "sudo: a terminal is required"
Can't sudo without password. Use `~/.local/bin/` instead.

### Python heredoc brace errors
```
SyntaxError: invalid syntax ... unexpected token `f"    {rid}  {rlabel}"'))
```
Fix: Change `<<PY` to `<<'PY'` (quote the heredoc delimiter) so bash doesn't expand `{field}` as a bash variable.

### IDs not found
Check how IDs are stored in the DB. `ssrq_id` may be stored as `000001` not `per000001`. Add normalization:
```python
id = re.sub(r'^per','',raw).zfill(6)
```

### Empty search results
Check the DB columns and adjust the SQL query. The persons DB had empty `surname` fields — query fallback was needed.

---

## File Locations

| File | Path |
|------|------|
| Shell script | `~/.local/bin/ssrq` |
| SKILL.md | `skills/ssrq/SKILL.md` |
| Database | `/home/dh/.openclaw/tmp/ssrq_v6.db` |
| Source TTL | `/home/dh/resources/ssrq__fuseki_042810.ttl` |
| Temp script | `tmp/ssrq` (working copy) |