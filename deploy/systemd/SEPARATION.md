# Separating the bot from the OpenClaw agent (S2 #567, S3 #568)

Part of the separation epic #565. The Historian bot and the OpenClaw agent
`dh-bot` both run on `tei` as user `dh` and read the same `.env.gpustack` — which
holds the OpenClaw agent's mail / calendar / SSH credentials that the bot never
needs. S2 gives the bot its own secrets, its own confinement, and (optionally)
its own user.

## What this repo provides

- **`dh-bot.env.example`** — the bot's own secrets file, listing only the keys
  its commands need. No OpenClaw credentials.
- **`agentic-historian.service.d/secrets.conf`** — a drop-in that points the unit
  at `/etc/dh-bot.env` and sets `AH_ENV_FILE`, so the bot reads only its own
  secrets.
- **`config.py` `AH_ENV_FILE` support** — with `AH_ENV_FILE` set, the bot reads
  only that file for its file-sourced secrets and never falls back to
  `.env.gpustack`. The real process environment still wins over any file.
- **`agentic-historian.service.d/hardening.conf`** — the systemd sandbox (item 2:
  `ProtectSystem=strict`, `NoNewPrivileges`, narrow `ReadWritePaths`, …). This
  ships with the security epic (#580) and is installed alongside this drop-in.

## Install on tei

```
# 1. The bot's own secrets, root-owned and unreadable by other users.
sudo install -m 600 -o root -g root dh-bot.env /etc/dh-bot.env
#    fill it from dh-bot.env.example — copy ONLY the bot's keys out of the old
#    .env.gpustack, never the OpenClaw mail/calendar/SSH ones.

# 2. Point the unit at it, and sandbox the process.
sudo cp deploy/systemd/agentic-historian.service.d/secrets.conf  /etc/systemd/system/agentic-historian.service.d/
sudo cp deploy/systemd/agentic-historian.service.d/hardening.conf /etc/systemd/system/agentic-historian.service.d/
sudo systemctl daemon-reload && sudo systemctl restart agentic-historian
```

## Items and status

1. **Own `EnvironmentFile` with only the bot's secrets** — `dh-bot.env.example` +
   `secrets.conf` + `AH_ENV_FILE` here. ✅ (repo side)
2. **Harden the unit like the MCP server** — `hardening.conf` (#580). ✅ (repo side)
3. **Run as a dedicated, less-privileged user** — create a `dh-bot` user without
   read access to the OpenClaw secret file, then set `User=`/`Group=` in
   `secrets.conf`. ◻️ host action.

## Definition of done (#567)

The bot process cannot read the OpenClaw credentials, runs hardened under systemd,
and writes only to its data directory. The repo pieces above make that
configurable; the remaining steps are operator actions on the host:

- [ ] `/etc/dh-bot.env` created (root:root 0600) with only the bot's keys.
- [ ] `secrets.conf` + `hardening.conf` installed; `systemd-analyze security
      agentic-historian` looks sane; `ReadWritePaths` in `hardening.conf` edited
      to the real data dir.
- [ ] A dedicated `dh-bot` user that cannot read the OpenClaw secret file, with
      `User=` set — or, until then, OS permissions on the OpenClaw secret file so
      the bot's current user cannot read it.
- [ ] Rotate any secret that lived only in the shared `.env.gpustack` and was
      therefore handled by both processes.

## S3 — the OpenClaw home is no longer in this repo (#568)

`workspace/` was the OpenClaw agent's home — `AGENTS.md`, `SOUL.md`,
`IDENTITY.md`, `USER.md`, `HEARTBEAT.md`, `TOOLS.md`, `skills/`, plus its own
`requirements.txt`, `ocr_pipeline_README.md` and a scratch `scramble.py` — living
in the bot's repository. That coupled the two systems at the repository and put
the agent's identity, personal notes and infrastructure details into the bot's
tree. The bot never imported or read anything from it (the `standalone` CI job,
S5, proves import and start without it), so it is removed; what the bot *did*
keep there moved:

| was | is now |
|---|---|
| `workspace/gpustack.env.example` | `deploy/gpustack.env.example` (the `.env.gpustack` template) |
| `workspace/CONSOLIDATION_AND_FEEDBACK.md` | `docs/CONSOLIDATION_AND_FEEDBACK.md` (project backlog) |
| `workspace/KNOWLEDGE_HUB_DATA_INTEGRATION.md` | `docs/KNOWLEDGE_HUB_DATA_INTEGRATION.md` (plan) |
| everything else | the OpenClaw agent's own project — see below |

### ⚠️ Before deploying past this change

`update.sh` runs `git merge --ff-only` / `git reset --hard origin/main` on the
production checkout. If the OpenClaw agent's workspace on tei **is** that
checkout's `workspace/` (its `AGENTS.md` says "this folder is home", and
`openclaw-workspace-state.json` is gitignored right there), the first deploy
after this change deletes the agent's tracked home files — `AGENTS.md`,
`SOUL.md`, … — out from under a running agent, while its untracked state
(`memory/`, `MEMORY.md`, the state json) stays behind, orphaned. So, **on tei,
before `/update` or `update.sh` reaches this commit**:

```
# 1. Give the agent a home outside the bot checkout (or clone its new repo there).
cp -a /path/to/agentic_historian/workspace  ~/openclaw-workspace

# 2. Point OpenClaw's workspace setting at the new path, restart it, and
#    confirm it reads AGENTS.md/SOUL.md from there (and writes memory/ there).

# 3. Only then deploy the bot. `git status` in the checkout afterwards must not
#    list a leftover workspace/ — if it does, that is the old home, still in use.
```

The removed files are not lost: they are in history, and this prints them back
without knowing a commit id —

```
rm_commit=$(git log --diff-filter=D --format=%H -1 -- workspace/AGENTS.md)
git archive --prefix=openclaw-workspace/ "${rm_commit}^" workspace | tar -x -C /tmp
```

### Definition of done (#568)

- [x] No OpenClaw home file is tracked in this repository (`test_ah_568`).
- [x] The bot imports, starts and deploys without `workspace/` (`standalone` CI job).
- [x] The env template and the two planning documents are still findable.
- [ ] The agent's workspace on tei lives outside the bot checkout, and OpenClaw
      is pointed at it — a host action, done before the deploy above.
- [ ] The OpenClaw agent has its own repository (optional; the archive recipe
      above seeds it).

The rest of the separation epic — S1 (own Discord application/token, #566), S4
(LLM-orchestration overlay off by default, #569), S5 (standalone CI smoke test,
#570) — is tracked under #565.
