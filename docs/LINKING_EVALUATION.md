# Measuring entity linking against the historian's decisions

Status: in use, 2026-10-15. How `agentic_historian/eval/linking.py` scores the
entity linker against Gate 3 decisions and attributes every miss to a cause.
Ported from [outremer](https://github.com/thodel/outremer) (its #89), where
the pattern was built after two published baselines turned out to be
measurement artefacts.

This document covers the **instrument**, like
[EVALUATION_HARNESS.md](EVALUATION_HARNESS.md) does for recognition. It
measures *linking agreement* — whether the pipeline proposed what a historian
later chose — and nothing about transcriptions. The closest-reading rule holds:
a historian's choice among offered candidates is the best available option,
not independently established truth, and it is never a denominator for
accuracy of anything else.

## The two incidents the pattern comes from

outremer links extracted persons to its authority file (`AUTH:CR…`) and,
separately, to Wikidata (`wikidata:Q…`). Scholars adjudicated pairs from both
systems in one review interface. Two baselines were published before the
measurement was right:

| published | what it actually measured |
|---|---|
| 0.6479 | the fixture builder read `authority_id` where the candidates carried `outremer_id`, so an accept could **never** be counted as a hit — the number was the share of rejects in the gold |
| 0.6761 | Wikidata adjudications were counted against the authority linker, which cannot propose a QID; every one of them was a fabricated miss |

The corrected figure was 0.9014 over 71 pairs (authority 0.8727 over 55,
Wikidata 1.0 over 16). Neither wrong number looked wrong. Both would have been
caught by two rules, which `eval.linking` enforces:

1. **An id names its system.** Pairs are `gnd:…`, `hls:…`, `wikidata:…` or
   `hub:…`, and each is judged only against the system that could have
   proposed it (`split_pairs_by_system`). An id without a prefix is an error,
   not a guess.
2. **Agreement is reported next to the null linker.** Agreement rewards two
   things: proposing what was accepted, and not proposing what was rejected.
   On reject-heavy gold, proposing nothing scores well — outremer's authority
   gold of 2026-07-30 (7 accepts, 48 rejects) gave the null linker 0.873
   against a measured 0.891. `lift_over_null` is the signal;
   `accept_rate` is the positive evidence alone.

## What the gold is here

Gate 3 (`entity_review.py`) shows a historian up to five unverified or
low-confidence entities with the top candidates from the MCP federation and
"kein Link". Since this change the review leaves three things on the entity
that make the decision usable afterwards:

| field | written by | meaning |
|---|---|---|
| `pipeline_link` | `apply_entity_link`, once | the pipeline's proposal (`gnd`, `hls`, `wikidata`, `hub_id`, `link_method`, …) **before** the human choice overwrote it |
| `offered_candidates` | `build_review_items` | the ids of the candidates shown, as `system:id` |
| `rejected_candidates` | "kein Link" | the offered ids, now declined — and no other record |

`gate_decisions["entity_links"]` records `chosen` and `offered` as ids as well;
the former `value` (a position in a list that no longer exists) stays for
compatibility.

From an entities file, `gold_from_entities` derives:

- **accepted** — for every `human_confirmed` entity, one pair per system id it
  carries (a candidate with a GND and an HLS id is two pairs, one per system);
- **rejected** — for every `human_none` entity, one pair per rejected candidate.

A historian who declined three candidates has judged those three. The
evaluation does not infer a verdict on records nobody showed her.

## Why every miss gets a cause

`diagnose` classifies each accepted pair the pipeline did not propose:

| cause | meaning | whose problem |
|---|---|---|
| `not_extracted` | no entity with that mention in the document's output | extraction |
| `no_candidates` | extracted, nothing proposed, no candidate kept | linker recall |
| `ranked_below_top` | the adjudicated id was among the candidates, not chosen | ranking |
| `candidate_missing` | candidates exist, the adjudicated id is not among them | candidate generation |

In outremer this is what turned "linking is broken" (agreement 0.11 and 0.0 on
two documents) into "today's extraction no longer finds Tancred, Albert of
Aachen, Guibert of Nogent and Orderic Vitalis" — four `not_extracted` rows,
no linker bug (#42). For the taxonomy to say `ranked_below_top`, the linker
has to keep its runners-up: `_mcp_link` now records the top three federation
candidates as `candidates`.

## Running it

```bash
python -m eval.linking                                  # data/outputs/*_entities.json
python -m eval.linking --entities-dir DIR --json out.json
```

The report prints one row per system with pairs, agreement, null baseline,
lift, accept hit/miss and reject hit/avoided, then the cause breakdown and
every miss. A directory without Gate 3 decisions prints that and nothing
else — an empty gold is not a perfect score.

## What it refuses to do

- Score a transcription. Linking agreement says nothing about text.
- Count a pair against a system that could not have proposed it.
- Treat a decided entity whose proposal was not stashed (outputs older than
  this change) as a hit or a miss: without `pipeline_link` there is no
  recoverable prediction, and scoring the human choice against itself would
  manufacture agreement.
