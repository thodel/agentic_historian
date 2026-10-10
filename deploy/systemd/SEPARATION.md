# Separating the bot from the OpenClaw agent (S2, #567)

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

The rest of the separation epic — S1 (own Discord application/token, #566), S3
(lift the OpenClaw `workspace/` out of the repo), S4 (LLM-orchestration overlay
off by default), S5 (standalone CI smoke test) — is tracked under #565.
