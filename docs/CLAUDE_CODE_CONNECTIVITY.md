# Reaching tei and idhefix from a Claude Code session

Every session so far has ended at the same wall: the model can write the batch
runner, the registry entries and the runbook, and then cannot run a single line
of it on the machines that have the GPUs. The work gets pasted into a terminal by
hand, and every diagnosis costs a round trip through a person.

This file records **why** that is, and the one request that fixes it for all
future sessions rather than for one.

---

## What is actually blocked

Claude Code on the web runs in an ephemeral cloud container whose outbound
traffic goes through an agent proxy. The proxy enforces an allowlist at CONNECT
time. Measured in this session, 2026-09-14:

```
$ curl -sI https://tei.dh.unibe.ch/
HTTP/1.1 403 Forbidden

[agent-proxy] tei.dh.unibe.ch:443 — connect_rejected
  (the egress proxy denied the CONNECT (organization policy))
```

Same for `cloud.gugw.tu-darmstadt.de` (the Nextcloud share holding the
*digitalisate*). A raw TCP connect to `tei.dh.unibe.ch:22` times out — there is no
path around the proxy.

The environment is:

| | |
|---|---|
| name | **Default** — "trusted network access" |
| id | `env_018hw3ZuVMJ6aismztuBWeTJ` |
| kind | `anthropic_cloud` |

"Trusted network access" is not "any network access": package registries (pypi,
npm, crates, GitHub) are pre-cleared, and everything else is refused.

### What *does* work, and why it is the clue

MCP calls succeed from the same session — the HBLS, Königsfelden and
Economies-of-Space servers answer normally. They do **not** go through the egress
proxy: MCP traverses Anthropic's MCP broker, which is a different network path
with a different policy. This matters twice below: it is why "the network is
broken" is the wrong description, and it is why the MCP fallback at the end works
at all.

---

## The request (option 4) — worth sending, but it is not a shell

Two hostnames on 443, one change, inherited by every future session. **Ports
cannot be adjusted**, though (confirmed 2026-09-15), so ssh is out of reach: this
gets a cloud session the Nextcloud share and anything tei serves over HTTPS, and
it does not get it a command line on either box. For the GPU work, see
[*The local session*](#the-local-session-what-actually-works) below — the two are
complementary, not alternatives.

**Who it goes to:** whoever administers the Claude.ai organisation that owns this
environment. For an organisation-level policy that is an org owner, in the
Claude.ai admin settings; for a personally-owned environment it is a setting on
the environment itself.

> ### Request: extend the egress allowlist of environment `env_018hw3ZuVMJ6aismztuBWeTJ`
>
> Please allow outbound HTTPS (TCP 443) from this Claude Code environment to:
>
> | host | why |
> |---|---|
> | `tei.dh.unibe.ch` | the DH research server (Uni Bern). Hosts the agentic_historian checkout, the MCP endpoint, Voyant and the QLever SPARQL endpoint. It is where batch ATR runs are driven from. |
> | `cloud.gugw.tu-darmstadt.de` | Nextcloud at TU Darmstadt, holding the *Laßberg* scans to be transcribed. Public share, read-only, password-protected. |
>

> **What this grants.** Outbound connections from the container to those two
> hosts. Both are already reachable from the public internet and both are behind
> their own authentication (ssh keys / an API key / a share password) — the
> allowlist does not hand out any credential, it only stops the proxy from
> refusing the connection before authentication is ever attempted.
>
> **What it does not grant.** No inbound access to the container. No access to
> any other host. No change to the credentials themselves, which stay where they
> are (`.env` on the servers) and are never committed.
>
> **Why it is worth doing.** The work is ATR over a few hundred manuscript pages
> on two GPU servers. Everything that can be done without the servers is done and
> merged; what is left is running it, and today every command is copied into a
> terminal by hand and every error copied back. Two hostnames remove that loop.

---

## The local session — what actually works

**A Claude Code session running on your own machine has no agent proxy.** The
proxy is a property of *this* environment (`kind: anthropic_cloud`); a local
session uses the machine's own network stack — its VPN, its `~/.ssh/config`, its
ssh agent, its resolver. Everything that works in your terminal works there, with
nothing to request from anybody.

Nothing is lost by switching. Both repositories are merged to `main`, and the
runbooks were written for a person at a terminal, not for this session's memory:

- [`BATCH_ATR.md`](BATCH_ATR.md) — pull the share, run the batch, publish
- `serving-atr-inference/docs/GERMAN_XIX_MODELS.md` — the models, the merge, the
  two failure modes met so far
- `serving-atr-inference/docs/DEPLOY.md` — the services and the GPU budget

```bash
# on the laptop, VPN up
git clone https://github.com/thodel/agentic_historian
git clone https://github.com/thodel/serving-atr-inference
cd agentic_historian && claude
```

Then `/permissions` once, adding `Bash(ssh:*)` and `Bash(scp:*)`, or the run is
one confirmation prompt per command — which is the same hand-pasting loop in a
different costume.

### Better still: on tei, in tmux

No VPN between the agent and the machine at all, idhefix one hop away over the
existing `:8200` gateway, and a dropped laptop connection no longer kills a
multi-hour run. The trade is an Anthropic login on a shared research server.

**Claude Code is not installed on tei** (checked 2026-09-15). The native installer
needs no root, no Node and no package manager — it drops a binary in
`~/.local/bin/claude` with its versions under `~/.local/share/claude/`, which is
what a user without sudo on a shared box needs:

```bash
ssh tei.dh.unibe.ch
curl -fsSL https://claude.ai/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"     # and into ~/.bashrc, same as ATR_API_KEY
claude --version                         # expect e.g. "2.1.211 (Claude Code)"
claude doctor                            # install + settings diagnostics, no session
```

Two things that bite on a headless server:

- **Login has no browser.** `claude` prints a URL; open it on the laptop, and paste
  the code back into the ssh session. Needs a Pro, Max, Team, Enterprise or Console
  account — the free plan does not include Claude Code.
- **tei needs outbound HTTPS to Anthropic.** It serves the public web, so this is
  near-certain, but it is the one thing that can make the install useless after the
  fact. `claude doctor` says so before a run does.

Then:

```bash
tmux new -s atr
cd ~/agentic_historian && claude      # detach Ctrl-B d, reattach: tmux attach -t atr
```

The installer auto-updates in the background, so this is a one-time cost. If a
shared server should not follow `latest`, put `{"autoUpdatesChannel": "stable"}`
in `~/.claude/settings.json`.

### The handoff

A local session starts with no memory of this one. This is the whole of what it
needs; paste it as the first message.

> Two repos, both checked out here and both merged to `main`: `agentic_historian`
> and `serving-atr-inference`. Read `agentic_historian/docs/BATCH_ATR.md` first —
> it is the runbook for exactly this task.
>
> The job: transcribe the Laßberg *digitalisate* from the Nextcloud share at
> `cloud.gugw.tu-darmstadt.de` with the dh-unibe German-XIX models on the ATR
> gateway, and publish the output to `thodel/lassberg` under
> `data/vlm-outputs/atr_test_lassberg`.
>
> State you should know before you start:
>
> - **The machines.** `tei.dh.unibe.ch` has the checkout, the venv and the
>   gateway key; idhefix (`130.92.59.240`) has the two A40s and serves the ATR
>   gateway on `:8200`. Drive the run from tei.
> - **Only one of the three models is servable.** `qwen3vl-german-xix-v1` works.
>   `qwen3.5-4b/2b-german-xix-v1` are registered `enabled: false`: no transformers
>   on the box can load a `qwen3_5` base, and the vLLM that could serve it needs a
>   driver newer than idhefix's 565. Pair qwen3vl with `kraken-fondue_gd_v2` and
>   `party` for the comparison instead.
> - **The GPU budget is computed now**, from `vram_mb` and `nvidia-smi`
>   (serving-atr-inference#127). Do not set
>   `ATR_VLLM_GPU_MEMORY_UTILIZATION` by hand. Each launch logs its arithmetic:
>   `journalctl --user -u atr-gateway | grep "gpu budget"`. A 502 that names
>   free/total means the card is genuinely full — `GET /gpu` says who has it, and
>   twice that was an orphaned training process.
> - **`ATR_VLLM_MAX_NEW_TOKENS` must be 4096**, not the default 512. These models
>   are served page-level; past the ceiling vLLM returns a normal 200 whose text
>   simply stops mid-sentence. `report.md` has a "cut off" column — read it.
> - **The API key in `.env` on idhefix and `.env.gpustack` on tei was exposed in
>   a terminal transcript on 2026-09-14.** Rotate it before the first run if that
>   has not happened.
>
> Start with `pull-share --list`, then `atr-batch --dry-run`, then `--limit 3`,
> and read the *end* of one transcription before letting the full run go.

---

## The other two fallbacks

**Keep pasting.** What has happened so far. It works, and it costs a person's
attention for the length of every run.

**Expose the operations as MCP tools — built, see
[`deploy/mcp-atr/`](../deploy/mcp-atr/README.md).** tei already serves MCP at
`https://tei.dh.unibe.ch/mcp`, and MCP reaches a cloud session when plain HTTPS
does not. `mcp_atr/` is a fifth server for that endpoint which can *do* something:
`gateway_models`, `share_list`, `pull_share`, `start_batch`, `job_status`,
`job_log`, `stop_job`, `batch_report`, `run_files`, `read_pages`. Behind the same
nginx, on the same 443, so the port constraint below never comes up.

It is better than ssh in one respect — the session can only do those ten things
— and it costs one thing ssh does not: the endpoint starts jobs on a GPU host and
sits on the public internet. Argv a caller can never reach is what stands in for
the shell's absence, which is why `mcp_atr/jobs.py` has more tests than code.

**Authentication had to be OAuth.** The first build used a static bearer token,
which the claude.ai connector cannot send: its dialog takes a URL and, under
Advanced settings, an OAuth client id and secret, and nothing else. Given a 401 it
runs the MCP discovery flow and, against a server without OAuth, stops at dynamic
client registration — which is what it reported on 2026-09-15. So the server is
now its own authorization server, with a password form in front of `/authorize`:
without a login, an authorization server hands tokens to whoever asks.
