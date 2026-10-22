# Reading a whole collection with several models

How to take a folder of scans and get every model's reading of every page, kept
apart so they can be compared. Written against the first run it was built for —
the Laßberg *digitalisate* through the three `dh-unibe` German-XIX fine-tunes —
but nothing here is specific to that corpus except the values.

The commands run on **tei**, where the stack, the venv and the gateway key already
live. The GPU work happens on **idhefix** either way: tei uploads each page to
the ATR gateway on `:8200`, and the models run there. Driving it from idhefix
instead only changes `ATR_GATEWAY_URL` to `http://127.0.0.1:8200` and needs a
checkout plus a venv on a box whose single partition has been full before.

---

## Denk-Tokens sind Ausgabe-Tokens

`Basel__lassberg-letter-1743__PA 82a B 9_Seite_027`, dreimal gelesen:

| Prompt | Thinking | Deckel | Ergebnis |
|---|---|---:|---|
| `@092fb6b0` | Modell-Default | 8192 | vollständig, 1592 Zeichen |
| `@03c5a660` | Modell-Default | 8192 | **abgeschnitten**, 1153 Zeichen |
| `@03c5a660` | **`low`** | 8192 | vollständig, 1375 Zeichen, **392 Ausgabe-Tokens** |

392 von 8192. Der sichtbare Text ist ein Zwanzigstel des Budgets — die anderen
~7800 gingen unter der Default-Stufe ins Nachdenken, bis für die Seite nichts
übrig war. Transkribieren, was auf einer Seite steht, ist keine
Schlussfolgerungsaufgabe. `GEMINI_REASONING=low` ist deshalb der Default.

Nebeneffekt, gemessen: **6,6 s/Seite statt 23,3** — 300 Seiten in gut einer
halben Stunde statt in zwei.

## Drei von vier „abgeschnittenen" Seiten waren keine Transkription

Das fiel erst bei der Validierung des zweiten Prompts auf, und es korrigiert
einen Rat, der vorher in diesem Dokument stand. `gemini-300-strict` meldete vier
Seiten als `cut off`. Drei davon tragen **keinen Text der Seite**, sondern das
Modell, das sich laut durch die Schrift rätselt:

```
- e - n - k - t !
LOOK AT WORD 3:
g - e - s - c - h - e - n - k - t !
Wait, does it have 'sch'?
…
Entry stroke, downstroke, loop up to the right... THAT'S A 'V'!
```

Das ist der Denkprozess im Inhaltskanal. Die Seiten sind nicht `empty`, nicht
`repetitive` (jede Zeile anders, längster Zeichenlauf 3) und nicht `short`
(816–928 Zeichen) — **keine Spalte bewegte sich**. Gesehen wurden sie
ausschliesslich, weil das Rätseln in den Token-Deckel lief. Eine Seite, die
vierhundert Zeichen lang rätselt und dann sauber endet, wäre Teil des Korpus
geworden.

Und damit war der naheliegende Rat für genau diese Seiten falsch: **Deckel hoch
kauft mehr Rätseln.** Was sie brauchen, ist ein anderer Prompt oder eine andere
Denkstufe — unter `lassberg_atr_strict2.md` sind alle vier saubere
Transkriptionen beziehungsweise ein `[unlesbar]`.

`page_quality` hat dafür jetzt ein Urteil, `deliberation`, und der Bericht eine
Spalte `arguing`. Die Erkennung läuft über die Dichte englischer, selbstadres-
sierter Zeilenanfänge („Wait", „Look at", „Line 12:"): gemessen **0,38 / 0,55 /
0,70** auf den drei geleakten Seiten gegen **0,00** auf jeder sauberen Lesung
derselben Seiten. Die Schwelle liegt bei 0,2, also weit von beiden Seiten. Eine
Transkription dieses Korpus gibt Deutsch und Latein und spricht sich nie selbst
an; ein englischer Einschub mitten in einer Zeile zählt nicht, nur ein
Zeilenanfang.

Die vierte Seite (`wlb stuttgart__Cod.hist.qt.407__32`) war eine echte, am
Deckel mitten im Wort abgeschnittene Transkription — dafür gilt der
Deckel-Rat weiter.

### Wie viele Seiten rätselten, ohne in den Deckel zu laufen: keine

Das war die offene Frage, und sie war die bedrohliche: die drei Seiten fielen
nur auf, weil das Rätseln in den Token-Deckel lief. Eine Seite, die
vierhundert Zeichen rätselt und dann sauber endet, hätte keine Spalte bewegt.
`gemini-300-strict` am 11. Oktober mit dem neuen Urteil neu klassifiziert:

| Urteil | Seiten | |
|---|---:|---|
| `ok` | **249** | 83 % |
| `short` | 19 | Stempel, `[unlesbar]`, Adressfelder — kein Defekt |
| `empty` | 28 | davon 21 echte Leerseiten |
| `deliberation` | **3** | |
| `repetitive` | 1 | |
| *davon* `cut off` | 4 | überschneidet sich mit `deliberation` |
| **beanstandet** | **33** | 11 % |

Die drei sind genau die drei, die der Deckel verraten hatte. **Keine vierte.**
Das Problem ist auf 33 bekannte Seiten begrenzt, nicht über das Korpus
verstreut.

Zwei Dinge, die diese Zahl erst zu einer Messung machen, und beide haben in
dieser Session je einen Anlauf gekostet:

- `verdicts` summiert auf `pages` — 249 + 19 + 28 + 3 + 1 = 300. Eine
  Urteilstabelle, die nicht aufgeht, ist kein Ergebnis (#620).
- `code.stale` war `false`. Ein Lauf vorher antwortete derselbe Aufruf mit der
  Struktur von #619, weil der Server-Prozess Code von Stunden vorher bediente
  (#621, § *Welcher Code hat geantwortet?*).

**Und was die Zahl nicht sagt:** dass die 249 `ok`-Seiten richtig gelesen sind.
`ok` heisst „keiner der bekannten Defekte", nicht „korrekt" — dafür gibt es in
diesem Lauf keine Referenz.

## Eine abgeschnittene Seite galt als gelesen

Das war die Falle darunter. Eine Seite am Token-Deckel kommt als gewöhnlicher
Erfolg zurück — 200, `.txt` und `.json` auf der Platte, Text, der mitten im Satz
endet. Der Bericht zählt sie in `cut off`, also *benannt* — aber der naheliegende
Reflex, **Deckel hoch und nochmal laufen lassen**, tat bei genau diesen Seiten
nichts, weil Resumierbarkeit-durch-Anwesenheit sie längst als fertig verbucht
hatte. Am 10. Oktober traf das drei der ersten dreizehn Seiten eines 300er-Laufs.

Der Fix ist nicht „immer neu lesen": gleiche Einstellungen heissen gleiches
Ergebnis, und eine abgerechnete API würde zweimal dafür bezahlt. Also erklärt
der Leser, *was* eine Lesung erzeugt hat — Prompt-Prüfsumme, Thinking-Stufe,
Deckel — und nur eine Änderung öffnet die Seite wieder:

```
settings: prompt-03c5a660+think-low|max8192
```

Ein Leser, der nichts erklärt (die Gateway), verhält sich unverändert: dort hat
die Frage „käme das jetzt anders heraus?" keine Antwort. Und `SCHEMA` bleibt, wo
es war — ein Versionssprung hätte jede Seite jedes früheren Laufs für unfertig
erklärt und ein Korpus neu gelesen, um ein Feld zu gewinnen, das darin überall
`None` ist.

## Der 300-Seiten-Lauf, 10. Oktober 2026

300 Seiten des Laßberg-Korpus, Stichprobe (Seed 20260915) aus 6466 noch nicht
transkribierten, `gemini-3.8-flash`, Prompt `lassberg_atr_strict.md @03c5a660`,
`--reasoning low`. Lauf `gemini-300-strict`.

| | |
|---|---|
| gelesen | **300 von 300**, 0 Fehler, 0 übersprungen |
| Wanduhr | 32,4 min · 6,4 s/Seite |
| `cut off` | **4 von 300** (1,3 %) |
| `empty` | 28 von 300 (9 %) |
| `looped` | 1 von 300 |
| Tokens | 450.566 Eingabe + 74.785 Ausgabe über 300 Aufrufe |
| Seitenbilder | 175 aus dem Cache, **125 aus dem Archiv**, 0 vom Share geholt |

**Die Deckel-Hypothese hält.** Unter der Default-Denkstufe waren drei der ersten
dreizehn Seiten abgeschnitten; mit `low` sind es vier von 300. Pro Seite 1.502
Eingabe- und **249** Ausgabe-Tokens — der längere Prompt kostet mehr Eingabe, und
die Ausgabe sinkt trotzdem von 380 auf 249, weil das Nachdenken nicht mehr aus
demselben Budget bezahlt wird. Die Schätzung von ~0,71 $ für 300 Seiten bleibt in
der Größenordnung; teuer ist die Ausgabeseite, und die ist kleiner geworden.

**Die Form hält auch.** Keine Vorrede, keine erfundenen Strukturlabels, kein
Markdown, Zeilenumbrüche erhalten, `[durchgestrichen: …]` und `wort[?]` wie
vereinbart — über 300 Seiten, nicht über drei.

### Was der strikte Prompt eingetauscht hat: Schweigen

`compare_readings` zwischen `gemini-300` (Original-Prompt) und
`gemini-300-strict` über die 205 gemeinsamen Seiten:

| | |
|---|---|
| leer **nur** im strikten Lauf | **21** |
| leer **nur** im Original-Lauf | 1 |
| leer in beiden | 0 |
| verglichen (beidseitig ≥100 Zeichen) | 163, Median-Abweichung 23,4 % |

21 Seiten, die dasselbe Modell unter dem Original-Prompt mit Text gelesen hat,
kommen unter dem strikten leer zurück. Die markierte `looped`-Seite zeigt
dieselbe Bewegung in ihrer anderen Form — 24 Zeilen `[...]` hintereinander, also
genau die Verwendung des Zeichens, die der Prompt verbietet („never as a place to
put a guess").

**Was diese Zahlen nicht sagen: welche Seite richtiger ist.** Es gibt hier keine
Referenz. Die 21 Lesungen des Original-Prompts können gelesene Seiten sein oder
flüssig erfundene; der strikte Prompt verweigert, wo der andere schrieb, und ob
Verweigerung besser ist als Erfindung, entscheidet ein Blick aufs Bild oder eine
Ground Truth, nicht diese Tabelle.

Zwischen den beiden Läufen hatten sich *zwei* Dinge geändert, Prompt *und*
Denkstufe, also trennte der Lauf die Ursache nicht. Das ist nachgemessen.

### Die unterscheidende Messung: der Prompt war es, und er hatte meistens recht

Lauf `gemini-strict-silence`, 11. Oktober: die 33 beanstandeten Seiten,
**Original-Prompt `@092fb6b0`, Denkstufe konstant `low`** — die eine fehlende
Zelle im 2×2.

```bash
python3 -m agentic_historian report-run \
  --run-dir agentic_historian/data/vlm_test/gemini-300-strict \
  --dry-run --keys-out "$GT_ROOT/strict-flagged.txt"
python3 -m agentic_historian atr-batch --source "dav:digitalisate" \
  --cache-dir "$ATR_PAGE_CACHE" --models gemini-3.8-flash \
  --run gemini-strict-silence --keys-from "$GT_ROOT/strict-flagged.txt" \
  --via external --prompt lassberg_atr.md --reasoning low
```

**26 der 28 leeren Seiten kommen mit Text zurück.** Die Denkstufe war nicht die
Ursache; der Prompt war es. Damit ist die Frage, ob das ein Verlust ist, erst
gestellt — und sie lässt sich nur durch Hinsehen beantworten. Alle 28 Lesungen,
einzeln gelesen:

| was auf der Seite steht | Seiten |
|---|---:|
| **echte Transkription**, die der strikte Prompt verloren hat | **2** |
| Stempel oder Registraturvermerk — Text, aber nicht die Briefhand | 3 |
| **Kommentar über eine leere Seite** („Vakatseite", Durchschlag, Wasserzeichen) | 21 |
| leer in beiden Läufen | 2 |

Die 21 sind der eigentliche Befund, und er geht gegen den Original-Prompt, nicht
gegen den strikten. Was dort im Transkriptionsfeld steht, ist Museumsprosa:

```
Marbach__HS00787953_150dpi_0094:  ### Transkription
                                  *[Die Seite enthält keinen handschriftlichen
                                  Text / vacat. In der Papiermitte ist schwach
                                  ein Wasserzeichen erkennbar.]*
UB Bern__Mss_h_h_XLIV_180_32_169: ### Befundbeschreibung
                                  * **Rechte Seite:** Vollständig unbeschrieben.
                                  * **Linke Seite:** … Durchschlagen …
```

Auf einer unbeschriebenen Seite ist **nichts** die richtige Transkription. Der
strikte Prompt antwortet richtig, und `page_quality` zählt die Antwort des
anderen als Text — darum sah die Spalte `empty` wie ein Ausfall aus. Umgekehrt
heisst das: jeder Lauf mit `lassberg_atr.md` trägt für jede Leerseite einen
erfundenen Befundtext im Transkriptionsfeld. Publiziert wäre das ausgewiesener
Fremdtext.

**Der echte Verlust sind 5 von 28 Seiten**, und er hat eine klare Form: der
strikte Prompt schweigt auch dort, wo *etwas* steht, das nicht die Briefhand ist
— zwei beschriebene Seiten (`Donaueschingen …0258__002`, 1143 Zeichen;
`StATG …1339_00101`, ein Pupikofer-Brief, Bischofszell 25. Juli 1829) und drei
Seiten mit Bibliotheksstempel oder Registraturvermerk. Das ist eine
Prompt-Ergänzung, keine Modellfrage: der strikte Prompt sagt, was *nicht* zu
schreiben ist, und nennt nie, dass Stempel, Foliierung und fremdhändige Vermerke
zum Seitenbefund gehören.

### Die Variante `lassberg_atr_strict2.md @9e0503e8`

Alle fünf Verluste gehen auf **einen Satz** zurück. `An empty page is an empty
answer` war die einzige Aussage des strikten Prompts über Leere, und das Modell
hat sie auf jede Seite angewandt, die es nicht ausschreiben wollte. Eine leere
Antwort hieß damit zweierlei — „dieses Blatt ist unbeschrieben" und „diese Seite
schreibe ich nicht aus" —, und genau in der zweiten Bedeutung sind zwei
beschriebene Seiten verschwunden. Eine leere Antwort ist für jedes Signal des
Runners ein Erfolg; darum konnten sich fünf Seiten in einer Spalte namens
`empty` verstecken.

Die Variante trennt die drei Fälle und gibt jedem eine eigene Antwort:

| auf der Seite | Antwort |
|---|---|
| kein Schriftzug | leere Antwort — und sie **behauptet** jetzt, dass das Blatt leer ist |
| Schrift, lesbar | Transkription, `[...]` und `wort[?]` wo nötig |
| Schrift, nichts davon lesbar | `[unlesbar]`, einmal — nicht 24 Zeilen `[...]` |
| Stempel, Akzessionsnummer | `[stempel: …]` |
| Foliierung, Dorsualnotiz, spätere Hand | `[fremde hand: …]` |

Dazu dreierlei, was ausdrücklich **kein** Text dieser Seite ist und weder
transkribiert noch beschrieben wird: Durchschlag von der Gegenseite,
Wasserzeichen und Papierzustand, und alles Erschlossene. Das ist der Stoff, aus
dem die 21 Leerseiten-Beschreibungen des Original-Prompts bestanden — und der
Satz „schreib nie, dass eine Seite leer ist" steht jetzt da, statt sich aus
„keine Vorrede" ergeben zu müssen.

Die Ausgabedisziplin, die über 300 Seiten gehalten hat, ist wörtlich übernommen.
Was dazukommt, sind zwei Marken und eine; jede zeigt auf eine Seite eines
gemessenen Laufs. Dass ein Stempel **markiert** statt nackt transkribiert wird,
ist die Überlegung dahinter: ein unmarkiertes „Schillermuseum 11551" mitten in
einer Transkription ist schlimmer als kein Stempel, weil es sich nicht mehr
entfernen lässt. Die Marken sind eine editorische Entscheidung und in
`agentic_historian/prompts/README.md` als solche ausgewiesen.

Validiert an denselben 33 Seiten, die den Befund ergeben haben — die 21 leeren
als Kontrolle, die 5 Verluste als Probe:

```bash
python3 -m agentic_historian atr-batch --source "dav:digitalisate" \
  --cache-dir "$ATR_PAGE_CACHE" --models gemini-3.8-flash \
  --run gemini-strict2-check --keys-from "$GT_ROOT/strict-flagged.txt" \
  --via external --prompt lassberg_atr_strict2.md --reasoning low
```

Lauf `gemini-strict2-check`, 11. Oktober: 33 von 33, 0 Fehler, 1,8 min,
`cut off: 0`, `looped: 0`.

| | |
|---|---|
| Leerseiten-Kontrolle | **22 von 22 bleiben leer** — keine Seite hat Prosa dazubekommen |
| von den 5 Verlusten zurück | **4**: beide beschriebenen Seiten, beide Stempel |
| noch verloren | 1 — die Dorsualnotiz `blb lassberg__K 2911,132__page_0003` |
| neu gefunden | 2: ein Randfragment und eine Archivnummer |

Die Marken tun, was sie sollen:

```
Marbach__HS00755396_150dpi_0007:  [stempel: Schillermuseum Marbach] [fremde hand: 11551]
Ms_M_8_32_a_0040:                 [fremde hand: 61 a]
UB Bern__Mss_h_h_XXVI_105_5_44:   [unlesbar]
```

Die letzte Zeile ist die aussagekräftigste: dieselbe Seite, die unter dem ersten
strikten Prompt 24 Zeilen `[...]` hintereinander produzierte, meldet jetzt eine
einzige aufgezeichnete Verweigerung.

Eine leere Seite ist dabei **nicht** gleich einer leeren Seite: durch die Gateway
heißt leer „der Segmentierer fand keine Zeilen", durch einen externen Leser gibt
es keinen Segmentierer — das Modell wurde nach der Seite gefragt und antwortete
mit nichts. Der Bericht sagt das inzwischen auch so. Und was die Frage
tatsächlich entschieden hat, war nicht der Blick ins Bild, sondern die Lesung
desselben Korpus unter dem anderen Prompt: zwei Minuten und ein paar Cent.

## Der erste externe Lauf, 10. Oktober 2026 — und was er über den Prompt sagt

Drei Seiten, `gemini-3.8-flash`, Prompt `lassberg_atr.md @092fb6b0`.

**Die Mechanik hält.** 3 von 3 gelesen, 0 Fehler, 23,3 s/Seite, `cut off: 0`.
Der Preflight fand das Modell bei der API gelistet. Dass eine Seite mit
„doch nicht am=" endet, ist **keine** Abschneidung, sondern die Silbentrennung
der Vorlage am Seitenende.

**Die Kosten, aus gemessenen Zahlen statt aus einer Schätzung:** 1.228 Eingabe-
und 380 Ausgabe-Tokens pro Seite. 8192 als Deckel war großzügig. Für 300 Seiten
ergibt das rund 368k + 114k Tokens, zum Einführungspreis etwa **0,71 $**, und bei
dieser Nebenläufigkeit etwa zwei Stunden.

**Der Prompt hält nicht.** Alle drei Seiten tragen Text, der nicht auf der Seite
steht:

```
Seite_160:  Hier ist die getreue Transkription des Briefes:
            [Kopfvermerk von der Hand des Empfängers:]  [Oben rechts:]
            [Anrede:]  [Text:]
Seite_027:  **[Transkription]**  ·  ~~durchgestrichen~~
```

Vorrede, erfundene Strukturlabels, nicht angekündigtes Markdown. In eine
CER-Messung gerechnet verfälscht das jede Zahl; als Transkription publiziert ist
es ausgewiesener Fremdtext. Bei `Seite_027` stehen zudem vier Klammerzeilen, die
kein kohärentes Deutsch sind („Theuerster, theurer Handschuh!") — das sieht aus
wie plausibel klingendes Füllmaterial für eine schwer lesbare Stelle.

**Der Fix gehört in den Prompt, als neue Datei mit neuer Prüfsumme** — eine
Bearbeitung der bestehenden macht den gemessenen Lauf unvergleichbar. Ein
strikter Prompt müsste verbieten: Vorrede, Strukturlabels, Markdown; und `[...]`
auf unlesbare *Wörter* beschränken statt auf ganze Zeilen.

### Ein Fehlalarm, den dieser Lauf aufgedeckt hat

`Seite_030` wurde als `repetitive` markiert, bei `repeat_ratio: 0.0` und
`longest_char_run: 34`. Die 34 waren die Leerzeichen einer rechtsbündigen
Unterschrift:

```
                                  JvLaßberg.
```

Die Engines hinter der Gateway geben eine Zeile pro Textzeile und kein Layout
aus, also konnte das nie vorkommen. Ein Modell, das das Seitenlayout mitliest,
löst es bei jeder eingerückten Unterschrift, Adresse und Datierung aus — und der
Bericht sagt über eine markierte Seite „every average over them is wrong", was
über eine saubere Seite eine Unterstellung ist, die den Lauf überlebt.
`longest_char_run` zählt Whitespace jetzt nicht mehr: ein Lauf von 34 Buchstaben
ist Füllmaterial, ein Lauf von 34 Leerzeichen ist ein Layout.

## Ein externes Modell lesen lassen

```bash
# Key nach .env.gpustack, nie auf die Kommandozeile (argv steht in `ps`)
python -m agentic_historian atr-batch --source "dav:digitalisate" \
  --cache-dir "$ATR_PAGE_CACHE" --run gemini-pilot \
  --via external --models gemini-3.8-flash \
  --keys-from "$GT_ROOT/gemini-pilot.txt"
```

`--via external` tauscht nur den Leser; alles andere bleibt — Resumierbarkeit
durch Anwesenheit, die Ausfall-Schaltkreise, die Berichte. Der Leser ist ein
OpenAI-kompatibler Client gegen `GEMINI_BASE_URL`, weil `openai` ohnehin schon
Abhängigkeit ist (GPUStack wird genauso angesprochen).

**Was dabei nicht entsteht: Zeilengeometrie.** Eine Chat-Completion hat keine
Polygone. `lines` bleibt leer, statt eine Zeile pro Newline zu erfinden — das
sähe wie Segmentierung aus und wäre keine. Diese Lesungen sind vergleichbar und
publizierbar, aber sie können `pagexml-hf` nicht füttern und kein Zeilenmodell
trainieren.

**Drei Dinge, die mitgeschrieben werden:**

| | warum |
|---|---|
| `truncated` aus `finish_reason` | eine Seite am Token-Deckel kommt als normaler Erfolg zurück und bricht mitten im Satz ab |
| die Prompt-Prüfsumme | zwei Lesungen derselben Seite unter verschiedenen Prompts sind verschiedene Messungen |
| Token-Verbrauch pro Lauf | ein Lauf gegen eine abgerechnete API, der nicht sagen kann, was er gekostet hat, lässt sich nicht ein zweites Mal genehmigen |

Der Prompt ist eine **versionierte Datei** unter `agentic_historian/prompts/`,
`--prompt NAME` wählt sie, und ein Pfad wird abgelehnt: ein Prompt, der von
überall kommen kann, ist einer, den kein Protokoll identifizieren kann.
`--structured` verlangt `diplomatic` + `normalized` als JSON — ohne das hat die
Prompt-Regel „Expansions go into the normalized field only" keinen Ort, was sich
als „nicht expandieren" liest.

## „Alles, was Laßberg selbst geschrieben hat" — ein Join, keine Heuristik

Die Hand einer *untranskribierten* Seite kann man ihr nicht ansehen: sie wird aus
der Datierung gelesen, und die steht in der Transkription. Das Verzeichnis der
Edition nennt den Absender pro **Brief**, und die Brief-ID steckt im
Seitenschlüssel — damit ist es eine Nachschlage-Operation.

```bash
git clone --depth 1 https://github.com/michaelscho/lassberg
export LASSBERG_REGISTER=$PWD/lassberg/data/letters

# 1. Was das Verzeichnis abdeckt, und die Brief-IDs rausschreiben
python -m agentic_historian letter-register --lassberg --out /tmp/lassberg-briefe.txt
#   279 Briefe, 128 von Laßberg (GND 118778862)

# 2. Alle Seiten dieser Briefe, minus alles, was schon gelesen ist, die ersten 300
python -m agentic_historian atr-batch --source "dav:digitalisate" \
  --cache-dir "$ATR_PAGE_CACHE" --models … --run lassberg-300 \
  --letters-from /tmp/lassberg-briefe.txt \
  --exclude-from "$GT_ROOT/gt-keys.txt" \
  --limit 300 --keys-out "$GT_ROOT/lassberg-300.txt" --dry-run
```

Ein Brief, zu dem dieses Korpus keine Seite hat, wird benannt — das Verzeichnis
ist fremde Daten, und der Share hat schon Ordner verloren (`docs/MISSING_IMAGES_2026-10-04.md`).
Eine Seite ohne Brief-Segment (lose im Archivordner) gehört zu keinem Brief und
wird nie ausgewählt.

### Dasselbe aus einer Session

```
letter_register(out="lassberg-briefe.txt")        # 279 Briefe, 128 von Laßberg
start_batch(models=["qwen3vl-german-xix-v2"], run="lassberg-300",
            source="dav:digitalisate",
            letters_from="lassberg-briefe.txt",
            exclude_from=["gt-keys.txt"],
            limit=300, keys_out="lassberg-300.txt", dry_run=True)
```

`letter_register` ist **synchron** — es liest 280 kleine lokale Dateien, da wäre
eine Job-ID Zeremonie um eine Antwort, die in die Rückmeldung passt.

`letters_from`, `exclude_from` und `keys_out` sind **Namen von Dateien unter
`GT_ROOT`, keine Pfade** — dieselbe Regel wie `keys_from` seit #509. Der Server
steht hinter einem Bearer-Token im offenen Netz, und diese Validierung ist, was
zwischen dem Token und einer Maschine mit zwei A40 steht: eine Liste, die ein
Pfad sein darf, macht aus einem Werkzeug, das Seiten wählt, eines, das Dateien
wählt.

Zugeordnet wird über `hf_export.letter_of`, dieselbe Regel, die der Export für
das Datensatz-Label benutzt: so können eine Seite und ihr Label nie uneins
darüber sein, zu welchem Brief die Seite gehört.

## Die Seiten auswählen, die noch niemand gelesen hat

`--keys-from` nimmt eine Liste; `--exclude-from` lässt eine weg, und mehrere
davon. Damit ist „die Seiten, die noch niemand transkribiert hat" eine
Subtraktion statt einer Einschätzung — beide Listen existieren schon:

```bash
# 1. Welche Seiten handkorrigierten Text haben
python -m agentic_historian score-gt --run-dir atr_corpus_qwen35_line \
  --keys-out "$GT_ROOT/gt-keys.txt"

# 2. Welche ein früherer Lauf schon gelesen hat
ls data/vlm_test/atr_corpus_qwen35_line/<modell>/*.txt \
  | xargs -n1 basename | sed 's/\.txt$//' > /tmp/read-keys.txt

# 3. Das Korpus minus beides, dann die ersten 300 — und die Auswahl als Datei
python -m agentic_historian atr-batch --source "dav:digitalisate" \
  --cache-dir "$ATR_PAGE_CACHE" --models … --run neu-300 \
  --exclude-from "$GT_ROOT/gt-keys.txt" --exclude-from /tmp/read-keys.txt \
  --limit 300 --keys-out "$GT_ROOT/neu-300.txt" --dry-run
```

**Die Reihenfolge ist dieselbe Lektion wie #531, von der anderen Seite.** Erst
auf 300 schneiden und dann das Gelesene entfernen ergibt, wie viele dieser 300
zufällig ungelesen waren — eine Zahl, die niemand gewählt hat. Jeder Filter
sorgt deshalb dafür, dass die Discovery das ganze Korpus zurückgibt und der
Schnitt zuletzt kommt.

**`--keys-out` schreibt die Auswahl auf.** Ein Lauf über „die 300, die niemand
gelesen hat" ist nur wiederholbar, wenn die 300 eine Datei sind: nächste Woche
neu abgeleitet ergibt ein Share, das eine Seite gewonnen oder verloren hat, eine
andere Auswahl — lautlos. Dass das kein Schreckgespenst ist, hat der 4. Oktober
gezeigt (35 Seiten verschwanden zwischen zwei Läufen).

Ein Ausschluss-Schlüssel, der nichts getroffen hat, wird benannt. Hier heisst
das das Gegenteil von #535: nicht Ground Truth ohne Bild, sondern eine Lesung
einer Seite, die das Korpus nicht mehr anbietet.

## 0 · Before anything: are the models actually servable?

Registration in `config/models.yaml` is not the same as being servable. See
[`serving-atr-inference/docs/GERMAN_XIX_MODELS.md`](https://github.com/thodel/serving-atr-inference/blob/main/docs/GERMAN_XIX_MODELS.md)
for the box-side work — in short, on idhefix:

```bash
cd ~/Repo/serving-atr-inference && . ./.env      # HF_TOKEN: the repos are private
df -h /                                          # need ~40 GB; it has hit 0 before
.venvs/vllm/bin/python scripts/merge_loras.py --only qwen3vl-german-xix-v1
.venvs/vllm/bin/python scripts/merge_loras.py --only qwen3.5-4b-german-xix-v1
.venvs/vllm/bin/python scripts/merge_loras.py --only qwen3.5-2b-german-xix-v1
systemctl --user restart atr-gateway
```

Then, from tei, confirm the gateway agrees:

```bash
curl -sH "X-API-Key: $ATR_API_KEY" "$ATR_GATEWAY_URL/models" \
  | python3 -c "import json,sys; print([m['id'] for m in json.load(sys.stdin)['models'] if 'german-xix' in m['id']])"
```

Three ids, or stop here. A batch against an unregistered model is one 404 and an
abandoned model — which is the right behaviour, and still a wasted trip.

**And check the token ceiling.** These are served page-level, and
`ATR_VLLM_MAX_NEW_TOKENS` defaults to 512 — enough for a line, not for a page.
Past it vLLM stops and returns a normal `200` whose transcription simply ends
mid-sentence, with nothing anywhere saying it was cut off. Set
`ATR_VLLM_MAX_NEW_TOKENS=4096` in the gateway's `.env` and restart before the
first real run, then read the **end** of a smoke-run page: a reading that stops
mid-word is the ceiling, not the model.

### And is there room on the card?

Registered, servable, and still unable to start: the three engine services hold
card 1 whether anyone is using them or not, and a vLLM model that does not fit
finds out at its cold start — which is *after* the share has been walked.
Measured 2026-09-23 on idhefix, card 1 of 46 068 MiB:

| service | MiB held | used by a corpus run? |
|---|---|---|
| `atr-party` | 11 858 | no |
| `atr-trocr` | 3 908 | no |
| `atr-kraken` | 988 | no |
| `qwen3.5-4b-german-xix-v2` needs | 15 848 | — |

With party resident there are 9 742 MiB free and the run dies at page 1 with
`GPU 1 has 9742 MB free, needs 15848 MB`, 24 minutes of enumeration already
spent. Check before starting:

```bash
# from tei — what is on the serving box's cards right now
curl -sH "X-API-Key: $ATR_API_KEY" "$ATR_GATEWAY_URL/gpu" \
  | python3 -c "import json,sys; c=json.load(sys.stdin)['cards'][1]; \
                print(c['memory_free_mib'], 'free'); \
                [print(' ', p['used_mib'], p['service']) for p in c['processes']]"
```

Short of the model's `vram_mb`, free the card **on idhefix** — no sudo needed,
these are user units:

```bash
systemctl --user stop atr-party         # the big one; restart it after the run
```

Two things about that line. It frees ~11.8 GB and nothing restarts it — party
has been down since 2026-09-23 because a chat message is the only record that it
should come back. And card 0 is not an alternative: its 10 392 MiB belong to
another user's `rag-change` workers and are never ours to free.

Both are why this is done by hand today and should not be. #470 replaces it: the
run states what it needs, the gateway frees what it can, and a lease with a
timeout puts the engines back even when the run dies — which this one did three
times in a day.

---

## 1 · Configure the share

In `.env.gpustack` at the repo root (never committed):

```ini
NEXTCLOUD_SHARE_URL=https://cloud.gugw.tu-darmstadt.de/nextcloud/s/<share-token>
NEXTCLOUD_SHARE_PASS=…          # the share password
NEXTCLOUD_REMOTE_DIR=digitalisate
```

Paste the URL as the browser shows it — including the `…/authenticate/showshare`
a password-protected share redirects to. The share token is the WebDAV username;
`utils/nextcloud.py` takes it apart.

Look before you pull:

```bash
cd ~/agentic_historian
python -m agentic_historian pull-share --list
```

That prints every ingestable file with its size and a total. It is also the check
that the password is right: a wrong one fails here, in a second, rather than an
hour into a transfer.

---

## 2 · Get the pages within reach

Two ways, and the choice is about disk, not convenience.

### Mount it (the Laßberg corpus, since 2026-09-17)

The scans are uncompressed TIFF — 2636 × 3212 at three bytes a pixel is 25 MB a
page, about 160 GB for the share against the 92 GB tei has in total. There is no
mirror that fits, so the share is **mounted read-only** and read in place:

```bash
# once, as root (tei has sudo); see docs/NEXTCLOUD_MOUNT.md for the whole setup
sudo mount -a
ls "$ATR_MOUNT_DIR" | head      # on tei: /home/dh/gwdg
```

**Take the path from `ATR_MOUNT_DIR`, not from this page.** Its default is
`/mnt/gwdg` and tei's `.env` sets `/home/dh/gwdg`; a batch validates `--source`
against the configured value, so a hardcoded path from a document is refused with
"outside the corpus roots" even when it is where the corpus used to be. That cost
two wrong turns on 2026-10-02. `gateway_models` and a refused `atr-batch` both
print the roots in force.

A mount makes opening a page a network transfer, and a batch opens every page at
least once per model — so pair it with `--cache-dir` (§3), which converts each
page to its JPEG working copy on first read and serves every later read locally.
One transfer per page, ~0.7 MB on disk instead of 25 MB, full resolution kept.

### Read the cache (when the share is not there)

The page cache is a corpus root in its own right. It is keyed by each page's path
relative to the corpus root, so it mirrors the share's structure and a page read
from it gets the **same key** it would have from the share or the mount — which is
what lets one corpus be read partly one way and partly another.

```bash
python -m agentic_historian atr-batch --source "$ATR_PAGE_CACHE" --models … --run …
```

This stopped being a convenience on 2026-10-02, when the share answered **500 to
every PROPFIND** — both `public.php/webdav` and `public.php/dav/files/…` — and
`ATR_MOUNT_DIR` was an empty directory. Neither route to the pages worked, while
some 6700 of them sat on local disk and could not be named as a source.

Two things to know. A run reading from the cache gets **no** `--cache-dir`: a page
that is already a working copy needs no working copy, and pointing one at itself
would re-encode the JPEG a generation per model. And the cache holds the pages a
previous run actually fetched, so it is not necessarily the whole corpus — a dry
run says how many it has, and the share or the mount is still what fills the gaps.

### Mirror it (a corpus that fits)

```bash
python -m agentic_historian pull-share --folder digitalisate
# → agentic_historian/data/nextcloud/digitalisate/…
```

Safe to re-run. A file already present at the remote size is skipped, and every
download lands through a `.part` file, so an interrupted transfer is never
mistaken for a finished one. `--limit 5` first, if you want to see the shape of
the material before committing to all of it.

`--limit` stops the walk as soon as it has enough, which matters more than it
sounds for a mirror. The enumeration is one `PROPFIND` per folder — measured at **1.75 s**
against this share — so before that, a ten-file test walked 100 folders and ran
three minutes before fetching a single byte. What you get is the first N in
*traversal* order, not the first N of the sorted whole; for a subset that
represents the collection, the tool is `atr-batch --sample`.

---

## 3 · Read every page with every model

```bash
python -m agentic_historian atr-batch \
    --source     "$ATR_MOUNT_DIR" \
    --cache-dir  data/page_cache/lassberg \
    --models     qwen3vl-german-xix-v1,qwen3.5-4b-german-xix-v1,qwen3.5-2b-german-xix-v1 \
    --run        atr_test_lassberg
```

`--cache-dir` is what makes `--source` on a mount affordable. Without it every
model's pass re-reads 25 MB a page across the network; with it the first pass
pays that once, converts to JPEG at full resolution, and every later read — a
retry, the next model, next week's re-run — is a local file. Its state is what is
on disk, like the rest of the runner: deleting the cache costs time, never
correctness. Omit it for a corpus already on local disk, where it would only
duplicate files.

The result JSON still carries the **sha256 of the archival file**, not of the
working copy, and names the working copy separately under
`source.working_copy`. The digest is taken from the one read the cache already
makes; hashing the original again would mean pulling it back across the mount
for a number we were handed.

Output lands in `$VLM_TEST_ROOT/atr_test_lassberg/<model id>/`, two files per page:

| file | what it is |
|---|---|
| `<key>.txt` | the transcription, nothing else |
| `<key>.json` | the same text plus timings, the engine that answered, party's second opinion, the **sha256 of the source image**, and — for line-level models — the per-line readings with their geometry |
| `manifest.jsonl` | one append-only line per page attempt, across all models |
| `report.md` / `report.json` | what each model produced and what it cost |

The three German-XIX models are registered **page-level**: the whole image goes to
the model in one call, so `lines` is empty for them and `segmented_by` is null.
That is not a gap — there was no segmentation step to report. A line-level model
(TrOCR, LightOnOCR) run through the same command fills both in.

`<key>` is the page's path inside the share with separators folded to `__`
(`letter-01/001.jpg` → `letter-01__001`), so one flat directory per model holds
the whole corpus and every file still says where it came from.

**Dry-run first.** It costs nothing and it is the only place the page count,
the model list and the total number of gateway calls appear before the run
starts:

```bash
python -m agentic_historian atr-batch --source … --models … --run … --dry-run
```

### `--limit` or `--sample`

`--limit N` takes the first N pages, `--sample N` takes N at random. They are
alternatives, and the difference decides what a short run can tell you.

This share is one folder per document, so `--limit 10` is ten consecutive pages
of one letter: the same hand, the same ink, usually the same scanner setting. It
proves the path works and says almost nothing about how a model reads the
collection. `--sample 10` crosses documents.

Sampling is deterministic — a fixed seed, overridable with `--seed`. That is not
tidiness: the runner treats what is on disk as its state, so a sample that moved
between runs would mean a resumed run reading pages the first one never saw, and
a second model reading a different corpus than the first. The same seed on
another machine draws the same pages, which is what lets two people compare notes
about the same ten.

**Smoke-run second.** `--sample 10` reads ten pages with each model. Do this
once per new corpus: it pays the cold vLLM start for each model and proves the
whole path end to end in minutes, instead of finding out at 3 a.m. that the
models will not take this scanner's TIFFs or that every reading is stopping at
the token ceiling. Read the *end* of one of the transcriptions before you let the
full run go.

### `--keys-from`: only the pages we can grade

Ground truth exists for 552 pages of 6742 (#504, #507), of which 276 locate a
corpus page unambiguously. To find out which of eight candidate engines reads this
hand best, each has to read *those 276*. Over the whole corpus that is some 36
GPU-hours a model at the 19.3 s a page the first run measured, and a fortnight for
eight on a card that is also serving everything else.

So the two commands are a pair. Scoring writes the keys it located:

```bash
python -m agentic_historian score-gt --run-dir atr_corpus_qwen35_line --keys-out
```

Bare `--keys-out` writes `GT_ROOT/gt-keys.txt`. **Not `/tmp`:** this page used to
say `/tmp/gt-keys.txt`, tei rebooted, and sixteen minutes of matching went with it.

The runner reads it back by path. To get that path without a shell variable:

```bash
KEYS=$(python -c "import sys; sys.path.insert(0,'agentic_historian'); import config
print(config.GT_ROOT / 'gt-keys.txt')")

python -m agentic_historian atr-batch \
  --keys-from "$KEYS" --source dav:digitalisate \
  --models trocr-kurrent,trocr-kurrent-xvi-xvii --run atr_gt_trocr
```

**Take every path from the configuration, never from a shell variable.**
`VLM_TEST_ROOT`, `GT_ROOT` and `ATR_PAGE_CACHE` live in `.env`, which dotenv loads
for Python and an interactive bash has never seen — so
`--run-dir $VLM_TEST_ROOT/atr_corpus_qwen35_line` pasted into a shell expands to
`/atr_corpus_qwen35_line`. Worse, a variable that is *set but not exported* expands
in the argv while `os.environ` stays empty, which is how `--source
"$ATR_PAGE_CACHE"` once named a directory holding unrelated images and produced 357
"no image" lines.

So the arguments resolve their own defaults: `--run-dir` takes a bare run name
under `VLM_TEST_ROOT`, `--gt` defaults to `GT_ROOT`, and `--source` and
`--cache-dir` default to `config.page_cache_dir()` — one answer every entry point
reads, because having two is what cost three rounds of diagnosis. An existing path
still wins, so a full path works as before.

One page per line, `#` comments allowed, duplicates collapsed — two ground-truth
files can match the same page and reading it twice would double its weight in
every average afterwards. It composes with `--limit` and `--sample`, which apply
to what is left after the filter — **the filter first, the cut second**. It used
to be the other way around, and the smoke run that found it (#531) cut the corpus
to its first three pages, matched those against the 276 keys, and reported "none
of the 276 key(s) name a page under dav:digitalisate" — which reads like a
key-spelling problem and is not one.

**A key that names no page under `--source` is printed on stderr, never dropped
quietly** — all of them, and `--missing-out PATH` writes them to a file. The list
is produced somewhere else, so some of it may name pages this source does not
have; a run that silently read thirteen of sixteen would be reported as if it had
read all sixteen. None of them matching is an error rather than an empty run.

On 2026-10-04 that list was the deliverable. The share had stopped holding the
whole `Briefe UB Freiburg` holding — 6722 files where the day before there were
6742 — and 35 of the 276 ground-truth pages went with it. Those pages still have
hand-corrected text in Transkribus and no image anywhere, so *which 35* is what
the people who keep the corpus need. The file is written even when nothing is
missing, because a dated "all 276 resolved" is evidence where an absent file
could also mean nobody looked. Pair it with `--no-listing-cache`: the file list
is kept for 48 h, and a stale list promises pages that are gone.

`--keys-out` writes only **located** pages, which is stricter than the
`agreed_key` the report prints. Agreement is not identification: with one reading
the readings agree trivially, and on 2026-10-02 a single reading agreed with
itself at 106.9 % CER against a runner-up at 107.7 %. A wrong key here does not
produce a bad number — it sends eight models to read a different page and then
scores their readings of it against this page's ground truth. So a key needs
agreement *and* at least one confident match.

### The candidate comparison, in order

Five steps, and the order is the whole thing: each one produces what the next one
reads. Run on 2026-10-03 for seven engines over 276 pages.

```bash
# 1 · which corpus pages the ground truth locates, and their keys
python -m agentic_historian score-gt --run-dir atr_corpus_qwen35_line --keys-out
#   → 552 files scored, 24 unusable, 276 located → GT_ROOT/gt-keys.txt

# 2 · prove the path on three pages and two engines before 1932 calls
python -m agentic_historian atr-batch --source dav:digitalisate \
  --keys-from "$(python -c 'import sys; sys.path.insert(0,"agentic_historian"); import config; print(config.GT_ROOT / "gt-keys.txt")')" \
  --models trocr-kurrent,kraken-mendelssohn_letters --run atr_gt_candidates \
  --limit 3 --dry-run        # then the same line without --dry-run

# 3 · the candidates, all of them, over the located pages
#     drop --limit; the key list is already the restriction
#     leave out any engine that has read these pages in another run

# 4 · the one table that is quality: every reading against the same truth
python -m agentic_historian score-gt \
  --run-dir atr_gt_candidates --run-dir atr_corpus_qwen35_line

# 5 · the pages and their corrected text, as a dataset
python -m agentic_historian export-hf --dry-run      # then without
```

Step 3 takes the engines **that have not already read these pages**.
`qwen3.5-4b-german-xix-v2` read all 6719 in `atr_corpus_qwen35_line`, so repeating
it here would spend 89 GPU-minutes to produce files we have; step 4 takes several
`--run-dir`, which is what makes that legitimate rather than a gap.

Step 2 is not optional on a new engine. Four of the kraken candidates had never
read a page in this stack, and "it is registered and it fits on the card" is not
the same as "it answers".

**Take the timing from the runner, not from the first few pages.** It prints its
own rate and ETA per model (`1.2 p/min · ETA 2.8 h` at page 70 of 276), and that
is the number to plan with: counting pages between two polls gave 11 s a page
here and the truth was 50. The shape to expect is one slow model and six fast
ones — the first pass downloads every page (~25 MB each) and the rest read the
warm cache, so for these 276 pages:

| | s/page | 276 pages |
|---|---:|---:|
| `trocr-kurrent`, cold cache | ~50 | 3.7 h |
| `trocr-kurrent-xvi-xvii`, warm | 18.9 | 1.5 h |
| each kraken, warm | 11.3 | 0.9 h |
| `qwen3vl-german-xix-v2`, warm | ~19 | 1.5 h |

Some ten hours for the seven, against a little over two weeks for the same seven
over the whole corpus. The per-page figures are the smoke run's and the first
corpus run's, so treat the total as an extrapolation from measured rates rather
than as a measurement.

### Why it iterates model-major

All three models are `residency: lazy` on GPU 1, which holds **one** at a time.
The runner therefore does every page of one model before touching the next.
Page-major — all three models per page — would evict and reload weights on every
single page, which on a few hundred pages is the dominant cost of the entire run
and buys nothing.

Nothing else in the repo enforces this, so anything else driving the gateway over
several models has to do the same.

### What it does when things go wrong

| what happened | what the runner does |
|---|---|
| timeout, connection dropped, 5xx | retries (2 by default), backing off 2s → 4s; then records the page and moves on |
| 400/422 on one page | no retry — the request was wrong for *this* page. Records it, moves on |
| 404 (model id the gateway does not have), 401/403 | abandons **that model at once** — every remaining page would fail identically |
| 5 failures in a row | abandons that model. Something is wrong with the model or the gateway, and the rest of the run still has models to get through |
| a model abandoned | the other models still run; `report.md` names which stopped and why; the process exits non-zero |

The counter resets on every success, so a corpus with a few unreadable scans runs
to the end while a gateway that has gone away does not consume the night first.

### When the share will not open

Two things about this share are worth knowing before reading its error messages,
both measured on 2026-10-03.

**The legacy endpoint serves the root and nothing below it.** With the same
credentials, one second apart:

| endpoint | `/` | `digitalisate` |
|---|---|---|
| `public.php/webdav` | 207 | **404** |
| `public.php/dav/files/<token>` | 207 | 207 |

So `_connect` asks the newer endpoint first and keeps the legacy one as the
fallback for older servers. A 404 from the legacy endpoint says nothing about
whether the folder is there.

**The process environment beats every `.env` file.** `load_dotenv(override=False)`
cannot replace a value already in `os.environ`, so a service started with a stale
`NEXTCLOUD_SHARE_PASS` keeps using it however often `.env.gpustack` is corrected.
That is how the same share answered 207 from an interactive shell and 401 from
`atr-mcp` seconds apart. A 401 now says which file the password came from, or —
when it came from the environment — says to check `/proc/<pid>/environ` of the
process that failed and the `EnvironmentFile=` of its unit.

**`.env.gpustack` is not shell-sourceable.** `ATR_WATCH_MENTION=<@817…>` is a
redirect to bash and valid to dotenv, so `set -a; . ./.env.gpustack` stops there
and every variable below it stays unset — which looks exactly like a wrong
password. Quote such values (`ATR_WATCH_MENTION="<@817…>"`) or read them through
Python, which is what reads that file anyway.

### Interrupted? Re-run the same command

A page whose `.json` is on disk and parses is skipped. There is no state file to
reconcile — the output *is* the state. Ctrl-C, a reboot, a gateway restart: run
the same line again and it picks up where it stopped.

### Timing

Page-level: one gateway call per page, so a warm model is seconds a page, plus a
minute or more of cold vLLM start the first time each model is asked for
anything. With three models that cold start is paid three times and no more —
which is the whole reason for the model-major order. Multiply by pages × models,
then run it under `tmux`: an ssh session that drops takes the run with it, and
although a re-run resumes, the time already spent is gone.

```bash
tmux new -s atr
# … run the command …          detach: Ctrl-B d      reattach: tmux attach -t atr
```

---

## 4 · Publish the outputs

```bash
python -m agentic_historian publish-batch --run-dir $VLM_TEST_ROOT/atr_test_lassberg
# → thodel/lassberg  textrecognition/atr_test_lassberg
# → pull request against michaelscho/lassberg  main
```

A **pull request, not a push.** The edition repository is somebody else's and its
maintainer decides what enters it; a token with write access would work and would
make that decision for them. The commits land in our own fork
(`GITHUB_TEXT_FORK`), so this needs no rights on the target repository at all.

### Der Laßberg-Gemini-Korpus, publiziert 12. Oktober 2026

300 Seiten als weitere Version in [PR #3](https://github.com/michaelscho/lassberg/pull/3),
mit `--push` in den Branch des Forks, aus dem der PR offen ist:

```bash
python3 -m agentic_historian publish-batch \
  --run-dir $VLM_TEST_ROOT/gemini-300-final \
  --repo    thodel/lassberg \
  --branch  textrecognition/atr_corpus_qwen35_line \
  --path    data/textrecognition/gemini-300-final --push
```

`--repo thodel/lassberg` ist dabei nicht kosmetisch: ohne das ginge der Push
gegen die Edition selbst.

| | |
|---|---:|
| Seiten | **300** |
| mit Text | **281** |
| leer | 19 |
| `looped` / `arguing` / `cut off` | 0 / 0 / 0 |
| Dateien im Baum | 602 (300 `.txt`, 300 `.json`, `report.md`, `report.json`) |
| Prompt | `lassberg_atr_strict2 @9e0503e8`, `reasoning low` |

### Geprüft wird im publizierten Baum, nicht im Log

Das Publikations-Log sagt „200 file(s) (1/4)" viermal, und daraus lässt sich
rechnen, dass 602 Dateien gingen. Es sagt nicht, was in ihnen steht. Ein
Shallow-Sparse-Clone des Branches kostet Sekunden und beantwortet beides:

```bash
GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 --filter=blob:none --sparse \
  --branch <branch> https://github.com/thodel/lassberg verify
git -C verify -c core.quotePath=false ls-tree -r --name-only HEAD -- <pfad> | wc -l
```

Zwei Dinge, die dieser Blick zutage brachte und die kein Log gezeigt hätte:

- **Eine Seite trug zwei einander widersprechende Urteile** —
  `merged_from.verdict: ok` neben `quality.verdict: repetitive` (§ *`merge-runs`*).
  Gefunden, weil die Datei selbst gelesen wurde.
- **`core.quotePath`.** Ein erstes Zählen ergab 276 `.txt` statt 300 und sah aus
  wie ein unvollständiger Push. Git quotiert Pfade mit Nicht-ASCII, und die
  Laßberg-Schlüssel sind voll von `Zürrich` und `Mülinen` — die Zeilen enden
  dann auf `"` und nicht auf `.txt`. Eine Zählung, die von der Darstellung eines
  Werkzeugs abhängt, ist keine Zählung der Sache.

Und eine Nicht-Beobachtung, die wie ein Befund aussieht: ein `git fetch` in
einen **Shallow**-Clone meldet `+ alt...neu (forced update)` auch für ein
gewöhnliches Fortschreiben, weil die Abstammung jenseits der Shallow-Grenze
nicht beweisbar ist. Das ist kein Hinweis auf eine umgeschriebene Historie in
einem geteilten PR-Branch. Beweisen lässt es sich mit etwas mehr Tiefe:

```bash
git -C verify fetch --depth 12 origin <branch>
git -C verify merge-base --is-ancestor <alt> <neu> && echo fortgeschrieben
```

The branch is cut from the **upstream's** head, not the fork's. A fork that has
not been synced for a while has a stale `main`, and a pull request from a branch
off that stale main shows every upstream commit since as a deletion — a two-file
publish arriving as a mass revert of somebody else's month.

Destination and layout are in `config.py`, not in the command:
[`michaelscho/lassberg`](https://github.com/michaelscho/lassberg/tree/main/data)
`main`, under `data/textrecognition/<model id>/`. `--repo`, `--path`, `--base` and
`--fork` override for a one-off; `--push` commits directly, for a repository we
do own.

**Safe to run twice.** Re-running with the same branch adds commits to the pull
request that is already open rather than starting a second one — so "publish once
everything is collected" survives a corpus that gains pages afterwards.

The two halves of the corpus live in different places on purpose. The scans stay
in the Nextcloud share — mounted, never copied into git — and only the readings
are published.

Text only. `publish_tree` works from an allowlist of suffixes
(`.txt .json .md .jsonl .csv .xml`) and names anything it skipped — the scans
themselves are never committed, and a format nobody anticipated is left out
rather than pushed to a public repository.

Needs `GITHUB_TOKEN` with `contents: write` on **the fork** — not on the target.
Large trees are
split into commits of 200 files: one commit would be tidier, and a failure at
file 900 of a single commit publishes nothing at all. Re-running is safe — a
chunk that rewrites identical bytes is an empty diff.

---

## 4a · To the hub

The hand-corrected pages go to `dh-unibe` as a private dataset, built by
`pagexml-hf` from a tree this repository assembles. **[docs/HF_DATASET.md](HF_DATASET.md)**
has the run, and the geometry check that decides whether the export is usable at
all — mismatched line polygons crop the wrong strip of every page, and nothing
downstream says so.

---

## 4b · Comparing two readings

Two runs over the same share produce two directories and, until now, nothing that
read both. `compare-runs` joins them on the page key — identical across runs by
construction — and reports pairwise disagreement:

```bash
python -m agentic_historian compare-runs \
  --run-dir $VLM_TEST_ROOT/atr_trocr_corpus \
  --run-dir $VLM_TEST_ROOT/atr_corpus_qwen35_line
```

Only the intersection is compared; two runs of different coverage have no common
ground outside it. Pages empty on one side only, and pages too short on any side,
are counted and left out of the distribution — a five-character fragment against
an eight-character one disagrees by 75 % and says nothing about whether the
engines can read the hand. On the sample pair that moved the median from 45.9 %
to 16.8 %.

**The number is disagreement, not quality.** Without ground truth nothing here
says which reading is right (#326). What it settles is whether fusion is worth
trying: #416 measured majority voting *losing* to the best single engine where one
candidate dominates weaker ones, and winning only among comparable candidates with
uncorrelated errors. The `above no-merge` column is the share of pages
`fusion.fuse` would decline to blend at all (#300).

---

## Eine leere Antwort ist manchmal eine Eigenschaft des Versuchs

Das Rauschen zwischen zwei identischen Läufen hatte ich nie gemessen, und einen
Tag lang Prompt-Effekte aus Lauf-Differenzen gelesen. Nachgeholt am 11. Oktober:
`gemini-strict2-repeat` — dieselben 25 beanstandeten Seiten, **derselbe** Prompt
`@9e0503e8`, dieselbe Denkstufe, zweiter Versuch.

| | |
|---|---:|
| Seiten | 25 |
| in beiden Läufen leer | 21 |
| **leer im ersten, gelesen im zweiten** | **3** |
| umgekehrt | 0 |

Und was in diesen drei Seiten steht, ist nicht Beifang:

- `Winterthur__310-MsBRH_513-91-131` — **978 Zeichen**, ein vollständiger Brief
  von Justizrat Zellweger, Trogen, 27. April 1831. Diese Seite war in **vier**
  vorherigen Läufen leer: `gemini-300-strict`, `gemini-strict-silence` (anderer
  Prompt!), `gemini-strict2-check` und `gemini-300-strict2`. Beim fünften
  Versuch liest sie sich durch.
- `StATG …1339_00101` — 902 Zeichen, der Pupikofer-Brief. Dritter Versuch
  derselben Seite mit demselben Prompt: Text, leer, Text.
- `Marbach__HS00911718_0004` — leer → `[unlesbar]`.

**Das korrigiert zwei Aussagen von heute Vormittag.** „22 von 22 Leerseiten
bleiben leer" war die Kontrolle *eines* Versuchs, und `Winterthur` war unter
diesen 22 — als unbeschrieben verbucht, obwohl dort ein Brief steht. Und „21 der
28 sind unbeschriebene Rückseiten" hat dieselbe Seite als leer gezählt.

Auf gewöhnlichen Seiten ist die Streuung dagegen klein: 6,6 % Median zwischen
zwei *verschiedenen* Prompts über 240 Seiten. Das Rauschen sitzt nicht im
Fließtext, sondern an der Kippe zwischen „lese ich" und „antworte ich nichts".

### Zwei Versuche, nicht drei

Dritter Versuch über die 19 nach zwei Versuchen noch leeren Seiten
(`gemini-strict2-third`):

| Versuch | leer danach | neu gelesen | davon Text von Substanz |
|---|---:|---:|---|
| 1 (der 300er-Lauf) | 24 | — | — |
| 2 `repeat` | 21 | 3 | **2 vollständige Briefe** (978 + 902 Zeichen) |
| 3 `third` | 19 | 2 | **0** |

Die beiden Neufunde des dritten Versuchs sind Wortenden (`en, d [...]; nden
stels eiten mir,`) und eine als fremde Hand markierte Unlesbarkeit — beide
`short`, zu Recht. **Der zweite Versuch holt die Briefe, der dritte nur noch
Schnipsel.** Zwei Versuche ist also die Politik, die sich belegen lässt, und die
19 danach sind die plausibel wirklich leeren Blätter. Ein vierter Versuch könnte
theoretisch noch eine Seite umdrehen; gemessen ist nur, dass der Ertrag
kollabiert, nicht dass er null ist.

Nach Seiten gezählt sähe das aus wie 3, dann 2 — also kaum ein Abfall. Erst der
Blick in die Seiten zeigt den Einbruch. Eine Zählung von Seiten ist keine
Zählung von Text.

### `merge-runs`: die Versuche zu einem Korpus

Die Versuche liegen in getrennten Laufverzeichnissen, und `publish-batch`
publiziert genau eines. `merge-runs` schreibt daraus einen Lauf, der pro Seite
den **besten** Versuch hält:

```bash
python3 -m agentic_historian merge-runs \
  --run-dir …/vlm_test/gemini-300-strict2 \
  --run-dir …/vlm_test/gemini-strict2-repeat \
  --run-dir …/vlm_test/gemini-strict2-third \
  --out    …/vlm_test/gemini-300-final --dry-run
```

Aus einer Session: `merge_runs(runs=[…], run="gemini-300-final")`.

**Die Reihenfolge zählt** und ist die Aussage des Aufrufers, welcher Lauf der
kanonische ist: bei gleichem Urteil gewinnt der frühere. Entschieden wird nach
Urteil, nie nach Länge:

| Urteil | Rang | |
|---|---:|---|
| `ok` | 3 | eine Lesung der Seite |
| `short` | 2 | wenig, aber es steht auf der Seite |
| `empty` | 1 | behauptet nichts |
| `repetitive`, `deliberation` | 0 | **unter `empty`** |

Der letzte Rang ist die eigentliche Entscheidung und sie ist diskutabel: diese
Seiten tragen Text, der keine Lesung der Seite ist, und in einer Edition
publiziert ist das ausgewiesene Erfindung. Nichts ist besser als etwas
Erfundenes. Nach Länge zu entscheiden wäre genau falsch — eine `deliberation`-
Seite trägt 800 Zeichen Selbstgespräch und gewinnt jeden Grössenvergleich gegen
die Transkription, die sie ersetzt.

Jede zusammengeführte Seite behält die Nutzlast ihres Versuchs — `settings`,
`gateway_version`, Zeiten bleiben wahr — und bekommt einen `merged_from`-Block
mit dem gewählten Versuch und dem, was die anderen geantwortet haben. Ohne den
wäre das Ergebnis ein Korpus aus drei Läufen, das nicht sagen kann, welche Seite
aus welchem stammt. `SCHEMA` bleibt, wo es ist: der Block ist additiv, wie
`settings` in #613.

**Ein Feld wird neu gerechnet statt mitgenommen: `quality`.** Es ist der einzige
Teil der Nutzlast, der keine Tatsache über den Aufruf ist. `settings`,
`timing_ms` und `gateway_version` halten fest, was die API gefragt wurde und was
sie antwortete — die umzuschreiben wäre eine Lüge. `quality` ist ein Urteil, das
*dieser* Code aus dem Text ableitet, und ein mitgenommenes altes hat am
12. Oktober eine in die Edition publizierte Seite mit zwei einander
widersprechenden Urteilen hinterlassen:

```
merged_from.verdict : ok           ← vom Zusammenführen, heutige Regeln
quality.verdict     : repetitive   ← vom Lesen, vor #623
```

Der Marbacher Einlegezettel, dessen gedruckte Trennlinie seit #623 nicht mehr
als Füllmaterial gilt. Der mitgenommene Block hatte zudem die Masse nicht, die
der heutige Klassifikator speichert (`run_share`, `run_on_own_line`) — er liess
sich also nicht einmal aus seinen eigenen Zahlen neu beurteilen. Neu gerechnet
sind die beiden Urteile *konstruktionsbedingt* dieselbe Zahl, und die Seiten
eines zusammengeführten Laufs stimmen mit dem Bericht daneben überein.

### Was daraus folgt: n Versuche statt eines Blicks

Der Bericht sagt bei `empty` „look at the images". Das trägt bei 28 Seiten und
nicht bei 2800. Billiger und schärfer ist: **dieselbe Seite nochmal lesen** — und
nur die beanstandeten Seiten brauchen den zweiten Versuch, also 25 Aufrufe statt
300.

> **Korrektur vom 13. Oktober.** Hier stand: „Eine Seite ist leer, wenn sie in
> *jedem* Versuch leer ist." Das ist als Definition falsch, und zwar an der
> Stelle, die zählt — sie misst die Streuung *eines* Lesers, nicht die Seite.
> Vier Versuche desselben Prompts haben eine beschriebene Seite viermal
> verschwiegen; ein anderes Modell liest dort 24 Zeilen (§ *Der vierte Versuch
> holt nichts, ein zweiter Leser eine ganze Seite*). Richtig ist: eine Seite ist
> leer, wenn sie **mehrere Leser aus verschiedenen Familien** leer lassen.

Resumierbarkeit-durch-Anwesenheit verhindert das aktiv: `is_complete`
überspringt die Seite. Ein zweiter Versuch braucht deshalb einen **neuen
Laufnamen**, und der Vergleich der beiden Läufe ist die Messung.

```bash
python3 -m agentic_historian atr-batch … --run <lauf>-repeat \
  --keys-from "$GT_ROOT/<lauf>-flagged.txt" --prompt … --reasoning low
# dann: compare_readings(["<lauf>", "<lauf>-repeat"]) → "empty only in …"
```

## Der vierte Versuch holt nichts, ein zweiter Leser eine ganze Seite

Nach dem Merge blieben **19 Seiten** des publizierten Korpus leer, und die
belegte Erwartung war, dass dort nichts steht. Beides ist am 13. Oktober
nachgemessen worden, mit zwei Läufen und einem Dry-Run.

### Die Schlüsselliste ist das Werkzeug, nicht die Zahl

```bash
# 19 Schlüssel aus dem Bericht in eine Liste, die --keys-from liest
batch_report(run="gemini-300-final", keys_out="final-empty-19.txt")
# → /…/data/gt/final-empty-19.txt, 19 Schlüssel
```

Dieselbe Liste treibt danach jeden Gegenversuch. Ohne sie wäre jeder davon eine
Handauswahl und damit nicht wiederholbar (§ *10. A count is a complaint* in
`LESSONS_2026-10.md`).

### Vierter Versuch, derselbe Leser: 0 von 19

```bash
python3 -m agentic_historian atr-batch --source "dav:digitalisate" \
  --cache-dir "$ATR_PAGE_CACHE" --models gemini-3.8-flash \
  --run gemini-strict2-fourth --keys-from "$GT_ROOT/final-empty-19.txt" \
  --via external --prompt lassberg_atr_strict2.md --reasoning low
```

| | |
|---|---:|
| gelesen | 19 von 19, 0 Fehler |
| Wanduhr | 1,4 min · 4,3 s/Seite |
| **leer** | **19 von 19** |

Damit steht die Ertragskurve über vier Versuche: **3 → 2 → 0**. Die
Zwei-Versuche-Politik ist bestätigt, und „noch ein Versuch" ist als Hebel
erschöpft — nicht theoretisch, sondern gemessen.

### Zweiter Leser, zwei andere Familien: 1 von 19

```bash
python3 -m agentic_historian atr-batch --source "dav:digitalisate" \
  --cache-dir "$ATR_PAGE_CACHE" \
  --models qwen3.5-4b-german-xix-v2,trocr-kurrent-xvi-xvii \
  --run gateway-final-empty-19 --keys-from "$GT_ROOT/final-empty-19.txt"
```

19 Seiten × 2 Modelle = 38 Aufrufe, **5,9 min**, beide Modelle auf derselben
Karte, 0 Fehler. `trocr-kurrent-xvi-xvii` segmentiert Zeilen — es fragt nicht
„schreibe ich diese Seite aus", sondern „sind hier Zeilen".

| Modell | gelesen | leer | Zeichen/Seite |
|---|---:|---:|---:|
| `qwen3.5-4b-german-xix-v2` | 19 | 13 | 33 |
| `trocr-kurrent-xvi-xvii` | 19 | 13 | 43 |

**Dieselben 13.** Was übrig bleibt, zerfällt in zwei Gruppen, und die Trennung
ist keine Augenmaßfrage:

| | Seiten | Befund |
|---|---:|---|
| von drei Lesern aus drei Familien stumm | **13** | leer, so weit eine Maschine das sagen kann |
| Tintenspur, von beiden Segmentierern erfunden gelesen | **5** | Foliierung, Stempel, Durchschlag — kein Brieftext |
| **beschrieben** | **1** | `blb lassberg__K 2911,93__page_0001` |

### Woran man Rauschen erkennt, ohne das Bild zu sehen

Die fünf Seiten der Mittelgruppe sind der negative Befund derselben Messung, und
drei Masse belegen ihn:

- **Null gemeinsame Wörter** ab fünf Zeichen zwischen den beiden Lesungen, über
  alle fünf Seiten. Zwei Modelle, die dieselbe Tinte lesen, treffen sich
  irgendwo.
- **Jedes Modell wiederholt sich über verschiedene Blätter**: `qwen` schreibt
  `orchecker`/`classica` auf zwei Seiten, `trocr` setzt
  `haußhaltung zu Buchstetten verschicken` wörtlich auf zwei. Derselbe Satz auf
  zwei Blättern ist eine Eigenschaft des Modells.
- **`trocr` datiert sich aus dem Korpus heraus**: `1655`, `den 16t Aug.1792`,
  `Buchstetten`. Es ist auf das 16./17. Jahrhundert trainiert und greift in seine
  Trainingsverteilung; auf einem Laßberg-Blatt (1770–1855) kann 1655 nicht
  stehen.

Dass beide Modelle auf genau diesen fünf Seiten *etwas* produzieren und auf den
13 nichts, ist dabei selbst ein Befund: dort liegt eine Spur, an der sich beide
Segmentierer festmachen. Für eine davon gibt es eine unabhängige Aussage —
`UB Bern__Mss_h_h_XLIV_180_32_169` ist die Seite, über die derselbe Gemini unter
dem *ersten* Prompt „Vollständig unbeschrieben … Durchschlagen" geschrieben hat.
`strict2` verbietet ausdrücklich, Durchschlag zu transkribieren, antwortet dort
also richtig mit nichts.

### Die eine beschriebene Seite

`blb lassberg__K 2911,93__page_0001`, bei Gemini in vier Versuchen leer. Beide
lokalen Modelle liefern **24 Zeilen**, und 14 der 24 stimmen überein:

| # | `qwen3.5-4b-german-xix-v2` | `trocr-kurrent-xvi-xvii` |
|---:|---|---|
| 1 | `K 2941,93` | `k 2911 , 93` |
| 2 | `67` | `67` |
| 3 | `Gottesvollgeboren` | `Hochwolgebohrner` |
| 5 | `berinnigen Monate habe ich nun gestern` | `hier einigen Monatio habe ich inen giessen` |
| 7 | `benden für einen Dienst erlinder unss` | `branntus für einen dienst erliedern errichten` |
| 17 | `zu führen.` | `zuführen ,` |
| 24 | `… nicht neutral. welche diese Re-` | `… nicht naturalis , welche diche Hal¬` |

Gleiche Zeilenzahl aus zwei Architekturen, dieselbe Signatur in Zeile 1,
dieselbe `67` in Zeile 2, eine Anrede in Zeile 3, und beide brechen die letzte
Zeile mit einem Trennstrich ab — der Text läuft auf die Folgeseite weiter.
**Keine der beiden Lesungen ist brauchbar**; dass dort ein beschriebenes Blatt
liegt, ist damit so gut belegt, wie es ohne Bild geht.

Und es ist ein **Prompt-Verstoß**, kein Grenzfall. `strict2` sagt wörtlich, eine
leere Antwort behaupte, das Blatt sei unbeschrieben, und eine Seite, von der man
nichts lesen kann, sei `[unlesbar]`. Viermal nimmt das Modell genau die
Abkürzung, die der Satz verbietet. Ein Satz im Prompt ist keine Durchsetzung.

**Eine Kette ist nicht geschlossen:** beide Läufe lesen über denselben
`--cache-dir`, also dieselbe Arbeitskopie. Nachweisen liesse sich das über die
`source.sha256` beider Seiten-JSONs. Wäre die Arbeitskopie beschädigt, hätte
`qwen3.5` daraus keine 462 Zeichen geholt — der Schluss ist sehr wahrscheinlich
und nicht verifiziert.

### `merge-runs` kann das nicht flicken, und das ist die sichere Seite

Der naheliegende Reflex war, die Gateway-Lesung über `merge-runs` in den Korpus
zu holen. Als Dry-Run gemessen statt vermutet:

```
merge_runs(runs=["gemini-300-final", "gateway-final-empty-19"], …, dry_run=True)
→ pages: 338   improved: []   38 page(s) appear only in a later run
```

**Eine Seite wird über (Modell, Schlüssel) identifiziert, nicht über den
Schlüssel allein.** 300 + 38 = 338: die Gateway-Lesungen kollidieren gar nicht
mit den Gemini-Seiten, sie landen daneben, und `improved` ist leer, weil nichts
verglichen wurde.

Das ist die sichere Seite des Irrtums. Über den Schlüssel allein hätten die
**fünf Rauschseiten** mit `short` (Rang 2) das `empty` (Rang 1) geschlagen, und
`orchecker classica` stünde als Transkription in einer Edition. Die Rangfolge
schützt gegen `repetitive` und `deliberation` — gegen eine *kurze, flüssige*
Erfindung eines fremden Modells schützt sie nicht. Ein Urteil ist innerhalb
eines Lesers eine Währung und zwischen zwei Lesern keine.

### Was damit korrigiert ist

- „Die 19 danach sind die plausibel wirklich leeren Blätter" → **18 plausibel
  leere und eine beschriebene.**
- „Eine Seite ist leer, wenn sie in jedem Versuch leer ist" → gilt pro Leser,
  nicht pro Seite (Korrektur oben).
- Publiziert ist nichts Falsches: eine leere `.txt` behauptet nicht, dass das
  Blatt leer ist. Was fehlt, ist der Hinweis, welche der 19 beschrieben ist —
  und das ist ein Nachtrag an der Edition, keine Änderung am Baum.

## Der Eval-Split der HTR-Ground-Truth, mit Gemini gelesen — und zum ersten Mal ein CER

22. Oktober 2026. Das HTR-Trainingsprojekt hält auf dem Forschungsspeicher eine
zufällige Aufteilung seiner Transkribus-Ground-Truth in `train`/`dev`/`eval`
(`Projekt_Lassberg/HTR/data/ground_truth/manifests/`, auf tei unter
`/mnt/wbkolleg_dh_1/…`). `eval_manifest_random.txt` nennt **39 PAGE-XML-Dateien
aus 12 Dokumenten**, als Pfade `…/documents/done_<Titel>_<docId>/<Seite>.xml`
(acht Seiten `lassberg-letter-1781`, acht `lassberg-letter-0077`, sieben
`lassberg-letter-1209`, dazu drei BLB-Konvolute `K 2911,…` und sechs Briefe mit
ein bis zwei Seiten). Die Frage: wie liest `gemini-3.8-flash` genau diese
Seiten, mit genau den Einstellungen des 300er-Laufs — und weil es GT-Seiten
sind, erstmals **mit Referenz**.

**Es brauchte keinen neuen Code**, nur die Reihenfolge, in der die Werkzeuge
schon zusammenpassen:

1. Die 39 Dateien in einen Ordner unter `GT_ROOT` kopieren. Der Server nimmt
   Namen, keine Pfade (#509), und `001.xml` kommt in mehreren Dokumenten vor,
   also trägt jede Kopie ihren Dokumentordner im Namen:

   ```bash
   GT=/home/dh/agentic_historian/agentic_historian/data/gt
   M=/mnt/wbkolleg_dh_1/Projekt_Lassberg/HTR/data/ground_truth/manifests/eval_manifest_random.txt
   mkdir -p "$GT/eval-random"
   while IFS= read -r f; do
     src="${f/#\/storage\/research\//\/mnt\/}"
     cp "$src" "$GT/eval-random/$(basename "$(dirname "$src")")__$(basename "$src")"
   done < "$M"
   ```

   Die Manifest-Pfade beginnen mit `/storage/research/wbkolleg_dh_1/` — der
   Mountpunkt der Maschine, die das Manifest geschrieben hat; auf tei heisst
   derselbe Share `/mnt/wbkolleg_dh_1/`.

2. Die Seiten **über ihren Inhalt** im Korpuslauf finden und die Schlüssel
   schreiben — `score_ground_truth` gegen `atr_corpus_qwen35_line`, den Lauf, der
   im September das ganze Share gelesen hat (6719 Seiten, also auch die seit dem
   4. Oktober verschwundenen):

   ```
   score_ground_truth(runs=["atr_corpus_qwen35_line"], gt_dir="eval-random",
                      keys_out="eval-random.txt")
   ```

   | | |
   |---|---:|
   | Dateien im Manifest | 39 |
   | ohne transkribierten Text (übersprungen, benannt) | 1 — `lassberg-letter-1209 …/007.xml` |
   | Status | 34× `FINAL`, 4× `Done` |
   | eindeutig lokalisiert → `eval-random.txt` | **35** |
   | nicht sicher lokalisiert (im Bericht, nicht in der Liste) | 3 |

   Und als Nebenbefund die Zahl, die vorher niemand hatte — das Qwen-Zeilenmodell
   auf dem Eval-Split: **Median-CER 14,6 %, p90 31,6 %, beste Seite 6,6 %,
   schlechteste 37,4 %** (35 Seiten). Das ist die Vergleichsgrösse für Gemini,
   gemessen an derselben Referenz.

3. Der Gemini-Lauf, Einstellungen des 300er-Laufs (`gemini-300-final`:
   `lassberg_atr_strict2.md @9e0503e8`, `reasoning low`, Deckel 8192 → Settings
   `prompt-9e0503e8+think-low|max8192`), erst trocken, dann drei Seiten, dann
   alle:

   ```
   start_batch(models=["gemini-3.8-flash"], run="gemini-eval-random",
               source="dav:digitalisate", keys_from="eval-random.txt",
               via="external", prompt="lassberg_atr_strict2.md", reasoning="low",
               missing_out="eval-random-missing.txt", dry_run=True)
   ```

   `missing_out`, weil der Share seit dem 4. Oktober 35 der 276 GT-Seiten nicht
   mehr hält und ein anderswo gezogener Split Seiten nennen kann, die hier
   fehlen. Der Trockenlauf traf auf eine abgelaufene Listing-Cache (48 h) und
   begann die volle Begehung des Shares — und starb nach zwölf Minuten und 520
   Ordnern am 521.: `St. Gallen Kantonsbibliothek/lassberg-letter-1648`
   antwortete 500, dann 404. **Das war ein Defekt im Walker, kein Pech.** Ein
   Ordner, der nach dem Listen seines Elternordners selbst 404 antwortet, kam
   als webdav4-`ResourceNotFound` an — weder einer der Fehler, die `_ls`
   wiederholt, noch der `NextcloudError`, den `_walk` als „unlesbar, übersprungen,
   nicht gecacht" behandelt. Seit diesem Tag wird ein 404 unterhalb der Wurzel
   sofort (ohne Wiederholung: der Ordner ist nicht da) zu diesem Fehler und
   damit zu einem übersprungenen Ordner; ein 500/502/503 wird wie ein Timeout
   wiederholt; eine Ausnahme ohne Status bleibt ein Fehler im Client und
   propagiert. Die Wurzel bleibt fatal.

   **Der Weg um die Begehung herum** ist das Kaltarchiv des Seiten-Caches
   (`ATR_PAGE_CACHE_ARCHIVE`): der Share-Cache legt Seiten als
   `<Bestand>/<Ordner>/<Seite>.jpg` ab, ohne Präfix, also ergibt ein
   Verzeichnis-Lauf darüber **dieselben Schlüssel**, und der 300er-Lauf hat 125
   seiner Seiten genau aus diesen Arbeitskopien gelesen. Eine `.jpg`-Quelle wird
   beim Cachen byte-kopiert, nie neu kodiert (`CONVERTIBLE_SUFFIXES` kennt kein
   JPEG) — gleiche Pixel. Nur `_roots()` der MCP kennt das Archiv nicht, darum
   per CLI im Produktions-Checkout:

   ```bash
   cd /home/dh/agentic_historian
   .venv/bin/python3 -m agentic_historian atr-batch \
     --source /mnt/wbkolleg_dh_1/tei/archive/agentic_historian-page_cache \
     --models gemini-3.8-flash --run gemini-eval-random \
     --keys-from agentic_historian/data/gt/eval-random.txt \
     --missing-out agentic_historian/data/gt/eval-random-missing.txt \
     --via external --prompt lassberg_atr_strict2.md --reasoning low --dry-run
   ```

   Dann, per CLI gegen das Archiv: Rauchtest `--limit 3`, Kontrolle des
   Textendes einer Seite (eine Seite am Deckel bricht mitten im Satz ab und
   sieht sonst wie ein Erfolg aus — sie endete in „Es ist mein sehnlicher",
   ein Satzanfang, aber `cut off: 0` und `truncated: false` sagen, dass das
   Modell selbst aufgehört hat), dann der Rest. Lauf `gemini-eval-random`:

   | | |
   |---|---:|
   | gelesen | **35 von 35**, 0 Fehler, 0 übersprungen |
   | Wanduhr | 2,9 min · 5,3 s/Seite · 32 Seiten aus dem Archiv geholt |
   | `cut off` / `looped` / `arguing` | 0 / 0 / 0 |
   | `empty` | 1 — `blb lassberg__K 2911,110__page_0002` |
   | Urteile | 34× `ok`, 1× `empty`, 0× `short` |
   | Zeichen/Seite | 1076 |

4. Die Tabelle, die Qualität ist: beide Leser gegen dieselbe Referenz.

   ```
   score_ground_truth(runs=["gemini-eval-random", "atr_corpus_qwen35_line"],
                      gt_dir="eval-random")
   ```

   | Leser | Seiten | Median-CER | p90 | beste | schlechteste |
   |---|---:|---:|---:|---:|---:|
   | `gemini-eval-random/gemini-3.8-flash` | 34 | **4,8 %** | 12,8 % | 0,7 % | 17,1 % |
   | `atr_corpus_qwen35_line/qwen3.5-4b-german-xix-v2` | 34 | 14,5 % | 31,7 % | 6,6 % | 37,4 % |

   34 statt 35, weil die Lokalisierung jetzt die **Einigkeit beider** Lesungen
   verlangt und die eine Seite, die Gemini leer zurückgab, sich über ihren
   (fehlenden) Inhalt nicht platzieren lässt. Dieselbe Regel, die das
   Qwen-Ergebnis von 14,6 % (allein) auf 14,5 % (zu zweit) verschiebt: ein
   anderer Nenner, nicht ein anderes Modell.

   **Gemini liest den Eval-Split dreimal so genau wie das feinjustierte
   Zeilenmodell.** Die schlechteste Gemini-Seite (17,1 %) liegt über dem
   Qwen-Median (14,5 %), aber weit unter dessen p90 (31,7 %); die beste
   Qwen-Seite (6,6 %) ist besser als Geminis p90 (12,8 %). Die Spannen sagen
   also, wo der Unterschied sitzt: die leichten Seiten liest das Zeilenmodell
   ordentlich, verloren wird auf den schweren Händen, wo Qwen bis 37 % geht
   und Gemini bei 17 % bleibt.

**Was der Lauf nicht misst:** Gemini liest ohne Zeilengeometrie, Qwen über die
Segmentierung des Gateways; beide landen als Seitentext in derselben
CER-Rechnung, aber ein Fehler des Segmentierers zählt beim einen und kann beim
anderen nicht vorkommen. Und 35 Seiten aus 12 Dokumenten sind 12 Hände, nicht 35
— eine Seite mehr von Laßberg selbst verschiebt den Median spürbar. Der
Eval-Split ist die Referenz des *Trainingsprojekts*, zufällig über Dokumente
gezogen; dass er für einen Vergleich zweier Leser fair ist, folgt daraus nicht,
aber er ist derselbe Massstab für beide. **Und zwar zugunsten von Qwen, nicht
gegen es:** `qwen3.5-4b-german-xix-v2` ist im September auf der
Ground Truth dieses Projekts feinjustiert worden, die Aufteilung in
`train`/`dev`/`eval` stammt vom 8. Oktober — ob die 35 Eval-Seiten in Qwens
Trainingsmenge lagen, sagt kein Protokoll hier, aber ausschliessen lässt es
sich nicht. Gemini hat sie sicher nie gesehen. Ein Vorsprung des Zeilenmodells
wäre damit zu erklären gewesen; der Rückstand nicht.

## Ein Lauf aus 80 Unterstrichen ist eine Linie

Die dritte Seite dieses Korpus, die für ihr eigenes Layout als Füllmaterial
beschuldigt wurde. Die erste war eine rechtsbündige Unterschrift mit 34
Leerzeichen (#606). Diese ist ein Einlegezettel des DLA Marbach, von
`strict2` korrekt transkribiert und korrekt als `[fremde hand: …]`
gekennzeichnet — mit einer gedruckten Trennlinie aus 80 Unterstrichen.

`_` namentlich auszuschliessen wäre der dritte Flicken derselben Form gewesen,
und das nächste Formular zieht seine Linie mit Punkten. Was eine Linie von
Füllmaterial trennt, ist nicht das Zeichen, sondern **wo der Lauf sitzt**:

| | Anteil | eigene Zeile | |
|---|---:|---|---|
| 20 Nullen am Satzende | 0,14 | nein | Füllmaterial |
| 80 Unterstriche im Formular | 0,10 | ja | **Linie** |
| 3000 Nullen in einer Textzeile | 0,997 | nein | Füllmaterial |
| 8000 Nullen als eigene Zeile | 0,99 | ja | Füllmaterial (Anteil) |

Ein langer Lauf wird also nur verschont, wenn er **eine Zeile für sich** ist
*und* unter einem Viertel der Seite bleibt. Beide Masse stehen in jedem
Ergebnis (`run_share`, `run_on_own_line`), eine Schwellenänderung bleibt
Arithmetik. Je ein gemessener Fall pro Ecke — dünn, aber jede Ecke ist eine
echte Seite dieses Korpus.

## Welcher Code hat geantwortet?

Nicht jede Änderung braucht denselben Deployment-Schritt, und der Unterschied
hat am 11. Oktober dreimal zugeschlagen:

| was sich geändert hat | nötig auf tei |
|---|---|
| Prompt, CLI-Flag, Klassifikator **für einen Lauf** | `git pull` — `start_batch` startet einen Unterprozess, der die Dateien frisch liest |
| alles, was `batch_report`, `flagged_keys`, `letter_register` **im Server** rechnet | `git pull` **und** `systemctl restart atr-mcp` |

Ein langlebiger Prozess ist ein Cache deines Quelltexts. Darum trägt die Antwort
von `batch_report` jetzt einen `code`-Block:

```json
"code": {"loaded": "5bc95782919c", "checkout": "5bc95782919c",
         "stale": false, "started_at": "2026-10-11T10:31:54+00:00"}
```

`loaded` steht fest, sobald das Modul **importiert** wurde — das ist der Code,
der tatsächlich antwortet. `checkout` wird jetzt gelesen. `stale: true` heisst
also: jemand hat gepullt und nicht neu gestartet, und jede Zahl in dieser
Antwort ist die des alten Codes. `stale: null` heisst, es lässt sich nicht
feststellen — nicht dasselbe wie `false`.

Die naheliegende Implementierung wäre falsch gewesen: `provenance.git_commit()`
liest die Platte und hätte nach einem Pull ohne Restart den **neuen** Commit
gemeldet, während der alte Code antwortet. Das ist keine fehlende Angabe, das
ist eine falsche Beruhigung.

### Deployment ist eine Abstammungsfrage, keine Gleichheitsfrage

`loaded` gegen den Commit zu prüfen, den man gemerged hat, ist der falsche Test.
Am 14. Oktober meldete der Server nach einem Pull `3c360ccd`, mein Merge war
`a94d80d` — und das Deployment war trotzdem korrekt, weil `main` inzwischen zwei
weitere PRs aufgenommen hatte. Eine Gleichheitsprüfung hätte einen richtigen
Zustand als Fehlschlag gemeldet und einen 27-Minuten-Lauf verhindert.

Die Frage ist nicht „ist es *mein* Commit", sondern „**enthält** es meinen":

```bash
git fetch origin main
git merge-base --is-ancestor <mein-commit> <loaded> && echo enthalten
```

Zwei Fälle, in denen die Abstammung ebenfalls die einzige brauchbare Antwort
ist, und beide sind in diesem Projekt aufgetreten:

- **Squash-Merge.** Der Branch-Commit ist nach dem Merge *kein* Vorfahre von
  `main` — der Squash ist ein neuer Commit mit demselben Inhalt. Wer vor einem
  `--force-with-lease` prüfen will, ob nichts verloren geht, vergleicht
  deshalb **Bäume**, nicht Historie:
  `git diff --stat <branch-commit> origin/main -- <die berührten Dateien>`,
  und leer heisst: der Inhalt ist drin.
- **Shallow Clone.** Dort scheitert `--is-ancestor` jenseits der Shallow-Grenze
  und `git fetch` meldet `(forced update)` für ein gewöhnliches Fortschreiben
  (§ *Geprüft wird im publizierten Baum*). Erst `--depth 12` macht die Frage
  entscheidbar.

## 5 · Reading the result

`report.md` gives, per model: pages read, pages skipped, pages failed, **pages
empty**, **pages cut off**, characters per page, seconds per page, wall time.

**On a resumed run, rebuild it first.** The report records what the runner
observed, and a resumed run observes most of its corpus as `skipped` — counting
nothing about it, including how many pages came back empty. `report-run --run-dir
<dir>` reads the results on disk instead. (The 2026-09-17 corpus run had 77 blank
pages of 899, 8.6 %, and its original report had no empty column at all.)

**Read the "arguing" column with the "cut off" one.** A page can hit the ceiling
because it is long, or because the model talked itself through the hand until the
budget ran out — and on 2026-10-11 the second was **three of four**. Raising the
ceiling buys more arguing, so check `arguing` before buying tokens; those pages
need another prompt or another thinking level (§ *Drei von vier „abgeschnittenen"
Seiten waren keine Transkription*).

**Then the "cut off" column.** It counts pages where the model stopped at its
token ceiling instead of at the end of the text. Those readings are real but
short, and they end mid-sentence — indistinguishable, reading them, from a model
that gave up. If the column is not zero, raise the ceiling —
`ATR_VLLM_MAX_NEW_TOKENS` on the gateway, `GEMINI_MAX_TOKENS` for an external
reader — delete the affected `.json` files, and re-run the same command: only the
missing pages are read again. Before buying tokens, check the thinking level: a
reasoning model spends the same ceiling on thinking before it writes a character,
and `--reasoning low` bought back 300 pages that a larger ceiling would have been
billed for (§ *The ceiling is spent on thinking first*).

### The pages a report flags are a selection

Empty, padded and cut off are three defects with three remedies and one next
step: read those pages again. The report names them, and `--keys-out` writes
them as the page-key list `--keys-from` takes, so that step is a selection
rather than an instruction to a reader:

```bash
# what went wrong in this run, as a list
python3 -m agentic_historian report-run \
  --run-dir agentic_historian/data/vlm_test/gemini-300-strict \
  --dry-run --keys-out agentic_historian/data/gt/strict-flagged.txt
# → flagged: 33 page(s)

# read exactly those again, under whatever the report said to change
python3 -m agentic_historian atr-batch --source dav:digitalisate \
  --models gemini-3.8-flash --run gemini-300-strict-retry \
  --keys-from agentic_historian/data/gt/strict-flagged.txt \
  --via external --prompt lassberg_atr.md --reasoning low
```

Over MCP it is `batch_report(run=…, keys_out="strict-flagged.txt")` — a name
under the ground-truth root, never a path — and then
`start_batch(keys_from="strict-flagged.txt", …)`.

Two things the keys are counted from, not parsed: `report.md` caps each list at
twenty names for reading, and a **resumed** run's report says nothing about the
pages it skipped. `--keys-out` and the MCP tool both walk the results on disk, so
neither inherits either gap. `report.json` carries every key as well.

It counts zero if the gateway does not report truncation at all (before
serving-atr-inference#123), so a zero there is "not reported", not "did not
happen" — which is another reason to read the end of a page by hand on the first
run.

**None of those columns is quality.** There is no ground truth in a run like this.
Characters per page says how much a model wrote, not how much of it is right, and
a model that hallucinates fluently leads that column. Confidence is the model's
opinion of itself. The comparison is the point — three readings of the same page,
side by side, for a historian to look at — and the numbers only say what each run
cost and whether it finished.

Measuring accuracy is a different job and a different instrument: transcribed
lines and `eval/linebench.py` (see [EVALUATION_HARNESS.md](EVALUATION_HARNESS.md)).
Two cautions if you go there with these models:

- all three were trained on the same four corpora, listed in their registry
  entries. An evaluation set drawn from any of them measures memorisation.
- the CER on their model cards (≈1 %) is each run's **own** held-out split. It
  says the training converged. It does not transfer to a corpus they have not
  seen, and it is not comparable to any other CER in this project.

---

## Configuration reference

| variable | default | what it does |
|---|---|---|
| `NEXTCLOUD_SHARE_URL` | — | the share, as pasted from a browser |
| `NEXTCLOUD_SHARE_PASS` | — | share password (empty if it has none) |
| `NEXTCLOUD_REMOTE_DIR` | `""` | folder inside the share |
| `NEXTCLOUD_STAGING_DIR` | `data/nextcloud` | where the mirror lands |
| `ATR_PAGE_CACHE` | — | default `--cache-dir`; empty = read pages where they are |
| `NEXTCLOUD_LISTING_TTL_S` | `172800` (48 h) | how long a cached share listing stays usable; `0` disables it |
| `GITHUB_TEXT_REPO` | `michaelscho/lassberg` | where recognised text is published |
| `GITHUB_TEXT_BRANCH` | `main` | branch the pull request targets |
| `GITHUB_TEXT_FORK` | `thodel/lassberg` | where the commits land; set equal to the repo to branch inside it |
| `GITHUB_TEXT_PATH` | `data/textrecognition` | path prefix inside that repo |
| `VLM_TEST_ROOT` | `data/vlm_test` | root for comparison runs |
| `ATR_BATCH_PAGE_CONCURRENCY` | `1` | pages in flight per model |
| `ATR_BATCH_RETRIES` | `2` | retries per page for timeouts and 5xx |
| `ATR_GATEWAY_URL`, `ATR_API_KEY` | — | the gateway (existing) |
| `ATR_HTTP_TIMEOUT` | `300` | per-call budget; must not be tighter than the gateway's own |

`ATR_BATCH_PAGE_CONCURRENCY` defaults to 1 on purpose. The gateway already
recognises the lines of one page about six at a time, so a second page in flight
queues against the same GPU rather than finding an idle one — and it makes a
failure harder to attribute. Raise it only for an engine that leaves the GPU idle
between lines, and check `report.md` actually improved.
