# SSRQ OpenClaw Skill — Setup Guide (2026-07-08)

## Current Status: ✅ Fully Deployed

| Component | Status | Location |
|---|---|---|
| SQLite DB | ✅ Live | `/home/dh/.openclaw/tmp/ssrq_v6.db` |
| CLI script | ✅ In PATH | `/home/dh/.local/bin/ssrq` |
| Skill doc | ✅ Live | `skills/ssrq/SKILL.md` |
| Gateway registration | ✅ Live | `skills.entries.ssrq` in openclaw.json |

## Verify It's Working

```bash
ssrq ping
# Expected output:
#   ✓ SSRQ DB connected
#     Persons:     23,674
#     Organisations: 7,047
#     Name index:  138,298

ssrq search Johann | head -10
# Returns person rows with name variants
```

## Step-by-Step: Rebuilding from Scratch

If you need to redeploy everything on a fresh system:

### Step 1 — RDF → SQLite

```bash
# On tei.dh.unibe.ch, as dh user
cd /home/dh/.openclaw/tmp/

# The parser script (once written, saves to ssrq_v6.db)
python3 ssrq_parse_v6.py   # produces ssrq_v6.db
# Takes ~1 minute for 72MB TTL → ~30MB SQLite
```

The parser reads `/home/dh/resources/ssrq__fuseki_042810.ttl` (Fuseki RDF dump, 72MB)
and populates three tables:
- `persons` — authority records with life dates, relationships
- `orgs` — organisation records
- `name_index` — all name variants (orig + alias), keyed by `ssrq_id`

### Step 2 — Install the CLI Script

```bash
# Copy the script to ~/.local/bin (already in $PATH)
cp /home/dh/.openclaw/workspace/tmp/ssrq ~/.local/bin/ssrq
chmod +x ~/.local/bin/ssrq

# Alternative: symlink (if you have sudo)
sudo ln -s ~/.local/bin/ssrq /usr/local/bin/ssrq

# Verify
ssrq ping
```

### Step 3 — Register the Skill

OpenClaw skill files live in `~/.openclaw/workspace/skills/<name>/SKILL.md`.
The skill file is already deployed at `skills/ssrq/SKILL.md`.

It also needs to be registered in `~/.openclaw/openclaw.json` under
`skills.entries`:

```json
"skills": {
  "entries": {
    "ssrq": { "enabled": true }
  }
}
```

Verify current registration:
```bash
openclaw gateway config dump | python3 -c "import sys,json; d=json.load(sys.stdin); print('ssrq' in d.get('skills',{}).get('entries',{}))"
```

### Step 4 — Restart Gateway

```bash
openclaw gateway restart
```

### Step 5 — Verify End-to-End

```bash
ssrq ping
ssrq search Johann
ssrq person per000001
```

## Files

| File | Purpose |
|---|---|
| `/home/dh/.local/bin/ssrq` | CLI script — ping/search/person/org/names/related |
| `/home/dh/.openclaw/tmp/ssrq_v6.db` | SQLite DB (Fuseki TTL parsed) |
| `skills/ssrq/SKILL.md` | OpenClaw skill declaration |
| `/home/dh/resources/ssrq__fuseki_042810.ttl` | Source RDF dump (72MB) |
| `.openclaw/tmp/ssrq_parse_v6.py` | RDF→SQLite parser |

## MCP Server (Alternative)

An MCP server is also running at `https://tei.dh.unibe.ch/mcp/ssrq/`
with the same data. The MCP wrapper script at `.openclaw/tmp/ssrq`
can be used for HTTP-based access (requires auth token).

For OpenClaw skill use, the direct SQLite CLI is preferred — no network
round-trip, no auth needed.

## Troubleshooting

**`ssrq: command not found`**
```bash
echo $PATH | tr ':' '\n' | grep local
# Should show /home/dh/.local/bin
# If not:
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

**DB not found**
```bash
ls -lh ~/.openclaw/tmp/ssrq_v6.db
# If missing, re-run the parser (Step 1)
```

**Skill not picking up**
```bash
openclaw gateway restart
openclaw gateway logs 2>&1 | grep -i ssrq
```

**Skill shows as excluded in gateway**
```bash
# Check skill is in skills.entries (see Step 3 above)
openclaw gateway config dump | python3 -c "
import sys,json
d=json.load(sys.stdin)
entries = d.get('skills',{}).get('entries',{})
enabled = entries.get('ssrq',{}).get('enabled', False)
print(f'ssrq enabled: {enabled}')
"
```