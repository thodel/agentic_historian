# Separating the bot from the OpenClaw agent (S1 #566, S2 #567, S3 #568)

Part of the separation epic #565. The Historian bot and the OpenClaw agent
`dh-bot` both run on `tei` as user `dh` and read the same `.env.gpustack` — which
holds the OpenClaw agent's mail / calendar / SSH credentials that the bot never
needs. S2 gives the bot its own secrets, its own confinement, and (optionally)
its own user.

## What this repo provides

- **`agentic_historian.env.example`** — the bot's own secrets file, listing only the keys
  its commands need. No OpenClaw credentials.
- **`agentic-historian.service.d/secrets.conf`** — a drop-in that points the unit
  at `/etc/agentic_historian.env` and sets `AH_ENV_FILE`, so the bot reads only its own
  secrets.
- **`config.py` `AH_ENV_FILE` support** — with `AH_ENV_FILE` set, the bot reads
  only that file for its file-sourced secrets and never falls back to
  `.env.gpustack`. The real process environment still wins over any file.
- **`agentic-historian.service.d/hardening.conf`** — the systemd sandbox (item 2:
  `ProtectSystem=strict`, `NoNewPrivileges`, narrow `ReadWritePaths`, …). This
  ships with the security epic (#580) and is installed alongside this drop-in.

## Durchführung auf tei — ein Lauf, alle drei Punkte (#567)

Vorbereitet am 21.10.2026, **auf tei noch nicht ausgeführt.** Die Reihenfolge
ist Pflicht: ein Bot, der als neuer Benutzer startet, bevor seine Secrets-Datei
existiert, kommt ohne Token hoch, weil `.env.gpustack` `0600 dh:dh` ist und
der neue Benutzer sie nicht lesen kann — das ist der Zweck der Umstellung, und
das Skript bricht deshalb vor dem Neustart ab, statt zu warnen.

```
# 0. als dh, im Checkout: auf den Stand von main bringen. Der Bot zieht nicht
#    mehr selbst (/update berichtet nur noch), der Deploy läuft von aussen.
#    Erst nachsehen, auf welchem Branch das Checkout steht: am 22.10.2026 war
#    es `ruemlang-test` (13 Commits vor, 15 hinter main, von einer anderen
#    Session, die daraus gerade einen Upload fuhr). Dort ist `git pull
#    --ff-only` kein Fast-Forward, und ein `git checkout main` zöge dem
#    laufenden Job den Code weg — also entweder dort `git merge origin/main`
#    (fasst sich am 22.10. konfliktfrei) oder warten, bis der Branch zurück ist.
cd /home/dh/agentic_historian && git branch --show-current && git pull --ff-only

# 0b. Einmalig: das Paket `acl`. Ohne `setfacl` bleibt das Skript in Schritt 2
#     stehen (exit 5), weil /home/dh 0750 ist. tei hatte es am 22.10.2026 nicht.
sudo apt install acl

# 1. Die Secrets-Datei des Bots — einmalig, von Hand, nie per Skript.
#    Nur die Keys aus agentic_historian.env.example — aber aus DREI Quellen,
#    nicht aus einer: .env.gpustack (Secrets, URLs, Schalter), .env im Checkout-
#    Root (HF_TOKEN, DISCORD_GUILD_ID, REQUIRED_*_ROLE_ID) und
#    agentic_historian/.env (ATR_MOUNT_DIR). Mit AH_ENV_FILE liest der Bot nur
#    noch die neue Datei; was nicht drinsteht, fällt still auf den Default.
sudo install -m 600 -o root -g root deploy/systemd/agentic_historian.env.example /etc/agentic_historian.env
sudoedit /etc/agentic_historian.env
#    Drei Keys stehen in .env.gpustack mehrfach (LASSBERG_REGISTER, NEXTCLOUD_REMOTE_DIR,
#    NEXTCLOUD_SHARE_URL). python-dotenv nimmt die LETZTE Zuweisung — die gilt heute,
#    und nur die gehört in die neue Datei.

# 1b. Die Kopie messen statt ihr glauben: welche Settings ändern sich, wenn der
#     Bot nur diese Datei liest? Druckt Namen, nie Werte. Leere Liste = weiter.
#     (Am 22.10. wären es mit den Keys des damaligen Beispiels vier gewesen:
#     ATR_MOUNT_DIR, ATR_PAGE_CACHE_ARCHIVE, LASSBERG_REGISTER, NEXTCLOUD_REMOTE_DIR.)
sudo agentic_historian/.venv/bin/python deploy/systemd/env_diff.py /etc/agentic_historian.env

# 2. Trockenlauf: misst, ändert nichts. Endet mit "would run: systemctl restart", exit 0.
sudo deploy/systemd/switch_to_own_user.sh --dry-run

# 3. Umstellung — dieselben Schritte, diesmal ausgeführt; am Ende läuft check_separation.py.
sudo deploy/systemd/switch_to_own_user.sh

# 4. Die Definition of Done messen: den Pfad der OpenClaw-Credentials nennen.
#    Auf tei: /home/dh/.openclaw/openclaw.json (0600 dh) hält die Konfiguration
#    samt Tokens, /home/dh/.openclaw/credentials/ (0700 dh) die Pairing-Dateien.
#    Durch das venv-Python, und mit --secrets: ohne den Schalter misst der Checker
#    die dotenv-Dateien des Checkouts (config.ENV_SOURCE), nicht die Datei, die
#    der Bot jetzt liest.
sudo agentic_historian/.venv/bin/python deploy/systemd/check_separation.py \
  --secrets /etc/agentic_historian.env --openclaw-secrets /home/dh/.openclaw/openclaw.json
```

**Durchgeführt am 23.10.2026** (Schritte 0–7 von Tobias, 17:46 CEST Neustart):
9 erfüllt, 0 nicht erfüllt, 1 nicht gemessen (ProtectHome). `User=agentic_historian`,
`ProtectSystem=strict`, die zwei Datenpfade, alle drei dotenv-Dateien 0600 dh, die
OpenClaw-Credentials für den Bot-Benutzer unerreichbar. Offen: die Rotation.

Was das Skript tut, in dieser Reihenfolge, und wo es stehen bleibt:

| Schritt | tut | bleibt stehen (exit) wenn |
|---|---|---|
| 0 | liest die Datenverzeichnisse aus der laufenden Konfiguration (`writable_paths.py --data-only`) und prüft, dass `hardening.conf` jedes davon freigibt | keine Pfade lesbar (3); eines fehlt in `hardening.conf` (6) — unter `ProtectSystem=strict` wäre das ein Schreibfehler beim ersten Job (#643) |
| 1 | `useradd --system` `agentic_historian`, ohne Login, ohne Home | — |
| 2 | prüft, ob der neue Benutzer durch jedes Verzeichnis über Checkout und venv **hindurchkommt**. Ein Stock-`/home/dh` ist `0750`: er käme nicht, und der Dienst stürbe beim ersten Import mit *permission denied* auf einem Pfad, der sichtbar existiert. Abhilfe: `setfacl -m u:agentic_historian:--x` auf genau diese Verzeichnisse — Durchgang für einen Benutzer, nicht `o+rx` für alle | `setfacl` fehlt (5) — auch im Trockenlauf, seit 22.10.2026; danach, als `agentic_historian` gemessen, venv-Python oder `bot.py` trotzdem unlesbar (5) |
| 3 | `/etc/agentic_historian.env` → `root:agentic_historian 0640` | die Datei fehlt (4) |
| 4 | `chown -R agentic_historian` auf die Datenverzeichnisse | — |
| 5 | installiert `secrets.conf`, `hardening.conf`, `user.conf`, `daemon-reload`, `restart` | — |
| 6 | wartet 3 s, meldet `is-active` und den gemessenen `User=`, dann `check_separation.py --secrets /etc/agentic_historian.env` | — (der Bericht sagt es) |

Nicht im Skript, mit Absicht: die Secrets-Datei füllen, den OpenClaw-Agenten
anfassen, Checkout oder venv für den neuen Benutzer schreibbar machen.

**Nach der Umstellung bleibt die Rotation (Punkt 4 der Definition of Done).**
Die Keys, die nach `/etc/agentic_historian.env` kopiert wurden, stehen weiter
in `.env.gpustack`, und die liest der OpenClaw-Agent als `dh` wie bisher. Die
Trennung wirkt erst ab dem Moment, in dem die Werte dort nicht mehr gelten:
Discord-Token über S1 (#566, eigene Application), GitHub-Token, GPUStack-Key,
ATR-Key, HF-Token, SwitchDrive- und Nextcloud-Passwort neu ausstellen und nur
noch in `/etc/agentic_historian.env` eintragen. Die Zeilen aus `.env.gpustack`
erst entfernen, wenn feststeht, dass kein anderer Prozess sie liest.

**Durchgang nach `/home/dh` heisst Lesbarkeit für alles, was dort `o+r` ist.**
Der Bot kann `/home/dh` danach nicht auflisten, aber jede weltlesbare Datei
darunter öffnen, deren Namen er kennt. `check_separation.py
--openclaw-secrets` misst die eine Datei, die zählt; `find /home/dh -maxdepth 2
-perm -o+r -type f` zeigt, ob es weitere gibt.

**Rückweg**, falls der Dienst nicht hochkommt und `journalctl -u
agentic-historian -n 50` es nicht erklärt:

```
sudo rm /etc/systemd/system/agentic-historian.service.d/{user,hardening,secrets}.conf
sudo chown -R dh:dh /home/dh/agentic_historian/agentic_historian/data \
                    /home/dh/agentic_historian/agentic_historian/knowledge_hub/data
sudo systemctl daemon-reload && sudo systemctl restart agentic-historian
```

Danach läuft er wieder als `dh` aus den dotenv-Dateien, wie vor der Umstellung.

## Nachgemessen auf tei, 2026-10-13

Der Abschnitt oben beschreibt, was das Repo bereitstellt. Was auf dem Host **in
Kraft** ist, war etwas anderes — gemessen, nicht angenommen:

```
systemctl cat agentic-historian | grep -E "EnvironmentFile|^Environment=|^User="
→ User=dh          (kein EnvironmentFile, kein AH_ENV_FILE)

Drop-In: /etc/systemd/system.control/agentic-historian.service.d/50-MemoryMax.conf
         /etc/systemd/system/agentic-historian.service.d/restart.conf
```

Weder `secrets.conf` noch `hardening.conf` ist installiert. Der Bot liest seine
Konfiguration also wie zuvor direkt aus den dotenv-Dateien im Checkout, läuft
ohne systemd-Sandbox und als `dh`. **Keiner der drei Punkte von #567 ist auf dem
Host wirksam**, obwohl die Repo-Seite als ✅ geführt wurde.

### Und die Begründung hat sich verschoben

`.env.gpustack` hält **36 Keys, und jeder gehört dem Bot** — kein `IMAP_*`, kein
`SMTP_*`, kein `CALDAV_*`, kein SSH-Key. Der Satz oben, die Datei halte „the
OpenClaw agent's mail / calendar / SSH credentials that the bot never needs",
trifft auf diese Datei nicht mehr zu. Die Rechte sind ausserdem schon `0600
dh:dh`, es liegt dort also nichts offen.

Was real bleibt, ist die **umgekehrte** Richtung der ursprünglichen Sorge: beide
Prozesse laufen als `dh`, also kann der OpenClaw-Agent die Secrets des **Bots**
lesen — Discord-Token, GitHub-Token, GPUStack-Key, Transkribus- und
SwitchDrive-Passwort. Dagegen hilft kein eigenes `EnvironmentFile`, sondern nur
Punkt 3: ein eigener Benutzer. Das verschiebt die Gewichtung der drei Punkte,
und es ist der Grund, 1 nicht länger als das Wichtige zu behandeln.

### Der Konflikt, den Punkt 2 mitbringt

`ProtectSystem=strict` macht alles schreibgeschützt, was nicht in
`ReadWritePaths` steht. `/update` führt `git -C <checkout> pull` **und** `pip
install -r requirements-dev.txt` aus — es braucht also Schreibrecht auf den
Checkout und das venv, mithin die Erlaubnis, den Code dieses Prozesses zu
überschreiben. Beides geht nicht zusammen:

| | Confinement | `/update` |
|---|---|---|
| nur die Datenverzeichnisse | wirksam | bricht |
| plus Checkout und venv | schützt alles ausser dem Wichtigsten | funktioniert |

`hardening.conf` hat die zweite Variante aktiv, weil das dem heutigen Verhalten
entspricht; die erste ist einen Kommentar entfernt. Die Entscheidung gehört zu
`/update`, nicht zu dieser Datei.

## Nachgemessen auf tei, 2026-10-22

Vor der Durchführung, als `dh`, mit `check_separation.py` von `origin/main`
(das Checkout selbst stand auf `ruemlang-test`, ohne #673):

```
  [FEHLT] User= (dh)
  [FEHLT] ProtectSystem=no         erwartet strict
  [FEHLT] NoNewPrivileges=no       erwartet yes
  [FEHLT] PrivateTmp=no            erwartet yes
  [FEHLT] ReadWritePaths: leer
  [?    ] ProtectHome=no           nicht anwendbar (Checkout unter /home)
  [FEHLT] OpenClaw-Credentials (0o600, dh): der Bot läuft als 'dh' … er kann sie lesen
0 erfüllt, 6 nicht erfüllt, 1 nicht gemessen.
```

Unverändert gegenüber dem 13.10. Dazu vier Befunde, die das Runbook oben
geändert haben, jeder eine Messung, keiner eine Vermutung:

1. **Der Checker nannte das «Nichts konnte geprüft werden» (exit 2).** Sechs
   Zeilen waren gemessen und alle sechs gefallen; die Meldung hing an
   `held == 0` statt an «nichts gemessen». Korrigiert: exit 1, und der Satz
   fällt nur, wenn weder erfüllt noch nicht erfüllt gezählt wurde.
2. **`ATR_PAGE_CACHE_ARCHIVE` stand in den Datenverzeichnissen** — gesetzt auf
   `/mnt/wbkolleg_dh_1/tei/archive/agentic_historian-page_cache`, ein
   CIFS-Mount mit `forceuid uid=1000`. Das Skript wäre in Schritt 0 mit exit 6
   stehen geblieben (nicht in `hardening.conf`), und hätte man es eingetragen,
   in Schritt 4 am `chown -R` gestorben — nach dem `useradd`, vor den Drop-ins.
   Der Bot *liest* das Archiv nur (`utils/images.py`, HF-Export); geschrieben
   wird es nachts von tei-vm-sanity als `dh`. Aus `writable_paths.py` entfernt.
3. **Das Beispiel kannte drei der vier Quellen nicht.** `env_diff.py` hat auf
   tei gemessen, was sich ändert, wenn der Bot nur die Keys des Beispiels
   liest: `ATR_MOUNT_DIR` (lag in `agentic_historian/.env`),
   `ATR_PAGE_CACHE_ARCHIVE`, `LASSBERG_REGISTER`, `NEXTCLOUD_REMOTE_DIR`. Die
   `GPUSTACK_MODEL_*`- und `GEMINI_*`-Zeilen in `.env.gpustack` entsprechen
   den Defaults; `GEMINI_MODEL`, `GEMINI_THINKING`, `GEMINI_CONCURRENCY` liest
   niemand. Beispiel ergänzt, Schritt 1b eingefügt.
4. **`acl` fehlt auf tei**, und der Trockenlauf verschwieg es (die Prüfung war
   mit `DRY = 0` verriegelt). Jetzt bleibt auch der Trockenlauf mit exit 5 stehen.

Weiter offen und nur von Hand: `sudo apt install acl`, die Secrets-Datei, der
Lauf selbst, die Rotation (Punkt 4 der Definition of Done). 632 Dateien unter
`/home/dh` sind weltlesbar (Tiefe 2, fast alle Provider-Code unter
`dodis_mcp/`, `eos_mcp/`); nach dem Durchgangsrecht kann der Bot sie öffnen,
wenn er ihre Namen kennt — Code, keine Secrets, aber gemessen, nicht angenommen.

## Nach der Umstellung, 2026-10-23: zwei Prozesse, die als `dh` weiter schreiben

Die Umstellung hat in Schritt 4 jedes Datenverzeichnis aus
`writable_paths.py --data-only` an `agentic_historian` übergeben (`chown -R`).
Zwei Prozesse schreiben dort weiter **als `dh`**. Keiner von beiden stand in
diesem Runbook.

1. **`update.sh`, Import-Smoketest.** Als `dh` kann `knowledge_hub` seine
   Sperrdatei `knowledge_hub/data/.hub.json.lock` nicht anlegen
   (`PermissionError`). Der Deploy von `431810c` brach deshalb nach dem
   Stoppen ab, und der Bot blieb aus, obwohl der Code in Ordnung war.
   **Behoben mit #704:** Der Test läuft jetzt als `User=` der Unit, mit deren
   `AH_ENV_FILE`.
2. **Der ATR-MCP-Server (`atr-mcp.service`, `User=dh`).** Er schreibt unter
   `DATA_DIR`:
   - jeden Job nach `data/mcp_jobs/<job id>/`,
   - die Läufe nach `VLM_TEST_ROOT` (`start_batch`, `transkribus_*`,
     `export_hf`),
   - den Share-Spiegel nach `NEXTCLOUD_STAGING_DIR` (`pull_share`),
   - Schlüssellisten nach `GT_ROOT` (`score_ground_truth`).

   Seit der Umstellung gehörte das alles `agentic_historian 0755`. Jedes
   Werkzeug, das einen Job startet, scheiterte damit schon beim Anlegen des
   Job-Verzeichnisses. Die lesenden Werkzeuge (`gateway_models`, `ubelix_*`)
   waren nicht betroffen.

### Was gilt: beide Benutzer schreiben per ACL (gewählt am 23.10.2026)

```
D=/home/dh/agentic_historian/agentic_historian/data
sudo setfacl -R -m u:dh:rwX "$D"                                    # was schon da ist
sudo setfacl -R -d -m u:dh:rwX -m u:agentic_historian:rwX "$D"      # was neu entsteht
getfacl -p "$D/mcp_jobs" | head -12
sudo systemctl restart atr-mcp.service
```

So stand es auf tei nach dem Lauf:

```
# file: …/data/mcp_jobs
# owner: agentic_historian
user::rwx
user:dh:rwx
group::r-x
mask::rwx
other::r-x
default:user::rwx
default:user:agentic_historian:rwx
default:user:dh:rwx
```

Die Default-ACL wirkt in beide Richtungen. Was der Bot neu anlegt, kann `dh`
schreiben. Was der MCP-Server als `dh` anlegt (eine Datei mit `owner dh`),
kann der Bot schreiben.

- **Ein erneuter `switch_to_own_user.sh`** lässt das stehen: `chown -R`
  ändert keine ACL-Einträge, und `chmod -R u+rwX` berührt die Maske nicht.
- **Liegt ein Datenverzeichnis ausserhalb von `DATA_DIR`,** braucht es
  dieselben zwei Zeilen. Welche das sind, sagt
  `.venv/bin/python deploy/systemd/writable_paths.py --data-only`.
  `knowledge_hub/data` gehört nicht dazu: Der MCP-Server schreibt dort nicht.

**Was das für die Trennung heisst.** Die Secrets bleiben getrennt:
`/etc/agentic_historian.env` ist `root:agentic_historian 0640`, und daran
ändert die ACL nichts. Die *Daten* sind wieder gemeinsam. `dh`, als der auch
der OpenClaw-Agent läuft, kann die Daten des Bots schreiben, wie vor der
Umstellung. Die Definition of Done unten («writes only to its data
directory») misst den Bot und bleibt erfüllbar. Ob der Agent dort schreiben
darf, ist eine eigene Frage, und diese ACL beantwortet sie mit Ja.

**Die Alternative, nicht jetzt.** Der MCP-Server liefe selbst als
`agentic_historian`, mit `AH_ENV_FILE=/etc/agentic_historian.env`. Seine Jobs
brauchen aber Keys, die nur in `.env.gpustack` stehen, etwa die
Transkribus-Zugangsdaten der `transkribus_*`-Werkzeuge. Was in der neuen Datei
fehlt, fällt still auf den Default zurück, wie in Schritt 1 oben. Vorher wäre
also mit `env_diff.py` zu messen, welche Keys die Jobs lesen, und die neue
Datei entsprechend zu ergänzen. Danach lassen sich die ACL-Einträge für `dh`
wieder entfernen (`setfacl -R -x u:dh -x d:u:dh "$D"`).

## Items and status

1. **Own `EnvironmentFile` with only the bot's secrets** — `agentic_historian.env.example` +
   `secrets.conf` + `AH_ENV_FILE`. ✅ (repo side) / ◻️ **nicht installiert**
   (gemessen 2026-10-13). Nachrangig geworden, siehe oben; installiert
   `switch_to_own_user.sh` in einem Zug mit 2 und 3.
2. **Harden the unit like the MCP server** — `hardening.conf` (#580). ✅ (repo side)
   / ◻️ **nicht installiert**. Seit 17.10.2026 eine Stufe: nur die zwei
   Datenverzeichnisse, abgeleitet aus der laufenden Konfiguration
   (`writable_paths.py`); `/update` berichtet und zieht nicht mehr. Das Skript
   prüft vor dem Neustart, dass die Datei jedes Verzeichnis freigibt, das die
   Konfiguration schreibt.
3. **Run as a dedicated, less-privileged user** — `agentic_historian` (nicht
   `dh-bot`), `user.conf`. ✅ (repo side) / ◻️ **Host-Schritt**: der Lauf oben.
4. **Rotate** what both processes handled while they shared a user — nach dem
   Lauf, von Hand; siehe oben.

Ob es in Kraft ist, sagt `deploy/systemd/check_separation.py` — es liest die
wirksamen Eigenschaften von systemd, nicht die Dateien in diesem Checkout.

## Definition of done (#567)

The bot process cannot read the OpenClaw credentials, runs hardened under systemd,
and writes only to its data directory. Alles unten ist eine Messung auf tei, kein
Repo-Zustand:

- [ ] `/etc/agentic_historian.env` exists (`root:agentic_historian 0640`) with only the
      bot's keys — `check_separation.py` row for the file.
- [ ] `secrets.conf` + `hardening.conf` + `user.conf` installed and loaded:
      `check_separation.py` rows `User= (agentic_historian)`, `ProtectSystem=strict`,
      `NoNewPrivileges=yes`, `PrivateTmp=yes`, `ReadWritePaths` all `ok`;
      `systemd-analyze security agentic-historian` looks sane.
- [ ] `check_separation.py --openclaw-secrets <path>` answers `ok` — the file is
      neither world/group-readable nor owned by the service user.
- [ ] Every secret that lived in the shared `.env.gpustack` rotated, the new value
      in `/etc/agentic_historian.env` only.

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

## S1 — the bot's own Discord application and token (#566)

`dh-bot` is the OpenClaw agent's Discord identity (user id `1519706253314756758`).
Whether the Historian bot logs in through that same application — the same
`DISCORD_BOT_TOKEN` — is recorded nowhere in this repository, and nothing in it
can tell: the token lives on tei, in `.env.gpustack` (or `/etc/agentic_historian.env` after
S2). Shared, the two bots share identity, permissions and blast radius: a leak
of either process's token is a leak of both.

### 1. Find out — one call, on tei

```
# Reads the token out of the file; never put it on a command line (argv is in ps).
f=/etc/agentic_historian.env; [ -r "$f" ] || f=/path/to/agentic_historian/.env.gpustack
tok=$(grep -m1 '^DISCORD_BOT_TOKEN=' "$f" | cut -d= -f2- | awk '{print $1}')
curl -s -H @<(printf 'Authorization: Bot %s\n' "$tok") https://discord.com/api/v10/users/@me; echo
```

`"id": "1519706253314756758"` → the Historian bot **is** `dh-bot`: shared, do
all of the steps below. Any other id → already separate: note it on #566 and go
to step 5.

### 2. Create the application — Discord Developer Portal

- *New Application* → name it **`agentic_historian`** (decided 17.10.2026).
  This is what members see on every reply; today they see `dh-bot`, which is
  the OpenClaw agent's identity — the point of S1 is that they stop.
- *Bot* → **Reset Token** → copy it once; it is shown once. **Public Bot: off.**
- *Bot → Privileged Gateway Intents*: **Presence, Server Members, Message
  Content — all off.** The bot runs on `Intents.default()` (`bot.py`) and needs
  none of them: it is driven by slash commands and buttons, its role gate reads
  the member Discord sends with each interaction, and it never reads a message.
  `test_ah_566` pins that in code, so this stays a portal-only decision.

### 3. Invite it with the least it needs

*OAuth2 → URL Generator*: scopes **`bot`** and **`applications.commands`**. Bot
permissions, exactly these:

| permission | why the bot needs it |
|---|---|
| View Channels | see the channels it posts in |
| Send Messages | replies beyond the interaction; the progress and credential-watch channels |
| Create Public Threads, Send Messages in Threads | the one thread it opens |
| Embed Links | the one embed it posts |
| Read Message History | it re-fetches and edits its own progress messages |

Not Administrator, not Manage Messages (editing its own messages needs none),
not Attach Files, not Mention Everyone, not Add Reactions — the bot uses none of
them. Open the generated URL and pick the server.

### 4. Give the token to the bot — and only the bot

`DISCORD_BOT_TOKEN=` goes into `/etc/agentic_historian.env` (S2) and nowhere else; set
`DISCORD_GUILD_ID` too, so the commands appear at once instead of after
Discord's hour-long global roll-out. Then:

```
sudo systemctl restart agentic-historian
journalctl -u agentic-historian -n 30      # the login line names the new bot user
```

and in Discord, any slash command now shows the new name.

### 5. Restrict it in the server

*Server Settings → Integrations → the application*: *Commands* — turn the
`@everyone` default **off** and allow the project role; *Channels* — only the
project channels. That is Discord's layer. The bot's own gate stays beneath it:
`REQUIRED_DISCORD_ROLE_ID` (fail-closed, SEC-1 #572) for every sensitive
command, `REQUIRED_ADMIN_ROLE_ID` set in its own right for `/update` (SEC-5
#576).

### 6. If it was shared: cut the old token's reach

In the `dh-bot` application: *Bot → Reset Token*, and give the new token to
OpenClaw only. The copy the Historian host held is dead from that moment. Remove
the old `DISCORD_BOT_TOKEN` line from `.env.gpustack` — the bot no longer reads
that file once `AH_ENV_FILE` is set (S2) — and rotate the rest of what the two
processes shared, per #567's list.

### Definition of done (#566)

- [ ] Step 1 answered — shared or not — and the answer noted on #566.
- [ ] Own application named `agentic_historian`, own avatar; its token in
      `/etc/agentic_historian.env` only.
- [x] No privileged intent in code (`Intents.default()`, pinned by `test_ah_566`).
- [ ] Privileged intents all off in the portal; Public Bot off.
- [ ] Invited with `bot` + `applications.commands` and the six permissions above,
      nothing more.
- [ ] Commands restricted to the project role and channels under
      *Integrations*.
- [ ] If shared: `dh-bot`'s token reset, the old line gone from `.env.gpustack`.

The rest of the separation epic — S4 (LLM-orchestration overlay off by default,
#569), S5 (standalone CI smoke test, #570) — is done; the epic is tracked under
#565.
