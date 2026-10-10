# Bot systemd units (`agentic-historian.service`)

The Discord bot runs on tei as a system service, `agentic-historian.service`. The
main unit lives on the host; this directory holds the drop-ins that are tracked in
the repo and copied into `/etc/systemd/system/agentic-historian.service.d/`.

Install a drop-in:

```
sudo cp deploy/systemd/agentic-historian.service.d/<file>.conf \
    /etc/systemd/system/agentic-historian.service.d/
sudo systemctl daemon-reload && sudo systemctl restart agentic-historian
```

## Drop-ins

- **`restart.conf`** (#247) — `Restart=always`, so systemd brings the bot back
  after a crash and after `/update` exits the process to redeploy.
- **`hardening.conf`** (SEC-9, #580) — sandboxes the process the way the MCP
  server unit already is: `ProtectSystem=strict`, `NoNewPrivileges`, a narrow
  `ReadWritePaths`, and the kernel/cgroup/SUID restrictions. **Edit the writable
  paths and the `ProtectHome` note for the real checkout before installing**, then
  check with `systemd-analyze security agentic-historian`.

## Secrets held by the bot (SEC-9, #580)

The bot process currently holds every secret in `.env.gpustack` at once:

| Secret | Reach if leaked |
| --- | --- |
| Discord bot token | the bot's Discord identity — and, while the application is shared with the OpenClaw agent `dh-bot`, the agent's too (S1, #566) |
| GPUStack key | the GPU scheduler |
| ATR gateway key | start jobs on a two-A40 host |
| GitHub token | write on the outputs repo; branch/PR on the code repo |
| SwitchDrive app password | the hot-folder share |
| Nextcloud share password | the published-outputs share |
| Hugging Face token | the HF account |

`hardening.conf` reduces the **blast radius** of a bot compromise (no writes
outside the data dir, no privilege escalation). It does **not** reduce the **set**
of secrets in the one process — splitting the bot's secrets from the OpenClaw
agent and giving each only what it needs is tracked in #567 (and the separation
epic #565), not here.

## Key rotation (SEC-9, #580, action required)

`docs/CLAUDE_CODE_CONNECTIVITY.md` records that the **ATR gateway key was exposed
in a terminal transcript on 2026-09-14**. Whether it was rotated afterwards is not
documented. Before the open-server deployment:

- [ ] Rotate the ATR gateway key (and any other secret that has appeared in a log,
      transcript, or screen share), then update `.env.gpustack` on tei and the MCP
      server's `EnvironmentFile`.
- [ ] Record the rotation date here so the next reader does not have to re-ask.

Rotation is an operator action on the host — it cannot be done from the repo.
