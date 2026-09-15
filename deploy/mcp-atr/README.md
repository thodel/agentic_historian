# The ATR operations MCP server

A door on tei, wide enough for eight things and no wider.

## Why it exists

A Claude Code session in the cloud cannot open a socket to `tei.dh.unibe.ch` —
the egress proxy refuses the CONNECT, only 80 and 443 are open on the box, and
neither constraint can be changed. **MCP is the exception**, and not by accident:
it does not travel the session's egress path at all, it is dialled by Anthropic's
broker. tei already proves this daily — the corpus servers under `/mcp/` answer
in sessions that cannot reach the same host with `curl`.

This adds a server at that endpoint which can *do* something: mirror the share,
start a batch, say how far it is. Full reasoning in
[`docs/CLAUDE_CODE_CONNECTIVITY.md`](../../docs/CLAUDE_CODE_CONNECTIVITY.md).

## What it is not

Not a shell, not an interpreter, not a file server. Every tool is read-only or
starts one fixed command whose arguments have been checked against a charset and
a root directory. A caller chooses *which* corpus and *which* models. It cannot
choose a command, a path outside the corpus roots, or a flag.

That restraint is load-bearing. The corpus servers next door are read-only, so a
stolen credential there costs a public-domain lexicon. Here it reaches a machine
with two A40s — so the blast radius is exactly the tool list below, which is why
the list is short and why `mcp_atr/jobs.py` has more tests than code.

| tool | |
|---|---|
| `gateway_models` | the ATR gateway's `/health`, `/models`, `/gpu` — read-only |
| `share_list` | what is in the Nextcloud share, with sizes; downloads nothing |
| `pull_share` | mirror a share folder onto tei (async → job id) |
| `start_batch` | read every page with every model (async → job id) |
| `job_status` · `job_log` · `stop_job` | poll, tail, interrupt |
| `batch_report` | the run's `report.md` and per-model page counts |

## Authentication: OAuth, because the connector speaks nothing else

The claude.ai connector dialog takes a URL and, under Advanced settings, an OAuth
client id and secret. **There is no field for a header.** Given a 401 it does what
the MCP spec says — discover the protected-resource metadata, discover the
authorization server, register itself, run authorization-code with PKCE — and
against a server without OAuth it fails at the first step, which is exactly what
it did here on 2026-09-15 (*Registrierung beim Anmeldedienst … fehlgeschlagen*).

So this server is an OAuth protected resource **and** its own authorization
server. The MCP SDK implements every endpoint; `mcp_atr/oauth.py` supplies the
one thing it cannot — **who is allowed in**. That is a password form in front of
`/authorize`. Without it an authorization server hands tokens to whoever asks,
which is an open door with extra steps, and worse than a shared token because it
looks authenticated.

`ATR_MCP_TOKEN` remains as an optional second door for clients that *can* send a
header — the Claude Code CLI takes one with `--header`. It is seeded into the
token store rather than checked by a wrapper in front of it, so one place decides
whether a request is authorised.

---

## Install

### 1. Credentials

```bash
sudo install -m 0600 /dev/null /etc/atr-mcp.env
sudo tee -a /etc/atr-mcp.env >/dev/null <<EOF
ATR_MCP_PUBLIC_URL=https://tei.dh.unibe.ch/mcp/atr
ATR_MCP_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
ATR_GATEWAY_URL=http://130.92.59.240:8200
ATR_API_KEY=…
NEXTCLOUD_SHARE_URL=https://cloud.gugw.tu-darmstadt.de/nextcloud/s/…
NEXTCLOUD_SHARE_PASS=…
NEXTCLOUD_REMOTE_DIR=digitalisate
EOF
sudo sed -n 's/=.*/=<gesetzt>/p' /etc/atr-mcp.env      # keys, not values
```

Two of these the server **refuses to start without**, on purpose — a deployment
accident must fail at boot rather than quietly serve an endpoint that starts GPU
jobs:

| | |
|---|---|
| `ATR_MCP_PUBLIC_URL` | the https URL this server is published at. Every OAuth redirect and both metadata documents are absolute and are read by a browser on the far side of nginx, so it cannot be derived from the request. http is allowed on the loopback only. |
| `ATR_MCP_PASSWORD` | ≥ 16 characters. The only thing between the public internet and this host's GPUs. |
| `ATR_MCP_TOKEN` | optional, ≥ 32 characters. For header-capable clients. |

### 2. The service

`atr-mcp.service` carries three placeholders — the user, the checkout path and
the venv. Set them, then:

```bash
sudo cp deploy/mcp-atr/atr-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now atr-mcp
systemctl --no-pager status atr-mcp
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8300/mcp   # expect 401
```

**401 is the success condition here.** Anything else means authentication is not
in front of the transport, and the endpoint must not be exposed until it is.

### 3. nginx

```bash
sudo cp deploy/mcp-atr/nginx-mcp-atr-proxy.conf /etc/nginx/snippets/mcp-atr-proxy.conf
sudo cp deploy/mcp-atr/nginx-mcp-atr.conf       /etc/nginx/snippets/mcp-atr.conf
```

One line inside the `tei.dh.unibe.ch` server block that already holds the
federation's `/mcp/...` locations — `/etc/nginx/sites-available/tei.dh.unibe.ch`
as of 2026-09-15. Insert it before the first of them, with a backup and a
rollback, because a bad config takes the whole site down:

```bash
F=/etc/nginx/sites-available/tei.dh.unibe.ch
B=$F.bak-$(date +%Y%m%d-%H%M%S)
sudo cp "$F" "$B"
sudo sed -i '0,/^[[:space:]]*location \/mcp\//s//    include \/etc\/nginx\/snippets\/mcp-atr.conf;\n\n&/' "$F"
sudo diff "$B" "$F"
if sudo nginx -t; then sudo systemctl reload nginx; else sudo cp "$B" "$F"; echo ROLLBACK; fi
```

`0,/re/` makes sed act on the first match only — there are two `server_name
tei.dh.unibe.ch` blocks (the TLS one and the HTTP redirect), and anchoring on the
first `location /mcp/` lands inside the right one without depending on which.

**Four location blocks, not one.** The flow is served from two different places
in the URL space: everything under `/mcp/atr/` (prefix **stripped** on the way
in, because the app serves `/mcp`, `/authorize`, `/token` at its own root), and
the RFC 9728 protected-resource metadata at the **host root**, which cannot be
moved — the spec builds that path by inserting `/.well-known/
oauth-protected-resource` between the host and the resource path. It must arrive
**unstripped**. One block cannot do both: `proxy_pass` with a trailing slash
strips and without one preserves.

### 4. Verify from outside

```bash
curl -s -o /dev/null -w '%{http_code}  mcp\n'     https://tei.dh.unibe.ch/mcp/atr/mcp
curl -s -o /dev/null -w '%{http_code}  prm\n'     https://tei.dh.unibe.ch/.well-known/oauth-protected-resource/mcp/atr/mcp
curl -s -o /dev/null -w '%{http_code}  as-meta\n' https://tei.dh.unibe.ch/mcp/atr/.well-known/oauth-authorization-server
curl -s -o /dev/null -w '%{http_code}  hbls\n'    https://tei.dh.unibe.ch/mcp/hbls/mcp
```

Expect **401, 200, 200**, and for hbls whatever it answered before — the last
line is the check that the federation is untouched.

The 401 must carry the discovery pointer, which is what makes the connector able
to proceed at all:

```bash
curl -si -X POST https://tei.dh.unibe.ch/mcp/atr/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{}' | grep -i www-authenticate
```

```
www-authenticate: Bearer error="invalid_token", …,
  resource_metadata="https://tei.dh.unibe.ch/.well-known/oauth-protected-resource/mcp/atr/mcp"
```

### 5. Register the connector

claude.ai → **Customize → Connectors → + → Add custom connector**

- URL: `https://tei.dh.unibe.ch/mcp/atr/mcp`
- Advanced settings: **leave empty** — the server offers dynamic client
  registration, so the connector registers itself.
- Click Add. A browser window opens on this server's password form; enter
  `ATR_MCP_PASSWORD`.

From then on every Claude session — cloud included — can drive a run. A session
that was already open when the connector was added will not see it: tool sets are
fixed at session start.

---

## Operating it

A batch is detached from the server on purpose: `start_new_session`, and the unit
is `KillMode=mixed`. **Redeploying or restarting `atr-mcp` does not kill a running
batch.** That is deliberate — a run is hours long and a deploy is not a reason to
lose it — and it is also why `job_status` can report on jobs the current server
process never started.

Jobs live in `<checkout>/agentic_historian/data/mcp_jobs/<job id>/`: `meta.json`
(the exact argv), `log`, and `exit_code` once finished. A job whose process is
gone **without** an exit code reports `vanished`, not `done` — the reboot and OOM
case, and calling it success would cost somebody a re-run they did not know they
needed.

OAuth state lives in `<checkout>/agentic_historian/data/mcp_oauth.json`, mode
0600: registered clients, access tokens, refresh tokens. Authorization codes are
not there and never will be — they live five minutes, and a lost one costs a
retry.

### Revoking access

```bash
sudo systemctl stop atr-mcp
rm ~/agentic_historian/agentic_historian/data/mcp_oauth.json   # every token dies
sudo systemctl start atr-mcp
```

The connector then asks for the password again on its next call. To change the
password itself, edit `/etc/atr-mcp.env` and restart — existing tokens keep
working until they expire (an hour) or the store is cleared, which is the point
of having both levers.

### Turning it off

```bash
sudo systemctl disable --now atr-mcp
```

The nginx blocks then return 502. To remove the door entirely, drop the `include`
line and reload nginx.

---

## What was verified, and how

The whole flow was driven against a real nginx in front of the real server before
any of this shipped — not reasoned about:

| | |
|---|---|
| 401 + `WWW-Authenticate` carrying `resource_metadata` | ✓ |
| protected-resource metadata at the host root | ✓ |
| authorization-server metadata, both spellings | ✓ |
| dynamic client registration | ✓ |
| `/authorize` → password form, wrong password refused | ✓ |
| code exchanged under PKCE, same code refused twice | ✓ |
| `initialize` with the resulting token, through nginx | ✓ |
| the federation's `/mcp/hbls/mcp` unchanged | ✓ |
| a forged `Host` header → **421** | ✓ |

That last one is worth naming: `streamable_http_app()` defaults to
`host="127.0.0.1"`, which switches on DNS-rebinding protection allowing only
loopback `Host` headers. Behind nginx the header says `tei.dh.unibe.ch`, and
every request would have come back 421 — invisible when testing against
127.0.0.1. The allowlist is therefore set explicitly from `ATR_MCP_PUBLIC_URL`.
