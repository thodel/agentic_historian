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

```
$ systemctl cat agentic-historian | grep -nE "EnvironmentFile|^Environment=|^User=|^Group="
12:User=dh
```

```
WorkingDirectory=/home/dh/agentic_historian/agentic_historian
ExecStart=/home/dh/agentic_historian/agentic_historian/.venv/bin/python bot.py
Drop-In: /etc/systemd/system.control/agentic-historian.service.d/50-MemoryMax.conf
         /etc/systemd/system/agentic-historian.service.d/restart.conf
Memory: 96.4M (max: 300.0M)
```

Drei Dinge, die man daraus wissen muss:

* **Der Dienst heisst `agentic-historian`**, nicht `dh-bot`. `dh-bot` ist der
  OpenClaw-Agent *und* der Discord-Anzeigename dieses Bots (`dh-bot#5188`) —
  eine Namensgleichheit, die beim Debuggen zweimal Zeit gekostet hat.
* **Es gibt kein `EnvironmentFile` und kein `Environment=`.** systemd injiziert
  nichts. Alles kommt aus den dotenv-Dateien, die `config.py` selbst liest.
* **`ExecStart` benutzt das venv unter `agentic_historian/.venv`**, nicht das im
  Repo-Wurzelverzeichnis. Das zweite existiert auch und hat `watchdog` nicht —
  ein Skript mit dem falschen venv bricht mit `ModuleNotFoundError`.

## Woher die Konfiguration kommt

Aus einer Session beantwortet das seit #690 `server_status()`: die Dateien in
Lesereihenfolge und ob jede existiert, `AH_ENV_FILE`, die Datenwurzeln, der
Checkout mit Branch und Commit. Keine Werte von Schlüsseln, nur `set`/`unset`.


`AH_ENV_FILE` ist **nicht** gesetzt, also liest `config.py` der Reihe nach:

| Datei | Keys | was daraus kommt |
|---|---|---|
| `/home/dh/agentic_historian/.env.gpustack` | 36 | alle Secrets und Tunables |
| `/home/dh/agentic_historian/.env` | 3 | `REQUIRED_DISCORD_ROLE_ID`, `REQUIRED_ADMIN_ROLE_ID`, `DISCORD_GUILD_ID` |
| `/home/dh/agentic_historian/agentic_historian/.env` | 1 | `ATR_MOUNT_DIR` |

Die erste Datei, die einen Key nennt, gewinnt (`load_dotenv(override=False)`).
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
sie**, solange `secrets.conf` nicht installiert ist (siehe unten). Sie hielt
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

## Was *nicht* in Kraft ist: S2 (#567)

Keiner der drei Punkte, obwohl das Issue geschlossen war:

| Punkt | Zustand |
|---|---|
| 1 eigenes `EnvironmentFile` nur für den Bot | `secrets.conf` **nicht installiert** |
| 2 systemd-Sandbox | `hardening.conf` **nicht installiert** |
| 3 eigener, weniger privilegierter Benutzer | `User=dh`, also nein |

Die Drop-In-Liste oben ist der Beleg: nur `50-MemoryMax.conf` und
`restart.conf`. #567 ist deswegen wieder geöffnet, mit der Messung und einer
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
| `/home/dh/agentic_historian/.env.gpustack` | `-rw-------` dh dh | unverändert korrekt |
| `/home/dh/agentic_historian/.env` | `-rw-rw-r--` dh dh | `chmod 600` |
| `/home/dh/agentic_historian/agentic_historian/.env` | `-rw-rw-r--` dh dh | `chmod 600` |
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
   Benutzer heisst **`agentic_historian`** — `dh-bot` ist die Discord-Identität
   des OpenClaw-Agenten, und so hiesse das, wovon getrennt wird. Vorbereitet
   als `deploy/systemd/switch_to_own_user.sh` (alle drei Punkte in einem Lauf,
   Trockenlauf zuerst), Ablauf in [SEPARATION.md](../deploy/systemd/SEPARATION.md),
   **auf tei noch nicht ausgeführt** (Stand 21.10.2026).
3. **Befehlsberechtigungen in Discord**, am Desktop.
4. `CREDENTIAL_WATCH_CHANNEL_ID`, falls der Credential-Watcher laufen soll.
5. Die doppelten Keys in `.env.gpustack` auflösen — spätestens beim Befüllen
   von `/etc/agentic_historian.env`, wo nur die jeweils letzte Zuweisung
   hingehört.
6. **Wo die OpenClaw-Credentials liegen.** Ohne den Pfad bleibt die Definition
   of Done von #567 ungemessen: `check_separation.py --openclaw-secrets <pfad>`.
