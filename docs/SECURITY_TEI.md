# tei und der Rapid7-Bericht vom 01.11.2026 — Stand und der Weg zum Nachscan

Epic #792. Der Bericht (`Asset_report_for_…_2-1.pdf`, nicht im Repo: er enthält
Hostnamen und Pfade) zählte 20 kritische, 69 schwere, 21 moderate Funde. Was
davon übrig ist, und was vor dem Nachscan noch geschehen muss.

## Was erledigt ist

| Fund im Bericht | Ursache | erledigt durch | seit |
|---|---|---|---|
| Eclipse Adoptium (OpenJDK), 80 CVEs | TEI-Publisher-Container und sein Export unter `/root/teipublisher_container`, Image `eclipse-temurin:17-jdk` | entfernt (#793) | 03.11. |
| Apache Log4j Core 2.19.0, 2 CVEs | dasselbe eXist-db | entfernt (#793) | 03.11. |
| VMware Spring CVE-2022-22950 | `spring-*-2.5.6.jar` im selben eXist-db, sonst nirgends | fällt mit #793 (#794) | 03.11. |
| `libcjson1`, `libzvbi0t64`, `libass9`, `libmbedcrypto7t64` | **nie auf dem Host** — im Debian-Baum des TEI-Publisher-Exports | mit #793 weg; der Nachscan bestätigt es | 03.11. |
| `gh`, `python3-pip`, `python3-wheel`, `rclone` | ungenutzt | entfernt (#795); `gh` kam für den Queue-Worker (#799) aus `cli.github.com` zurück, nicht aus Ubuntu | 03.11. |
| `python3-pip-whl` | Abhängigkeit von `python3.12-venv` | Ubuntu Pro, `esm-apps`: 24.0+dfsg-1ubuntu1.3+esm3 | 04.11. |
| weltschreibbare Dateien, Home-Modus | zwölf `uv`-Lockdateien; das Dienstkonto `agentic_historian` hatte ein Home, das es nicht gab | `o-w`; `/home/agentic_historian` angelegt, 0750 (#796) | 03.11. |
| `mnt-gwdg.mount` failed | Freigabe-Passwort geändert (siehe #792, 05.11.) | repariert; `systemctl --failed` leer | 05.11. |
| ungenutzte Docker-Images | `weaviate`, `hello-world`, alte `*-mcp`- und `ch-h-bot-*:latest`-Images | entfernt; es bleiben `ch-h-bot-{app,api,bridge}:mcp`, `swisslib-mcp`, `adfreiburg/qlever` | 05.11. |

## Was noch offen ist

| | wartet auf | was dann |
|---|---|---|
| `python3-urllib3` 2.0.7-1ubuntu0.8 (CVE-2025-66418, -66471) | Canonical: `esm-apps` hat noch keine korrigierte Version | `sudo apt upgrade python3-urllib3`, sobald `apt policy` eine `+esm`-Version nennt. Bis dahin begründet akzeptiert: Abhängigkeit von `python3-requests` im System-Python; die Dienste laufen in venvs mit eigenem urllib3, das System-Python ruft keine fremden URLs auf (#795) |
| OpenClaw, Node, `deb.nodesource.com` | H6-Rückbau (#802), ab 13.11.2026 | Rückbau vor dem Nachscan spart einen zweiten Scan: Node/npm sind dann vom Host |
| Neustart | Wartungsfenster | dbus, logind und unattended-upgrades wurden nach dem Paket-Update vom 04.11. verschoben (`needrestart`) |
| **Nachscan** | Informatikdienste | Bericht an #792; dann #795 und #792 schliessen |

## Bereit für den Nachscan?

```bash
cd ~/agentic_historian && git pull --ff-only
sudo bash deploy/tei/check_rapid7.sh
```

Das Skript liest nur. Je Prüfung eine Zeile, `OK`, `WARN` oder `FAIL`; Exit 1
bei einem `FAIL`. Es gibt Paketnamen, Versionen, Pfade, Unit-Namen und
HTTP-Codes aus, nie Dateiinhalte. Es prüft:

- **Java:** kein `log4j-core*.jar`, kein `*/openjdk/release`, kein `spring-*.jar`
  ausserhalb der Docker-Schichten, kein `/root/teipublisher_container`.
- **Docker:** nur noch die Images der Behaltensliste.
- **Dienste:** `systemctl --failed`, die Units `nginx docker agentic-historian
  atr-mcp apertus-web R7ScanAssistant`, `nginx -t`, und die lokalen Ports
  8300 (atr-mcp), 8000–8004 (die SSH-Tunnel der Föderation nach idhefix) und
  7019 (QLever).
- **Pakete:** `esm-apps`/`esm-infra` aktiv, keines der sieben entfernten Pakete
  wieder da, `gh` aus `cli.github.com`, der Stand von `python3-urllib3` und
  `python3-pip-whl`, nichts mehr aktualisierbar.
- **Dateien:** weltschreibbare Dateien; die Home-Verzeichnisse von root, aller
  Konten ab UID 1000 und aller Konten mit einem Home unter `/home` — auch
  Dienstkonten mit `nologin` und System-UID wie `agentic_historian`: vorhanden,
  nichts für «andere», kein Schreibrecht für die Gruppe.
- **Was der Scan sonst sieht:** ein ausstehender Neustart, OpenClaw/Node.

Ohne `sudo` läuft es auch und sagt, was es dann nicht prüfen konnte.

Erwartet vor dem Nachscan: kein `FAIL`; als `WARN` nur `python3-urllib3`
(solange Canonical nichts liefert) — nach Rückbau und Neustart sonst nichts.

## In dieser Reihenfolge

1. H6-Rückbau (#802, Schritt 6) — OpenClaw und Node vom Host.
2. `sudo apt update && sudo apt upgrade`.
3. Neustart im Wartungsfenster; danach `systemctl --failed` leer und
   `ls /home/dh/gwdg | head -3` zeigt die Freigabe (der Automount muss nach dem
   Neustart wieder greifen, siehe #792, 05.11.).
4. `sudo bash deploy/tei/check_rapid7.sh` — kein `FAIL`.
5. Nachscan bei den Informatikdiensten anfragen (Text unten).
6. Den neuen Bericht an #792 hängen (als Zusammenfassung; das PDF nicht ins
   Repo). Was er noch findet, bekommt ein eigenes Issue; ohne kritische Funde
   #795 und #792 schliessen.

## Anfrage an die Informatikdienste

> Betreff: Rapid7-Nachscan für tei (Bericht vom 02.11.2026)
>
> Guten Tag
>
> Ihr Rapid7-Bericht vom 02.11.2026 für unseren Server tei (DH Center) nannte
> 20 kritische und 69 schwere Schwachstellen. Wir haben sie inzwischen
> bearbeitet:
>
> - Der nicht mehr gebrauchte TEI-Publisher-Container ist mit seinen Images
>   und seinem Export entfernt. Damit sind OpenJDK (80 CVEs), Log4j Core
>   2.19.0 und Spring 2.5.6 vom Host.
> - Ubuntu Pro ist angehängt (`esm-apps`, `esm-infra`, Livepatch); die
>   Ubuntu-Pakete sind aktualisiert oder entfernt. Vier der gemeldeten Pakete
>   (`libass9`, `libmbedcrypto7t64`, `libcjson1`, `libzvbi0t64`) waren nie
>   installiert, sondern lagen im entfernten Container-Export.
> - Für `python3-urllib3` (CVE-2025-66418, -66471) gibt es auch in `esm-apps`
>   noch keine korrigierte Version; wir spielen sie ein, sobald Canonical sie
>   liefert.
>
> Könnten Sie tei bitte erneut scannen und uns den Bericht schicken?
>
> Freundliche Grüsse

Die IP-Adresse und den Hostnamen aus dem Bericht ergänzen; sie stehen bewusst
nicht hier.
