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

## Install on tei

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
F=/etc/nginx/sites-available/tei.dh.unibe.ch
B=$F.bak-$(date +%Y%m%d-%H%M%S)
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
which takes 1–3 minutes. The page says so while it waits.

## Taking it down after the test

```bash
sudo systemctl disable --now apertus-web
```

Then remove the include line from the site file and reload nginx.
