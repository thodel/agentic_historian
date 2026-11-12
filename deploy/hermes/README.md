# dh-bot on Hermes — runbook (#803)

`dh-bot` is the DH unit's agent on tei. Until November 2026 it was an OpenClaw
agent running as `dh`; it moves to [Hermes Agent](https://github.com/NousResearch/hermes-agent)
as its own user `hermes`, with its own clones, its own secrets and a gateway
under systemd. This directory holds what the move needs from the repository
side: the skills the cron jobs run, the two scripts, the config and env
templates, and the cron commands. What was measured on tei before the move is
in #797; the decisions are in #801.

**The rule that this layout enforces:** dh-bot is the agent — conversation,
research over the MCP sources, mail/calendar/web, skills, memory, cron reports,
and pull requests for issues labelled `dh-bot`. The Historian bot
(`agentic-historian.service`) is the control panel — deterministic commands
with role gates, alone with access to GPU, training, data and share. **dh-bot
reaches the Historian only through `atr-mcp`, read-only; the Historian does not
know dh-bot.** dh-bot edits the Historian's *code* through PRs and never
*operates* it. The capability table lives in `docs/BOTS.md`.

## What is here

| path | purpose |
|---|---|
| `skills/dh-bot-queue/SKILL.md` | the queue worker: issues labelled `dh-bot` → one mergeable PR each (the "dh-bot Protokoll" of 2026-10-04, carried over) |
| `skills/repo-status/SKILL.md` | Tuesday 15:00, the weekly repository overview |
| `skills/dh-digest/SKILL.md` + `scripts/digest_fetch.py` | Sunday 16:00, the DH article digest; the script fetches the 17 feeds, the agent rates |
| `scripts/snsf_check.sh` | 25.3. and 25.9., has the SNSF grants CSV changed — a `--no-agent` job, silent unless something changed |
| `cron.md` | the four `hermes cron create` commands |
| `decommission_openclaw.sh` | H6 step 6: measures what is left of OpenClaw (dry run by default), `--apply` does the reversible part, prints the irreversible part for a human; `--collect-old-values`/`--verify-gone` for step 7 |
| `rotation.md` | H6 step 7: every secret OpenClaw could read, where it lives, who issues a new one, how to prove the old one dead — names, never values |
| `config.yaml.example`, `env.example` | what goes into `~/.hermes/config.yaml` and `~/.hermes/.env` (names, never values) |

The skills follow the agentskills.io layout Hermes reads
(`SKILL.md` with frontmatter, `scripts/` beside it). `blueprint:` in the
frontmatter registers each as a suggested cron job once the skill is installed;
`cron.md` creates them explicitly, which is what the first run should do.

## Install on tei (H2, #798)

Step 1 as `dh` with sudo; the rest as `hermes`. Nothing here touches
`/home/dh` except reading the agent files once for the import.

```bash
# 1. user, tools
sudo useradd -m -s /bin/bash hermes && sudo chmod 750 /home/hermes && sudo loginctl enable-linger hermes
sudo install -dm 755 /etc/apt/keyrings
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
sudo apt update && sudo apt install gh git-lfs
```

Then the staging copy of the agent files and the import — the exact commands
are in #798 (step 2 and 3): only `AGENTS.md`, `SOUL.md`, `MEMORY.md`,
`USER.md`, `TOOLS.md`, `memory/`, `skills/` and an `openclaw.json` with every
token and key removed go to `/home/hermes/openclaw-src`; the OpenClaw
*workspace* (project checkouts, scripts, `sample/`) stays where it is, as
`dh`'s working directory. `hermes import-agent openclaw --source ~/openclaw-src
--workspace-target ~/dh-bot` imports persona, memory, skills, model and MCP
entries. Cron jobs, heartbeat, `IDENTITY.md` and `TOOLS.md` are archived, not
imported: the identity goes into `SOUL.md` by hand, the cron jobs come from
`cron.md`.

```bash
# 3. this bundle, as hermes
git clone -q https://github.com/thodel/agentic_historian.git ~/repos/agentic_historian
mkdir -p ~/.hermes/skills ~/.hermes/scripts ~/.hermes/cron
cp -a ~/repos/agentic_historian/deploy/hermes/skills/* ~/.hermes/skills/
cp -a ~/repos/agentic_historian/deploy/hermes/scripts/snsf_check.sh ~/.hermes/scripts/
hermes skills list | grep -E 'dh-bot-queue|repo-status|dh-digest'
```

```bash
# 4. secrets and config — values through the editor, never on a command line
chmod 700 ~/.hermes; touch ~/.hermes/.env && chmod 600 ~/.hermes/.env    # fill from env.example
hermes config set model.provider custom
hermes config set model.base_url https://gpustack.unibe.ch/v1
hermes config set model.default lfm2.5-24b-a2b
hermes config set model.context_length 125000    # GPUStack: 125K; unset, Hermes assumes 256000
hermes config set providers.gpustack-oss.base_url https://gpustack.unibe.ch/v1
hermes config set providers.gpustack-oss.model gpt-oss-120b
hermes config set providers.gpustack-oss.key_env GPUSTACK_API_KEY
hermes config set platforms.discord.enabled true
hermes config set discord.require_mention true
hermes config set approvals.mode smart
hermes config set terminal.cwd /home/hermes/dh-bot
hermes status
```

Model: the default is `lfm2.5-24b-a2b` on GPUStack (since 09.11.2026; before
that `qwen3.8-27b`, as in OpenClaw), and `gpt-oss-120b` (a reasoning model; pin
it only where a job wants it). Check the id against
`curl -s -H "Authorization: Bearer $GPUSTACK_API_KEY" https://gpustack.unibe.ch/v1/models`
before switching — a wrong id fails every turn with a 404 from GPUStack. The API
key is the one in `.env.gpustack` until H6 rotates it.

```bash
# 5. gateway as a service
hermes gateway install && hermes gateway start && sleep 8 && hermes gateway status
tail -20 ~/.hermes/logs/gateway.log
```

Discord: until the cut-over (H6) the gateway runs with a **test application**
`dh-bot-test` in a test channel — Hermes locks a token to one gateway, and
`dh-bot`'s token is OpenClaw's until then. Hermes needs the Message Content and
Server Members intents (the Historian bot needs none). `DISCORD_ALLOWED_ROLES`
on the project role is a deliberate change: OpenClaw ran with
`groupPolicy: open`, no allowlist at all.

```bash
# 6. the clones for the queue worker
mkdir -p ~/repos && cd ~/repos
for r in live-translate tei-vm-sanity serving-atr-inference training-atr-models ch-h-bot outremer reformation agentic-historian-outputs; do git clone -q https://github.com/thodel/$r.git; done
curl -LsSf https://astral.sh/uv/install.sh | sh
gh auth login --with-token      # token with repo scope, from the password manager
```

## MCP (H4, #800)

The federation behind nginx speaks Streamable HTTP at `/mcp/<name>/mcp`;
`/mcp/<name>` without the suffix is a landing page for humans (302), which is
what broke OpenClaw. One entry per source, see `config.yaml.example`. `hls`
needs a key nobody has recorded — leave it out until #800 settles it.
`atr-mcp` gets a tool filter: the reading tools only.

Measured 05.11.2026, as `hermes`: kf 14, eos, hbls, ssrq (imported), outremer
4, sikart 7, wd-pre1500 6, hls 10 (Basic auth, `HLS_BASIC` in `.env`), atr 16
of 39 (`auth: oauth`, once `hermes mcp login atr`: open the printed URL in a
browser, enter the server's password, paste the `127.0.0.1:27890/callback?…`
address back; tokens in `~/.hermes/mcp-tokens/`, 30-day refresh). The gateway
log line `MCP: registered 130 tool(s) from 9 server(s)` is the tally; a server's
own count comes from `MCP server '<name>' (HTTP): registered N tool(s)`. Probe:
"was läuft auf der ATR-Karte" answers over `gateway_models`; "starte einen
Batch" is refused because the tool is not there — not because approval is.

## Cron (H3, #799)

Four jobs, all of which were failing in OpenClaw. `cron.md` has the commands;
each gets one `hermes cron run <name>` by hand and one scheduled run before
H3 is done. Delivery is a DM to Tobias — verify on the test profile that
Hermes delivers to a user id, else use a private home channel. The seven
jobs in `dh`'s crontab (ch-h-bot, outremer, eperiodica, tei-vm-sanity) are
not the agent's and stay.

## Cut-over and after (H6, #802)

Done on 06.11.2026 07:47: OpenClaw stopped and disabled, Hermes alone as
`dh-bot-hermes` (that application stays), `hermes cron resume`. The old
application `dh-bot` (`1519706253314756758`) stays until the end of the
observation week, **13.11.2026**; until then `systemctl --user start
openclaw-gateway` as `dh` is the way back.

### Step 6 — the Rückbau, from 13.11.2026

`decommission_openclaw.sh` runs as root, because it reads both homes and acts
as `dh` or `hermes` for each step. Without an option it changes nothing and
prints, per section: is `openclaw-gateway` active or enabled, where the
`openclaw` binary and its npm prefix are, which global npm packages exist,
whether anything else needs Node (`apt-cache rdepends --installed nodejs`,
other users' node processes, units and crontabs; the `deb.nodesource.com`
list), the sizes of `~/.openclaw` and `~/openclaw-workspace`, Git checkouts in
the workspace with uncommitted or unpushed work, the names (never values) of
the secrets OpenClaw held, the four Hermes cron jobs and their last output, and
whether the imported skill `openclaw-imports/batch-issue-processor` is still
installed.

```bash
# 1. as dh: the script from main
cd /home/dh/agentic_historian && git pull --ff-only
sudo bash deploy/hermes/decommission_openclaw.sh | tee /dev/shm/rueckbau-dry.txt
#    read every [!!] and [STOP]; a [STOP] blocks --apply. Git work in the
#    workspace: push or copy it out first. The H3 check: "letzte Ausgabe
#    dh-digest" after 08.11. 16:00 and "repo-status" after 10.11. 15:00.

# 2. the secrets as they are now, before anything moves (step 7 needs them)
sudo bash deploy/hermes/decommission_openclaw.sh --collect-old-values /dev/shm/alte-werte

# 3. the reversible part: dated tar.gz of the workspace and of ~/.openclaw in
#    /home/dh/archiv/ (0600), ~/.openclaw -> ~/.openclaw.pre-migration, the old
#    skill -> /home/hermes/archiv/openclaw-imports-<date>/; `hermes claw cleanup`
#    only if /home/hermes holds an OpenClaw directory of its own
sudo bash deploy/hermes/decommission_openclaw.sh --apply

# 4. the irreversible part: the numbered list the script printed last,
#    "Für den Menschen" — unit file, `npm uninstall -g openclaw`, Node only if
#    section 3 said nobody needs it, the workspace, the Discord application
#    `dh-bot` in the portal, the archive deletion date (+30 days)
```

The script stops (exit 3) before the first change while `openclaw-gateway` is
active or enabled, an OpenClaw process runs, a process works inside the
workspace, or the date is before 13.11.2026. Each `--apply` step is a `mv` or
a new file; the script prints the way back. It never prints a secret value:
process lines are cut to PID, user and program name, files are counted, not
shown.

### Step 7 — rotation

`rotation.md`: per secret, issue a new one, put it everywhere the table says,
restart, revoke the old one, probe it with `401`. Done when

```bash
sudo bash deploy/hermes/decommission_openclaw.sh --verify-gone /dev/shm/alte-werte
```

says `kein alter Wert mehr in den Live-Dateien` (exit 0) — `.env.gpustack`,
`/etc/agentic_historian.env` and `~hermes/.hermes/.env` among them. Then
`shred -u /dev/shm/alte-werte`, and the Rapid7 rescan for #792/#795
(`docs/SECURITY_TEI.md`). This README replaces the OpenClaw sections in
`docs/DEPLOYMENT_TEI.md`, which keeps the history.
