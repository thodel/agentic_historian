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
# 1. TEI and IIIF manifest per volume. The licence is read per file and printed.
python -m eval.ecodices_bench fetch runs/ecodices-2026-11 saa-0428 <id> <id> …
#    A manifest that links no TEI: give it, --tei saa-0428=https://…/saa-0428_….xml
# 2. Agent B, three runs per volume, first page only (= production, pages[0]).
python -m eval.ecodices_bench run runs/ecodices-2026-11 --runs 3
# 3. Anywhere: report.md and scores.json in the run directory.
python -m eval.ecodices_bench score runs/ecodices-2026-11
```

**Choosing the volumes.** Aim for 30 of them, spread over centuries and holdings.
Prefer archive holdings (closest to our administrative records) and volumes the
catalogue gives several hands. Check the parser against two or three
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

**What it is not.**
- A hit on a description published years ago proves less, because the model may
  have read it in training. The report gives each volume's publication year.
- e-codices is mostly codices, and our corpus is mostly chancery and
  administrative records.
- `--pages spread` is refused until Agent B takes several images (#771). This
  report is the baseline that #771, #775 and #776 are measured against.
