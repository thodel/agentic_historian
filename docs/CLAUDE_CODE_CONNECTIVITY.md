# Reaching tei and asterAIx from a Claude Code session

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
broken" is the wrong description, and it is why option **C** works at all.

---

## The request (option 4) — copy this and send it

Adding two hostnames to this environment's egress allowlist is the whole fix. It
is one change, it is made once, and every future session inherits it.

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
> And, if the policy can express host+port rather than host only, additionally
> **TCP 22 on `tei.dh.unibe.ch`**, so the session can drive a run over ssh
> instead of having every command pasted through a person.
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

## If the request is declined

Three fallbacks, worst to best, all of which we can build:

**A — keep pasting.** What happens today. Works, and costs a person's attention
for the length of every run. The runbooks
([BATCH_ATR.md](BATCH_ATR.md), `serving-atr-inference/docs/GERMAN_XIX_MODELS.md`)
are written so that this is at least mechanical rather than exploratory.

**B — run the agent on tei.** Claude Code CLI on `tei.dh.unibe.ch` itself, inside
tmux. Then there is no proxy between the agent and the machine, and asterAIx is
one hop away over the existing `:8200` gateway. Needs the CLI installed there and
an API key on the box; that key then lives on a shared research server, which is
the trade.

**C — expose the operations as MCP tools.** tei already serves MCP at
`https://tei.dh.unibe.ch/mcp`, and MCP reaches this session when plain HTTPS does
not. A small server there — `pull_share`, `start_batch`, `batch_status`,
`tail_journal` — would let any future session drive a run through a typed,
auditable surface instead of a shell. More work than an allowlist entry, and
strictly better than ssh in one respect: the session can only do the four things
the server offers.

C is the durable answer if the allowlist is a policy the institution will not
change. Option 4 is the cheap one, and it is cheap enough to ask for first.
