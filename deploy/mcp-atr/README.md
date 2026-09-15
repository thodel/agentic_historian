# The ATR operations MCP server

A door on tei, wide enough for four things and no wider.

## Why it exists

A Claude Code session in the cloud cannot open a socket to `tei.dh.unibe.ch` —
the egress proxy refuses the CONNECT, only 80 and 443 are open on the box, and
neither constraint can be changed. **MCP is the exception**, and not by accident:
it does not travel the session's egress path at all, it is dialled by Anthropic's
broker. tei already proves this daily — the four read-only corpus servers under
`/mcp/` answer in sessions that cannot reach the same host with `curl`.

This adds a fifth server that can *do* something: mirror the share, start a
batch, say how far it is. Full reasoning in
[`docs/CLAUDE_CODE_CONNECTIVITY.md`](../../docs/CLAUDE_CODE_CONNECTIVITY.md).

## What it is not

Not a shell, not an interpreter, not a file server. Every tool is read-only or
starts one fixed command whose arguments have been checked against a charset and
a root directory. A caller chooses *which* corpus and *which* models. It cannot
choose a command, a path outside the corpus roots, or a flag.

That restraint is load-bearing. The corpus servers next door are read-only, so a
stolen token there costs a public-domain lexicon. Here it reaches a machine with
two A40s — so the blast radius of the token is exactly the tool list below, which
is why the list is short and why `mcp_atr/jobs.py` has more tests than code.

| tool | |
|---|---|
| `gateway_models` | the ATR gateway's `/health`, `/models`, `/gpu` — read-only |
| `share_list` | what is in the Nextcloud share, with sizes; downloads nothing |
| `pull_share` | mirror a share folder onto tei (async → job id) |
| `start_batch` | read every page with every model (async → job id) |
| `job_status` / `job_log` / `stop_job` | poll, tail, interrupt |
| `batch_report` | the run's `report.md` and per-model page counts |

---

## Install

### 1. The token

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

It goes in an environment file root-owned and unreadable by anyone else. The
server **refuses to start** without one of at least 32 characters — a deployment
accident must fail at boot rather than quietly serve an open endpoint that starts
GPU jobs.

```bash
sudo install -m 0600 /dev/null /etc/atr-mcp.env
sudo tee /etc/atr-mcp.env >/dev/null <<'EOF'
ATR_MCP_TOKEN=…
ATR_GATEWAY_URL=http://130.92.59.240:8200
ATR_API_KEY=…
NEXTCLOUD_SHARE_URL=https://cloud.gugw.tu-darmstadt.de/nextcloud/s/…
NEXTCLOUD_SHARE_PASS=…
NEXTCLOUD_REMOTE_DIR=digitalisate
EOF
```

### 2. The service

`atr-mcp.service` carries three placeholders — the user, the checkout path and
the venv. Set them, then:

```bash
sudo cp deploy/mcp-atr/atr-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now atr-mcp
systemctl status atr-mcp
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8300/mcp   # expect 401
```

**401 is the success condition here.** Anything else means the bearer check is
not in front of the transport, and the endpoint must not be exposed until it is.

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

Then — **always test before reloading**, a bad config takes the whole site down:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### 4. Verify from outside

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://tei.dh.unibe.ch/mcp/atr/mcp          # 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://tei.dh.unibe.ch/mcp/atr/mcp \
  -H "Authorization: Bearer $ATR_MCP_TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}'
```

401 then 200. If step 2 works and this does not, the problem is nginx, not the
server.

### 5. Register it as a connector

In claude.ai → Settings → Connectors, add a custom connector:

- URL `https://tei.dh.unibe.ch/mcp/atr/mcp` — the house convention on this host; every federation server is published as `/mcp/<name>/mcp`
- header `Authorization: Bearer <token>`

From then on every Claude session — cloud included — can drive a run.

---

## Operating it

A batch is detached from the server on purpose: `start_new_session`, and the unit
is `KillMode=mixed`. **Redeploying or restarting `atr-mcp` does not kill a
running batch.** That is deliberate — a run is hours long and a deploy is not a
reason to lose it — and it is also why `job_status` can report on jobs the
current server process never started.

Jobs live in `<checkout>/agentic_historian/data/mcp_jobs/<job id>/`: `meta.json`
(the exact argv), `log`, and `exit_code` once finished.

A job whose process is gone **without** an exit code reports `vanished`, not
`done`. That is the reboot/OOM case, and calling it success would cost somebody a
re-run they did not know they needed.

## Rotating the token

```bash
sudo sed -i 's/^ATR_MCP_TOKEN=.*/ATR_MCP_TOKEN=NEW/' /etc/atr-mcp.env
sudo systemctl restart atr-mcp
```

Then update the connector. Running batches are unaffected — see above.

## Turning it off

```bash
sudo systemctl disable --now atr-mcp
```

The nginx block then returns 502. To remove the door entirely, drop the `include`
line and reload nginx.
