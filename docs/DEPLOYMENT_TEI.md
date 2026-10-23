# Was auf tei wirklich läuft

Gemessen am 2026-10-13/14 beim Einrichten des Bots für einen offenen
Discord-Server. **Kein Soll-Zustand** — die Soll-Zustände stehen in
[SEPARATION.md](../deploy/systemd/SEPARATION.md) und in den Issues. Hier steht,
was der Host an diesem Datum tatsächlich tat, mit dem Befehl, der es gezeigt
hat, damit die nächste Prüfung nicht wieder bei Null anfängt.

Der Anlass: #571 war geschlossen, #567 war geschlossen, und beide hatten
Zustände dokumentiert, die auf dem Host nicht in Kraft waren. Ein Issue zu
schliessen beschreibt das Repo; es beschreibt nicht die Maschine.

---

## Der Dienst

Seit 23.10.2026 (S2, #567), `systemctl show` statt der Datei:

```
User=agentic_historian            Group=agentic_historian
EnvironmentFiles=/etc/agentic_historian.env (ignore_errors=no)
ProtectSystem=strict  NoNewPrivileges=yes  PrivateTmp=yes
ReadWritePaths=/home/dh/agentic_historian/agentic_historian/data
               /home/dh/agentic_historian/agentic_historian/knowledge_hub/data
WorkingDirectory=/home/dh/agentic_historian/agentic_historian
ExecStart=/home/dh/agentic_historian/agentic_historian/.venv/bin/python bot.py
Drop-In: /etc/systemd/system.control/agentic-historian.service.d/50-MemoryMax.conf
         /etc/systemd/system/agentic-historian.service.d/{restart,secrets,hardening,user}.conf
```

Vier Dinge, die man daraus wissen muss:

* **Der Dienst heisst `agentic-historian`**, nicht `dh-bot`. `dh-bot` ist der
  OpenClaw-Agent *und* der Discord-Anzeigename dieses Bots (`dh-bot#5188`) —
  eine Namensgleichheit, die beim Debuggen zweimal Zeit gekostet hat.
* **Der Bot läuft als `agentic_historian`**, nicht als `dh`. Er kann das
  Checkout, das venv und alles ausserhalb der zwei Datenverzeichnisse nicht
  schreiben (`ProtectSystem=strict`), und er kann nichts lesen, was `dh` für
  sich behält (`/home/dh` ist 0750; der Bot hat nur ein Durchgangsrecht per
  ACL auf `/home/dh` und das Checkout).
* **Seine Konfiguration ist `/etc/agentic_historian.env`** — systemd injiziert
  sie, und `AH_ENV_FILE` sorgt dafür, dass `config.py` nur diese Datei liest,
  nicht mehr die dotenv-Dateien im Checkout (nächster Abschnitt).
* **`ExecStart` benutzt das venv unter `agentic_historian/.venv`**, nicht das im
  Repo-Wurzelverzeichnis. Das zweite existiert auch und hat `watchdog` nicht —
  ein Skript mit dem falschen venv bricht mit `ModuleNotFoundError`.

### Deploy seit S2

Der Bot zieht nicht mehr selbst (`/update` vergleicht nur). Der Deploy läuft als
`dh` von aussen, und der Dienst wird mit dem einen passwortfreien sudo neu
gestartet:

```bash
cd /home/dh/agentic_historian && git branch --show-current && git pull --ff-only
agentic_historian/.venv/bin/python -m pip install -r requirements.txt   # nur wenn sie sich geändert hat
sudo -n systemctl restart agentic-historian.service
```

Erst der Branch, dann der Pull: am 23.10.2026 stand das Checkout nach dem Lauf
zweimal nicht auf `main` — einmal auf `ruemlang-test` (Feature-Branch einer
anderen Session, mit laufendem Upload daraus), einmal auf einem veralteten
lokalen `main` mit zwei ungepushten Commits, das eine andere Session 21 Sekunden
nach einem Fast-Forward ausgecheckt hatte. Der laufende Bot merkt davon nichts,
bis ein später Import oder ein Neustart den alten Baum liest. Die beiden Commits
liegen in `sicherung-main-2026-10-22`.

**Neue oder geänderte Keys** gehören per `sudoedit /etc/agentic_historian.env`
in die Datei des Bots, nicht mehr nach `.env.gpustack`; danach Neustart. Die
dotenv-Dateien im Checkout liest nur noch die **CLI** (`python -m
agentic_historian …`, als `dh`, ohne `AH_ENV_FILE`) — zwei Konfigurationen für
zwei Prozesse, und die Rotation (unten) ist der Moment, in dem sie
auseinanderlaufen dürfen.

## Woher die Konfiguration kommt

Aus einer Session beantwortet das seit #690 `server_status()`: die Dateien in
Lesereihenfolge und ob jede existiert, `AH_ENV_FILE`, die Datenwurzeln, der
Checkout mit Branch und Commit. Keine Werte von Schlüsseln, nur `set`/`unset`.


**Der Dienst** (seit 23.10.2026): `AH_ENV_FILE=/etc/agentic_historian.env` ist
gesetzt, also liest `config.py` genau diese eine Datei — root:agentic_historian
0640, 26 Keys (die Keys aus `agentic_historian.env.example`, die auf tei gesetzt
waren), gebaut mit `deploy/systemd/build_env_file.py` aus den drei Dateien
unten, gemessen mit `deploy/systemd/env_diff.py`: kein Setting ändert sich
gegenüber dem Lesen der drei Dateien. Wer prüfen will, was die Datei enthält,
druckt Namen: `sudo grep -oE '^[A-Z0-9_]+' /etc/agentic_historian.env`.

**Die CLI** (`python -m agentic_historian …` als `dh`, `AH_ENV_FILE` nicht
gesetzt) liest weiter der Reihe nach:

| Datei | Keys | was daraus kommt |
|---|---|---|
| `/home/dh/agentic_historian/.env.gpustack` | 36 | alle Secrets und Tunables |
| `/home/dh/agentic_historian/.env` | 4 | `REQUIRED_DISCORD_ROLE_ID`, `REQUIRED_ADMIN_ROLE_ID`, `DISCORD_GUILD_ID`, `HF_TOKEN` |
| `/home/dh/agentic_historian/agentic_historian/.env` | 1 | `ATR_MOUNT_DIR` |

Die erste Datei, die einen Key nennt, gewinnt (`load_dotenv(override=False)`).
Drei Keys stehen nur dort und nicht in `.env.gpustack` — `HF_TOKEN`,
`ATR_MOUNT_DIR` und die Rollen-IDs —, und das war am 22.10.2026 der Grund, die
Kopie zu messen statt ihr zu glauben: mit den Keys des damaligen Beispiels aus
`.env.gpustack` allein wären `ATR_MOUNT_DIR`, `ATR_PAGE_CACHE_ARCHIVE`,
`LASSBERG_REGISTER` und `NEXTCLOUD_REMOTE_DIR` still auf ihre Defaults gefallen.
Die autoritative Abfrage ist nicht `grep`, sondern `config.ENV_SOURCE`:

```bash
sudo -u dh /home/dh/agentic_historian/agentic_historian/.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "/home/dh/agentic_historian/agentic_historian")
import config
print([str(p) for p in config.ENV_FILES])
print(config.ENV_SOURCE.get("REQUIRED_DISCORD_ROLE_ID"))
PY
```

`config.py` führt darüber Buch, und ein `grep` über die Dateien tut es nicht:
ein eigener Versuch mit `grep -ohE "^[[:space:]]*[A-Z0-9_]+="` hat zwei Runden
gekostet, weil er eine Datei ausliess, die vorher noch nicht existiert hatte.

### `/etc/dh-bot.env` ist tot

Die Datei existiert, enthält aber nur noch einen Kommentarkopf. **Nichts liest
sie** — `secrets.conf` ist seit 23.10.2026 installiert und zeigt auf
`/etc/agentic_historian.env`, nicht auf sie. Sie hielt
zwischenzeitlich eine zweite Kopie der drei Rollen-Keys — eine wirksame und eine
wirkungslose Kopie derselben Einstellung, was genau einmal zu der Verwirrung
geführt hat, die es zu erwarten war.

## Was abgesichert ist, und wodurch

Seit `2026-10-13 20:56:51` (`MainPID=1619786`, `NRestarts=0`):

```
[sec] Sicherheitslage — alles Prüfbare gesetzt
  [ok  ] REQUIRED_DISCORD_ROLE_ID
  [ok  ] REQUIRED_ADMIN_ROLE_ID (ausdrücklich)
  [ok  ] Admin-Rolle, effektiv: eigene Rolle
  [ok  ] DISCORD_GUILD_ID
  [?   ] Befehlsberechtigungen in Discord — von hier aus nicht prüfbar
```

Der Bericht kommt aus `bot.log_security_posture` (SEC-19, #646) und läuft in
`on_ready`, ins Log und auf die Konsole. Prüfen:

```bash
journalctl -u agentic-historian --since "-5 min" --no-pager | grep '\[sec\]'
```

**Belegt ist das Gate von beiden Seiten**, am 2026-10-13 um 20:59 in Discord:

| Test | Ergebnis |
|---|---|
| Mitglied **mit** Rolle, `/train_list` | läuft durch |
| Mitglied **ohne** Rolle, `/train_list` | `⛔ Du hast nicht die erforderliche Rolle für diesen Befehl.` (ephemer) |
| Admin, `/update` | antwortet (`Bereits auf dem neusten Stand — 3c360ccd7257`) |

Die mittlere Zeile ist die, die zählt. Dass es mit Rolle funktioniert, ist die
halbe Information — ein fail-open-Gate sieht von innen identisch aus.

### Noch nicht gesetzt

Die **Befehlsberechtigungen in Discord** (Servereinstellungen → Gruppe *Apps* →
Integrationen → der Bot → Befehle). Diese Schicht wirkt *vor* dem Code; ohne sie
sehen alle Mitglieder die Befehle im Autocomplete und bekommen die Ablehnung
erst nach dem Aufruf. Zwei Dinge dazu, aus der Dokumentation:

* Konfigurierbar **nur am Desktop**, nicht in der Mobil-App.
* **Mitglieder mit Administrator-Recht sind davon nicht betroffen.** Die Schicht
  kann also nicht leisten, was der Code leistet — weshalb der Startbericht sie
  als nicht prüfbar führt und nicht als Haken.

## S2 (#567): seit 23.10.2026 in Kraft

| Punkt | 13.10.2026 | 23.10.2026 |
|---|---|---|
| 1 eigenes `EnvironmentFile` nur für den Bot | `secrets.conf` nicht installiert | `/etc/agentic_historian.env`, root:agentic_historian 0640 |
| 2 systemd-Sandbox | `hardening.conf` nicht installiert | `ProtectSystem=strict`, `NoNewPrivileges`, `PrivateTmp`, zwei `ReadWritePaths` |
| 3 eigener, weniger privilegierter Benutzer | `User=dh` | `User=agentic_historian` |
| 4 Rotation | — | **offen** |

Gemessen mit `deploy/systemd/check_separation.py --secrets
/etc/agentic_historian.env --openclaw-secrets /home/dh/.openclaw/openclaw.json`,
als root: 7 erfüllt, 0 nicht erfüllt, 1 nicht gemessen (`ProtectHome`, nicht
anwendbar bei einem Checkout unter `/home`). Der Lauf und seine vier Vorab- und
zwei Nachbefunde stehen in
[SEPARATION.md](../deploy/systemd/SEPARATION.md) («Nachgemessen 22.10.»,
«Durchgeführt 23.10.»).

Zur Vorgeschichte — am 13.10.2026 war keiner der drei Punkte in Kraft, obwohl
das Issue geschlossen war; die Drop-In-Liste hielt nur `50-MemoryMax.conf` und
`restart.conf`. #567 wurde deswegen wieder geöffnet, mit der Messung und einer
geänderten Reihenfolge — **Punkt 3 zuerst**, weil die gemessene Richtung des
Problems eine andere ist als die im Issue beschriebene:

`.env.gpustack` hält 36 Keys, und jeder gehört dem Bot. Keine Mail-, Kalender-
oder SSH-Zugänge des OpenClaw-Agenten; Rechte `0600 dh:dh`. Der Bot liest also
nichts, was ihm nicht gehört. Real ist das Gegenteil: **beide Prozesse laufen
als `dh`**, der OpenClaw-Agent kann also die Secrets des Bots lesen —
Discord-Token, GitHub-Token, GPUStack-Key, Transkribus- und
SwitchDrive-Passwort. Dagegen hilft kein eigenes `EnvironmentFile` (wer als `dh`
läuft, liest es genauso), sondern nur ein eigener Benutzer.

## Dateirechte

| Datei | gefunden | jetzt |
|---|---|---|
| `/etc/agentic_historian.env` | — | seit 23.10.2026 `-rw-r-----` root agentic_historian: root besitzt, die Gruppe des Dienstes liest |
| `/home/dh/agentic_historian/.env.gpustack` | `-rw-------` dh dh | unverändert korrekt |
| `/home/dh/agentic_historian/.env` | `-rw-rw-r--` dh dh | `chmod 600` |
| `/home/dh/agentic_historian/agentic_historian/.env` | `-rw-rw-r--` dh dh | `chmod 600` (am 23.10. wieder 0664 vorgefunden und erneut gesetzt) |
| `/etc/dh-bot.env` | `-rw-r--r--` root root | `chmod 600`, Inhalt durch Kommentar ersetzt |

Die Secrets-Datei war schon richtig gesetzt — die Warnung davor beruhte auf
einer Hochrechnung von der Nachbardatei und war insofern unbegründet. Die
weltlesbaren Dateien enthielten Rollen-IDs und einen Mount-Pfad, also keine
Geheimnisse.

### Doppelte Keys in `.env.gpustack`

42 Zeilen mit Zuweisungen, 36 eindeutige Namen. Dreifach vergeben:

```
LASSBERG_REGISTER
NEXTCLOUD_REMOTE_DIR
NEXTCLOUD_SHARE_URL
```

`python-dotenv` nimmt die **letzte** Zuweisung. Wer die Datei von oben liest,
sieht also möglicherweise einen Wert, der nicht gilt — bei
`NEXTCLOUD_SHARE_URL` entscheidet das, wohin publiziert wird. Ob die Werte
überhaupt verschieden sind, ohne sie zu drucken:

```bash
for k in LASSBERG_REGISTER NEXTCLOUD_REMOTE_DIR NEXTCLOUD_SHARE_URL; do
  n=$(grep -E "^[[:space:]]*$k=" /home/dh/agentic_historian/.env.gpustack \
      | sed -E "s/^[^=]*=//" | sort -u | wc -l)
  echo "$k: $n verschiedene Werte"
done
```

## Hintergrundprozesse, wie sie starten

```
[hot-watch] started on …/agentic_historian/data/hot_folder
[atr-watch] every 300s into #1519707390956798034 (mention=True, seeded=True)
[trainwatch] every 60s, 0 run(s) in the registry
```

`[trainwatch] 0 run(s)` bei laufendem Training ist **kein Fehler**: der Watcher
aus #363 verfolgt nur Läufe, die über `/train_start` ins Register kamen — „ein
Lauf im Register wurde absichtlich dort hingelegt". Ein Lauf, der anders
gestartet wurde, meldet sich beim Abschluss nicht; man sieht ihn nur über
`/train_list`. Einen Weg, einen laufenden Job nachträglich in die Beobachtung zu
nehmen, gibt es nicht.

Der Credential-Watcher läuft **nicht**: `CREDENTIAL_WATCH_CHANNEL_ID` ist nicht
gesetzt, und bei dieser Einstellung ist der Kanal der Schalter.

## Gateways

`http://130.92.59.240:8200` antwortet (`/train/jobs`, `/train/gpu` → 200).
Zu `130.92.59.240:8201` (kraken) liegt aus dieser Sitzung **keine eigene
Messung** vor; der Stand ist der zuvor berichtete „unten". `/atr_engines` sagt
es verlässlich.

## Was bei einem Neustart passiert

Die wirksame Konfiguration liegt in Dateien im Checkout, also übersteht sie
Reboots. `NRestarts=0` zählt nur *automatische* Neustarts — manuelle erhöhen den
Zähler nicht, eine `0` heisst also nicht „nie neu gestartet".

Hätten die Rollen-IDs im Manager-Environment gelegen
(`systemctl set-environment`), wären sie beim nächsten Manager-Neustart weg und
das Gate wäre still auf „nur Server-Admins" zurückgefallen. Sie liegen dort
nicht (`systemctl show-environment` ist leer) — geprüft, weil der Unterschied
zwischen „stabil" und „hält bis zum nächsten Reboot" von aussen nicht sichtbar
ist.

## Offene Entscheidungen, die kein Commit treffen kann

1. ~~**`/update` behalten oder Deploy von aussen.**~~ Entschieden am 17.10.2026:
   `/update` behält den Vergleich und verliert den Pull — es berichtet beide
   Commits und zieht nicht. Der Deploy läuft von aussen, als `dh`, mit `git
   pull`. Damit ist `hardening.conf` eine Stufe: nur die Datenverzeichnisse.
2. ~~**Eigener Benutzer `dh-bot`** (Punkt 3)~~ Entschieden am 17.10.2026: der
   Benutzer heisst **`agentic_historian`**. ~~Auf tei noch nicht ausgeführt~~
   **Durchgeführt am 23.10.2026** mit `deploy/systemd/switch_to_own_user.sh`,
   Ablauf und Messung in [SEPARATION.md](../deploy/systemd/SEPARATION.md).
3. **Befehlsberechtigungen in Discord**, am Desktop.
4. `CREDENTIAL_WATCH_CHANNEL_ID`, falls der Credential-Watcher laufen soll.
5. ~~Die doppelten Keys in `.env.gpustack` auflösen~~ Für den Bot erledigt am
   23.10.2026: `build_env_file.py` hat die jeweils letzte Zuweisung übernommen,
   `/etc/agentic_historian.env` hält jeden Key einmal. In `.env.gpustack` stehen
   sie weiter dreifach; das betrifft nur noch die CLI.
6. ~~**Wo die OpenClaw-Credentials liegen.**~~ `/home/dh/.openclaw/openclaw.json`
   (0600 dh, die Konfiguration samt Tokens) und `/home/dh/.openclaw/credentials/`
   (0700 dh, Pairing-Dateien); gemessen am 23.10.2026, für den Bot unerreichbar.
7. **Die Rotation** (Punkt 4 von #567). Die nach `/etc/agentic_historian.env`
   kopierten Werte stehen weiter in `.env.gpustack`, die der OpenClaw-Agent als
   `dh` liest. Pro Dienst neu ausstellen, per `sudoedit` eintragen, Neustart,
   alten Wert widerrufen; `.env.gpustack` erst bereinigen, wenn feststeht, dass
   die CLI den Key nicht braucht.
