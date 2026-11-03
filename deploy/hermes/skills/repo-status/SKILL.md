---
name: repo-status
description: Weekly status of the DH repositories — pushes, open PRs, stale issues, CI, Dependabot — as 10–15 lines of German for Discord.
version: 1.0.0
author: thodel
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [github, report, cron]
    requires_toolsets: [terminal]
    blueprint:
      schedule: "0 15 * * 2"
      deliver: discord
      prompt: Do the weekly repo status check as the skill says and reply with the overview only.
required_environment_variables:
  - name: GH_TOKEN
    help: GitHub token with repo scope, for gh.
    required_for: gh commands
---

# Repo status check

Weekly, Tuesday 15:00 Europe/Zurich (OpenClaw job "Repo Status Check", #799).
It ran fine there; what changes here is only where the token comes from (an
environment variable, not a file in a scratch directory) and that the stale
"known context" block is gone — the report describes today, not July.

## Repositories

thodel/agentic_historian, thodel/kf_mcp, thodel/outremer,
thodel/serving-atr-inference, thodel/hodelweb, thodel/training-atr-models,
thodel/ch-h-bot, thodel/live-translate.

## Collect, per repository

```bash
gh repo view thodel/<r> --json pushedAt,defaultBranchRef --jq '.pushedAt'
gh pr list --repo thodel/<r> --state open --json number,title,createdAt,isDraft
gh issue list --repo thodel/<r> --state open --json number,title,updatedAt --limit 200   # stale = updatedAt older than 30 days
gh run list --repo thodel/<r> --branch main --limit 3 --json conclusion,name,createdAt
gh api repos/thodel/<r>/dependabot/alerts --jq 'map(select(.state=="open")) | length' 2>/dev/null || echo "alerts: no access"
```

Six calls per repository; do them all before writing a line.

## Output

German, Discord-friendly, **10–15 lines in total**, no tables:

- one line per repository: `✅` / `⚠️` / `🔴`, then the facts (last push, open
  PRs with age, stale issues count, CI on main, open alerts);
- then "Sofort:" with at most three action items, or "Nichts dringend.";
- nothing else — no preamble, no closing. The reply is delivered as is.

`🔴` only for a red CI on main or an open security alert; `⚠️` for a PR older
than 14 days, a stale issue count above 10, or no access to alerts; `✅` otherwise.

## Pitfalls

- A repository without Actions returns an empty run list: that is `✅` for CI,
  not `⚠️`.
- `gh` rate limits at 5 000 requests/hour per token; this skill uses under
  fifty — if it ever gets `HTTP 403: API rate limit`, say so in the report
  instead of guessing numbers.
