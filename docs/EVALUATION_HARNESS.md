# Measuring recognition quality

Status: in use, 2026-09-01. How `agentic_historian/eval/linebench.py` measures
recognition against ground truth, and how to reproduce a run.

This document covers the **instrument**. The measurements it produced are
published separately at
<https://thodel.github.io/agentic-historian-outputs/evaluation.html>, and what
they imply for this codebase is in [PIPELINE_CONSEQUENCES.md](PIPELINE_CONSEQUENCES.md).
Keeping the three apart matters: an experiment that also argues for code changes
tends to get read as advocacy, and a pipeline that cites its own benchmark tends
to stop questioning it.

Entity **linking** has its own instrument, with its own gold and its own
boundary: [LINKING_EVALUATION.md](LINKING_EVALUATION.md).

## What it measures

`score_model(lines, recognise, model, engine)` runs one recogniser over a set of
lines and returns a `ModelScore`:

| field | meaning |
|---|---|
| `cer` | corpus-level, `errors / characters` — the metric `ketos test` reports |
| `cer_mean` | mean of per-line rates; a four-character line weighs as much as a ninety-character one |
| `cer_median`, `cer_worst` | median and 95th percentile over lines |
| `failures` | lines the engine could not answer |
| `profile` | `ErrorProfile`: length ratio, over- and under-generation, empty output |

Two rules are deliberate. A line the engine fails on counts as a **failure**, not
as a wrong reading — averaging an exception into a CER hides an outage as
mediocrity. And a model that produced no CER at all is **not ranked**, rather
than ranked last.

Report `cer` and `cer_median` together for anything generative. Two prompts for
the same vision model differed by 17 points corpus-wide and by 0.3 in the median;
the gap was two collapsed lines, not a worse prompt.

## Why the error profile exists

CER cannot distinguish *omitting*, *misreading* and *adding*, and those have
different consequences for an edition. An omission leaves a gap that
proof-reading catches. A substitution leaves a wrong word in the right place. An
insertion leaves text nobody wrote, and it does not read differently from the
transmission.

`ErrorProfile.length_ratio` separates them cheaply: CTC engines sit near 1.0
because their output is bounded by the width of the image, generative ones run
above it.

## The judge harness

`judge_lines(lines, scores, llm_fn, sample)` hands the candidate readings of one
line to a language model, asks for a ranking, and compares its top pick with the
reading that actually has the lowest CER.

Two details are load-bearing. The candidate labels are **reshuffled per line**,
seeded from the line name, so a model that always prefers "A" cannot score above
chance. And the return value is the set of **disagreements**, not the agreement
rate — the rate hides how expensive the mistakes are.

Measure regret, not agreement: the useful number is the CER a strategy's picks
cost against the simplest baseline, taking the strongest single engine and asking
nobody.

## Reproducing a run

The harness needs a manifest of line images with ground truth:

```
data/eval/<corpus>/manifest.jsonl   # {"image": "...", "gt": "...", "doc": "..."}
data/eval/<corpus>/lines/*.jpg
```

Cut lines with the **segmentation the corpus ships**, never by re-segmenting.
Published test sets provide polygons for exactly this reason, and page
segmenters behave pathologically on line-shaped input — `blla` needs about 290
seconds on a line strip against 5.8 on the same content in page shape, because
it scales to a fixed height and a wide, flat crop blows up.

```bash
.venv/bin/python -m pytest agentic_historian/tests/test_ah_linebench.py
```

## Localising the error, and pricing the convention

`eval/blocks.py` uses the same edit operations for a different question. `profile(pairs)`
attributes every substitution and deletion to the Unicode block of the reference character, and
every insertion to the block of the invented one, then normalises by how often each block occurs
in the reference. Without that normalisation Basic Latin always wins: it is around nine tenths of
the text.

On 15th-century bastarda the corpus leader reads Basic Latin at 10.2 % and is scored at 100.0 % on
Latin Extended-A, which holds the long s. One confusion carries that block: `ſ → s`, 599 times in
291 lines.

`ladder(pairs)` folds one transcription convention at a time — whitespace, case, long s,
ligatures, punctuation, combining marks — and re-scores the same output. Nothing is recognised
again. It separates "cannot read the hand" from "does not follow our conventions": two fifths of
the leader's measured error on that corpus, under a tenth on 19th-century Kurrent. The share
scales with how much of the reference sits outside plain ASCII.

Applied across systems it changed every number and no position. Use it to price the gap, not to
report a rate — and never as an output. An edition without long s and diacritics is worthless.

## Two traps worth knowing before trusting a number

**Check the crops by eye before measuring.** The first run against the Swiss
Federal Council minutes reported 68 % CER for a model that actually reaches 15 %.
The dataset's images are bands carrying three to four lines, with an accompanying
polygon marking which one is meant; using the images and ignoring the polygons
measured which line a model happened to latch onto. The signal was in the numbers
— ink covering 100 % of a 357-pixel-high crop cannot be one line — and it was
read as "clean crop" until someone looked at the image.

**Do not compare across aggregation levels.** Published results may average over
sample sets rather than over lines. Corpus-level CER is the usable proxy; on one
corpus it agreed with the group mean to two hundredths while the per-line mean
was three points off.

## Measuring a VLM before it joins the ensemble (#541)

`eval/vlm_bench.py` answers one question: should this VLM be allowed to run on
real material? It exists because the project has exactly one VLM comparison and
that comparison is a warning — AH-11, 07.09.2026, 291 lines over 8 Inzigkofen
pages, `qwen3.8-27b` at 27.7 % CER against `internvl3-8b` at **189.8 %**. Two
models both carried as "vision-capable", a factor of nearly seven apart.

Two phases, deliberately separate. `run` needs the GPU and the gateway; `score`
needs neither and holds every judgement, so the analysis is reviewable, re-runnable
and testable without one.

```bash
# On the machine that can reach the gateway:
python -m eval.vlm_bench run \
    --pages /path/to/inzigkofen/pages \
    --out   runs/vlm-bench-2026-10

# Anywhere, including CI:
python -m eval.vlm_bench score \
    --run runs/vlm-bench-2026-10 \
    --gt  /path/to/pagexml \
    --out docs/VLM_BENCH_2026-10.md
```

`--models a,b` restricts the run; the default is every candidate both registries
report — GPUStack's `VLM_MODELS` and the gateway's `engine: vllm` entries (#540).
The run is model-major, because the gateway's vLLM models are lazy on one GPU and
interleaving them pays a model load per page.

The run directory is the project's standard shape, `<run>/<model>/<key>.txt`, so
`compare-runs` and `score-gt` read it with no new loader. **A page the candidate
failed on gets a `.json` record and no `.txt`** — an empty file would score as
100 % CER and make "the service was down" indistinguishable from "the model read
nothing".

### What the report separates, and why each separation is load-bearing

**Line-level from page-level.** Five of the gateway's seven VLMs are `level: line`;
`qwen3.8-27b` reads a page, and `/recognize` does not auto-segment the way `/ocr`
does for TrOCR. One median over both measures the segmentation and calls it model
quality. The two tables are printed with an explicit note that they cannot be read
against each other.

**The collapse share from the central tendency.** This document already records the
effect from the other side: two prompts for one model differed by 17 points
corpus-wide and by 0.3 in the median, and the gap was two collapsed pages. The
bench reports collapses as a count and a share, using the pipeline's own
`_is_degenerate` rather than a second definition.

**Coverage from quality.** A candidate that answered on 3 of 8 pages has a median
over 3 pages, and the table says so.

**The fused text from the best single one.** #416's question, restricted to the
bench's candidates. Fusion runs with arbitration off, so scoring makes no LLM call
— a bench that needed a model to score a model would not run in CI.

**A field of equals from a field with a leader.** The aggregate over all pages is
reported and then explicitly disowned, because #416's finding is that fusion's
value is **not a property of the method but of the field it is given**, and the
two regimes were measured in opposite directions:

| field | measurement | fusion |
|---|---|---|
| one candidate dominates | 13 Federal-Council pages, HTR+ 3.38 % against rivals at 7–12 % | **lost** 1.44 pt |
| comparable strength, uncorrelated errors | published 150-line sample | **won**, 0.123 vs 0.145 |

One number over both is the average of two opposite effects — and it is the number
that would let somebody conclude from this bench without seeing the mechanism. So
the report cuts the pages by how far the strongest candidate led its nearest rival
(`COMPARABLE_LEAD`, 2 points, from the measured ~3.6-point lead where voting lost),
and again by how many candidates answered: two candidates have no majority at all,
so a tie there is broken by something other than a vote.

The lead is the gap to the **nearest rival**, not to the worst candidate: a leader
is voted down by the candidates that can outvote it, and `worst − best` would call
a page "led" whenever one candidate collapsed, however close the other two were.

A stratum with no pages prints `—` rather than a win rate of 0 %. "Not measured
here" and "measured, and fusion never won" are different facts, and telling them
apart is the whole reason for cutting the pages up.

**What this measures and does not decide.** The lead is a *quality* gap, so it
needs ground truth — which a bench has by definition and the pipeline does not.
That is the line between this measurement and the conditional rule #416 sketches
(fuse only when the candidates are close in quality): that rule would have to know
the field's quality at recognition time, and #313 records that the match score is
not a quality signal. So the bench answers the question and the pipeline's fusion
stays unconditional below the no-merge band (#300), as #416 asks — *"widening the
sample is the next step, not changing the pipeline."*

### Two things it refuses to do

**It does not rank two working models.** Below 8 located pages a candidate is marked
⚠ and its figures order nothing; the report says so whatever the page count, because
the limit is the sample and not the arithmetic. The signal may say **veto, not
rank** — the same conclusion #491 reached for perplexity, where using a veto as a
ranking certified the step doing the damage.

**It does not let the candidates under test decide which page they are reading.**
Ground truth is matched by content, and a bench deliberately contains candidates
that collapse or answer on two pages out of twenty. Both vote the same wrong way: a
collapsed reading is the same text on every page, a one-page reading has one answer
to give. On the first real run of this bench the two of them cost 2 of 3 pages for
*every* candidate including the good ones. Locating therefore counts only readings
that are non-degenerate and hold at least two pages — and the excluded candidate is
still scored on the page. The collapse is the finding, not a reason to lose the page.

## Fusion on a field of equals, with a VLM in it (#416, measured 29.10.2026)

#416 had three measurements and a condition: voting lost where one reader led
(13 Federal-Council pages, Hertwig), won once where the candidates were of
comparable strength with uncorrelated errors (150 lines), and asked for "the same
comparison on pages with three or more candidates of comparable strength,
including a VLM" before the pipeline changes. The T4 draw of training-atr-models
is that field: 5,952 lines on 214 pages from 18 datasets, read by a Qwen3.5-4B
line VLM and three TrOCRs on the same crops against the same references
(`docs/TROCR.md` there; the files on the share under
`eval_sets/t4-trocr-vs-vlm-xvi-xix/`, `SHA256SUMS` beside them). The 100-page
TrOCR arm trained on 27 of the draw's pages, so those are left out: **187 pages,
4,836 lines**.

```bash
python -m eval.fusion_on_lineset /mnt/wbkolleg_dh_1/Textrecognition_Training/eval_sets/t4-trocr-vs-vlm-xvi-xix \
    --arm vlm=t4b-vlm.raw.jsonl --arm trocr-full=t4b-trocr-full.raw.jsonl \
    --arm trocr-100p=t4b-trocr-100p.raw.jsonl --exclude-pages pages_in_100p_training.txt
```

The vote without arbitration, the no-merge band left where it is (it fires on
15 % of the lines at 0.35), scored with the harness's own `eval.metrics.cer`
(pooled rows raw; the medians fold case and punctuation like every bench table).

| reading | pooled CER |
|---|---:|
| `vlm` (Qwen3.5-4B line) | 0.1074 |
| `trocr-full` | 0.1064 |
| `trocr-100p` | 0.1491 |
| best single reader per page (an oracle) | 0.0835 |
| **fused, vote, candidates in that order** | **0.1519** |
| fused, vote, candidates reversed | 0.1898 |

| field | pages | fusion won | median fused | median best single | fusion costs | median lead |
|---|---:|---:|---:|---:|---:|---:|
| Kandidaten vergleichbar stark (lead ≤ 2 pt) | 115 | 10 (9 %) | 6.0 % | 3.8 % | **+2.17 pt** | 0.7 % |
| ein Kandidat führt klar | 72 | 0 (0 %) | 27.3 % | 11.9 % | **+15.41 pt** | 4.8 % |

Line by line instead of page by page (the training repo's `score_pairs`, which
reproduces TROCR.md's 0.0936 / 0.0910 to the digit) the same field gives fusion
0.1495 against 0.1108 / 0.1103 for the two good readers, and on the comparable
stratum +1.7 points with a 95 % page bootstrap of [+1.1, +2.5]. The condition
#416 named does not rescue the vote: **on a field of three comparable readers
including a VLM, fusion lands between the second-worst and the worst
candidate**, and loses on nine pages in ten even where the lead is under two
points. With `trocr-zeroshot` as a fourth, weak candidate it is 0.1411 — and
0.3080 with the candidates reversed, worse than the weak reader alone.

Two mechanisms, both measured:

* **The vote is not a vote where it matters.** 59 % of the aligned columns are
  consensus, 29 % a majority, and 12 % have three different readings. Those
  fall to `_most_backed`, which takes the first — the pivot, i.e. the longest
  candidate, and on equal length whichever the caller listed first. In
  production that is the VLM, because it is the first pick. Reordering the same
  three candidates moves the fused CER from 0.124 (the page's best reader
  first) to 0.189 (its worst first). That gap is list order, not evidence.
* **Two TrOCRs outvote the VLM where they are wrong alike.** They share an
  architecture and nested training data; where both misread the same way they
  are a majority against a correct VLM. That is the Federal-Council mechanism
  (#416) with a VLM in the field — the VLM's differently distributed errors do
  not help it when it is one against two.

What this does not say: that no fusion could work. A per-line oracle that picks
the best reader of each *line* reaches 0.0654 on all 214 pages, two points
under the best reader per page — there is signal in the field. The vote as
built does not find it, and the pipeline has no quality signal at recognition
time to find it with (#313). Both flags that would fuse in production,
`ENABLE_MULTI_ENGINE_FUSION` and `ENABLE_ENSEMBLE_HTR`, default to off.

## Agent B against the catalogue: e-codices (#779)

`eval/ecodices_bench.py` measures the source description (Agent B) against the
holding library's scholarly description on e-codices: TEI P5 `msDesc`, written by
someone who saw the whole volume. Agent B is shown page images only, never the
TEI. Until now the only thing measured about Agent B was its instability (#379).

Three phases, split like `vlm_bench`. `fetch` and `run` need e-codices and the
VLM and run **on tei**; this project's cloud sessions could not reach
e-codices.ch (31.10.2026). `score` needs neither and holds every judgement.

```bash
cd ~/agentic_historian/agentic_historian
# 0. Choose 30 volumes from the catalogue (OAI-PMH), not by hand. Writes
#    candidates.tsv (every inspected volume, and why it was or was not taken),
#    selected.txt, and the selected volumes' manifest/TEI in fetch's layout.
python -m eval.ecodices_bench candidates runs/ecodices-2026-11 --n 30 --max-inspect 300
# 1. TEI and IIIF manifest per volume. The licence is read per file and printed.
#    After step 0 only needed for volumes added by hand.
python -m eval.ecodices_bench fetch runs/ecodices-2026-11 --ids-from runs/ecodices-2026-11/selected.txt
#    A manifest that links no TEI: give it, --tei saa-0428=https://…/saa-0428_….xml
# 2. Agent B, three runs per volume, first page only (= production, pages[0]).
python -m eval.ecodices_bench run runs/ecodices-2026-11 --runs 3
# 3. Anywhere: report.md and scores.json in the run directory.
python -m eval.ecodices_bench score runs/ecodices-2026-11
```

**Choosing the volumes.** Thirty, spread over centuries and holdings, chosen by
`candidates` (`eval/ecodices_candidates.py`) rather than by hand — the volumes
one happens to know are the famous ones, which a model is likeliest to have read
about. It lists ids over OAI-PMH, inspects up to `--max-inspect` of them
(`saa` archive holdings first, then an even spread; cached under `_candidates/`),
keeps volumes with a date and at least `--min-pages` pages, and takes them
round-robin over centuries, several catalogued hands before one before unstated.
`saa-0428` (#375) is always in. The choice is deterministic. The OAI base URL is
e-codices' documented one but unverified from here (no cloud access): if it
answers otherwise, `--oai-url` or `--ids-file` (one id per line) route around it.

**A guest on the library's server.** The first run (6.11.2026) fired 300
manifest requests back to back; e-codices answered all but the first with 503
or 400. Every request now goes through `PoliteGetter`: `--delay` seconds
between requests (default 1, a global option before the subcommand), retries on
429/5xx with `Retry-After` or 5/20/60 s, and `candidates` stops after
`--max-failures` unreadable volumes in a row. What was read stays cached, so a
second run continues. 300 volumes take about ten minutes this way.

**What OAI calls a volume.** e-codices' OAI-PMH (3107 records, 6.11.2026)
names each record by its page, `oai:e-codices.ch:https://www.e-codices.ch/en/list/one/csg/0001`;
the volume id is library and shelfmark, `csg-0001`. The first runs took the
shelfmark alone (`an-i-0008`), which is no id: 400 from e-codices, and 503 once
the stream of bad requests went on.

**The binding is no page.** e-codices puts the binding first: canvas 0 was
"Front cover" on 29 of the first 30 volumes (7.11.2026), so `--pages first`
showed Agent B a book cover. `text_indices` drops canvases whose label names the
binding (cover, spine, edges, pastedowns, colour chart; German and French
labels too) before any page is picked, for `run` and for
`hand_consistency validate` alike. Without labels, every canvas counts.

**Nor are the flyleaves.** The run on the text pages (7.11.2026) still met
endleaves: rebound parchment codices have paper flyleaves with a librarian's
note, and Agent B read "Papier", "Kurrent", "19. Jh." for Carolingian volumes;
the hand's changepoints clustered at the front of the volume. `body_indices`
keeps the canvases whose label is a folio or page number (`1r`, `p. 3`,
`f. 1r`, `S. 5`); lettered and roman flyleaves drop out. When fewer than half
the written pages carry such a number, every written page counts.

**The leaf on a dark ground.** e-codices photographs each leaf on a dark ground,
often with a colour chart. Otsu over the whole photograph separated leaf from
ground, so line finding saw nothing on 21 of 30 volumes. `hand_consistency.ink_mask`
now finds the leaf first (the longest band of mostly bright rows, then
columns, when the image border is dark) and thresholds ink within it; a scan
cropped to the leaf, as ours are, is unchanged.
Check the parser against two or three
descriptions read by hand before trusting a report. The offline fixtures are
synthetic, so the first `fetch` is where the parser first meets real files.

**What is compared.** Per element and run, each verdict is one of: hit, miss, no
answer, or not in the catalogue.

- **Schrift:** compared by script family (`SCRIPT_FAMILIES`), with "gotisch" as
  the generic family.
- **Datierung:** Agent B's year range must overlap `origDate`
  `notBefore`–`notAfter`.
- **Schreiber:** the number of hands, from `handDesc/@hands` or counted
  `handNote`s.
- **Beschreibstoff:** `supportDesc/@material`.

Stability is the share of runs that give the most common answer. A run that
answers nothing lowers it.

**Schrift as a Top-2 set (#775).** Beside the single label, `score` reports the
stability of the set of the first two candidates (`Schrift.kandidaten[:2]`, by
family, order-free: "bastarda+kursive") under "Einzeletikett gegen Top-2-Menge",
with each volume's most common label and set. A run without candidates lowers
the set's stability as a run without `wert` lowers the label's. The #775
measurement is five runs on the #379 volume, `saa-0428`, in a directory of its
own so the 30-volume baseline keeps three runs per volume:

```bash
# the volume's catalogue, manifest and cached pages; not the baseline's runs
mkdir -p runs/ecodices-775/saa-0428
cp runs/ecodices-2026-11/saa-0428/{manifest.json,tei.xml} runs/ecodices-775/saa-0428/
ln -f runs/ecodices-2026-11/saa-0428/page-*.jpg runs/ecodices-775/saa-0428/ 2>/dev/null || true
AGENT_B_PAGES=4 python -m eval.ecodices_bench run runs/ecodices-775 --runs 5 --pages spread
python -m eval.ecodices_bench score runs/ecodices-775
```

**What it is not.**
- A hit on a description published years ago proves less, because the model may
  have read it in training. The report gives each volume's publication year.
- e-codices is mostly codices, and our corpus is mostly chancery and
  administrative records.
- `--pages first` shows page 1 alone, as Agent B did before #771. Its report is
  the baseline that #771, #775 and #776 are measured against.
- `--pages spread` shows the pages production now shows: `AGENT_B_PAGES` (default
  5) at even intervals, first and last included (#771). Run both into separate
  directories and compare the two reports.
- `--pages typical` puts the most and the least typical page among them (#774),
  chosen from page features (`page_features`, a stand-in until #773) measured on
  up to 60 pages. Production uses it with `AGENT_B_PAGE_CHOICE=typical`; the
  default stays `spread` until this comparison says `typical` is steadier.

### Prompt variants and the period bias (#844)

The third run (7.11.2026, 30 volumes × 3, text block, `--pages spread`) showed
Agent B's image path never dating below the 13th century (all 11 volumes before
1100 came out 13th–15th c.; Datierung 8 %, stable in 88 %), never naming
Carolingian minuscule, and calling the material after its own dating: the 7
parchment volumes it called *Papier* it had dated to the 15th c., the 2 paper
volumes it called *Pergament* too early (Beschreibstoff 67 %). The system prompt
all of Agent B shares introduces it as "spezialisiert auf spaetmittelalterliche
Verwaltungsquellen (… 14.-16. Jahrhundert)" (`agents/source_heuristic.py`).

**The variant.** `material-first` (`source_description.IMAGE_PROMPT_VARIANTS`)
asks for the writing material from what the leaf shows (hair and flesh side,
follicles, translucency; chain and laid lines, watermarks) before the script,
and the century last; it forbids inferring the material from the date, names
Carolingian minuscule among the possible scripts and says the specialisation
rules none out. The rest of the prompt is the default's. Production keeps the
default (`AGENT_B_IMAGE_PROMPT=default`); the setting also keys the description
cache, so a variant never reuses the default's descriptions.

**Recorded per run.** `run --prompt material-first` writes `runs/material-first-NN.json`
beside the default's `runs/NN.json`, every run records its `prompt`, and
`meta.json` keeps one entry per variant under `variants` (with the VLM's id).
`score` gives each variant its own report and, when there are several, a table
of them side by side first; `scores.json` has them under `by_prompt`. A run
without `prompt` was made before #844 and is the default's. Every report now
gives #844's two numbers: dating hits on volumes the catalogue dates no later
than 1100, and how many material misses come with a dating miss.

```bash
cd ~/agentic_historian/agentic_historian
# (a) The third run again under today's rules (#842's century spans). Files only:
#     no model, no network. --out keeps the third run's own report beside it.
python -m eval.ecodices_bench score runs/ecodices-2026-11 --out runs/ecodices-2026-11/rescored
# (b) The variant on the same volumes and the same pages (the cached page-NNNN.jpg;
#     --pages and AGENT_B_PAGES as in the third run), then both side by side.
python -m eval.ecodices_bench run runs/ecodices-2026-11 --runs 3 --pages spread --prompt material-first
python -m eval.ecodices_bench score runs/ecodices-2026-11 --out runs/ecodices-2026-11/844
# (c) Our own volumes: one directory per ground-truth volume, its page images in
#     pages/ (name order = document order) and a reference.json written from the
#     archive's catalogue — never from Agent B. Keys left out are not judged.
mkdir -p runs/agentb-gt/<volume>/pages
cp <the volume's page images> runs/agentb-gt/<volume>/pages/
cat > runs/agentb-gt/<volume>/reference.json <<'EOF'
{"date_from": <year>, "date_to": <year>, "material": "papier", "script": "<as catalogued>", "hands": <n or null>}
EOF
python -m eval.ecodices_bench run runs/agentb-gt --runs 3 --pages spread
python -m eval.ecodices_bench run runs/agentb-gt --runs 3 --pages spread --prompt material-first
python -m eval.ecodices_bench score runs/agentb-gt
```

The commands of (c) are the bench's: a volume with `reference.json` and `pages/`
instead of `tei.xml` and a manifest is read from disk, needs no `fetch` and is
judged by the same rules. `material` takes `papier`, `pergament`, `gemischt` or
the TEI's `chart`/`perg`/`mixed`. Since #842 `NOT_TEXT` knows more binding
labels than the third run did, so a volume that fell back to every written page
(csg-1278) may be shown other pages in (b); `pages_shown` in each run says which.

**A second reader on the same bench.** `run --describe external --model <id>`
sends the image-only prompt, in the variant `--prompt` names, with the same
pages at the same scaling, to an OpenAI-compatible API (`GEMINI_BASE_URL`,
`GEMINI_API_KEY`, as the external ATR reader; `eval/ecodices_external.py`).
Only the model changes, so the dating floor can be laid at the model's door or
the prompt's: a second reader that dates the Carolingian volumes right makes it
the model's; one that does not puts the prompt first. The model id is
preflighted before the first page; `--reasoning` and `GEMINI_MAX_TOKENS` apply
as for the ATR reader; each run file keeps the model, the tokens spent and
whether the answer was cut off (`truncated`), and `meta.json` records the
reader under the variant. Run it into a directory of its own, since its runs
would otherwise sit beside Agent B's under the same names, and compare the
reports.

```bash
# the same volumes and cached pages, a directory of its own, no runs of Agent B in it
cp -r runs/ecodices-2026-11 runs/ecodices-2026-11-argon
rm -rf runs/ecodices-2026-11-argon/*/runs runs/ecodices-2026-11-argon/{_descriptions,meta.json,report.md,scores.json,rescored,844}
python -m eval.ecodices_bench run runs/ecodices-2026-11-argon --runs 1 --pages spread \
    --describe external --model gemini-4-argon --reasoning low
python -m eval.ecodices_bench score runs/ecodices-2026-11-argon
```

**What would justify switching the default.** All four, measured before anyone
sets `AGENT_B_IMAGE_PROMPT=material-first` in production:

1. On e-codices, Beschreibstoff hits clearly more often than the default's 67 %,
   and the material misses no longer travel with dating misses — not a lower
   miss count bought with "no answer", which counts as not hit.
2. On e-codices, dating of the volumes up to 1100 rises above zero and
   Datierung overall does not fall; stability stays near the default's.
3. On our own volumes, no element is worse than under the default: our
   corpus is 13th–19th-c. archival paper, and a prompt that helps Carolingian
   codices and costs chancery hands is not shipped (#844's scope note).
4. Schrift is no worse on either, since it drives the model selection.

If the variant fixes the material but the dating floor stays, the floor is the
model's and not the prompt's, which is what a second reader was to tell apart.

**No second reader yet.** #844 asked to run the bench with the external reader
of #747 as `describe`. That reader (`external_atr.openai_recogniser`, reader G
in `ruemlang/pipeline.py`) transcribes: one page per call, a fixed prompt file
from `prompts/`, page text back (`Reading.text`). It cannot take Agent B's
prompt, several pages or return the 16-element JSON, so it cannot be the
bench's describer as it stands. A describer for that API would have to send
`_image_prompt` with the pages to `GEMINI_BASE_URL` and parse the answer with
Agent B's `_parse_response` — new code against a metered API, not a plug-in.

**Comparing two choices (#774).** `compare` scores two or more run directories
again and puts them side by side: per element hit rate and stability over the
volumes every run has, the Top-2 stability (#775) and the mean reward (#782),
then a per-volume balance (how many volumes are steadier, hit more often, under
the second choice than under the first) and the Schrift and Datierung cells of
every volume. The column names are each directory's `pages`; `--label` renames
them.

**Four images per call, and the budget.** tei's vLLM takes at most four images
per prompt (12.11.2026); a five-page call is refused and Agent B falls back to
the first page alone, which is what the third run (`--pages spread`, 7–8.11.)
actually measured: every one of its 90 calls saw one page. Run multi-page
variants with `AGENT_B_PAGES=4`. Four pages make the answer longer, and 4 of 5
answers on `saa-0428` broke off inside the JSON at 3500 tokens: an answer the
budget cuts off (`finish_reason == "length"`) whose JSON does not parse is now
asked for once more with twice `AGENT_B_MAX_TOKENS`; raise that setting for a
run that still shows `Antwort … abgeschnitten` in its log.

```bash
# the same 30 volumes, typical pages, into a directory of its own
mkdir -p runs/ecodices-2026-11-typical
for v in runs/ecodices-2026-11/*/; do
  id=$(basename "$v"); [ -f "$v/tei.xml" ] || continue
  mkdir -p "runs/ecodices-2026-11-typical/$id"
  cp "$v"/manifest.json "$v"/tei.xml "runs/ecodices-2026-11-typical/$id/"
done
AGENT_B_PAGES=4 python -m eval.ecodices_bench run runs/ecodices-2026-11-typical --runs 3 --pages typical
python -m eval.ecodices_bench compare runs/ecodices-2026-11 runs/ecodices-2026-11-typical \
    --out runs/ecodices-2026-11-typical/compare.md
```

## Consistency of the hand, all pages against all (#773)

`hand_consistency.py` asks whether page images say, without the VLM, if a
volume is one hand and where it changes. It follows Ferilli & Piscopo (CEUR
Vol-4304, paper 1): every page against every other, the most typical page by
centrality, outliers at mean + 2 sd, block means along the volume, and the best
single changepoint (mean distance across the split / within its parts).

Per page: Otsu binarisation, lines from the row profile (or from a PAGE XML),
and per line x-height, slant, stroke width, ink density and the 256-ray radial
profile of each letter-sized component (±5° tolerance). CPU only, numpy and
Pillow.

```bash
cd ~/agentic_historian/agentic_historian
# one volume: page images in a directory, in name order
python -m hand_consistency analyse <dir> --out report.json
# validation against catalogued hands: the volumes `eval.ecodices_bench fetch` saved,
# 30 pages each at even intervals (cached as page-NNNN.jpg), on tei (e-codices network)
python -m hand_consistency validate runs/ecodices-2026-11 --pages 30
```

`validate` writes `hand_consistency.md` beside the bench: per volume the
catalogue's number of hands, the 2-cluster silhouette, the changepoint ratio
and where it falls. **The decision:** the measure separates hands only if
volumes with several hands score clearly higher on both, over at least five
volumes with a known count. If they do not, #773 ends with that finding and
#777 and #778 fall away. Until that table exists nothing computed here goes into
a description, and `page_features` (#774, #776) stays the stand-in.

**The reward (#782).** `eval/ecodices_reward.py` turns each verdict into a
number, and `score` writes the mean per term and the script's Brier score under
"Belohnung" in the report and `reward` in `scores.json`.

- A hit scores +1, a miss −1, no answer −0.5, and an element the catalogue does
  not name scores 0.
- A dating hit scores `min(1, 50 / width in years)`, so "12.–16. Jh." does not
  count like "Mitte 15. Jh.".
- A Brier term on the first script candidate's confidence (#775) penalises a
  confident wrong script. A bare label counts as p = 1, so leaving out the
  confidence does not avoid the term.
- A completion that is not the 16-element JSON scores −1 in total.

`trl_reward_funcs()` returns the same terms in the signature TRL's
`GRPOTrainer` takes, `f(completions, reference, **kwargs)`, with their weights.
