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
