# What the recognition evaluation implies for this pipeline

Status: open agenda, 2026-09-01. Each item names the measurement behind it and
the change it argues for. Nothing here is implemented yet.

The measurements are published at
<https://thodel.github.io/agentic-historian-outputs/evaluation.html>; the
instrument that produced them is described in
[EVALUATION_HARNESS.md](EVALUATION_HARNESS.md). This document is the only one of
the three that says what the code should do.

Three corpora, all with supplied segmentation: Swiss Federal Council minutes
(1848–1903, 150 lines), Inzigkofen (15th century, 291 lines on 8 pages), Valais
census forms (1870/80, 1 075 cells on 16 sheets).

---

## 1. Vision models should see the page, not the line

The pipeline cuts lines and asks line by line. For vision models that is the
worst available mode.

| corpus | whole page | line or cell |
|---|---|---|
| Inzigkofen, CER | **20.4 %** | 57.3 % |
| Valais, CER | **44.1 %** | 59.3 % |
| Valais, exact cells | **43.0 %** | 32.9 % |

Page mode won on all eight Inzigkofen pages individually, and needed 8 calls
instead of 291. At 20.4 % a commercial model with no training on the material
draws level with the best fine-tuned model in the field.

The mechanism is visible in the outputs: given one isolated line, the model
leaves the task and starts analysing letterforms — `shape: ascender loop up,
descender down) Stroke 4: descend` where a transcription should be. The full page
anchors it, and on a form the column position carries meaning that a cell crop
destroys outright.

**Change:** route VLM candidates through a page-level call and align the result
back onto the known line geometry, instead of sending crops. The line path stays
for kraken and TrOCR, which are line-level by construction.

**Caveat:** whole-page context amplifies what is there in both directions. A
locally hosted 8B model produced 189.8 % CER on the same page — 6 832 characters
for a 2 909-character page, collapsing into one repeated phrase. The mode helps
only a model that can already read the script.

## 2. The judge must be gated, or it costs more than it returns

As currently conceived, asking a language model to pick the best reading is worse
than not asking:

| strategy | mean CER of the chosen reading |
|---|---|
| oracle, best reading per line | 0.110 |
| **always the strongest single model, no judge** | **0.170** |
| the judge | 0.277 |

Its picks are statistically indistinguishable from choosing at random among four
candidates. Where it disagrees with the CER-best reading, its choice costs 25
additional CER points, and 44 % of those disagreements go to the weakest system
in the field — which writes fluent modern German while the accurate ones produce
spaced punctuation and historical spellings.

What helps, measured on the same forty lines:

| strategy | CER | vs. no judge | calls |
|---|---|---|---|
| gate + anti-fluency prompt | **0.131** | −0.039 | 7 of 40 |
| gate alone | 0.151 | −0.019 | 7 of 40 |
| prompt alone | 0.214 | +0.044 | 40 of 40 |

**Change:** escalate to the judge only when the two fine-tuned recognisers
disagree by more than 0.30 CER, and state in the prompt that historical spelling
and spaced punctuation are expected rather than defects. On the other 33 lines
the reliable systems agree and a choice can only do harm.

**Do not build:** handing the judge the facsimile. Measured at 0.283 against
0.273 without — no help. The model used reaches 78.9 % CER on this material
itself; it cannot read the line, so it cannot check it. It also picks its own
reading 14 times in 40, against a chance value of 10.

**Note for any future comparison:** the judge is not reproducible at temperature
0. Three runs with identical prompts returned 0.273, 0.277 and 0.297. Differences
below 0.02 are not interpretable without repetitions.

## 3. Vote per character before selecting per line

Character-level voting across the candidates, aligned to the strongest reading,
costs no model calls and is deterministic:

| method | CER | vs. no judge |
|---|---|---|
| oracle including the vote as a candidate | 0.095 | −0.050 |
| oracle over line selection | 0.103 | −0.042 |
| **unweighted vote across all four** | **0.123** | −0.022 |
| weighted by reliability | 0.130 | −0.015 |
| vote without the weakest system | 0.136 | −0.009 |

Three results run against intuition and should be kept in mind when implementing:
weighting by reliability makes it **worse**, because the strongest model then
outvotes everyone and the method collapses towards "always use the best model";
dropping the weakest system also makes it worse, because a model at 79 % CER
still gets the easy characters right as long as its errors are uncorrelated; and
gating hurts here, unlike with the judge — voting is safe everywhere.

Line selection was never the ceiling. The vote produces a better reading than any
single candidate on 32 of 150 lines. **Change:** vote first, then offer the
result as an additional candidate to a gated selection.

## 4. Run the degeneration detector before evaluation, not after

`_is_degenerate` already exists in `agents/text_recognition.py`. It runs too late
to protect the numbers.

| system | collapsed lines | CER | CER without them |
|---|---|---|---|
| VLM, production prompt | 2 of 149 | 96.2 % | 54.5 % |
| VLM, line prompt | 1 of 149 | 78.9 % | 53.9 % |
| kraken-catmus-medieval | 5 of 149 | 67.3 % | 67.2 % |

One collapsed line carries 25 CER points; two carry 42. Five kraken collapses
move its figure by a tenth of a point, because a CTC output is bounded by the
width of the image while a generative model can write indefinitely.

**Change:** screen candidates for degeneration before they enter selection,
fusion or any reported metric, and record the count rather than silently
dropping them.

## 5. The model registry does not describe the models

Four of six checked kraken entries in `serving-atr`'s `config/models.yaml`
resolve to a DOI for a different model:

| registry id | actually |
|---|---|
| `kraken-early_modern_german` | CATMuS Medieval |
| `kraken-bohemian_19th` | a generalised English **printed** text model |
| `kraken-czech_historic_v2` | Printed Ottoman Base Model (OpenITI) |
| `kraken-early_modern_german_16` | 11th-century manuscripts |

`agent_a/model_selector.py` reasons over these names — language, script family,
century. A page routed to "early modern german" is read by CATMuS Medieval. The
remaining 36 kraken entries are unverified.

**Change:** verify DOIs against the Zenodo record titles and add a check that
fails on a mismatch, so the selector's premises are enforced rather than assumed.

## 6. A missing vision model fails silently

`GPUSTACK_MODEL_VISION` pointed at `qwen3-vl-30b-a3b-instruct` while that model
was not deployed. Every vision call returned HTTP 404 — *"Model not found or no
running instances available"* — for more than a day, with no startup check and no
degraded-mode signal.

**Change:** probe the configured vision model at startup and either fail loudly
or fall back to a deployed one, with the substitution recorded on the run.

## 7. Recognition quality is domain fit, not capability

The same seven models move by up to 43 percentage points between the two prose
corpora, in both directions:

| model | Inzigkofen, 15th c. | Federal minutes, 19th c. |
|---|---|---|
| trocr-medieval-escriptmask | 20.0 % | 53.9 % |
| trocr-kurrent-XIX | 56.9 % | 13.9 % |
| kraken-catmus-medieval | 25.8 % | 67.3 % |

Only the general-purpose model varies by less than ten points. **Consequence for
the selector:** a benchmark ranking is valid for the corpus it was measured on
and nowhere else, so any prior it learns must be conditioned on the material.
Robustness across domains and peak accuracy within one are separate objectives —
`kraken-catmus-medieval` also reads Inzigkofen at 0.05 s/line, seven times faster
than the best TrOCR for 5.8 points more error, which is the relevant trade for
bulk runs.

## 8. On tabular sources the metric selects the model

For the census forms, CER and exact cell accuracy disagree about the winner:

| model | CER | exact cells |
|---|---|---|
| trocr-kurrent-XIX | **49.5 %** | 19.5 % |
| kraken-bohemian_19th_v2 | 63.4 % | **28.6 %** |

A generative model gets closer on average because it continues plausibly; a CTC
model stays short and hits exactly more often. On a four-character cell "close on
average" is worthless — a name is right or it is not.

**Consequence:** for tabular material the pipeline should optimise and report
exact cell accuracy, not CER, and the selector should not carry a CER-derived
prior into that regime. Nearly half these cells are a single character, and no
model in the fleet is trained on isolated form cells; they all expect lines of
text.

---

## Depends on serving-atr

`/recognize` accepts a `lines` parameter, the gateway forwards it, and
`engines/kraken_svc/app.py` discards it — the comment says so, and `blla` runs
unconditionally. A caller that already knows where the lines are cannot say so.
On a line strip that costs about 290 seconds against 5.8 for the same content in
page shape.

This blocks item 1 for kraken on any corpus that ships its own segmentation, and
it is the reason the evaluation calls the engines directly rather than through
the gateway.
