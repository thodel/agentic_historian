# Apertus 1.5 test page

`https://tei.dh.unibe.ch/apertus/`: upload an image, pick or write a question,
and read Apertus 1.5's answer. It exists to test the model
(serving-atr-inference `docs/APERTUS_1_5.md`), mainly for **image description**.

## What it is, and what it is not

A page and two routes (`apertus_web/app.py`). The browser never sees the gateway
or its key. The app forwards **one request shape** to the gateway's
`/v1/chat/completions`: one image (resized to 1.96 MP, re-encoded as JPEG), one
prompt (≤ 4000 characters), `max_tokens` ≤ 4096, to **one model**
(`apertus-1.5-8b`). The browser cannot choose another model or send another body.
That restraint is the point, because the gateway starts GPU work on a shared card.

- **Password:** a shared one (HTTP Basic, `APERTUS_WEB_PASSWORD`, ≥ 16 characters).
  The app refuses to start without it.
- **Cross-site POSTs** are refused. Browsers send Basic credentials on those too.
- **One request at a time** reaches the model. Three more may wait, and a further
  one gets 429.
- **Nothing is stored on the server.** The history lives in the browser tab and
  can be downloaded there as JSONL.

## Up and down

```bash
sudo bash /home/dh/agentic_historian/deploy/apertus-web/apertus_up.sh     # page + warm model
sudo bash /home/dh/agentic_historian/deploy/apertus-web/apertus_down.sh   # page off
```

- **`apertus_up.sh`** runs `install_on_tei.sh` (idempotent, keeps the
  password). Then it sends one tiny image through the gateway, so the cold start
  on idhefix (~6 min) happens before the first tester arrives. It waits until
  Apertus answers and prints the URL and the password. `--no-warm` brings up
  only the page.
- **`apertus_down.sh`** stops and disables the unit and removes the nginx
  include, with a backup outside `sites-enabled/` and a rollback. Unit, snippet
  and env file stay in place, so `apertus_up.sh` brings back the same password.
- **From the page itself:** the panel *System: Diagnose und Modell hochfahren*
  does the model half of `apertus_up.sh` without root. **Prüfen** checks, in
  order: gateway reachable, key accepted, model registered, model loaded, and
  room on its card (naming who holds the memory, and an orphaned
  `VLLM::EngineCore` as such). **Modell hochfahren** warms the model and shows
  the elapsed time, the test answer, or on failure a hint, the gateway's own
  message, the `journalctl` command for idhefix and a log of the attempt. The
  panel opens by itself when a check or a question fails. Installing the page
  stays with `apertus_up.sh`: it needs sudo, and a page that is down cannot
  start itself.
- Card 1 on idhefix needs no step of its own: the gateway evicts Apertus as soon
  as another model needs the room.

## Install on tei

**One script does all of it** (`install_on_tei.sh`): it takes user and venv from
the `atr-mcp` unit and gateway URL and key from `/etc/atr-mcp.env`, generates a
password, writes and starts the unit, adds the nginx include with backup and
rollback, checks for 401 from outside and prints the password. Run it from a
worktree of the branch, so the checkout that serves the bot and the MCP server
stays on main:

```bash
cd /home/dh/agentic_historian
git fetch origin claude/zen-faraday-f0jpr8
git worktree add /home/dh/ah-apertus origin/claude/zen-faraday-f0jpr8
sudo bash /home/dh/ah-apertus/deploy/apertus-web/install_on_tei.sh /home/dh/ah-apertus
```

The steps below are what it does, for doing them by hand.

### 1. Credentials

```bash
sudo install -m 0600 /dev/null /etc/apertus-web.env
sudo tee -a /etc/apertus-web.env >/dev/null <<EOF2
APERTUS_WEB_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')
ATR_GATEWAY_URL=http://130.92.59.240:8200
ATR_API_KEY=…            # the same value as in /etc/atr-mcp.env
EOF2
sudo grep APERTUS_WEB_PASSWORD /etc/apertus-web.env   # pass this to the testers
```

### 2. The service

Set `User=`, `WorkingDirectory=` and `ExecStart=` in `apertus-web.service` to the
values `atr-mcp.service` uses on tei. Then:

```bash
sudo cp deploy/apertus-web/apertus-web.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now apertus-web
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8310/        # expect 401
```

### 3. nginx

Use the same procedure as `deploy/mcp-atr/`: make a backup, insert one include line
before the first `location /mcp/`, and roll back if `nginx -t` fails.

```bash
sudo cp deploy/apertus-web/nginx-apertus.conf /etc/nginx/snippets/apertus.conf
F=$(readlink -f /etc/nginx/sites-enabled/tei.dh.unibe.ch)   # on tei a copy, not a symlink
sudo mkdir -p /etc/nginx/backup
B=/etc/nginx/backup/$(basename "$F").$(date +%Y%m%d-%H%M%S)   # never inside sites-enabled/
sudo cp "$F" "$B"
sudo sed -i '0,/^[[:space:]]*location \/mcp\//s//    include \/etc\/nginx\/snippets\/apertus.conf;\n\n&/' "$F"
sudo diff "$B" "$F"
if sudo nginx -t; then sudo systemctl reload nginx; else sudo cp "$B" "$F"; echo ROLLBACK; fi
```

### 4. Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://tei.dh.unibe.ch/apertus/        # 401
curl -s -u x:"$PW" https://tei.dh.unibe.ch/apertus/api/status                   # {"gateway":"ok",...}
```

`"resident": false` means the next question cold-starts the model on idhefix,
which takes about six minutes (16 GB of weights off the research share, then
compilation). The page says so while it waits.

## Taking it down after the test

`apertus_down.sh` does this; by hand:

```bash
sudo systemctl disable --now apertus-web
```

Then remove the include line from the site file and reload nginx.
