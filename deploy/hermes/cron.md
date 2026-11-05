# The four cron jobs (H3, #799)

Created as `hermes`, after the skills are installed (README step 3) and the
gateway runs. Flag spellings follow the Hermes cron docs; confirm with
`hermes cron create --help` on the installed version before the first run.
Until the cut-over, deliver to the test channel (`--deliver discord` goes to
`DISCORD_HOME_CHANNEL`, which is the test channel on the test profile).

```bash
# 1. queue worker — 13:00, 17:00, 21:00 Europe/Zurich; a run may take 45 min
hermes cron create "0 13,17,21 * * *" --timezone Europe/Zurich --name dh-bot-queue \
  --skill dh-bot-queue --deliver discord --catch-up off \
  "Run the dh-bot queue worker exactly as the dh-bot-queue skill says. Work autonomously; never ask for confirmation. Reply only with the one-line report the skill defines, or nothing when there was nothing to do."

# 2. repo status — Tuesday 15:00
hermes cron create "0 15 * * 2" --timezone Europe/Zurich --name repo-status \
  --skill repo-status --deliver discord --catch-up on \
  "Do the weekly repo status check as the repo-status skill says. Reply with the 10–15 line overview only."

# 3. DH digest — Sunday 16:00 (OpenClaw had Europe/Berlin)
hermes cron create "0 16 * * 0" --timezone Europe/Zurich --name dh-digest \
  --skill dh-digest --deliver discord --catch-up on \
  "Produce the DH weekly digest as the dh-digest skill says: run digest_fetch.py first, then rate ~/.hermes/cron/digest_items.json. Always output a digest."

# 4. SNSF grants — 25 March and 25 September 09:00, no agent, silent unless changed
hermes cron create "0 9 25 3,9 *" --timezone Europe/Zurich --name snsf-grants \
  --script snsf_check.sh --no-agent --deliver discord --catch-up on
```

Then, for each:

```bash
hermes cron list
hermes cron run dh-bot-queue        # the first run with an empty state creates the state file and scans
hermes cron run repo-status
hermes cron run dh-digest
hermes cron run snsf-grants         # first run: "Baseline angelegt"; second run: silent
hermes cron status
ls ~/.hermes/cron/output/
```

What each job needs, beyond the skill:

| job | needs | from |
|---|---|---|
| dh-bot-queue | `gh` signed in (`gh auth login`), `uv`, `git-lfs`, the nine clones under `~/repos`, `command_allowlist` in config | README steps 1, 6; `config.yaml.example` |
| repo-status | `gh` signed in | README step 6 |
| dh-digest | network to the 17 feeds (four are Google Alerts) | — |
| snsf-grants | network to data.snf.ch; writes `~/.hermes/scripts/snsf_grants_baseline_meta.json` | — |

Model per job: the default (`qwen3.8-27b`) for all four; the queue worker ran
on `gpt-oss-120b` in OpenClaw — pin it with `hermes cron edit dh-bot-queue`
only if Qwen turns out to write worse PRs, and measure that on the merged PRs,
not on a feeling.
