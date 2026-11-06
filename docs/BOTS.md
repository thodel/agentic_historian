# Die zwei Bots: dh-bot und agentic-historian

Stand 05.11.2026 (H5, #801; Epic #803). Zwei Bots auf demselben
Discord-Server, zwei Anwendungen, zwei Benutzer auf tei, eine Grenze.

**Die Regel in einem Satz:** dh-bot erreicht den Historian nur über `atr-mcp`,
lesend; der Historian kennt dh-bot nicht. dh-bot ändert den *Code* des
Historian über Pull Requests und *bedient* ihn nie.

## Was welcher Bot ist

| | **dh-bot** (Hermes, Benutzer `hermes`) | **agentic-historian** (discord.py, Benutzer `agentic_historian`) |
|---|---|---|
| Was es ist | ein Agent: Gespräch, Recherche, Gedächtnis, Skills, Cron | ein Bedienpult: deterministische Slash-Befehle mit Rollen-Gate, keine LLM-Orchestrierung (S4 #569 aus) |
| Recherche in den Quellen (KF, EOS, HBLS, SSRQ, HLS, SIKART, outremer, wd-pre1500, DMJ) | **ja**, über die MCP-Föderation (#800, neun Quellen, DMJ nur lesend) | nur, wo es die eigene Pipeline speist (`/search`, `/entity`, `/find`, `/sparql`, `/ask`) |
| ATR-Läufe, Training, Votes, Kampagnen, Publizieren, Share, Hot-Folder, Engines | **nein** — fragt über `atr-mcp` lesend nach dem Stand (16 Lesewerkzeuge, kein `start_batch`) | **ja**, allein: die einzigen Prozesse mit GPU-, Trainings-, Daten- und Share-Zugriff |
| Issues mit Label `dh-bot` bearbeiten, PRs öffnen | **ja**, dreimal täglich (Queue-Worker, #799) — auch im Code des Historian | nein, nie |
| Mergen | nie (Regel 8 des Protokolls) — ein Mensch oder der Prüflauf | nie |
| Mail, Kalender, Web, Dateien des Teams, Ad-hoc-Skripte | ja (Credentials rotiert in H6) | nein, nie |
| Cron und Berichte nach Discord | ja (#799: Queue, Repo-Status, Digest, SNSF) | nein — ausser dem, was Jobs selbst melden |
| Secrets | `~/.hermes/.env` von `hermes` | `/etc/agentic_historian.env` (0640 `root:agentic_historian`); für `hermes` unlesbar |
| Identität | Anwendung `dh-bot` (`1519706253314756758`); bis H6 die Test-Anwendung `dh-bot-hermes` | Anwendung `agentic_historian` (`1567234836224286832`, S1 #566) |
| Wer ihn anspricht | Menschen mit der Projektrolle (`DISCORD_ALLOWED_ROLES`) und erlaubte Bots per Erwähnung | Menschen mit `REQUIRED_DISCORD_ROLE_ID`; Admin-Befehle mit `REQUIRED_ADMIN_ROLE_ID` |

## Kanäle

| Kanal | wer schreibt |
|---|---|
| *Allgemein* (`1519707390956798034`) | dh-bot antwortet auf Erwähnung; seine Cron-Berichte landen hier (`DISCORD_HOME_CHANNEL`); der Historian meldet hier den ATR-Watcher |
| Gate-Karten, Kampagnen-Threads | nur der Historian (`/route`, `/votes`, `/campaign`) |
| Direktnachrichten | keiner der beiden antwortet; dh-bot nur, wenn der Absender in `DISCORD_ALLOWED_USERS` steht |

Zwei Bots in einem Kanal: dh-bot antwortet einem Bot nur, wenn der ihn im Text
nennt (`DISCORD_ALLOW_BOTS=mentions`); der Historian liest keine Nachrichten
und kann dh-bot nicht ansprechen. Eine Schleife zwischen beiden ist damit
nicht möglich.

## Die Befehle des Historian, einzeln

Gate: *Rolle* = `REQUIRED_DISCORD_ROLE_ID`, *Admin* = `REQUIRED_ADMIN_ROLE_ID`
(beide fail-closed, #572), *offen* = jedes Mitglied. Spalte *dh-bot*: was
mit dem Befehl geschieht, weil es jetzt dh-bot gibt — **nichts wird abgebaut**,
weil jeder dieser Befehle entweder Betrieb ist oder die eigene Pipeline speist.
Die Liste wird gegen `bot.py` geprüft (`test_ah_803_hermes_deploy.py`).

| Befehl | Gate | Was er tut | dh-bot |
|---|---|---|---|
| `/status` | offen | Overall pipeline status | bleibt — dh-bot fragt den Stand über `server_status`/`gateway_models` per atr-mcp |
| `/search` | offen | Federated person search across the Knowledge Hub (HLS/HBLS/KF/EOS) | bleibt — sucht über die KH-Quellen, speist `/entity` und die Pipeline; dh-bot recherchiert dieselben Quellen im Gespräch, baut nichts doppelt |
| `/entity` | offen | Look up an entity in the local index (documents, authority links) | bleibt — der eigene Index (Dokumente, Normdaten) |
| `/route` | Rolle | Show the Gate-1 routing card for a document (correct metadata, re-rout | bleibt — Betrieb, nur der Historian |
| `/votes` | Rolle | Show the Gate-2 voting card for a doc whose engines disagreed (no-merg | bleibt — Betrieb, nur der Historian |
| `/votes_queue` | Rolle | Offene Gate-2-Abstimmungen, nach Informationsgehalt sortiert | bleibt — Betrieb, nur der Historian |
| `/campaign` | Admin | Stimm-Kampagne auf einem Bestand starten (Budget in Karten) | bleibt — Betrieb, nur der Historian |
| `/campaign_status` | Rolle | Abdeckungsbericht einer Stimm-Kampagne | bleibt — Betrieb, nur der Historian |
| `/votes_stats` | Rolle | Was die Gate-2-Stimmen bisher gemessen haben | bleibt — Betrieb, nur der Historian |
| `/reprocess` | Rolle | Re-process a document after correcting criteria or stages | bleibt — Betrieb, nur der Historian |
| `/run` | Rolle | Run the full A→B→C pipeline on a file | bleibt — Betrieb, nur der Historian |
| `/run_agent_a` | Rolle | Run Agent A (HTR) only | bleibt — Betrieb, nur der Historian |
| `/hotfolder` | Rolle | Process all files in the hot folder | bleibt — Betrieb, nur der Historian |
| `/env_reload` | Admin | Admin: .env-Dateien neu lesen, ohne Neustart — und gleich prüfen | bleibt — Betrieb, nur der Historian |
| `/pull_preflight` | Rolle | Briefkasten getrennt prüfen: Konfiguration, Host, Zugangsdaten, Pfad | bleibt — Betrieb, nur der Historian |
| `/pull` | Rolle | Einen Briefkasten-Ordner holen und verarbeiten | bleibt — Betrieb, nur der Historian |
| `/pull_folder` | Rolle | Process each SwitchDrive subfolder as ONE multi-page document | bleibt — Betrieb, nur der Historian |
| `/agent_d` | Rolle | Run Agent D corpus analysis | bleibt — Betrieb, nur der Historian |
| `/agent_e` | Rolle | Run Agent E — meta report | bleibt — Betrieb, nur der Historian |
| `/progress` | offen | Show phase progress | bleibt |
| `/find` | Rolle | Stellen im Korpus finden — nach Bedeutung, nicht nach Wortlaut | bleibt — der eigene Passagen-Index und die eigenen Lesungen |
| `/ask` | Rolle | Eine Frage an den Korpus — jede Behauptung mit wörtlich geprüftem Beleg (Agent F, #812) | neu, beim Historian — zitiert die eigenen Lesungen mit ihrem Gate-2-Status und publiziert nach `docs/questions/`, beides nur hier; Editionstext der Föderation (EOS) nur als gekennzeichneter Beleg. Feste Stufenfolge, das Modell wählt keine Werkzeuge (S4 bleibt aus) |
| `/atr_jobs` | Rolle | Training jobs on asteraix | bleibt — Betrieb, nur der Historian |
| `/atr_job` | Rolle | One training job in detail | bleibt — Betrieb, nur der Historian |
| `/atr_gpu` | Rolle | GPU memory on both ATR machines, and what holds it | bleibt — Betrieb, nur der Historian |
| `/atr_progress` | Rolle | Tail of a training job's log | bleibt — Betrieb, nur der Historian |
| `/update` | Admin | Admin: report whether this process runs origin/main | bleibt — Betrieb, nur der Historian |
| `/mcp_propose` | Rolle | Probe an MCP source and open a PR to add it (reviewed, never hot-loade | bleibt — schreibt in die eigene Queue und öffnet einen PR, geprüft |
| `/atr_engines` | Rolle | Welche Erkennungs-Engines antworten (und welche nicht) | bleibt — Betrieb, nur der Historian |
| `/atr_restart` | Admin | Eine Erkennungs-Engine auf idhefix neu starten (mit Bestätigung) | bleibt — Betrieb, nur der Historian |
| `/train_start` | Admin | Admin: ein Training aus einem benannten Preset starten | bleibt — Betrieb, nur der Historian |
| `/train_publish` | Admin | Admin: den Bericht eines Trainings-Laufs in die Outputs publizieren | bleibt — Betrieb, nur der Historian |
| `/sparql` | Rolle | Eine benannte Frage an den Korpus-Graphen (ohne Angabe: die Liste) | bleibt — der eigene QLever |
| `/train_log` | Rolle | Das Ende eines Stufen-Logs eines Trainings-Laufs | bleibt — Betrieb, nur der Historian |
| `/train_cancel` | Admin | Admin: einen Trainings-Lauf abbrechen | bleibt — Betrieb, nur der Historian |
| `/train_status` | Rolle | Status eines Trainings-Laufs (ohne Angabe: die beobachteten) | bleibt — Betrieb, nur der Historian |
| `/train_list` | Rolle | Die letzten Trainings-Läufe, neueste zuerst | bleibt — Betrieb, nur der Historian |

## Was dh-bot daraus wissen muss

Die drei Befunde der ersten Proben (05.11.2026, #800), als Regeln in
`AGENTS.md` von dh-bot — der Wortlaut liegt in
[`deploy/hermes/agents-boundaries.md`](../deploy/hermes/agents-boundaries.md):

1. **Ein fehlendes Werkzeug fehlt absichtlich.** Wer einen Batch, ein
   Training, eine Kampagne will, bekommt den Hinweis auf den Historian-Befehl
   — keine Suche nach einem Umweg (Dateien, Terminal, andere Werkzeuge).
2. **Nicht anbieten, was man nicht darf.** „Soll ich einen Batch starten?“
   ist die falsche Frage; die richtige ist „das macht `/hotfolder` beim
   Historian“.
3. **Lesen hat auch Kosten.** `share_list` und `read_pages` sind lesend, aber
   sie laufen auf dem Historian; nur aufrufen, wenn die Frage sie braucht.

## Offen

- H6 (#802): das `dh-bot`-Token wechselt zu Hermes, OpenClaw geht aus, die
  Credentials werden rotiert; dh-bot bekommt ein eigenes GitHub-Konto, damit
  seine PRs und Kommentare nicht unter dem Token-Inhaber erscheinen.
- Schreibende atr-mcp-Werkzeuge für dh-bot (z. B. `start_batch` mit
  Freigabe-Karte): **nicht in dieser Runde** — entschieden mit der Tabelle.
