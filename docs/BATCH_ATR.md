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

## 5 · Reading the result

`report.md` gives, per model: pages read, pages skipped, pages failed, **pages
empty**, **pages cut off**, characters per page, seconds per page, wall time.

**On a resumed run, rebuild it first.** The report records what the runner
observed, and a resumed run observes most of its corpus as `skipped` — counting
nothing about it, including how many pages came back empty. `report-run --run-dir
<dir>` reads the results on disk instead. (The 2026-09-17 corpus run had 77 blank
pages of 899, 8.6 %, and its original report had no empty column at all.)

**Check the "cut off" column first.** It counts pages where the model stopped at
its token ceiling instead of at the end of the text. Those readings are real but
short, and they end mid-sentence — indistinguishable, reading them, from a model
that gave up. If the column is not zero, raise `ATR_VLLM_MAX_NEW_TOKENS` on the
gateway, restart it, delete the affected `.json` files, and re-run the same
command: only the missing pages are read again.

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
