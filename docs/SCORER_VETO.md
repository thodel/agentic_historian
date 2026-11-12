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

Der Müllrand ist breit, das plausible Band schmal. Mit dem vorläufigen
`FUSION_VETO_MARGIN = 1.5` fallen die zwei kraken-Lesungen, TrOCR und das VLM
bleiben, und die No-Merge-Bande wählt unter den zweien.

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
| `FUSION_VETO_MARGIN` | `1.5` | bits/char über dem Besten der Seite; vorläufig, aus dem #298-Trace. |

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

## Was noch fehlt (Stand 12.11.2026)

- **L1 auf asteraix:** die XIX-Zelle (`de · Swiss · protocol · 19-20`) und die
  generische `de`-Zelle aus dem `prepare`-Manifest, Befehle in #491.
- **L2:** τ zwischen bits/char und Zeilen-CER auf den Bundesratsprotokollen,
  je Zelle und gegen die generische; τ < 0.3 auf dem plausiblen Band → L4
  fällt, dieses Veto bleibt, die Schwelle wird aus derselben Messung gesetzt.
- Dann: Scorer nach `data/scorers/` auf tei, `FUSION_SCORER_VETO=true`, und
  der Trace aus #298 lässt die TrOCR-Lesung stehen — das Fertig-Kriterium
  des Epics.
