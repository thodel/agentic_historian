# SSRQ OpenClaw Skill Setup — Step-by-Step Guide

Created: 2026-07-06

## Status

| Step | Status | Notes |
|------|--------|-------|
| 1. Shell script to /usr/local/bin/ssrq | **BLOCKED** | Needs sudo password (no TTY available) |
| 2. SKILL.md to workspace | ✅ Done | Written to `.openclaw/tmp/ssrq_skill/SKILL.md` |
| 3. Test ssrq ping/search | ✅ Done | Working from `/home/dh/.local/bin/ssrq` (in PATH) |
| 4. openclaw gateway restart | ⏸️ Skip? | Script works via exec — no gateway restart needed |
| 5. This guide | ✅ Done | You're reading it |

## What Works Right Now

The `ssrq` shell script is at `/home/dh/.local/bin/ssrq` and is already in PATH:

```bash
ssrq ping          # ✓ 23,674 persons, 7,047 orgs, 138,298 name variants
ssrq search Johann # ✓ returns results
ssrq person per000001  # ✓
```

## Remaining: Step 1 (sudo needed)

To complete the original plan, copy the script to `/usr/local/bin/ssrq`:

```bash
sudo cp /home/dh/.local/bin/ssrq /usr/local/bin/ssrq
sudo chmod 755 /usr/local/bin/ssrq
```

This needs to be done from a machine where sudo works without a password prompt (e.g., with `NOPASSWD` configured).

## Alternative: Keep as-is

The script works fine from `/home/dh/.local/bin/ssrq` — it's already in PATH and accessible to the agent via `exec`. No need to move it unless you prefer `/usr/local/bin/` for system-wide access.

## OpenClaw Skill Integration

The SKILL.md in `.openclaw/tmp/ssrq_skill/` documents usage but is not auto-loaded by OpenClaw. For full skill integration (so `ssrq` shows up as a tool), the skill needs to be either:

1. **Plugin skill** — place `SKILL.md` in `/home/dh/.openclaw/plugin-skills/ssrq/`
   (requires gateway restart to pick up)
2. **Registered skill** — add entry in `openclaw.json` skills block

## Database Location
`/home/dh/.openclaw/tmp/ssrq_v6.db`

## MCP Server (already running)
`https://tei.dh.unibe.ch/mcp/ssrq/` — 23,674 persons, 138k name_index rows