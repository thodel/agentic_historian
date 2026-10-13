# Fragen an den Korpus-Graphen (KG-5, #331)

Ein nackter SPARQL-Endpoint nützt einer Historikerin nichts, die kein SPARQL
schreibt — und der Sinn dieses Graphen ist, dass er Fragen beantwortet, die
keine einzelne Quelle beantworten kann. Die Oberfläche ist deshalb eine kleine
Menge **benannter Abfragen**, jede eine echte Forschungsfrage, jede gegen einen
Fixture-Graphen getestet.

    /sparql                                   → die Liste der Fragen
    /sparql frage:<name> [argument:<wert>]    → eine davon ausführen
    /sparql frage:<name> zeige_sparql:true    → die SPARQL-Abfrage zum Anpassen

Der Code liegt in `agentic_historian/knowledge_hub/queries.py`, die Tests in
`agentic_historian/tests/test_kg_5_sparql_queries.py`.

## Die vier Fragen

| Name | Frage | Argument |
|---|---|---|
| `persons-in-care-documents` | Welche Personen kommen in care-relevanten Dokumenten eines Jahrhunderts vor — mit ihren GND-/HLS-Ids? | Jahrhundert, z. B. `15` |
| `readings-of-page` | Welche Lesungen hat eine Seite, von welchem Modell — und welche hat ein Historiker als die nächste gewählt? | Dokument-Id |
| `authority-coverage` | Welche Normdatenquelle liefert die meisten Verknüpfungen — und welche Dokumente haben unverknüpfte Entitäten? | — |
| `co-occurrence` | Welche Personen werden im selben Dokument genannt — über das ganze Korpus? | — |

## Vier Regeln, jede weil die naheliegende Variante scheitert

**Jede Zeile trägt ihr Dokument.** Keine Konvention, sondern eine Prüfung im
Konstruktor: eine `CannedQuery`, deren Spalten kein `document` enthalten, lässt
sich nicht bauen. Eine Antwort, die eine Historikerin nicht auf eine Quelle
zurückführen kann, ist in der Wissenschaft nicht verwendbar — und eine
Canned-Query-Oberfläche macht es sehr leicht, ein plausibles Aggregat ohne
Belege zu veröffentlichen.

Diese Regel hat die Korpus-Gesundheitsfrage in eine bessere Form gebogen:
*welche Dokumente* unverknüpfte Personen haben, statt „47 Entitäten sind
unverknüpft“. Das erste kann man abarbeiten, das zweite nicht.

**Parameter werden gebunden, nicht interpoliert.** Ein Jahrhundert oder eine
Dokument-Id geht als RDF-Term über `initBindings` hinein; kein Text eines
Aufrufers wird je zu Abfrage-Syntax. Die eigenständige Form (`as_text`, zum
Einfügen in den öffentlichen Endpoint) rendert dieselben Terme über `n3()` —
rdflibs eigenes Escaping, denn ein Anführungszeichen in einer Id darf kein
Literal beenden.

**Die Präfixe kommen aus dem Exporter.** `rdf_export.PREFIXES` ist die einzige
Quelle, also kann eine Abfrage kein Präfix verwenden, mit dem der Graph nicht
geschrieben wurde. Dasselbe für die Normdaten-Basen: welche URI zu welcher
Quelle gehört, steht in `entity_index._URI_TEMPLATES` und wird von dort gelesen,
nicht ein zweites Mal hingeschrieben. (Genau das war vorher der Fall — und beide
Kopien waren falsch, siehe unten.)

**Kein Treffer ist eine Antwort.** Sie wird als Satz berichtet, der die Frage
und die Parameter nennt — nie als leere Nachricht und nie als Absturz. „Nichts
gefunden“ und „nichts gefunden *für das 13. Jahrhundert*“ schicken eine
Leserin zu verschiedenen nächsten Schritten.

## Zwei Dokument-Fakten, die der Graph nicht trug

Die erste Frage braucht den Care-Flag und die Datierung; KG-2 emittierte
keinen von beiden. Beide stehen in Agent Bs Beschreibung, und beide sind
**Urteile**, also werden sie mit dem emittiert, was sie qualifiziert.

### Datierung und Jahrhundert sind zwei Prädikate

```turtle
ah:document/x sdhss:dating          "um 1450"@de ;
              sdhss:datingUncertain true ;
              sdhss:century         15 ;
              sdhss:centuryUncertain true .
```

`sdhss:dating` ist, was die Quelle (oder Agent B) sagt; `sdhss:century` ist
eine Zahl, die dieser Code daraus abgeleitet hat. Sie zusammenzulegen würde
verbergen, dass der Filter, den eine Historikerin benutzt, unsere Arithmetik
ist und nicht die Aussage des Dokuments.

`century_of` liest, was sich lesen lässt, und **verweigert sonst**:

| Datierung | Jahrhundert | Warum |
|---|---|---|
| `15. Jahrhundert`, `15. Jh.`, `Mitte 15. Jhdt` | 15 | explizit genannt |
| `Schrift des 16. Jahrhunderts` | 16 | der deutsche Genitiv — die häufigste Form |
| `1431` | 15 | Arithmetik, keine Interpretation |
| `1500` / `1501` | 15 / 16 | die Jahrhundertgrenze |
| `um 1450`, `zwischen 1430 und 1440` | 15 | eine Spanne **innerhalb** eines Jahrhunderts; die Unsicherheit trägt `datingUncertain` |
| `XV. Jahrhundert`, `saec. XVI` | 15 / 16 | römisch, aber nur mit dem Wort, das es zur Datierung macht |
| `14./15. Jh.`, `1498 und 1502` | **keins** | zwei Jahrhunderte — eines auszuwählen wäre eine editorische Entscheidung, getroffen von einem regulären Ausdruck |
| `keine Angabe`, `xv` | **keins** | nichts zu lesen |

Ein geratenes Jahrhundert würde ein Dokument auf die Kraft einer Regex hin in
das Ergebnis einer Historikerin setzen. Der einzige sichere Fehlschlag ist
*keine Zahl*.

### Der Care-Flag ist dreiwertig (seit #631)

```turtle
ah:document/a sdhss:careClassified true  ; sdhss:careRelated true ;
              sdhss:careType "Almosen"@de .   # geprüft, care-relevant
ah:document/b sdhss:careClassified true  ; sdhss:careRelated false .
                                              # geprüft, nicht care-relevant
ah:document/c sdhss:careClassified false .    # nie geprüft
```

`careClassified` wird ausdrücklich emittiert und nicht aus einem fehlenden
`careRelated` erschlossen: Abwesenheit in einem Graphen ist mehrdeutig — sie
könnte genauso einen älteren Build bedeuten —, und „dieses Fürsorgedokument
wurde nie geprüft“ ist eine Tatsache, auf die eine Gesundheitsabfrage selektieren
können muss.

Bis #631 konnte der Record das Negativ nicht tragen: Agent Bs Fehlerpfad
speicherte dasselbe `is_care_related: False` wie eine echte Negativ-Antwort, also
wurde hier nur das Positiv emittiert. `care_flag.read()` löst beide Jahrgänge
auf — ein `False` von vor #631 liest sich weiterhin als *ungeprüft*, denn das war
es. Siehe #631 und `agentic_historian/care_flag.py`.

Nicht emittiert werden:

- `care_context` — die freie Prosa eines LLM darüber, worum es in einem
  Fürsorgefall ging. Care-geflaggte Dokumente zu veröffentlichen (vom Owner
  genehmigt, `corpus_build`, 30.07.2026) ist nicht dasselbe, wie diese Prosa in
  einen offenen Endpoint zu stellen.
- `beteiligte` — Namen, die ein LLM aus dem Care-Kontext gelesen hat, nicht die
  NER-Ausgabe, aus der alle anderen Personenknoten des Graphen stammen. Sie als
  Entitäten zu publizieren wäre eine Erfindung.
- der **Grund**, warum eine Klassifikation fehlschlug — der kann einen
  Modellnamen oder eine interne URL enthalten und gehört in den Record und ins
  Log, nicht in einen offenen Endpoint.

Die `care_types` eines Negativs wandern ebenfalls nicht mit: ein Modell, das
„nein“ antwortet und trotzdem Care-Typen füllt, hat sich selbst widersprochen,
und der Flag ist die Antwort.

## Eine Präfix-Drift, gefunden beim Schreiben der Abfragen

`entity_index._URI_TEMPLATES` ist die Stelle, an der eine öffentliche
Normdaten-URI entsteht. Zwei der drei in `rdf_export` deklarierten Präfixe
stimmten damit nicht überein:

| Quelle | deklariertes Präfix | tatsächlich erzeugte URI |
|---|---|---|
| gnd | `https://d-nb.info/gnd/` | gleich ✓ |
| hls | `https://www.hls-dhs-dss.ch/articles/` | `https://hls-dhs-dss.ch/de/articles/` |
| wikidata | `https://www.wikidata.org/entity/` | `https://www.wikidata.org/wiki/` |

Das `hls:`-Präfix kürzte also in jedem veröffentlichten Turtle **nichts** ab und
sagte einem Konsumenten, HLS-Links sähen aus, wie sie nicht aussehen — es ist
jetzt auf die wirkliche Basis gesetzt, und ein Test hält es dort.

Bei Wikidata sind beide Formen legitim: `/entity/Q42` ist die kanonische
RDF-Identität (so mintet der Hub-Export), `/wiki/Q42` die Seite, die ein Mensch
anklickt (so mintet `entity_index`). Der Graph enthält deshalb beide, und die
Gesundheitsabfrage zählt beide als `wikidata` — ein Klassifikator, der nur eine
kennt, legt die Hälfte der Wikidata-Links unter „other“ ab, und so fängt eine
Korpus-Gesundheitszahl an, in Richtung „unverknüpft“ zu lügen.

Dass derselbe Wikidata-Eintrag im Graphen unter zwei URIs ohne `owl:sameAs`
zwischen ihnen stehen kann, ist eine echte Modell-Unstimmigkeit — aber eine,
deren Behebung veröffentlichte Identifikatoren ändern würde, und damit nicht
Sache dieses Issues.

## Welcher Graph gefragt wird

`load_graph()` nimmt `build/corpus.ttl`, wenn es existiert — das ist das
Artefakt, das der öffentliche Endpoint ausliefert, also beantwortet eine
Antwort daraus dasselbe, was der Endpoint beantworten würde. Sonst wird der
Graph aus den Pipeline-Records gebaut, und die Antwort **sagt das**: eine
Antwort aus einem anderen Graphen als dem ausgelieferten darf nicht genauso
aussehen wie eine aus ihm.

Jede Antwort nennt Quelle, Tripelzahl und Laufzeit. Der Cache ist auf
(Pfad, mtime, Größe) geschlüsselt, also kann ein frischer Build nicht aus dem
Cache bedient werden — eine veraltete Antwort, die aktuell aussieht, ist der
einzige Cache-Fehler, der hier zählt.

Für den öffentlichen Endpoint selbst siehe `deploy/qlever/README.md`.
