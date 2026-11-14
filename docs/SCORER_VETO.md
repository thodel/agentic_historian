# Das Scorer-Veto in der Fusion (#491 L3)

Ein Scorer ist ein Zeichen-n-Gramm je Zelle `(lang, region, document_type,
century_band)`, trainiert auf dem Trainingshost **nur** aus Editionstext und
korrigierter Ground Truth (training-atr-models#110, `atr_training.scorer`), nie
aus einer Engine-Ausgabe. Er bewertet, sonst nichts: `bits_per_char(text)` und
der z-Score gegen die Held-out-Verteilung seines eigenen Editionstexts.

Der Vorversuch in #491 hat die naive Verwendung widerlegt: das n-Gramm gab dem
Fusionsergebnis, das #298 als *schlechter als die beste Einzellesung*
diagnostiziert, die **beste** Bewertung. Lokale Flüssigkeit ist nicht
Korrektheit. Deshalb ist der Scorer in der Fusion ein **Veto, kein Ranking**:
er wirft den Müllrand aus der Abstimmung, er wählt nicht die beste Lesung.

## Die Regel

In `fusion.fuse` werden, wenn ein Scorer übergeben ist, alle brauchbaren
Kandidaten einer Seite unter **demselben** Scorer bewertet. Wer mehr als
`FUSION_VETO_MARGIN` bits/char über dem **besten Kandidaten dieser Seite**
liegt, nimmt an nichts teil, was folgt: nicht an der Ausrichtung, nicht an der
Abstimmung, nicht an der No-Merge-Auswahl (#300). Der beste Kandidat kann nie
vetoed werden, also wird eine Seite nie leer; bleibt einer übrig, kommt er
wörtlich zurück mit der Quelle `veto`.

Warum ein Abstand zum Besten und keine absolute Schwelle: bits/char zweier
Zellen sind zwei Skalen (#491, «Was dabei sicher schiefginge»). Innerhalb
einer Seite unter einem Scorer sind die Zahlen vergleichbar, über Zellen
hinweg nicht.

Auf dem Trace aus #298 (vier Lesungen derselben Zeile, 16 k Zeichen
Königsfelden-Urkundendeutsch, Ordnung 5):

| Lesung | bits/char | Abstand zum Besten |
|---|---:|---:|
| trocr-kurrent-xvi-xvii (die gute Lesung) | 5.23 | — |
| vlm/internvl3-8b-instruct | 5.57 | +0.34 |
| kraken-catmus_medieval | 6.92 | +1.69 |
| kraken-mccatmus | 8.46 | +3.23 |

Unter dem **echten** Scorer (generische `de`-Zelle, 2.4 Mio. Zeichen, tei
14.11.2026) liegen dieselben vier Lesungen so:

| Lesung | bits/char | Abstand zum Besten |
|---|---:|---:|
| trocr-kurrent-xvi-xvii | 4.85 | — |
| vlm/internvl3-8b-instruct | 5.22 | +0.37 |
| kraken-catmus_medieval | 7.01 | +2.16 |
| kraken-mccatmus | 8.16 | +3.31 |

Der Müllrand ist breit, das plausible Band schmal. Mit `FUSION_VETO_MARGIN =
2.5` fällt kraken-mccatmus; kraken-catmus bleibt 0.34 unter der Marge und geht
mit TrOCR und dem VLM in die No-Merge-Bande (#300), die bei 50 % paarweiser CER
nicht abstimmt, sondern die konfidenteste Lesung wörtlich nimmt. TrOCR steht.
Eine Marge von 2.0 hätte beide kraken-Lesungen genommen — und auf dem
L2-Material auf 1.7 % statt 0.9 % der Zeilen die beste Lesung (Tabelle unten).
Die Marge ist nach dem gemessenen Material gesetzt, nicht nach dieser einen
Zeile.

## Die Schwelle, gemessen (14.11.2026)

`calibrate_veto_margin.py` (training-atr-models) auf dem L2-Material: 231
Zeilen des XIX-Validierungsdraws, gelesen von acht VLM-XIX-Armen, unter der
generischen `de`-Zelle (2.4 Mio. Zeichen Editionstext; die XIX-Zelle gibt
dieselben Zahlen auf ±1). Plausibel = CER < 0.3, Müll = CER ≥ 0.5; «fatal» =
die Lesung mit der kleinsten CER der Zeile wurde vetoed.

| Marge | vetoed | plausibel verloren | Müll entfernt | fatale Zeilen |
|---:|---:|---:|---:|---:|
| 1.0 | 12.4 % | 6.8 % | 45 % | 6.9 % |
| 1.5 | 5.3 % | 2.0 % | 25 % | 3.5 % |
| 2.0 | 3.1 % | 0.9 % | 19 % | 1.7 % |
| **2.5** | 2.0 % | **0.6 %** | **12 %** | **0.9 %** |
| 3.0 | 1.8 % | 0.5 % | 11 % | 0.9 % |
| 4.0 | 0.6 % | 0.1 % | 4 % | 0.0 % |

Gewählt ist **2.5**: die kleinste Marge, die unter 5 % plausiblen Verlust und
unter 1 % fatalen Zeilen bleibt. Das vorläufige 1.5 hätte auf 3.5 % der Zeilen
die beste Lesung getroffen — der Fehler von #298 in die andere Richtung.

Was die Tabelle nicht zeigt: die acht Arme sind **ein** Modell mit kleinen
Unterschieden. «Müll» ist hier dessen schwere Zeilen, nicht die Ausgabe einer
fremden Engine auf einer Schrift, die sie nicht kennt (#298: kraken 1.7 und 3.2
bits/char über TrOCR). Gegen solchen Müll greift die Marge weiter; die 12 %
sind ein Boden, die 0.6 % Verlust die gemessenen Kosten. Dazu die Vorbehalte
von L2: eigener Draw, Scorer kennt andere Seiten derselben Hände.

## Was aufgezeichnet wird

`FusionResult.vetoed` nennt je vetoed Kandidat Label, bits/char, z und die
Zelle, die geantwortet hat. `FusionResult.scores` trägt die Zahlen **aller**
Kandidaten, vetoed oder nicht — die Aufzeichnung, auf der L2 die Schwelle
kalibriert, sobald Scorer auf tei liegen. Beides steht in `a_meta` der
Pipeline (`fusion_vetoed`, `fusion_scores`). Keine dieser Zahlen heisst
«Qualität» oder «CER» (#326, #336): ein halluzinierendes VLM, das flüssiges
Frühneuhochdeutsch erfindet, bekommt eine gute Zahl.

## Schalter und Dateien

| Einstellung | Default | Bedeutung |
|---|---|---|
| `FUSION_SCORER_VETO` | `false` | Das Veto ist aus, bis L2 (Kendall τ gegen die Bundesratsprotokolle, asteraix) die Schwelle gemessen hat. |
| `FUSION_SCORER_DIR` | `data/scorers` | Verzeichnis mit `*.scorer.json`, je Zelle eine Datei, wie `CharNgramScorer.save` sie schreibt. |
| `FUSION_VETO_MARGIN` | `2.5` | bits/char über dem Besten der Seite; kalibriert 14.11.2026, Tabelle oben. |

Die Zelle einer Seite kommt aus Agent B, wie der Modellselektor sie liest
(`lang`, `region`, `document_type`, `century`), mit den Pins der Historikerin
aus dem Run-State obenauf. Gibt es für die Zelle keinen Scorer, fällt die Suche
zurück wie der Modellselektor (Jahrhundertband → Gattung → Region → Sprache),
und die Antwort nennt die Zelle, die geantwortet hat. Ein Scorer, der das
Dokument der Seite im Training hatte, bewertet es nicht (#23); dann gibt es für
diese Seite kein Veto, und sie wird fusioniert wie bisher.

## Zwei Implementierungen derselben Formel

`agentic_historian/scorer_reader.py` liest das Artefakt und rechnet die Formel
nach — bewusst nicht das Trainingspaket im venv auf tei (Entscheidung
12.11.2026). Dass beide Seiten dasselbe rechnen, hält
`tests/fixtures/scorer/`: ein Artefakt, das der Trainingscode geschrieben hat
(`make_fixture.py`), und `expected.json` mit seinen Zahlen für die vier
Trace-Lesungen; `test_ah_491_scorer_veto.py` verlangt sie auf 1e-12 genau.
Ändert die Trainingsseite die Formel, wird das Fixture neu erzeugt und der Test
sagt, wenn der Leser nicht mitgezogen hat.

## Was gemessen ist, und was noch fehlt (Stand 14.11.2026)

- **L1:** XIX-Zelle (2.26 Mio. Zeichen, Held-out 1.73 ± 0.25 bits/char) und
  generische `de`-Zelle (2.42 Mio., 1.87 ± 0.76) aus dem Validierungsdraw der
  VLM-XIX-Jobs, Seite als Ausschlusseinheit (#23). Die Lernkurve ist bei 2.3
  Mio. Zeichen noch nicht flach.
- **L2:** τ zwischen bits/char und Zeilen-CER, acht Arme: plausibles Band
  0.21 … 0.44 (Median ≈ 0.37); Paare über Arme wie CER geordnet 0.57. Die
  spezifische Zelle bringt nichts gegen die generische. Folge: dieses Veto
  bleibt, L4 wird nicht gebaut, keine Matrix über eine Zelle je Sprache hinaus
  (#491, Kommentar vom 14.11.).
- **Auf tei:** `data/scorers/de.scorer.json` (Kopie von
  `/mnt/wbkolleg_dh_1/Textrecognition_Training/scorers/`, wo asteraix die
  Zellen ablegt). Der Trace aus #298 unter diesem Scorer steht oben: TrOCR
  bleibt stehen — das Fertig-Kriterium des Epics.
- **Noch offen:** der Schalter (`FUSION_SCORER_VETO=true` in
  `/etc/agentic_historian.env`), und die externe Referenz (Bundesratsprotokolle,
  2 751 Zeilen), sobald der Satz wieder auf einem Host liegt.
