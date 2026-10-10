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
