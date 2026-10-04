# Fehlende Bilder im Laßberg-Share, Stand 4. Oktober 2026

35 Seiten der Laßberg-Korrespondenz haben in Transkribus handkorrigierten Text
(Status DONE oder FINAL), aber auf dem GWDG-Nextcloud-Share
(`…/s/FaGXMmkkoY23eaA`, Ordner `digitalisate`) kein Bild mehr. Geprüft mit einer
frisch gelesenen Dateiliste, nicht aus dem Cache: der Share enthält aktuell
**6722 Dateien, 73,98 GB**, am 2. Oktober waren es 6742.

Betroffen sind zwei Ordner, die **als Ganzes** nicht mehr existieren — beide
antworten auf jeden WebDAV-Endpoint mit 404, nicht nur einzelne Dateien in ihnen:

- `digitalisate/Briefe UB Freiburg/` (28 Seiten)
- `digitalisate/Donaueschingen/Photos-1-001/` (7 Seiten)

---

## `digitalisate/Briefe UB Freiburg/` — 28 Seiten

Dateiendung `.tif`, belegt durch die Abrufversuche des Laufs
(`…/lassberg-letter-0077/001.tif` → 404).

| Brief | fehlende Seiten |
|---|---|
| `lassberg-letter-0000-1808-08-31` | 001, 002, 003, 004, 006, 007, 008 |
| `lassberg-letter-0000-1814-04-30` | 001, 002 |
| `lassberg-letter-0071` | 001, 002, 003 |
| `lassberg-letter-0073` | 003, 004, 005, 007 |
| `lassberg-letter-0076` | 001, 002, 003, 004 |
| `lassberg-letter-0077` | 001, 002, 003, 004, 005, 006, 007, 008 |

Lücken in der Numerierung (0000-1808-08-31 ohne 005, 0073 ohne 001/002/006)
bedeuten nicht, dass diese Seiten noch vorhanden sind. Aufgeführt sind nur die
Seiten, für die wir Ground Truth haben; der Ordner fehlt vollständig.

## `digitalisate/Donaueschingen/Photos-1-001/` — 7 Seiten

Handyfotos vom 11. August 2025. Dateiendung nicht gesichert (wahrscheinlich
`.jpg`), weil der Ordner verschwunden ist, bevor ein Abruf sie benannt hat.

| Brief | fehlende Seiten |
|---|---|
| `lassberg-letter-1247` | `PXL_20250811_113538665`, `PXL_20250811_113553532` |
| `lassberg-letter-1937` | `PXL_20250811_113612699`, `PXL_20250811_113628159` |
| `lassberg-letter-2079` | `PXL_20250811_113647995` |
| `lassberg-letter-2212` | `PXL_20250811_113720213.PORTRAIT.ORIGINAL`, `PXL_20250811_113735296.PORTRAIT.ORIGINAL` |

---

## Die 35 Seitenschlüssel, wie die Pipeline sie führt

Pfad relativ zu `digitalisate`, Trenner zu `__` gefaltet, Endung entfernt.

```
Briefe UB Freiburg__lassberg-letter-0000-1808-08-31__001
Briefe UB Freiburg__lassberg-letter-0000-1808-08-31__002
Briefe UB Freiburg__lassberg-letter-0000-1808-08-31__003
Briefe UB Freiburg__lassberg-letter-0000-1808-08-31__004
Briefe UB Freiburg__lassberg-letter-0000-1808-08-31__006
Briefe UB Freiburg__lassberg-letter-0000-1808-08-31__007
Briefe UB Freiburg__lassberg-letter-0000-1808-08-31__008
Briefe UB Freiburg__lassberg-letter-0000-1814-04-30__001
Briefe UB Freiburg__lassberg-letter-0000-1814-04-30__002
Briefe UB Freiburg__lassberg-letter-0071__001
Briefe UB Freiburg__lassberg-letter-0071__002
Briefe UB Freiburg__lassberg-letter-0071__003
Briefe UB Freiburg__lassberg-letter-0073__003
Briefe UB Freiburg__lassberg-letter-0073__004
Briefe UB Freiburg__lassberg-letter-0073__005
Briefe UB Freiburg__lassberg-letter-0073__007
Briefe UB Freiburg__lassberg-letter-0076__001
Briefe UB Freiburg__lassberg-letter-0076__002
Briefe UB Freiburg__lassberg-letter-0076__003
Briefe UB Freiburg__lassberg-letter-0076__004
Briefe UB Freiburg__lassberg-letter-0077__001
Briefe UB Freiburg__lassberg-letter-0077__002
Briefe UB Freiburg__lassberg-letter-0077__003
Briefe UB Freiburg__lassberg-letter-0077__004
Briefe UB Freiburg__lassberg-letter-0077__005
Briefe UB Freiburg__lassberg-letter-0077__006
Briefe UB Freiburg__lassberg-letter-0077__007
Briefe UB Freiburg__lassberg-letter-0077__008
Donaueschingen__Photos-1-001__lassberg-letter-1247__PXL_20250811_113538665
Donaueschingen__Photos-1-001__lassberg-letter-1247__PXL_20250811_113553532
Donaueschingen__Photos-1-001__lassberg-letter-1937__PXL_20250811_113612699
Donaueschingen__Photos-1-001__lassberg-letter-1937__PXL_20250811_113628159
Donaueschingen__Photos-1-001__lassberg-letter-2079__PXL_20250811_113647995
Donaueschingen__Photos-1-001__lassberg-letter-2212__PXL_20250811_113720213.PORTRAIT.ORIGINAL
Donaueschingen__Photos-1-001__lassberg-letter-2212__PXL_20250811_113735296.PORTRAIT.ORIGINAL
```

---

## Eine Spur, die nachzugehen lohnt

Am 2. Oktober meldete derselbe Lauf 13 PDFs im Share, darunter vier, die genau
die Briefnummern tragen, die jetzt unter `Briefe UB Freiburg` fehlen:

```
digitalisate/Donaueschingen/lassberg-letter-0071.pdf
digitalisate/Donaueschingen/lassberg-letter-0073.pdf
digitalisate/Donaueschingen/lassberg-letter-0076.pdf
digitalisate/Donaueschingen/lassberg-letter-0077.pdf
```

Heute meldet er **9** PDFs. 13 − 4 = 9 passt dazu, dass genau diese vier
verschwunden sind — bewiesen ist es nicht, weil die heutige Meldung nur eines der
neun namentlich nennt. Falls es zutrifft, sind dieselben vier Briefe in *beiden*
Formen vom Share genommen worden, als TIFF-Ordner und als PDF.

## Was das heisst

- Der Modellvergleich läuft über **241 statt 276** Seiten. Die Zahl ist nicht
  vergleichbar mit den 276 aus dem Bericht vom 3. Oktober und muss überall, wo
  sie auftaucht, als 241 benannt werden.
- Der Hugging-Face-Datensatz kann diese 35 Seiten nicht enthalten: er besteht aus
  Bild plus handkorrigiertem Text, und das Bild fehlt.
- Die Texte sind **nicht** verloren. Sie liegen unberührt in der
  Transkribus-Sammlung; es fehlen ausschliesslich die Digitalisate.

## Die Frage an die Share-Verwaltung

Wurden die beiden Ordner gelöscht, verschoben oder umbenannt? Bei verschoben oder
umbenannt sind die 35 Seiten mit einer korrigierten Pfadliste sofort wieder
messbar und publizierbar — die handkorrigierten Texte warten. Bei gelöscht
brauchen wir zu wissen, ob eine Sicherung existiert.
