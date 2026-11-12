# Rotation nach OpenClaw — Checkliste (#802 Schritt 7)

OpenClaw lief bis 06.11.2026 als `dh`. Was es hielt, ist damit mehr als
`openclaw.json`: **alles, was `dh` lesen kann** — `.env.gpustack` (36 Keys,
dieselben Werte wie in `/etc/agentic_historian.env`), die `.env` anderer
Projekte unter `/home/dh`, `~/.ssh` von `dh`. Jeder dieser Werte gilt als
bekannt und wird neu ausgestellt; der alte wird widerrufen. Diese Datei nennt
**Namen und Orte, nie Werte**. Den Rückbau davor (Schritt 6) macht
`deploy/hermes/decommission_openclaw.sh`, die Abfolge steht im
[README](README.md#cut-over-and-after-h6-802).

## Vorher: die alten Werte festhalten — ohne sie zu sehen

Vor der ersten Rotation, als root, auf tmpfs:

```bash
cd /home/dh/agentic_historian
sudo bash deploy/hermes/decommission_openclaw.sh --collect-old-values /dev/shm/alte-werte
```

Das Skript liest `.env.gpustack`, `openclaw.json` (auch unter
`.openclaw.pre-migration`), `.openclaw/tmp/.gh_token` und
`.openclaw/credentials/*.json`, nimmt jedes Feld, dessen Name nach Secret
klingt (`…TOKEN`, `…KEY`, `…PASS`, `password`, `apiKey`, …), und schreibt die
Werte in die Datei (0600, nie überschrieben). Auf dem Schirm stehen nur die
Namen. Der Trockenlauf (`decommission_openclaw.sh` ohne Option) zeigt dieselbe
Liste unter «Was OpenClaw hielt» — **jeder Name dort braucht eine Zeile
unten**; fehlt einer, hier ergänzen.

Für die Probe gegen eine API einen einzelnen alten Wert in eine eigene Datei,
ebenfalls ungesehen:

```bash
umask 077
sudo grep -m1 '^GPUSTACK_API_KEY=' /home/dh/agentic_historian/.env.gpustack | cut -d= -f2- | tr -d '"' > /dev/shm/alt-gpustack
```

## Pro Secret, immer gleich

1. neu ausstellen (Spalte «stellt aus»);
2. an **jedem** Ort der Spalte «liegt in» eintragen — mit `sudoedit` oder dem
   Editor, nie auf der Kommandozeile (argv steht in `ps`);
3. die Verbraucher neu starten und eine echte Anfrage abwarten;
4. den alten Wert beim Aussteller widerrufen;
5. die Probe: der alte Wert bekommt `401`/`403` (Spalte «Probe»), und am Ende
   zählt `--verify-gone` null.

Eine Probe mit dem alten Wert, ohne ihn in argv zu legen:

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  -H @<(printf 'Authorization: Bearer %s\n' "$(cat /dev/shm/alt-gpustack)") \
  https://gpustack.unibe.ch/v1/models          # erwartet: 401
```

## Die Liste

| Secret (Name) | liegt in | stellt aus | Probe, dass der alte tot ist |
|---|---|---|---|
| Discord-Token der Anwendung `dh-bot` (`1519706253314756758`) | `openclaw.json` (`channels.discord.token`); evtl. `DISCORD_BOT_TOKEN` in `.env.gpustack` | — wird nicht neu ausgestellt: die Anwendung im Developer Portal **löschen** (Schritt 6) | Portal: die Anwendung ist weg; `curl … /api/v10/users/@me` mit dem alten Wert → 401 |
| Discord-Token `agentic_historian` (`DISCORD_BOT_TOKEN`) | `/etc/agentic_historian.env`; falls derselbe Wert in `.env.gpustack` stand, kannte OpenClaw ihn | Developer Portal → `agentic_historian` → Bot → Reset Token | `--verify-gone` zeigt `/etc/agentic_historian.env` mit 0; der Bot meldet sich nach dem Neustart an |
| GPUStack-Key (`GPUSTACK_API_KEY`) | `.env.gpustack` (CLI als `dh`), `/etc/agentic_historian.env` (Bot), `~hermes/.hermes/.env` **zweimal** (`HERMES_CUSTOM_GPUSTACK_UNIBE_CH_API_KEY` und `GPUSTACK_API_KEY`), `openclaw.json` | GPUStack-Oberfläche (gpustack.unibe.ch → API Keys), Konto-Inhaber. **Drei Keys statt einem:** CLI, Bot, Hermes — dann lässt sich einer allein widerrufen | `/v1/models` mit dem alten → 401; `hermes gateway restart`, eine Antwort in Discord; `sudo -n systemctl restart agentic-historian`, `/atr_engines` |
| GitHub — Token des Queue-Workers | `~hermes/.config/gh/hosts.yml` (`gh auth login`, heute **Tobias' Token, schreibt als `thodel`**); `.openclaw/tmp/.gh_token` | **offen, Entscheid:** eigenes Konto (Maschinenbenutzer) oder GitHub App, siehe unten | `gh auth status` als `hermes` nennt das neue Konto; github.com → Settings → Tokens: der alte ist gelöscht; `api.github.com/user` mit dem alten → 401 |
| GitHub-Token des Bots (`GITHUB_TOKEN`) | `/etc/agentic_historian.env`, `.env.gpustack` | github.com → Settings → Developer settings → fine-grained token, nur `GITHUB_OUTPUT_REPO`/`GITHUB_CODE_REPO` | wie oben |
| Hugging Face (`HF_TOKEN`) | `/etc/agentic_historian.env`, `.env.gpustack` | huggingface.co → Settings → Access Tokens | `https://huggingface.co/api/whoami-v2` mit dem alten → 401 |
| ATR-Gateway (`ATR_API_KEY`) | `/etc/agentic_historian.env`, `.env.gpustack` | wer das Gateway auf idhefix betreibt (dessen `.env`, serving-atr-inference) | `/train/jobs` am Gateway mit dem alten → 401/403 |
| atr-mcp (`ATR_MCP_PASSWORD`, `ATR_MCP_TOKEN`), falls in `.env.gpustack` | die `.env` des MCP-Servers (`deploy/mcp-atr/README.md`) | ihr Betreiber; danach `hermes mcp login atr` als `hermes` neu | alte OAuth-Tokens in `~hermes/.hermes/mcp-tokens/` löschen, Login neu; `hermes mcp list` zeigt 16 Werkzeuge |
| HLS (`HLS_BASIC`) | `.env.gpustack`, `/etc/agentic_historian.env`, `~hermes/.hermes/.env`; Ursprung `HLS_AUTH_USER`/`HLS_AUTH_PASS` von mcp-hls auf idhefix | neues Passwort in der `.env` von mcp-hls, dann base64 neu | `/mcp/hls/mcp` mit dem alten Basic → 401 |
| Gemini (`GEMINI_API_KEY`), falls gesetzt | `.env.gpustack`, `/etc/agentic_historian.env` | Google AI Studio → API keys | Modellliste mit dem alten → 400/403 |
| Transkribus (`TRANSKRIBUS_USER`/`TRANSKRIBUS_PASS`, `TRANSKRIBUS_ACCESS_TOKEN`) | `.env.gpustack`, `/etc/agentic_historian.env` | Passwort im Transkribus-Konto ändern (READ-COOP) | Login mit dem alten scheitert; `/transkribus_status` mit dem neuen |
| SwitchDrive (`SWITCHDRIVE_PASS`) | `.env.gpustack`, `/etc/agentic_historian.env` | drive.switch.ch → Einstellungen → Sicherheit → App-Passwort neu, altes löschen | WebDAV-`PROPFIND` mit dem alten → 401 |
| Nextcloud-Freigabe (`NEXTCLOUD_SHARE_PASS`) | `.env.gpustack`, `/etc/agentic_historian.env` | das Passwort der Freigabe neu setzen (wer sie angelegt hat) | die Freigabe mit dem alten → 401 |
| DMJ-MCP (`DMJ_MCP_TOKEN`) | `/home/dh/dmj_management/.env` | dmj_management selbst (neuen Token setzen, Dienst neu starten) | `/mcp/dmj/mcp` mit dem alten → 401 |
| Mail (IMAP/SMTP, App-Passwort) | `openclaw.json` (Feldname zeigt der Trockenlauf) | Konto-Inhaber im Konto (Unibe-Konto: App-Passwort widerrufen) | Login mit dem alten scheitert. **Hermes bekommt keines** (#801) |
| Kalender (CalDAV/OAuth) | `openclaw.json`, `.openclaw/credentials/` | Konto-Inhaber: App-Zugriff widerrufen | wie Mail; Hermes bekommt keinen |
| SSH | die Schlüssel, die OpenClaw benutzte — `~dh/.ssh/` oder ein Pfad in `openclaw.json` | `ssh-keygen -t ed25519` neu als `dh`, der öffentliche Teil auf die Ziele | auf jedem Ziel (idhefix, asteraix, UBELIX, GitHub-Deploy-Keys): der alte Fingerabdruck (`ssh-keygen -lf alt.pub`) steht nicht mehr in `authorized_keys` (`ssh-keygen -lf ~/.ssh/authorized_keys`) |

Was **nicht** rotiert wird, weil OpenClaw es nie lesen konnte: das Token der
Anwendung `dh-bot-hermes` und `HLS_BASIC`-Kopien, die nur unter `/home/hermes`
liegen — **ausser** der Wert ist derselbe wie in `.env.gpustack` (dann zeigt
ihn `--verify-gone` bei `~hermes/.hermes/.env`).

## GitHub: Konto oder App — der eine offene Entscheid

| | eigenes Konto (Maschinenbenutzer) | GitHub App |
|---|---|---|
| PRs und Kommentare erscheinen als | das Konto, z. B. `dh-bot-…` | `<app>[bot]` |
| Einrichtung | Konto anlegen, als Collaborator in die neun Repos, fine-grained Token (Contents, Pull requests, Issues: read/write), `gh auth login --with-token` als `hermes` | App anlegen, in die neun Repos installieren, privaten Schlüssel nach `~hermes`, ein Helfer, der stündlich ein Installations-Token zieht und `gh` gibt |
| Ablauf | Token läuft ab (z. B. 90 Tage) — erneuern | Installations-Token läuft nach 1 h ab, der Helfer erneuert |
| Branch-Schutz, «Review durch Menschen» | wie ein Mensch; der Prüflauf kann das Konto erkennen | wie ein Bot; eigene Regeln möglich |
| Aufwand jetzt | klein | mittel (Helfer, Schlüssel) |

Beides beendet, dass der Worker als `thodel` schreibt. Danach Tobias' Token
aus `hosts.yml` von `hermes` entfernen und auf github.com widerrufen.

## Fertig, wenn

```bash
sudo bash deploy/hermes/decommission_openclaw.sh --verify-gone /dev/shm/alte-werte
```

**`kein alter Wert mehr in den Live-Dateien`** sagt (Exit 0): `.env.gpustack`,
die beiden `.env` im Checkout, `/etc/agentic_historian.env`,
`~hermes/.hermes/.env`, `config.yaml` und `hosts.yml` halten null Zeilen mit
einem alten Wert — das ist das `grep -c … = 0` aus #802, über alle Live-Dateien
statt nur einer. `.openclaw.pre-migration` und die Archive halten die alten
Werte, bis sie am Löschtag (Schritt 6) weg sind; sie sind dann widerrufen.
Danach:

```bash
shred -u /dev/shm/alte-werte /dev/shm/alt-*
```

und `.env.gpustack` aufräumen: nur noch die Keys, die die CLI als `dh` braucht,
jeder einmal (die drei Doppelten, `docs/DEPLOYMENT_TEI.md`). Dann der
Rapid7-Rescan für #792/#795 — weniger Node- und npm-Pakete —, wie in
`docs/SECURITY_TEI.md` beschrieben.
