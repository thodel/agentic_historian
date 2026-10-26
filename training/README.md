# Training on the BLB Laßberg ground truth

Three fine-tunes on `dh-unibe/image-text_lassberg-blb-gt`, one per engine the
trainer has, measured on the **same held-out objects**. Written 2026-10-23,
before the dataset was uploaded; the `revision` fields are `null` until it is.

The build wrote 2,700 pages: 1,419 of the 4,123 images carry an empty ALTO and
were left out (see the card, caveat 4). Handwriting: 1,808 pages in 324
objects; `drucke` 896.

## The split comes first

The trainer splits **pages** unless a dataset names `eval_projects`. Two pages
of one letter are the same hand, paper and ink, so a page split measures
recognition of hands the model trained on. `split-tree` (`split_tree` over MCP, #711) therefore
moves a seeded tenth of the **objects** (shelfmarks) of every project into a
sibling project `<project>_eval` before the tree is uploaded:

```
python3 -m agentic_historian split-tree --tree /mnt/wbkolleg_dh_1/tei/datasets/lassberg-blb-gt \
    --force-eval K2911-17,K2911-107,K2911-110             # shows what would move
python3 -m agentic_historian split-tree --tree … --force-eval … --apply
split_tree(tree="datasets:lassberg-blb-gt", force_eval=["K2911-17", "K2911-107", "K2911-110"])
```

Forced into the evaluation side: `K 2911,17`, `K 2911,107`, `K 2911,110` — the
objects of the HTR project's `eval_manifest_random.txt` that the BLB holds, so
the Qwen (14.5 %) and Gemini (4.8 %) figures of 22/23 October stay comparable.
The 54 K 2911 objects that overlap the project's Transkribus pages are **not**
held out: the point is to train on Laßberg's hand.

The upload then carries ten projects; `drucke`/`drucke_eval` (printed almanac,
Fraktur) are in the dataset and out of every spec below.

## The three specs (`training/specs/`)

| spec | engine | base | data | granularity |
|---|---|---|---|---|
| `blb-qwen35-page-v1.json` | vllm (QLoRA) | `Qwen/Qwen3.5-4B` | the 20 datasets of `german-kurrent-xvi-xix-page` (pinned, capped) **+ BLB** | page |
| `blb-olmocr2-page-v1.json` | vllm (QLoRA) | `allenai/olmOCR-2-7B-1025` | the same 20 **+ BLB** | page, 4.2 Mpx, native prompt |
| `blb-kraken-v1.json` | kraken | `kraken-german-xix-v1` | **BLB only** | lines cut from the PAGE geometry |

Why these and not others:

* **Qwen3.5 and olmOCR train on pages, not lines** (the user's instruction, and
  the only setting the PAGE geometry is good for: the baselines are the lower
  edges of ALTO boxes, fine for crops, not for teaching layout). Both start
  from the measured Kurrent XVI–XIX page recipes — the Qwen one scored 27.04 %
  page CER — and add the BLB set as a 21st dataset with `train_projects` /
  `eval_projects`. The 20 others keep their caps, so the BLB set's ~1,600
  handwriting training pages (1,808 minus the held-out tenth) are the largest
  single source, which is the point.
* **kraken is a fine-tune of the 19th-century model**, `kraken-german-xix-v1`
  (the "Kurrent XVI–XIX" of the request is a VLM recipe name; kraken's nearest
  base for 1800–1856 hands is the XIX model; `kraken-early_modern_german` is
  the alternative if the XIX one cannot be loaded — it lost its `promoted` flag
  in an engine outage, not on merit). `resize: union` for characters the base
  codec lacks, early stop on the validation set after a floor of 10 epochs,
  cosine schedule (the only one kraken 7.0.2 steps correctly), augmentation,
  NFD. BLB only: the base already carries the other corpora, and the question
  is what Laßberg's hand adds. ~1,650 training pages × ~20 lines ≈ 33k lines, batch 32
  → ~1,000 steps an epoch. `chunk_size: 400` bounds disk on the node.

## Running them

`train-spec` (`train_spec` over MCP, #715) reads a spec by name, drops the
`_note` keys, pins every `revision: null` to the hub's current commit and
says which. Checked on 24.10. against the trainer's `TrainRequest`
(training-atr-models e25ba03): all three validate, and no two of their
datasets share a project name.

```
train_spec(spec="blb-kraken-v1")                       # the pinned body, nothing sent
train_spec(spec="blb-kraken-v1", action="submit")      # asteraix, via the gateway
train_spec(spec="blb-kraken-v1", action="status")
train_spec(spec="blb-qwen35-page-v1", action="ubelix") # the Slurm commands, for a person
```

UBELIX is the default place for the two VLM runs (`WHERE_A_RUN_RUNS` in the
trainer). Its submission stays a person's act — it runs on an account and its
quota, and the `ubelix_*` tools are read-only on purpose. The commands come
from the tool, for both specs at once (#739):

```
train_spec(spec="blb-qwen35-page-v1,blb-olmocr2-page-v1", action="ubelix")
```

It prints what is typed on UBELIX, in `~/training-atr-models` after a pull:
`curl` of the pinned bodies from `training/requests/` on main (tei cannot copy
to UBELIX), one `prepare` that builds the corpus and a second that queues
behind it and adopts it, and the two trainings, each reading the **trainer** job
id its prepare wrote. When a pinned body is not on main yet, the first lines say
to write it in a working tree (`train-spec --spec … --write-request`) and commit
it.

`submit` is refused when the `model_id` already has a job. Before the kraken
run, check that `kraken-german-xix-v1` resolves in the registry the trainer
reads.

## What to compare afterwards

All three report CER on `*_eval` of the four handwriting projects. Beside them:
the Qwen3.5 base (27 % on the Kurrent page benchmark, 14.5 % on the
project's eval split), Gemini (4.8 % on the same split), and
`kraken-german-xix-v1` unchanged. Per-project numbers matter more than the
mean: `lassberg` is one hand, `korrespondenten` is dozens.

## Runs

| spec | where | job | dataset revision | submitted |
|---|---|---|---|---|
| `blb-kraken-v1` | asteraix (gateway) | `20261026T193646Z-kraken-german-xix-blb-v1` | `c25587dd22995eaea1058e78554e20e6194ab3ff` | 26.10.2026 |
| `blb-qwen35-page-v1` | UBELIX | — (commands handed over 26.10.) | same | — |
| `blb-olmocr2-page-v1` | UBELIX | — (commands handed over 26.10.) | same | — |

The dataset went up on 26.10. as `b84a723` (data) and `c25587d` (the card,
same data); 2704 rows, 31.8 GB because `pagexml-hf` stores every page as PNG.
