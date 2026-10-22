# Publishing the Laßberg ground truth to the hub

The hand-corrected pages of the Laßberg correspondence, as a private dataset
under `dh-unibe`, in the same shape as the fourteen `dh-unibe/image-text_*`
datasets that already exist.

## This repository does not convert anything

[`pagexml-hf`](https://github.com/The-Flow-Project/pagexml-hf) turns Transkribus
PAGE XML into a parquet dataset on the hub, and every existing `dh-unibe` export
was built with it. A second converter here would mean a second column layout for
the trainer to tolerate — `serving-atr-inference`'s `hf_source.py` already carries
aliases (`xml_content`/`xml`, `project_name`/`project`) because that happened once,
and the afternoon it cost is in that file's comments.

So `export-hf` produces that converter's **input** and stops:

```
<export>/<project>/<page>.jpg
<export>/<project>/page/<page>.xml
```

## From a session

`export_hf` over MCP runs the same CLI as a job, so a session that cannot reach
tei no longer has to dictate the command:

```
export_hf(runs=["atr_gt_candidates"])                 # plans, writes nothing
export_hf(runs=["atr_gt_candidates"], dry_run=False)  # writes the tree
```

```
upload_hf(tree="hf-export-20261005T143000")                 # checks, uploads nothing
upload_hf(tree="hf-export-20261005T143000", dry_run=False)  # uploads, private
```

`tree` is the **name** of a directory under `VLM_TEST_ROOT`, not a path: a caller
chooses which export to upload and never a location on the host.

**The upload is always private over MCP.** The CLI can upload without
`--private`, and then demands `--yes` as well, because the difference between the
two states is whether a collection of unpublished archival images sits on the open
web — and deleting the dataset afterwards does not undo that. The MCP tool has no
parameter for it and the flag is absent from the command it builds. Private is
also what makes an upload from a session unremarkable rather than a publication.

**The geometry check cannot be turned off** from either tool's MCP surface — a
page whose XML geometry disagrees with its image crops the wrong strip out of
every line, and nothing downstream of the dataset would say so, which is a thing
to decide while looking at the pages. `--source` and `--archive` are not passed
either, for the reason in "Do not pass `$ATR_PAGE_CACHE`" below.

### What the dry run checks

Every one of these otherwise surfaces minutes into a transfer, from inside a tool
this repository does not own:

| | Why it is checked here |
|---|---|
| the tree exists and holds PAGE XML | an empty directory uploads an empty dataset |
| an image for every XML file | `pagexml-hf` pairs them by stem and silently drops the odd ones |
| `pagexml-hf` on PATH | otherwise a `FileNotFoundError` from `subprocess` |
| a token is configured | otherwise a 401 after the first file. Reported as present or absent, never printed |
| the repo id is `owner/name` | otherwise a repository nobody meant, named after a flag |

## The run

```bash
# 1. which corpus pages the ground truth belongs to, and the plan
python -m agentic_historian export-hf --run-dir atr_corpus_qwen35_line --dry-run
#   --source defaults to the cache the runner itself uses
#   --archive defaults to ATR_PAGE_CACHE_ARCHIVE; see "Where the pages are" below

# 2. write the tree
python -m agentic_historian export-hf \
  --run-dir atr_corpus_qwen35_line --out /tmp/lassberg-hf

# 3. upload, private
export HF_TOKEN=…
pagexml-hf /tmp/lassberg-hf \
  --repo-id dh-unibe/image-text_lassberg-correspondence_xix --private --mode raw_xml
```

`--mode raw_xml` keeps the PAGE XML in the dataset, which is the form
`hf_source.row_to_page` materialises and the one that leaves line segmentation to
the trainer. `--mode line` would cut the crops here instead; that is a separate
decision and the geometry check below is what makes it safe to take.

## The check that decides whether this is usable at all

The PAGE XML's line polygons are in the pixel coordinates of **Transkribus's** copy
of the scan. The JPEG beside them is this pipeline's working copy of the **share's**
scan. If those differ in size — a different derivative, a different scan, a
rescaled export — every polygon crops the wrong strip of every page.

That failure is invisible downstream. The crops are plausible images, the text is
real text, and the only symptom is a model that trains badly for no stated reason.
So `export-hf` compares the XML's `imageWidth`/`imageHeight` against the actual
JPEG and **refuses** a page that disagrees. It does not rescale: rescaling is a
guess about which of two scans is authoritative, and this tool has no standing to
make it. A page whose XML records no geometry is refused for the same reason —
absent is not "matches".

`--no-geometry-check` exists for the case where somebody has established that the
two scans are the same and the attribute is simply wrong. It is off by default.

### Die Zahl ist 43, nicht 27 — und das Paar sagt, was zu tun ist

Zwei Korrekturen in einer. **43** Seiten wies der Lauf vom 13. Oktober wegen
Geometrie ab; die 27, die ich wochenlang als „Geometrie-Abweisungen" mitgeführt
habe, sind die Spalte `unbestimmt` desselben Berichts — Seiten ohne erkennbare
Datumszeile. Zwei Zahlen aus einer Ausgabe, eine davon falsch erinnert, und keine
Prüfung dazwischen. Dieselbe Familie wie § *5. Files are not pages* in
`LESSONS_2026-10.md`.

Und die Zahl allein beantwortet nichts. Der Plan nannte fünf Namen von 311
Abweisungen, und aus fünf lässt sich keine Ursache bestimmen — die naheliegende
Reaktion wäre `--no-geometry-check` gewesen, also genau die Entscheidung, die
dieser Abschnitt verbietet. Was entscheidet, ist das **Paar der beiden Grössen**,
und es entscheidet durch Arithmetik:

| Signatur | was sie bedeutet | reparierbar |
|---|---|---|
| Breite/Höhe vertauscht | ein Scan, gedreht | ja, durch Drehen |
| ein oder zwei Pixel daneben | ein Renderer gegen einen anderen | ja, es *ist* die Seite |
| ein Faktor auf beiden Achsen | eine skalierte Ableitung | ja, durch Multiplizieren |
| anderes Seitenverhältnis | zwei verschiedene Beschnitte | **nein** |

Darum trägt `Rejected` beide Grössen als Zahlen *neben* dem Satz statt in ihm,
`Plan.signatures` gruppiert danach, und der Bericht nennt pro Signatur die
Seitenzahl und die Deutung:

```
the geometry rejections are N distinct size pair(s) …
    12 page(s)  XML 3333x4391 → image 1420x1781
        different crop: 2.347 wide against 2.465 high, aspect 0.7591 against
        0.7973 — two derivatives of the page, not one rescaled
```

Die Seiten selbst gehen in eine Datei, weil eine Zählung eine Beschwerde ist und
nur die Schlüssel eine Aufgabe sind (§ *10* in `LESSONS_2026-10.md`, dort für
`batch_report`, hier dasselbe im zweiten Bericht):

```bash
python3 -m agentic_historian export-hf --run-dir … --dry-run \
  --writers-out  "$VLM_TEST_ROOT/hf-writers.tsv" \
  --rejected-out "$VLM_TEST_ROOT/hf-rejected.tsv"
```

Aus einer Session schreibt `export_hf` beide Tabellen von selbst; die Pfade stehen
im Ergebnis unter `writers` und `rejected`. Das ist eine Änderung im
**Serverprozess** — `git pull` **und** `systemctl restart atr-mcp`, siehe
§ *Welcher Code hat geantwortet?* in `BATCH_ATR.md`.

### Die vollständige Tafel: 21 Paare, zwei Gruppen

Lauf vom 14. Oktober, alle 43 Abweisungen aus `hf-rejected-*.tsv` nach Signatur:

| Seiten | XML | Bild | Faktor B / H | Abw. | Deutung |
|---:|---|---|---|---:|---|
| 8 | 3333×4362 | 1420×1781 | 2,347 / 2,449 | 4,2 % | Beschnitt |
| 4 | 3333×4391 | 1420×1781 | 2,347 / 2,465 | 4,8 % | Beschnitt |
| 4 | 3333×4304 | 1420×1781 | 2,347 / 2,417 | 2,9 % | Beschnitt |
| 3 | 1698×2568 | 1655×2549 | 1,026 / 1,008 | 1,8 % | Beschnitt |
| 2 | 2923×3690 | 1267×1612 | 2,307 / 2,289 | 0,78 % | Beschnitt |
| 2 | 1770×2598 | 1296×1636 | 1,366 / 1,588 | 14,0 % | Beschnitt |
| 1 | 1182×1506 | 1296×1636 | 0,912 / 0,921 | 0,92 % | Beschnitt |
| 1 | 791×1158 | 1296×1636 | 0,610 / 0,708 | 13,8 % | Beschnitt |
| 1 | 2479×3508 | **2968×2553** | 0,835 / 1,374 | 39,2 % | Beschnitt |
| 1 | 1566×2639 | **1566×1325** | **1,000** / 1,992 | 49,8 % | Beschnitt |
| 4 | 1770×2598 | 785×1152 | 2,2548 / 2,2552 | 0,02 % | **skaliert** |
| 2 | 2923×3690 | 1296×1636 | 2,2554 / 2,2555 | 0,00 % | **skaliert** |
| 2 | 1818×2457 | 913×1233 | 1,9912 / 1,9927 | 0,07 % | **skaliert** |
| 1 | 1818×2457 | 1148×1551 | 1,5836 / 1,5841 | 0,03 % | **skaliert** |
| 1 | 2171×2803 | 1371×1770 | 1,5835 / 1,5836 | 0,01 % | **skaliert** |
| 1 | 1541×2309 | 773×1159 | 1,9935 / 1,9922 | 0,07 % | **skaliert** |
| 1 | 1258×2015 | 775×1240 | 1,6232 / 1,6250 | 0,11 % | **skaliert** |
| 1 | 1258×2015 | 600×960 | 2,0967 / 2,0990 | 0,11 % | **skaliert** |
| 1 | 1258×1707 | 600×814 | 2,0967 / 2,0971 | 0,02 % | **skaliert** |
| 1 | 3137×4016 | 1192×1527 | 2,6317 / 2,6300 | 0,07 % | **skaliert** |
| 1 | 3137×4016 | 948×1213 | 3,3091 / 3,3108 | 0,05 % | **skaliert** |

**16 skaliert, 27 Beschnitt.** Keine Drehung, keine Rundung — kein Paar liegt
unter drei Pixel, der Rundungsverdacht aus der ersten Fassung dieses Abschnitts
ist damit erledigt. Die Grenze sitzt nicht in einer dichten Gegend: die 16
stimmen auf **0,11 % oder besser** überein, das nächste abgewiesene Paar ist
**0,78 %** daneben.

**Zwei Bildformate sind feste Rahmen**, und sie erklären den Beschnitt-Block:

- **1420×1781 für 16 Seiten**, bei drei verschiedenen XML-Höhen und immer
  derselben Breite 3333. Ein Derivat, das Seiten verschiedener Höhe auf ein
  identisches Format bringt, ist nicht pro Seite skaliert — nachgerechnet: eine
  3333×4362-Seite auf Breite 1420 wäre 1858 hoch, nicht 1781.
- **1296×1636 für 6 Seiten**, aus *vier* verschiedenen XML-Formaten, darunter
  zwei, bei denen das Bild **grösser** ist als das XML behauptet (791×1158 und
  1182×1506). Ein Bild grösser als die Vorlage, über die die Polygone sprechen,
  ist kein verkleinertes Derivat; da zeigen die beiden Dateien auf verschiedene
  Vorlagen.

**Zwei Einzelfälle sagen am meisten**, und beide sind keine Grössenfrage:
`1566×2639 → 1566×1325` hat **identische Breite** und die **halbe** Höhe
(Faktor 1,9917) — eine gescannte Doppelseite gegen ein halbiertes Blatt.
`2479×3508 → 2968×2553` ist A4 bei 300 dpi gegen ein **Querformat**; keine
Vertauschung (gedreht wäre 3508×2479), sondern eine andere Aufnahme,
wahrscheinlich eine Öffnung über zwei Seiten.

*Korrektur:* eine frühere Fassung dieses Abschnitts las den häufigsten Faktor
2,2548 als „300 gegen 133 dpi" und damit als systematisches Verhältnis zweier
Derivat-Pipelines. Die vollständige Tafel widerlegt das: die Faktoren sind
**1,58 · 1,62 · 1,99 · 2,10 · 2,26 · 2,63 · 3,31** — sieben verschiedene. 2,2548
trägt sechs Seiten und ist der häufigste, aber es gibt keine eine Beziehung zu
benennen; es ist die Streuung dessen, was neun Archive geliefert haben. Aus der
einen sichtbaren Signatur eine Regel zu machen, ist derselbe Fehler wie „8192 ist
großzügig" aus drei Seiten (§ *20* in `LESSONS_2026-10.md`).

### Die 16 kommen mit, und sie sagen es in ihren eigenen Daten

Ein Faktor, auf den sich **beide Achsen** bis auf ein Zehntel Prozent einigen,
ist nicht die Vermutung, die dieses Modul verweigert. „Welcher von zwei Scans ist
autoritativ" ist eine Entscheidung ohne Grundlage; „dies ist derselbe Scan in
zwei Auflösungen" ist eine Rechnung. Also werden die Polygone durch den Faktor
geteilt und die Seite exportiert — 16 **Dateien**, die 13 Seiten beschreiben,
also **+5,4 %** Trainingsmaterial (§ *Gemessen, 14. Oktober* unten; die Fassung
vor dem Lauf rechnete hier mit 16 Seiten und 6,6 %).

Drei Dinge gehören dazu, und jedes verhindert einen stillen Ausfall:

- **`imageWidth`/`imageHeight` werden mitgeschrieben.** Sonst widerspricht das
  Dokument seinem Bild eine Stufe weiter unten — genau die Prüfung, für die
  dieses Modul existiert, dort wo sie niemand laufen lässt.
- **`Coords` *und* `Baseline`.** Eines zu verschieben und das andere nicht würde
  richtig schneiden und die Zeilen falsch ordnen, und keine Spalte würde das
  zeigen.
- **Die Seite trägt ihren eigenen Vermerk**, als erster Kommentar im Dokument:

  ```xml
  <!-- agentic_historian: line coordinates divided by 2.255000; the ground truth
       describes 1770x2598, this image is 785x1152. imageWidth/imageHeight
       rewritten to the image. The pixels are the same scan at two resolutions:
       both axes agreed on the factor to within 0.019 %. -->
  ```

  Der Exportbaum besteht aus Bildern und XML und sonst nichts, und `pagexml-hf`
  trägt das XML wörtlich in eine Spalte — ein Hinweis in einer TSV daneben
  erreicht niemanden, der den Datensatz liest. Dieser hier reist mit.

Der Faktor jeder Seite steht zusätzlich in der Spalte `scale` der
Writers-Tabelle, und der Plan nennt die umgerechneten Seiten einzeln. Eine Seite
ohne Faktor bleibt eine **Byte-Kopie**, und „unangetastet" soll das heissen.

Die **27 Beschnitt-Seiten bleiben draussen**, und für sie ist die Frage keine
Code-Frage: welcher Scan autoritativ ist, entscheiden Transkribus und die
Share-Betreuer. Bei der Doppelseite und dem Querformat ist es nicht einmal eine
Grössenfrage, sondern die, welche Aufnahme die Seite überhaupt zeigt.

### Gemessen, 14. Oktober: 13 Seiten, nicht 16

| | vorhergesagt | **gemessen** |
|---|---:|---:|
| Seiten im Export | 257 | **254** |
| davon umgerechnet | 16 | **13** |
| Geometrie-Abweisungen | 27 | **27** |
| Abweisungen gesamt | 295 | **298** |

254 + 298 = 552, die Zahl der bewerteten Dateien — die Tafel geht auf. Nicht
lokalisiert 195, Duplikat **76** (war 73), Geometrie **27** (war 43).

**Die 16 sind 16 Dateien und beschreiben 13 Seiten.** Die anderen drei sind
Zweitkopien derselben Seite unter einer anderen Transkribus-Dokument-ID. Vorher
scheiterten beide Kopien an der Geometrie und wurden dort gezählt; jetzt wird die
erste exportiert, die zweite korrekt als Duplikat verweigert. Daher wanderte die
Duplikat-Spalte um +3, während die Geometrie-Spalte um −16 fiel: 241 + 13 = 254,
73 + 3 = 76, 43 − 16 = 27. Drei Spalten, die sich gegenseitig bestätigen.

Die Vorhersage 257 war meine, und sie war **Dateien auf eine Zählung von Seiten
addiert** — § *5. Files are not pages* in `LESSONS_2026-10.md`, begangen in einer
Vorhersage über einen Bericht, der die Warnung dagegen selbst druckt. Derselbe
Satz galt schon für „552 Ground-Truth-Seiten" (es sind 454 Seiten in 552
Dateien). Der Gewinn ist echt und ein Fünftel kleiner als angekündigt: **13 auf
241, also 5,4 %**.

Zwei Befunde, die erst der Lauf zeigte und die Arithmetik nicht:

- **Die Faktoren schwanken innerhalb eines Briefes.** Brief 2326 liefert zwei
  Seiten mit ÷2,0978 und ÷1,6241, Brief 2921 zwei mit ÷1,9920 und ÷1,5839. Die
  Derivate sind also nicht einmal pro Haus oder pro Brief einheitlich — der
  letzte Nagel für die „eine Pipeline, ein Verhältnis"-Deutung oben.
- **Sieben der 13 landen in `unbestimmt`.** Eine gerettete Seite ist meist eine
  Folgeseite, und eine Datumszeile steht auf der ersten. Der Anteil Seiten ohne
  benannte Hand steigt von 11,2 % auf 13,4 % (27/241 → 34/254), und eine der
  neuen Seiten bringt einen Hand-Konflikt mit (`lassberg-letter-2921`: eine
  Seite datiert nach Laßberg, eine nach einem Korrespondenten, also vererbt der
  Brief nichts). **Die Rettung bringt Material für ein Zeilenmodell und der
  Hand-Stratifizierung nichts.**

Zehn der 13 kommen von `UB Freiburg`, drei aus Basel. Jede steht einzeln im Plan
mit Grössenpaar und Faktor, und der Faktor zusätzlich in der `scale`-Spalte der
Writers-Tabelle — nachlesbar statt glaubenspflichtig.

Eine Aussage bleibt eine **Folgerung aus der Arithmetik**: dass die drei
zusätzlichen Duplikate genau die Zweitkopien der umgerechneten Seiten sind. Die
drei Spalten passen exakt, und sonst hat sich nichts geändert; belegen liesse es
sich mit einem `grep` der 13 Schlüssel in der Rückweisungstabelle unter
`duplicate`.

### Der Baum, 15. Oktober — und drei Prüfungen im Baum statt im Test

`hf-export-20261015T043850`, **254 Seiten**, 27 Minuten (das Lokalisieren, dann
254 Bilder aus dem kalten Tier). Dass ein Plan 13 umgerechnete Seiten *meldet*,
heisst nicht, dass 13 Dateien umgerechnet auf der Platte liegen — also drei
Prüfungen, und jede an einer anderen Stelle im Code:

| | geprüft | Ergebnis |
|---|---|---|
| 1 | `build` zählt beim Schreiben, nicht im Plan | `254 page(s) …, 13 with rescaled coordinates` |
| 2 | geschriebenes XML gegen das Bild daneben | 13 Zeilen, **0 Abweichungen** |
| 3 | jede umgerechnete Datei weist sich aus | **13** XML mit Vermerk, keine weitere |

**Prüfung 2 ist die eigentliche**, denn sie ist buchstäblich die Prüfung, die
diese 13 Seiten vorher aus dem Export geworfen hat — `imageWidth`/`imageHeight`
gegen die tatsächliche Bildgrösse —, jetzt auf den geschriebenen Dateien statt
auf einer erfundenen Vorlage im Test:

```bash
cd /home/dh/agentic_historian
.venv/bin/python3 - <<'PY'
import sys, pathlib
sys.path.insert(0, "agentic_historian")
import hf_export as hf
tree = pathlib.Path("agentic_historian/data/vlm_test/hf-export-<stempel>")
NOTE = "agentic_historian: line coordinates divided"
for x in sorted(tree.rglob("page/*.xml")):
    text = x.read_text(encoding="utf-8", errors="replace")
    if NOTE not in text:
        continue
    img, = [p for p in x.parent.parent.glob(x.stem + ".*") if p.is_file()][:1]
    said, got = hf.xml_size(text), hf.image_size(img)
    print(("OK    " if said == got else "FEHLER"), said, got, x.stem[:58])
PY
```

Die Funktionen des Projekts aufzurufen statt die Prüfung nachzubauen ist Absicht:
eine nachgebaute Prüfung prüft die Nachbildung.

**Und eine vierte Übereinstimmung, die nicht geplant war:** die 13 Zeilen sind
Seite für Seite dieselben wie im Plan, mit denselben Zielgrössen. Brief 2326
trägt seine beiden verschiedenen Formate (600×960 und 775×1240), Brief 2921
ebenso (913×1233 und 1148×1551), und 2902 liegt auf demselben 913×1233 wie
2921-001. Die Streuung der Faktoren innerhalb eines Briefes steht damit als
Dateigrösse im Baum. Plan, Schreiben und Datei sagen dasselbe, an drei Stellen
im Code.

Der Vermerk, wie er in der Datei steht — nach der Deklaration, vor `<PcGts>`:

```xml
<!-- agentic_historian: line coordinates divided by 2.254993; the ground truth
     describes 1770x2598, this image is 785x1152. imageWidth/imageHeight
     rewritten to the image. The pixels are the same scan at two resolutions:
     both axes agreed on the factor to within 0.019 %. -->
```

**Was diese drei Prüfungen nicht zeigen**, und was vor einem Upload gesagt sein
muss: ob die geteilten Polygone **auf der Tinte sitzen**. Konsistenz zwischen XML
und Bild ist dreifach nachgerechnet, Korrektheit nicht — dafür braucht es einen
Blick auf einen Zeilenschnitt aus einer der 13 Seiten. Und die 241 unveränderten
Seiten sind in diesem Durchgang nicht gegen ihre Bilder geprüft; sie sind im Baum,
*weil* sie dieselbe Prüfung im Plan bestanden haben, und ihre XML sind
Byte-Kopien. Das ist Prüfung durch Konstruktion, nicht durch Nachsehen.

## Do not pass `$ATR_PAGE_CACHE`

This page used to. On 2026-10-03 that shell variable was **set and not
exported**: bash put it in the argv, `os.environ` never saw it,
`config.ATR_PAGE_CACHE` was `None`, and the path it named was a directory that
existed and held other images. The export indexed 357 pages under keys that could
not match and reported "no image" for every ground-truth page — which from the
outside is indistinguishable from an empty cache. It was the third diagnosis in a
row that an argument default would have prevented.

`--source` now defaults to the cache the runner itself uses, the same fallback
`jobs.cache_dir_for` applies, with a test holding the two together. Leave it out.
The same applies to `$GT_ROOT` and `$VLM_TEST_ROOT`: those variables live in
`.env`, which dotenv loads for Python and an interactive bash has never seen.

## Where the pages are, which is two places

**The page cache is a cache, not a store.** A daily job moves anything older than
two days to the research share, paths preserved and sidecar alongside (#487) — the
tier `images.cold_fetch` looks in before going to the wire. So for any run but one
read today, most of the pages are *not* in `ATR_PAGE_CACHE`.

The first real export learned this the expensive way. It walked the hot cache only,
and reported:

```
pages to export: 0
left out: 552
  no image            357
  not located         195
```

Every located page said "no image". The corpus run that made those working copies
was twelve days old, so every one had been evicted. The images were never gone —
the code knew one of two answers to "where are the pages".

`export-hf` now searches both, hot winning on a collision (the same key in both
tiers means an entry was brought back, so the archived copy is the older
generation). `--archive` overrides; the default is `ATR_PAGE_CACHE_ARCHIVE`, and an
archive that is not a directory produces a warning rather than a silent miss.

**On tei the cold tier is:**

```
ATR_PAGE_CACHE_ARCHIVE=/mnt/wbkolleg_dh_1/tei/archive/agentic_historian-page_cache
```

It has to be exactly that directory, not `…/tei/archive`: `page_index` derives
each page key relative to the root it is given, so one level too high puts
`agentic_historian-page_cache__` in front of every key and none of them match.
The structure below it mirrors the page cache (`Basel/`, `Aarau/`,
`Donaueschingen/`, …) and a `MANIFEST.tsv` records
`sha256 · bytes · path here · original path on tei` for every file, so the
mapping back is checkable rather than assumed.

**This was unset until 2026-10-07, which means `images.cold_fetch` had never
run.** It was added in #487 exactly so an evicted working copy would come back
from the share instead of being re-downloaded, and for two weeks every batch
pulled 25 MB TIFFs over the wire and converted them again while the 1.5 MB
working copies sat on a share that reads at 410 MB/s. The cold tier holds 6719 of
them — the whole corpus. Nothing was broken: `cold_fetch` returns `None` when the
archive is unconfigured, which is correct on any host without a share. A cache
that is never consulted and a cache that always misses look identical from
outside, and `page_index`'s own line —
`0 page(s) in the cache, 6719 in the cold tier` — is the only place the
difference ever showed.

The eviction itself is `tei-vm-sanity/scripts/archive.sh`, in **`dh`'s user
crontab** at 04:10 daily (not in `/etc/cron.*`, which is where four rounds of
searching looked). It appends to `tei-vm-sanity/archive.log`, and that log names
the file count per night — which is how the empty cache was finally explained:
`moved 238 files` on 6 October, the exact number of images the 5 October dry run
had found.

A working copy is ~1.5 MB and the share reads at 410 MB/s, so pulling 276 of them
is well under a minute.

## Only the located pages

552 ground-truth pages were harvested; **276** locate a corpus page unambiguously,
and only those have an image to pair with. The rest are left out and counted by
reason:

- **not located** — no page of the run matched confidently enough. Two unrelated
  German pages of this corpus score about 68 % CER against each other, so a best
  match near that figure is a floor, not an identification. Pairing such a page
  with its best guess would put a different letter's scan beside this letter's
  transcription.
- **duplicate** — Transkribus holds the same page under several document ids
  (`doc7151593` and `doc4726794` score identically to three decimals). Exporting it
  twice is one page with twice the weight.
- **geometry** / **no geometry** — above.
- **no image** — the located key is not under `--source`. With the page cache as
  the source this means the page was never fetched.

## The project split is a heuristic

`project` becomes a directory in the dataset and is the axis any later
stratification uses. It has to be the **hand**, because that is what the error rate
splits on: measured on this corpus, one engine reads Laßberg's correspondents at
6–20 % CER and Laßberg himself at 27–45 %.

The archive folder will not do it. Basel holds both sides of the correspondence —
`doc7151991` (Wackernagel, 8.1 %) and `doc4726780` (Laßberg, 34.8 %) sit in the
same directory.

The hand is recorded nowhere in the data, so it is inferred from the dateline:
a house (Eppishausen, Meersburg) means Laßberg, a city (Basel, Zürich, Constanz, …)
means a correspondent. Only the first two lines are consulted — past the dateline a
place name is as likely to be something the letter discusses as where it was
written.

**A page with no recognisable dateline goes to `unbestimmt`**, and the dry run
prints that count prominently. It is not a third hand; it is the size of the guess,
and it belongs in the dataset card rather than being rounded away.

### Die Hand ist doch aufgezeichnet — im Verzeichnis der Edition

Diese Datei hat eine Woche lang behauptet, die Schreiberhand sei „nirgends in den
Daten aufgezeichnet". Sie ist es: in `michaelscho/lassberg`, derselben Edition,
in die dieses Projekt seine Lesungen publiziert (`GITHUB_TEXT_REPO`). Jeder Brief
dort ist eine TEI-Datei, deren `correspDesc` den Absender mit GND nennt:

```xml
<correspAction type="sent">
  <persName key="…#lassberg-correspondent-0373"
            ref="https://d-nb.info/gnd/118778862">Joseph von Laßberg</persName>
  <placeName key="…#lassberg-place-0147">Eppishausen</placeName>
  <date when="1833-08-13">1833-08-13</date>
</correspAction>
```

Gemessen am 10. Oktober 2026: **279 Briefe mit Absender, 128 davon von Laßberg
selbst**, vier Korrespondenzen (Pupikofer 84, Uhland 43, Wackernagel 24).

**Es hat jeden offenen Fall sofort entschieden:**

| Brief | Verzeichnis | was die Regel sagte |
|---|---|---|
| `-1737` | Laßberg, Eppishausen, 13.08.1833 | Konflikt 1/1 |
| `-1787` | Laßberg, Eppishausen, 27.06.1834 | Konflikt 1/1 |
| `-2358` | Laßberg, Meersburg, 02.08.1842 | Konflikt 1/2 |
| `-1209` | Wackernagel, Berlin, 14.12.1827 | `unbestimmt` |
| `-1280` | Pupikofer, 10.02.1829 | `unbestimmt` |

`-1280` ist der Brief, dessen dritte Zeile „nach Eppishausen befördert" lautet
und mit dem oben gegen ein breiteres Datierungsfenster argumentiert wird. Das
Verzeichnis sagt: Pupikofer. Das Argument ist unabhängig bestätigt.

**Identität über die GND, nie über den Namen.** „Laßberg" wird auch „Lassberg"
und „Laspberg" geschrieben; ein Name ist ein Label, `118778862` ist ein
Identifikator. Ein Eintrag ohne GND fällt auf den Namen zurück — und sagt das in
der Belegstelle (`[by name]`).

**Was es nicht erreicht.** 279 von rund 3226 Brief-IDs, vier Korrespondenten. Das
Verzeichnis ist deshalb die *primäre* Quelle und die Datierungsregel die
Rückfallebene, nicht umgekehrt. Und wo beide sich widersprechen, wird der
Widerspruch **berichtet statt lautlos aufgelöst**: dann ist eines von beiden über
diesen Brief falsch, und das will man sehen.

`export-hf --register DIR` (Default `LASSBERG_REGISTER`) schaltet es ein; fehlt
es, bleibt alles wie zuvor und eine Warnung sagt es.

### Die Hand gehört zum Brief, nicht zur Seite

Der erste Dry-Run mit Bildern, 5. Oktober 2026, labelte **57 von 211 Seiten** und
liess 154 auf `unbestimmt` — 27 %. Das ist kein Lesefehler der Regel. Eine
Datierung steht auf der *ersten* Seite eines Briefes; jede Folgeseite ist eine
Seite, auf der nichts zu lesen ist. Die Hand ist eine Eigenschaft des Briefes.

Also erbt sie entlang des Briefes: die datierte Seite entscheidet, die übrigen
Seiten desselben Briefes übernehmen. Was gruppiert, ist der **Briefordner**
(`lassberg-letter-NNNN`) — nicht die Signatur im Dateinamen.

#### Warum nicht die Signatur

Die Dateinamen tragen Signaturen, und das sah nach dem besseren Schlüssel aus.
Gemessen über die 241 Seiten von `atr_gt_candidates`, bevor entschieden wurde:

| Gruppierung | Seiten erfasst | Briefe pro Gruppe |
|---|---|---|
| `lassberg-letter-NNNN` | 216 / 241 | 1 |
| Signatur im Dateinamen | 241 / 241 | bis **36** |

`Basel__lassberg-letter-*__PA 82a B 9_Seite_NNN` sind 115 Seiten unter **einer**
Signatur, verteilt auf 36 Briefe; `Staatsarchiv Thurgau`s `…__75-1_00NNN` deckt 21
Briefe ab. Diese Signaturen benennen eine archivische *Einheit*, keinen Brief — und
Basel ist genau der Ordner, der beide Seiten der Korrespondenz enthält. Entlang der
Signatur zu erben hiesse, 36 Briefen eine einzige Hand zuzuweisen.

Die Antwort auf die Frage ist also: **nein**, die Signatur ist nicht ausschlaggebend.
Der Briefordner ist es, und wo er fehlt (25 Seiten), tritt der eigene Ordner an
seine Stelle — `blb lassberg__K 2911,104` ist ein Signaturen*ordner*, dessen Seiten
ein Stück sind. Zwei Seiten liegen lose in einem Archivordner (`Winterthur`); die
erben nichts, weil sonst ganz Winterthur ein Brief wäre.

#### Was die Vererbung nicht tut

- **Eine eigene Datierung wird nicht überschrieben.** Nur `unbestimmt`-Seiten erben.
- **Ein Brief mit widersprüchlichen Datierungen erbt nichts** und wird im Plan
  namentlich genannt. Zwei Hände unter einem Briefordner heissen entweder, dass der
  Ordner Brief *und* Antwort enthält, oder dass die Datierungsregel fehlgezündet
  hat; beides ist etwas zum Anschauen, nicht zum Mitteln.
- **Ein Brief, über den nichts datiert ist, bleibt `unbestimmt`.** Vererbung
  verteilt eine vorhandene Angabe, sie erfindet keine.
- Ein Brief, den nur eine *Folgeseite* entschieden hat, wird gezählt und benannt:
  so sieht ein Fehlschuss aus (eine Seite, die in ihren ersten Zeilen eine Stadt
  erwähnt), und die Vererbung verteilt ihn über den ganzen Brief.

#### Gemessen, 7. Oktober 2026

Derselbe Dry-Run über `atr_gt_candidates`, 211 exportierbare Seiten:

| | 5. Okt. | 7. Okt. | 8. Okt. |
|---|---:|---:|---:|
| mit Hand | 57 (27 %) | 111 (53 %) | **123 (58 %)** |
| `unbestimmt` | 154 (73 %) | 100 (47 %) | **88 (42 %)** |
| `korrespondenten` | 27 | 63 | 69 |
| `lassberg` | 30 | 48 | 54 |
| Briefe ohne Datierung | — | 55 | 47 |

(7. Oktober: die Vererbung. 8. Oktober: `E. am`, Berlin, Würzburg. Der
Foliierungs-Sprung ist noch nicht gemessen.)

54 Seiten erben die Hand ihres Briefes, aus **48 Briefen**. Zwei Briefe
widersprechen sich (`lassberg-letter-1737`, `-1787`, je eine Seite pro Hand) und
erben nichts; drei wurden von einer Folgeseite entschieden (`-1530`, `-1797`,
`-1952`) und sind als Fehlschuss-Kandidaten benannt. Fünf Briefe von Hand zu
prüfen ist machbar; 154 Seiten zu prüfen war es nicht.

**Was die restlichen 100 Seiten jetzt sind.** Nicht Folgeseiten — die sind
behoben. Sie liegen in Briefen, in denen *keine einzige* Seite einen Ort nennt,
den die Liste kennt. Bei 211 Seiten auf rund 105 Briefe und 48 entschiedenen
Briefen hat über die Hälfte der Briefe keine erkennbare Datierung. Das Problem
sitzt damit nicht mehr in der Gruppierung, sondern in `LASSBERG_PLACES` /
`CORRESPONDENT_PLACES`: 19 Orte für eine Korrespondenz über halb Europa.

#### Die Umfrage vom 8. Oktober 2026, und was sie widerlegt hat

`--writers-out` und die gedruckte Umfrage waren gebaut, um eine Hypothese zu
prüfen: 19 Orte sind zu wenig. **Gemessen erklärt das 2 von 55 Briefen.** Die
Eröffnungen, eine nach der anderen gelesen:

| Was in den ersten zwei Zeilen steht | Briefe |
|---|---:|
| Anrede, kein Ort — „Hochwohlgeborner Herr und Gönner!" | 22 |
| reine Archivnummern — `1256 / No 85` | 10 |
| Fortsetzungsseite, mitten im Satz | 7 |
| `E. am 15 Julij 1831.` | 5 |
| Nummer *plus* Anrede | 4 |
| Datum ohne Ort — `1827. Mart: 1.` | 4 |
| **echter Ort, der fehlt** — Berlin, Würzburg | 2 |
| gedruckte Todesanzeige | 1 |

Drei verschiedene Ursachen, und nur eine davon ist die Ortsliste.

**`E. am` ist Laßbergs eigene Abkürzung für Eppishausen.** Fünfmal: seine Hand,
mit Datum, in lesbarer Schrift — und die Regel sah ein `E.`, das in keiner Liste
steht. `LASSBERG_SHORTHAND` liest es jetzt, am Zeilenanfang verankert und mit
`am`/`den` plus Ziffer, weil ein blosses `E.` eine Initiale, ein Notenverweis
oder eine Zeilennummer ist. Ein `M.` für Meersburg gibt es **nicht** — niemand
hat eines gesehen, und es zu erfinden ist genau das Raten, das diese Umfrage
ersetzen soll.

**Berlin und Würzburg** stehen in der Liste, weil die Umfrage sie gedruckt hat,
nicht weil sie plausibel sind.

**Das Zwei-Zeilen-Fenster wird von Archivnummern aufgefressen.** 14 Briefe
beginnen mit einer Foliierung, bei zehn stehen Nummern in *beiden* Zeilen, die
die Regel liest. Deshalb zeigt die Umfrage jetzt `SURVEY_LINES = 4` Zeilen — und
**die Regel bleibt bei zwei**. Ein Ort, den die Umfrage auf Zeile drei sichtbar
macht, entscheidet nichts; er wird nur sichtbar. Das Fenster auf Verdacht zu
verbreitern ist, wie die 19 Orte zustande kamen. `--survey-lines N` verstellt
die Breite ohne Code-Änderung.

**Die grösste Gruppe ist kein Ort.** 22 Briefe öffnen mit einer Anrede *an*
Laßberg — „Herr Baron", „Herr und Gönner", `Carissimo Josepho … de Lasberg`. Wer
ihn so anredet, schreibt an ihn, nicht als er. Das wäre ein zweites Signal neben
der Datierung und das ergiebigste; es ist aber eine Aussage über die *Adresse*,
nicht über den Ort, und damit eine historische Entscheidung. Gemessen dafür: von
den 48 bereits als `lassberg` gelabelten Seiten redet **keine einzige** jemanden
als Baron oder Gönner an. Die Regel ist nicht gebaut.

#### Die zweite Messung, 8. Oktober: das Fenster war das Problem

Die Umfrage vier Zeilen breit, und der Befund ist eindeutig:

```
lassberg-letter-1009: 1256 / No 85 / Constanz am 7 July 1825. / Ich gehe diesen Morgen
lassberg-letter-1015: 1264 / 163. / No. 85. / Constanz am 30 July 1825.
lassberg-letter-1458: 209. / 179 / Eppishaus auf 25 Juny 1830 / Wertester Herr Hartmann!
lassberg-letter-1486: 282. / 198 / Eppish. am 1.ten 8br. 1830. / Wertester Herr Hartmann!
lassberg-letter-3111: 84. / 171 / Eppishausen. am April 1330 / Wertgeschäzter Herr!
```

**Acht der zehn Nummernköpfe tragen direkt darunter eine echte Datierung** — in
Orten, die die Liste längst hat. Die Regel verbrauchte ihr Fenster auf einer
Foliierung. `dateline_lines` überspringt sie jetzt, und zwar alle: `1015` hat
drei.

Dabei ist `Eppish.` eine vierte Schreibweise desselben Hauses. `LASSBERG_PLACES`
führt deshalb `eppish` statt dreier Varianten — der gemeinsame Präfix, der nichts
anderes im Korpus trifft und insbesondere nicht `Villa Epponis`, Laßbergs
latinisierten Namen dafür, der in einem Brief *an* ihn vorkommt.

#### Warum kein breiteres Fenster

Dieselbe Messung hat auch gezeigt, was ein Vier-Zeilen-Fenster kaputt macht.
`lassberg-letter-1280`, Brief eines Korrespondenten, dritte Zeile:

> …wartet die mitkommende Lieferung des Morgenblattes auf Gelegenheit nach
> **Eppishausen** befördert zu werden

Eppishausen ist, wohin der Brief ging, nicht woher er kam. Ein breiteres Fenster
kann das nicht unterscheiden; ein Foliierungs-Sprung kann es, denn eine Seite,
die mit Prosa beginnt, wird nicht übersprungen.

Und was ein Sprung freilegt, ist manchmal selbst Prosa:

> `Weimar__FA Hodel 236`: 284. / Lieber Leonhard! / Im Jare des heiles 1473. als
> **Konstanz** noch keine offizin hatte, wurde dahier ein buch gedruckt…

„als Konstanz noch keine offizin hatte" ist kein Schreibort. Eine Datierung ist
eine kurze, allein stehende Zeile: **jede, die die Umfrage gedruckt hat, misst 18
bis 44 Zeichen, diese Prosazeile 121.** `DATELINE_CHARS = 80` liegt dazwischen,
weit von beiden entfernt.

Die Umfrage zeigt jetzt sechs Zeilen, damit sie nach dem Sprung nicht wieder
blind ist.

#### Ein Test, den die Messung umgeworfen hat

`test_a_place_the_survey_reveals_still_decides_nothing` behauptete, ein Ort, den
die Umfrage auf Zeile drei sichtbar macht, dürfe nichts entscheiden. Das war
richtig, solange die einzige Grundlage eine Vermutung über das Fenster war, und
falsch, sobald die Eröffnungen gelesen waren. Der Test heisst jetzt
`test_a_dateline_behind_a_foliation_is_read` und trägt beide Fassungen im
Docstring — die Unterscheidung, die hält, ist nicht „Zeile drei entscheidet
nicht", sondern „Foliierung überspringen, Prosa nicht lesen".

#### Nachprüfbar, Seite für Seite

`--writers-out PATH` schreibt eine Zeile pro exportierter Seite: Schlüssel, Brief,
Gruppierungsgrund, Projekt, Herkunft (`dateline` oder `inherited`) und Belegstelle.
Immer, auch im Dry-Run — denn „154 Seiten haben eine Hand geerbt" ist eine Zahl,
die man glauben oder nicht glauben kann, und widersprechen kann man ihr nur, indem
man die Seiten liest, bei denen sie falsch war. Über MCP nennt das Ergebnis die
Datei im Feld `writers`; sie liegt unter `VLM_TEST_ROOT`, nicht in `/tmp`.

## Hochgeladen, 21. Oktober 2026

```
upload_hf(tree="hf-export-20261015T043850", dry_run=false)
→ pagexml-hf … --repo-id dh-unibe/image-text_lassberg-correspondence_xix \
               --mode raw_xml --private
```

Exit 0, **11,5 min** (10 min Konversion bei ~2 s/Seite, dann der Transfer).
`pagexml-hf 0.9.6+fork.1`, Python 3.12.3, installiert unter `/opt/pagexml-hf`
mit einem Symlink in `/usr/local/bin` — **nicht** in die Venv des Dienstes, deren
Pins `atr-mcp` bedient, und nicht nach `~/.local/bin`, das im PATH des Dienstes
fehlt (§ *Welcher Code hat geantwortet?* in `BATCH_ATR.md` gilt auch für
Werkzeuge, nicht nur für Code).

Geprüft am Hub, nicht im Log — dieselbe Abfrage, die vorher „not found" ergab:

| | |
|---|---|
| Status | 254 Beispiele, ein Split, 1,46 GB, **privat**, MIT |
| Schema | `image` (decode: false), `xml_content`, `filename`, **`project_name`** |
| `data/train/korrespondenten/…parquet` | 780,0 MB |
| `data/train/lassberg/…parquet` | 521,8 MB |
| `data/train/unbestimmt/…parquet` | 230,9 MB |

**Die Projektaufteilung ist doppelt angekommen**, als Spalte und als Ablage —
die Arbeit an der Hand steht damit im Datensatz und nicht nur im Plan. Fast
falsch gelesen: die Datensatzkarte schneidet ihre Feature-Liste nach
`xml_content…` ab, was aussieht, als fehle `project_name`. Erst die `README.md`
selbst zeigt alle vier Spalten. Eine abgeschnittene Darstellung ist keine
Aussage über den Inhalt (§ *17* in `LESSONS_2026-10.md`, dritter Fall).

**Was der Upload nicht beantwortet.** Der Dataset Viewer verweigert private
Datensätze außerhalb von PRO/Enterprise (`501 Not Implemented`) — keine
Zeilenvorschau, kein Parquet-Export, kein Schema-Browsing auf dem Hub;
`load_dataset` geht, Hineinsehen heisst Herunterladen.

### Im Parquet nachgesehen: 13 von 13

Die vierte Stelle, an der dieselben dreizehn Seiten auftauchen — nach Plan,
Schreiben und geschriebener Datei:

```bash
cd /home/dh/agentic_historian
set -a; . ./.env; set +a          # Token in die Umgebung, nicht in die History
/opt/pagexml-hf/bin/python3 - <<'PY'
from huggingface_hub import HfFileSystem
import pyarrow.parquet as pq

fs = HfFileSystem()
base = "datasets/dh-unibe/image-text_lassberg-correspondence_xix/data/train"
NOTE = "agentic_historian: line coordinates divided"
for shard in sorted(fs.glob(base + "/*/*.parquet")):
    t = pq.read_table(shard, columns=["filename", "xml_content"], filesystem=fs)
    hits = [f for f, x in zip(t["filename"].to_pylist(), t["xml_content"].to_pylist())
            if NOTE in x]
    print(f"{len(hits):3} von {t.num_rows:3}   {shard.split('/')[-2]}")
PY
```

**Der Interpreter ist `/opt/pagexml-hf/bin/python3` und nicht der des Projekts**
— `datasets`/`pyarrow` liegen absichtlich nicht in der Venv des Dienstes (§ oben),
und eine erste Fassung dieser Prüfung scheiterte an genau dem `import`. Dazu
zweierlei, was sie billig macht: die **Spaltenprojektion über Range-Requests**
holt zwei Textspalten statt 1,46 GB Bilder, und `set -a; . ./.env` bringt den
Token in die Umgebung, ohne ihn in die History zu schreiben.

| Shard | Zeilen | davon mit Vermerk |
|---|---:|---:|
| `korrespondenten` | **131** | 3 |
| `lassberg` | **89** | 3 |
| `unbestimmt` | **34** | **7** |
| | 254 | **13** |

Die Zeilenzahlen sind die des Plans, die dreizehn Dateinamen dieselben. Und zwei
Befunde, die erst diese Verteilung zeigt:

**Sieben der dreizehn liegen in `unbestimmt`** — 21 % jenes Shards gegen 2,3 %
und 3,4 % der beiden anderen. Dass eine gerettete Seite meist eine Folgeseite
ohne Datumszeile ist, war bisher eine Erklärung; jetzt ist es eine Verteilung,
mit einer Überrepräsentation um den Faktor sieben bis neun.

**Brief 2921 liegt in zwei verschiedenen Projekten**: `…-002` unter
`korrespondenten`, `…-001` unter `lassberg`. Das ist der Hand-Konflikt, den der
Plan als Warnzeile meldete („korrespondenten 1, lassberg 1") — zwei datierte
Seiten, die sich widersprechen, also vererbt der Brief nichts und jede Seite
behält ihre eigene Lesung. Im Datensatz sind daraus zwei Shards geworden, die
zwei Seiten *eines* Briefes halten.

Das hat eine Folge über Kosmetik hinaus: **wer nach `project_name` in Train und
Test teilt, legt diesen Brief über die Grenze** — eine Seite links, eine rechts,
derselbe Schreiber, dasselbe Papier, dieselbe Tinte. Ein Brief von 96, also
klein, aber nicht null, und es ist die Form eines Lecks in einer
Held-out-Messung. Hier ist nicht die Hand falsch, sondern eine Grenze
durchlässig, und das ist ein anderer Fehler als der, vor dem die Karte warnt.

### Sitzen die geteilten Polygone auf der Tinte? Ja — und der Weg dahin war der Lehrreiche

Die Frage stand an drei Stellen als offen, und ein Blick auf einen Zeilenschnitt
hätte sie mit „sieht richtig aus" beantwortet. Gemessen ist sie besser
beantwortet: **jede Abweichung vom angewandten Faktor fängt weniger Tinte.**

Das Mass ist der Anteil der Tintenpixel einer Seite, der **innerhalb** der
Zeilenpolygone liegt (Otsu-Binarisierung, Polygonmaske). Ist der Faktor richtig,
muss er ein **Optimum** sein: grössere, kleinere oder verschobene Kästen müssen
schlechter abschneiden. Ein dunkler Scanrand verschiebt alle Varianten gleich
und fällt damit aus dem Vergleich heraus — anders als beim Absolutwert.

**12 der 13 Seiten: `s* = 1.00`, `dx* = dy* = 0`, exakt.** Kein Wert im Raster
(0,90–1,10 Skalierung, ±4 % Verschiebung in beiden Achsen) bringt ein einziges
Tintenpixel mehr.

Die dreizehnte, `Basel__…Seite_164`, zeigte `s* = 1.10` mit `+4 % / −4 %`
Verschiebung und einem Gewinn von 0,246 → 0,264. **Alle drei Parameter lagen am
Rand des Rasters**, und das ist die Signatur eines Gradienten nach aussen, nicht
eines Optimums: ein echtes Maximum liegt innen und bringt viel. Nachgemessen mit
randfester Tintendefinition (innere 90 % der Seite, Pixel unter 20 als Rand
verworfen):

| Skalierung | 0,80 | 0,90 | **1,00** | 1,10 | 1,20 | 1,30 |
|---|---:|---:|---:|---:|---:|---:|
| Tinte im Kasten | 0,362 | 0,496 | **0,856** | 0,723 | 0,750 | 0,675 |

| Verschiebung (Höhe) | −10 % | −5 % | **0** | +5 % | +10 % |
|---|---:|---:|---:|---:|---:|
| Tinte im Kasten | 0,678 | 0,454 | **0,856** | 0,452 | 0,719 |

Inneres Maximum mit Abfall nach beiden Seiten, auf beiden Parametern. Der
Tinteanteil der Seite fiel dabei von 0,195 auf **0,056** — fast drei Viertel der
vorher gezählten „Tinte" war Rand, und 0,856 liegt mitten im Bereich der zehn
Freiburger Seiten (0,747–0,912). Diese Seite war nie ein Ausreisser der Daten,
sondern einer der Messung.

Ein Nebenbefund, der die Sache dichter macht als geplant: die Täler bei ±5 % und
die teilweise Erholung bei ±10 % sind der **Fingerabdruck der Zeilenperiodik** —
um anderthalb Zeilenhöhen verschoben landen die Kästen zwischen den Zeilen
(0,45), um dreieinhalb wieder näher an Zeilen (0,68–0,72). Diese Form hat nur
eine korrekte Ausrichtung auf einem periodischen Raster.

**Was dabei schiefging, ist die eigentliche Lehre.** Als Kontrolle liefen 25
unberührte Seiten mit, deren Polygone *konstruktiv* richtig sind — sie sind im
Baum, weil XML- und Bildgrösse ohne jede Änderung übereinstimmten. Ihr Median
lag bei **0,166** mit Lift **0,54**, also deutlich *unter* den umgerechneten
0,830. Das kann nicht an der Platzierung liegen; es liegt daran, dass Otsu auf
Archivscans mit dunklem Rand den Rand als Tinte zählt. Die Kontrolle hat damit
**das Instrument gemessen und nicht den Gegenstand** — und genau dadurch
verhindert, dass ich 0,830 für einen Beweis nehme (§ *28* in
`LESSONS_2026-10.md`).

## Die Datensatzkarte

Sie steht als [`hf-dataset-card.md`](hf-dataset-card.md) im Repository, mit der
YAML-Kopfzeile von `pagexml-hf` **wörtlich** erhalten — `dataset_info` und
`configs` liest der Hub, und sie neu zu tippen hiesse, die Grössen von Hand zu
pflegen.

**Seit #691 aus der Session:** `upload_hf_card(card="hf-dataset-card.md",
repo_id=…)` — holt das README vom Hub, setzt dessen Frontmatter wörtlich in die
Karte, schreibt die Karte nach `docs/` zurück und lädt sie als README hoch;
`dry_run=True` (Vorgabe) zeigt nur den Spleiss. Und `upload_hf` endet jetzt mit
dem, was der Hub hält (Zeilen, Bytes, Spalten, privat). Der Token kommt aus der
Konfiguration, nie in die Kommandozeile; `hf` wird über `HF_CLI` gefunden (auf
tei `/opt/pagexml-hf/bin/hf`), wenn `huggingface_hub` im venv fehlt.

Von Hand, wie bisher — am einfachsten im Web-Editor der Datensatzseite (*Edit README*):
einmalige Textänderung, kein Token, keine CLI. Von tei aus geht es auch, braucht
dann aber zwei Umstände, die je eine eigene Falle sind — `hf` liegt nur in der
Venv unter `/opt` und nicht auf dem PATH, und der Token steht in `.env`, das eine
interaktive Bash nie liest:

```bash
cd /home/dh/agentic_historian && git pull
set -a; . ./.env; set +a          # Token in die Umgebung, nicht in die History
/opt/pagexml-hf/bin/hf upload dh-unibe/image-text_lassberg-correspondence_xix \
  docs/hf-dataset-card.md README.md --repo-type dataset
```

`--token` wäre der dritte Weg und der schlechteste: ein Geheimnis in argv steht
in `ps` und in `.bash_history`.

Vier Dinge, die von diesen Daten gelten und ihnen nicht anzusehen sind — die
ersten drei standen hier schon, bevor es den Datensatz gab, der vierte kam mit
den umgerechneten Seiten dazu:

1. Die Aufteilung ist aus Datumszeilen erschlossen und wird auf manchen Seiten
   falsch sein. `unbestimmt` ist **keine dritte Hand**, sondern „nichts hat
   etwas gesagt".
2. Status DONE gilt hier als Ground Truth. Diese Sammlung hat das GT-Tag auf 19
   von 552 Seiten benutzt; es zu verlangen hätte 96 % der verfügbaren Wahrheit
   verworfen.
3. Zwei geerntete Dokumente (`doc1350777`, `doc1350778`) sind Seiten einer
   *gedruckten Edition* und keine Briefscans. Sie erreichen den Export nie, weil
   sie nichts lokalisieren — aber wer Zahlen gegen die Transkribus-Sammlung
   vergleicht, findet 85 Seiten fehlen und soll wissen, warum.
4. Dreizehn Seiten tragen umgerechnete Koordinaten und weisen es im eigenen XML
   aus; 27 weitere sind zwei verschiedene Aufnahmen eines Blattes und fehlen
   deshalb. Wer die 13 sucht oder ausschliessen will, filtert auf
   `agentic_historian: line coordinates divided`.

Und was die Karte ausdrücklich sagt, weil es sonst niemand weiss: **die Bilder
sind Digitalisate der besitzenden Häuser.** MIT gilt für die Konversion und die
Struktur, nicht für die Scans; der Datensatz ist privat, und das ist die
Bedingung, unter der er überhaupt entstehen durfte.

---

# Der zweite Datensatz: die Maschinenlesungen, seitenweise

Alles oben betrifft **handkorrigierte** Seiten: Bild plus das PAGE XML einer
Transkription, die ein Mensch durchgesehen hat, mit Zeilenpolygonen. Der Korpus
hält aber noch eine zweite Art Text, und sie ist die grössere: 300 Seiten, die
nie transkribiert wurden, gelesen im Oktober 2026 von einem kommerziellen Modell
hinter einer API. `export-run-hf` macht daraus einen eigenen Datensatz, und jede
Entscheidung darin dreht sich um **einen** Unterschied: hier hat niemand
nachgesehen.

## Warum ein eigener Datensatz und keine weiteren Zeilen im ersten

Weil die Spalte, die den Unterschied trägt, sonst erfunden werden müsste.
`pagexml-hf` schreibt vier Spalten (`image`, `text`/`xml_content`, `filename`,
`project_name`) und keine davon sagt „geprüft" oder „nicht geprüft". Ein
Datensatz, der beides mischt, hätte diese Information nur im Dateinamen — und
dieses Projekt hat genau diesen Fehler schon einmal von außen gesehen: zwei
Dokumente der geernteten Transkribus-Sammlung tragen **unkorrigierte
PyLaia-Ausgabe unter Status `FINAL`**, und nur weil jemand die Zeilenlängen
nachgezählt hat, ist es aufgefallen. Zwei Repositories sind zwei Namen, zwei
Karten und zwei Berechtigungen; eine gemischte Spalte ist eine Fussnote.

Der Name trägt es mit: `image-text_lassberg-correspondence_xix_uncorrected-atr`.
„machine-read" war der erste Vorschlag und ist verworfen — es ist einen
Buchstaben von „machine-readable" entfernt, also von der genau falschen Aussage.

## Kein Konverter, auch hier nicht — aber ein synthetisches PAGE-Dokument

Eine Chat-Completion hat keine Polygone. Das Modell bekommt ein Bild und gibt
Text zurück; es gibt keine Geometrie zu exportieren und kein PAGE-Dokument zu
kopieren. Der naheliegende Griff wäre ein kleiner eigener Konverter — Bild, Text,
zwei Spalten, fertig — und das ist genau der Griff, den dieses Repository
verweigert, aus dem Grund, den dieses Dokument oben nennt: `hf_source.py` im
Trainer trägt schon eine Aliasliste, weil es einmal passiert ist.

Also bekommen die Lesungen die Form, die der vorhandene Konverter als *Eingabe*
nimmt. Die Frage ist nur, was in das Dokument hinein muss, und die Antwort stand
im Quellcode des Konverters, nicht in seiner Dokumentation:

```python
# pagexml_hf/parser.py
def _get_text_equiv(self, element):
    unicode_el = element.find("pc:TextEquiv/pc:Unicode", self.namespace)
    if unicode_el is not None and unicode_el.text:
        return unicode_el.text.strip()          # ← die Region selbst
    lines = element.findall("pc:TextLine", self.namespace)
    if not lines:
        return ""                                # ← und sonst ihre Zeilen
```

Der Text einer Region ist ihr **eigenes** `TextEquiv`, und ihre Zeilen sind nur
der Rückfall. Eine `TextRegion` mit dem Seitentext, einem `Coords`-Rechteck um
die ganze Seite und **ohne eine einzige `TextLine`** ist damit eine vollständige
Eingabe für `--mode text`.

## Die fehlenden Zeilen sind die Entscheidung, nicht die Sparmaßnahme

Zeilenkästen liessen sich erfinden: die Seite in so viele waagrechte Streifen
teilen, wie das Modell Zeilen zurückgegeben hat. Jeder einzelne wäre eine
Vermutung, hinter der keine Messung steht — und, schlimmer, sie wäre von der
echten Sache **nicht zu unterscheiden**: `--mode line` würde 31 plausible
Streifen schneiden, jedem eine plausible Textzeile zuordnen und einem Trainer
einen Datensatz übergeben, dessen Ziele alle um einen unbekannten Betrag
verschoben sind. Das ist dasselbe Versagen, das der Geometrie-Check oben
verhindert, nur selbst gebaut.

Was das Fehlen tatsächlich tut, gemessen am 22. Oktober 2026 gegen
`pagexml-hf 0.9.6+fork.1` und nicht erschlossen:

| Modus | Ergebnis auf diesem Baum |
|---|---|
| `text` | der Seitentext, Zeichen für Zeichen wie das Modell ihn schrieb |
| `region` | eine Zeile pro Seite, `region_coords` = das Seitenrechteck. Wahr, wenn auch nutzlos |
| `line` | **Abbruch** mit `ZeroDivisionError` in der Statistik über null Zeilen |

Ein Absturz ist eine gröbere Antwort als „nichts", und sie ist die richtige: die
Frage, die ein zeilenweiser Export stellt, lässt sich aus diesen Daten
überhaupt nicht beantworten.

## Der Modus steht im Baum, nicht auf der Kommandozeile

`upload_hf` über MCP nimmt `tree`, `repo_id`, `dry_run` — **keinen Modus**, und
`UPLOAD_MODE = "raw_xml"` ist eine Modulkonstante. Ein Baum mit Maschinenlesungen,
in diesem Modus hochgeladen, hätte die synthetischen Dokumente in eine Spalte
`xml_content` gelegt, neben vierzehn Datensätze echter Transkribus-Ausgabe, und
dort hätte er ausgesehen wie mehr davon.

Darum schreibt jeder `build` jetzt eine `export.json` neben die Seiten, und
`upload-hf` liest den Modus daraus:

```json
{"mode": "text", "kind": "run", "written_at": "...", "run": "gemini-300-final",
 "models": ["gemini-3.8-flash"], "pages": 281,
 "note": "machine readings, uncorrected; no line geometry"}
```

Der Modus ist eine Eigenschaft der Daten, also reist er mit den Daten statt
Monate später auf einer Kommandozeile wiederholt zu werden. Ein Baum ohne
Manifest — jeder, der vor dem 22. Oktober 2026 geschrieben wurde — liest als
`raw_xml`, womit die alten Uploads unverändert bleiben. Und der Upload-Plan sagt
jetzt in einer eigenen Zeile, was für ein Text im Baum liegt:

```
mode      : text   (from export.json)
text      : MACHINE READING, uncorrected
```

## Was standardmäßig nicht mitkommt

Vier Ablehnungen, jede gezählt und mit Schlüssel in der TSV von
`--rejected-out`:

- **`empty`** — das Modell hat nichts zurückgegeben. Eine leere Antwort ist
  *keine* Transkription einer leeren Seite: die Gateway-Gegenprobe vom
  19. Oktober fand unter 19 „leeren" Seiten eine **beschriebene** mit 24
  lesbaren Zeilen (`blb lassberg__K 2911,93__page_0001`).
- **`repetitive`** — die Wiederholungsschleife, also der Defekt des Modells als
  Text gerendert.
- **`deliberation`** — das Modell ringt mit sich selbst in dem Feld, in dem die
  Transkription stehen soll.
- **`truncated`** — am Ausgabedeckel abgeschnitten. Eigene Kategorie und kein
  Urteil, weil so eine Seite als gewöhnlicher Erfolg zurückkommt (eine 200, ein
  Text, Urteil `ok`) und mitten im Satz aufhört.

Jedes davon als Trainingsziel lehrt den Defekt. `short` ist **nicht** dabei: das
ist eine Aussage über die Länge, und eine halb beschriebene Seite ist zu Recht
kurz. `--skip-verdicts ''` behält alles, für jemanden, der genau diese Seiten
untersucht.

## `project_name` ist das Modell, nicht die Hand

Im ersten Datensatz ist `project_name` die schreibende Hand, erschlossen aus der
Datumszeile und entlang des Briefes vererbt. Hier wäre das eine Erschließung auf
einer Erschließung: die Datumszeile müsste aus einer **ungeprüften**
Transkription gelesen und dann über einen Brief verteilt werden. Also steht dort
das Modell, und das Archiv samt Brief-Id steckt weiter in `filename`, das der
Seitenschlüssel ist.

Jedes Dokument sagt zusätzlich im eigenen ersten Kommentar, was es gelesen hat —
derselbe Griff wie die Umrechnungsnotiz oben, aus demselben Grund: ein Baum ist
Bilder und XML, und eine Notiz *daneben* erreicht niemand.

```xml
<!-- agentic_historian: MACHINE READING, NOT GROUND TRUTH.
     No line geometry: a chat completion returns text, so this document has
     one region covering the page and no TextLine elements. The text is as
     the model returned it, unverified and uncorrected by any human.

     model     gemini-3.8-flash (engine gateway)
     run       gemini-300-final
     read      2026-10-12T09:14:33+00:00, gateway 1.4.2
     settings  prompt-9f2c1a+think-low|max8192
     quality   ok, 1843 chars, 31 lines
     image     Basel__....jpg, sha256 ab12cd34ef56 of the original -->
```

`--` wird dabei zu `- -` gefaltet: XML-Kommentare dürfen es nicht enthalten, und
ein Modellname oder ein Prompt-Digest ist jemand anderes Zeichenkette. Ein
Dokument, das nicht parst, nimmt seine Seite aus dem Datensatz.

## Der Lauf

```bash
cd /home/dh/agentic_historian && git pull
# 1. der Plan: was mitkommt, was abgelehnt wird, und warum
.venv/bin/python3 -m agentic_historian export-run-hf \
    --run-dir gemini-300-final \
    --rejected-out /tmp/run-hf-refused.tsv --dry-run

# 2. den Baum schreiben (kopiert ein Bild pro Seite)
.venv/bin/python3 -m agentic_historian export-run-hf \
    --run-dir gemini-300-final \
    --rejected-out /tmp/run-hf-refused.tsv \
    --out agentic_historian/data/vlm_test/gemini-300-pagewise

# 3. hochladen — privat, Modus aus dem Baum
.venv/bin/python3 -m agentic_historian upload-hf \
    --tree agentic_historian/data/vlm_test/gemini-300-pagewise \
    --repo-id dh-unibe/image-text_lassberg-correspondence_xix_uncorrected-atr \
    --dry-run
```

### Aus einer Session

Seit #685 sind alle drei Schritte Werkzeuge, und der Baum wandert als **Name**
unter `VLM_TEST_ROOT` von einem zum nächsten — nie als Pfad:

```
export_run_hf(run="gemini-300-final")                      # 1. der Plan, schreibt nichts
export_run_hf(run="gemini-300-final", dry_run=False)       # 2. Baum gemini-300-final-pagewise
upload_hf(tree="gemini-300-final-pagewise", repo_id=…)     # 3. privat, Modus aus dem Manifest
```

`out` benennt den Baum anders (Vorgabe `<run>-pagewise`); ein Baum, der schon
Dateien hält, wird abgelehnt, bevor ein Job startet. `model` grenzt einen Lauf
mit mehreren Lesern ein. `skip_verdicts` wählt, welche Urteile abgelehnt werden
(`empty`, `short`, `repetitive`, `deliberation` — `ok` nie; Vorgabe alles
ausser `short`), `keep_truncated` lässt abgeschnittene Seiten zu. Die
Ablehnungen landen immer als TSV unter `VLM_TEST_ROOT`, der Pfad steht im
Ergebnis. Was das Werkzeug **nicht** nimmt: `--source`, `--archive` (beide aus
der Konfiguration — dass sie am 22. Oktober an der CLI nötig waren, lag am
Arbeitsbaum mit code-relativen Datenwurzeln, nicht am Export) und `--repo-id`
(das entscheidet der Upload).

Die Vorbedingung von #682 gilt weiter: das Werkzeug läuft als Unterprozess auf
dem **Produktions-Checkout**, und der muss `export-run-hf` kennen — am 22.
Oktober stand er auf einem Feature-Branch ohne den Befehl, und ein Job wäre mit
„invalid choice" gestorben (`batch_report` zeigt unter `code`, welcher Stand
antwortet).

Die Bilder liegen zu diesem Zeitpunkt zum größten Teil **nicht mehr im
Page-Cache**: der Lauf ist älter als zwei Tage, und ein Tagesjob verschiebt
alles Ältere unter Erhalt der Pfade auf den Research-Share (#487). `--source`
und `--archive` haben darum beide Vorgaben, und die Seiten, die in keiner der
beiden Schichten liegen, erscheinen als `no-image` in der TSV statt als
stillschweigend fehlende Zeilen.
