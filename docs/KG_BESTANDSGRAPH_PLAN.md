# Bestandsgraph: Dodis, SSRQ und Königsfelden in QLever — Umsetzungsplan

Stand 14.10.2026 · Epic #325, Phase 2 · Folgeepic für e-periodica: thodel/ch-h-bot#527

## 1. Ausgangslage, gemessen

Phase 1 des Epics (KG-1 bis KG-5, Juli–Oktober 2026) hat die Maschinerie gebaut: CIDOC-CRM-7.1-Export mit PROV-O (`knowledge_hub/rdf_export.py`), deterministischer Bau mit Manifest (`corpus_build.py`), Rebuild-and-Swap (`deploy/qlever/refresh.sh`), vier benannte SPARQL-Fragen (`queries.py`). Nachgemessen am 14.10.2026:

| | Plan | Stand |
|---|---|---|
| QLever-Endpunkt auf tei | «answers queries over the corpus graph» (#330) | kein Behälter, nginx-Snippet nie eingebunden, `/sparql/` → 404 |
| Graph | Korpusdokumente, Lesungen, Erwähnungen | Bau vom 30.07.2026: 2 835 Tripel aus 15 Dokumenten, `authority_links: {}` |
| Rohdaten `data/outputs` | das Korpus | 22 Pipeline-Dateien, 343 Entitäten, Dateinamen wie `votes-test`, `verify-final` |
| Normdatenregister (Plan Juni 2026, AH-80) | HLS-KNEX + HBLS | `hub.json` ist 2 KB — nie gebaut |

Die Maschinerie ist in Ordnung; der Gegenstand war zu klein, und die Verknüpfungsschicht hatte nichts, wogegen sie verknüpfen konnte.

## 2. Der neue Gegenstand: drei Bestände, in denen die Kanten schon liegen

| | Dodis | Königsfelden | SSRQ |
|---|---|---|---|
| Quelle | `dodis.db` (idhefix `/home/tobias/mcp/data/`, 418 MB, Open-Data-Export 2024) | `kf.db` (idhefix, 54 MB, 30.09.2026) | Register-Dump `ssrq__fuseki_042810.ttl` (tei `/home/dh/resources/`, 75 MB, 28.06.2026) + TEI `SSRQ-SDS-FDS/editio-data` (GitHub, 124 MB, 26.03.2024) |
| Dokumente | 48 001 (47 236 datiert) | 1 557 (1 326 datiert) | 4 624 (4 604 datiert) |
| Personen | 64 352 — ohne GND/Wikidata; URI `dodis.ch/P<id>` | 5 260 — **0 mit HLS-Id**, 512 mit Lebensdaten, 5 257 mit Familie | 27 996 — 27 644 datiert, 1 846 Ehen, 1 278 Eltern, 27 745 Familie; URI `ssrq-sds-fds.ch/Register/#per…`; 817 GND-, 2 876 HLS-Verweise im Dump |
| Orte | 12 326 — 12 110 geo, 3 217 Wikidata | 1 333 — 237 HLS, 170 GND, 384 geo | 2 322 loc-Ids referenziert, keine Ortstabelle |
| Gruppen/Themen | 1 379 Themen | 2 270 Organisationen | 9 134 Gruppen (7 583 Familien) |
| Dokument → Entität | 341 553 Personen in fünf Rollen (mention 251 883 · signatory 31 791 · author 28 272 · adressee 22 663 · address_of_copy 6 944); 372 122 Orte; 129 123 Themen | 32 995 Spans mit Ziel (21 988 Personen, 6 580 Orte, 4 427 Org.); 59 Ziele ohne Datensatz; 1 570 Personennennungen ohne Ziel | **keine in unserer SQLite** (`ingest_editio.py` streift Tags). In den TEI vorhanden: Probedatei 14 `persName ref`, 9 `placeName ref`; Suffixe (`per010051a.01`) sind TEI-lokal, der Stamm existiert im Register |

Grössenordnung: 2–3 Mio. Tripel, davon ~850 000 Dokument–Entität-Kanten aus Dodis.

Zwei Befunde nebenbei, die eigene Issues in den Provider-Repos verdienen: die KF-MCP-Beschreibung verspricht HLS-Ids an Personen, die Tabelle hat keine; `ssrq.related_persons` liefert für Orte `{"id": "[]"}`.

## 3. Entscheidungen

1. **QLever läuft auf idhefix, nicht auf tei.** tei: 3,9 GB RAM, 280 MB frei, 1 Kern, und der Bot teilt sich den Speicher. idhefix: 257 GB RAM, 48 Kerne, 665 GB frei, alle drei Datenbanken vor Ort. Öffentlich wird `https://tei.dh.unibe.ch/sparql/` über das bestehende Tunnelmuster (`ssh -L 127.0.0.1:800x:172.17.0.1:800x tobias@130.92.59.240`, heute für die MCP-Ports 8000–8004) und `deploy/qlever/nginx-qlever.conf`.
2. **Quelle der Wahrheit je Bestand:** Dodis und KF direkt aus SQLite (deterministisch, nicht über die MCP-Schnittstelle — `dodis.get_document` liefert Namen ohne Ids). SSRQ aus dem Register-Dump (schon RDF, mit GND/HLS) und aus den TEI für die Erwähnungen.
3. **Modell:** CIDOC-CRM 7.1 + PROV-O auf dem bestehenden Exporter. E31 Dokument; E21 Person; E53 Ort mit `geo:lat/long`; E74 Gruppe (Familien, Organisationen), E40 wo Rechtsperson; E55 Typ für Dodis-Themen (`P129_is_about`). Erwähnung `P67_refers_to` **vom Dokument** — es gibt hier keine Lesung. Autorschaft E65 Creation / `P14_carried_out_by`; Eltern `P152_has_parent`; Familie `P107_has_current_or_former_member`. Was CRM nicht hat (Ehe, Unterzeichner-/Adressaten-Rolle), steht im `sdhss:`-Namensraum und wird in der Mapping-Tabelle als solches benannt.
4. **Valide heisst prüfbar:** ein Test gegen das offizielle CRM-RDFS (Klasse/Property existiert, Domain/Range passt) und SHACL-Shapes für unser Profil, als CI-Tor; mit Gegenprobe, dass ein falscher Range die Prüfung scheitern lässt.
5. **Provenienz je Knoten:** `prov:wasDerivedFrom` dem versionierten Datensatz, jeder Bau eine `prov:Activity` mit Commit und Zeit. Keine Identität über Bestände hinweg ausser der, die die Quelle liefert (Wikidata an Dodis-Orten, HLS/GND an KF-Orten und im SSRQ-Register). Nichts wird erfunden; fehlende Ziele erscheinen als `withheld` im Manifest.
6. **«Link, don't copy»** aus #325 bleibt gegenüber den Normdatenbanken; gegenüber den drei Beständen wird der Graph aus ihren Datensätzen gebaut — der Graph sagt, was welche Quelle behauptet, nicht, was wahr ist.

## 4. Schritte

| Welle | Issue | Inhalt | Abnahme |
|---|---|---|---|
| 1 | #650 KG-6 | Mapping-Tabelle, CRM-RDFS-Prüfung, SHACL, Fixture je Bestand | Fixture aus drei Beständen besteht; absichtlich falscher Range scheitert |
| 2 | #651 KG-7 | Dodis-Export | Zählungen = SQL (48 001 / 64 352 / 12 326 / 1 379; 341 553 / 372 122 / 129 123) |
| 2 | #652 KG-8 | Königsfelden-Export | 1 557 E31, 32 995 P67, `dangling_refs == 59` |
| 2 | #653 KG-9 | SSRQ: Register-RDF + TEI-Parser für Erwähnungen | ≥ 817 GND-/2 876 HLS-Links; Erwähnungszahl gemessen |
| 3 | #330 KG-4 (wieder offen) | Betrieb auf idhefix, Tunnel + nginx auf tei, Refresh-Timer | `/sparql/` antwortet mit der Tripelzahl aus dem Manifest |
| 3 | #654 KG-11 | vier Fragen umgestellt, drei neue | `persons-of-century-across-sources`: 14. Jh. → KF+SSRQ, 20. Jh. → Dodis |
| 4 | ch-h-bot#527 | e-periodica mit LLM-NER (N-1 bis N-7, #528–#534) | Übernahme in N-7 (#534) |

Reihenfolge innerhalb Welle 2: KG-7 zuerst (grösster Bestand, einfachstes Schema), dann KG-8, dann KG-9 (TEI-Parser ist der aufwändigste Teil).

## 5. Phase 2 danach: e-periodica (ch-h-bot#527)

68 552 Artikel, 655 930 Seiten, 1,66 Mrd. Zeichen OCR, 36 Zeitschriften, de/fr/it. Verfahren nach NER4all (Dröge u. a., arXiv 2502.04351): reines Prompting, Rückgabe des Textes mit Inline-Tags, fuzzy Verankerung, PER/ORG/LOC. Der Befund, der die Reihenfolge bestimmt: Kontext im Prompt bringt F1 0,81 → 0,88, Beispiele und Tricks 1–2 Punkte. Goldstandard zuerst (N-1), Messung der Kosten auf asteraix (N-4) entscheidet Vollkorpus oder Auswahl; Messläufe nie am Produktions-Gateway. Kein Volltext im Graphen — nur Offsets und Oberflächenform (Rechte: ch-h-bot#424).

## 6. Risiken

- **Dodis-Personen ohne Normdaten** (64 352): bleiben lokale Knoten mit dodis.ch-URI; eine Verknüpfung mit HLS/Wikidata ist Arbeit nach Phase 2, nicht Teil von ihr.
- **SSRQ-Orte ohne Namen**: 2 322 loc-Ids, keine Tabelle; KG-9 prüft zuerst, ob SSRQ einen Orts-Dump hat, sonst E53 mit URI und ohne Label.
- **tei als Proxy-Einzelpunkt**: fällt der Tunnel, ist `/sparql/` weg, der Graph auf idhefix bleibt. Der Tunnel läuft wie die MCP-Tunnel als dh-Prozess ohne Unit — ein Unit-File ist Teil von KG-4.
- **Bau-Dauer**: 2–3 Mio. Tripel in rdflib sind Minuten; QLever-Index ebenfalls. Die Zahl steht nach KG-7 im Manifest, nicht hier.
