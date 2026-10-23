# Training on the BLB Laßberg ground truth

Three fine-tunes on `dh-unibe/image-text_lassberg-blb-gt`, one per engine the
trainer has, measured on the **same held-out objects**. Written 2026-10-23,
before the dataset was uploaded; the `revision` fields are `null` until it is.

## The split comes first

The trainer splits **pages** unless a dataset names `eval_projects`. Two pages
of one letter are the same hand, paper and ink, so a page split measures
recognition of hands the model trained on. `scripts/blb_gt_split.py` therefore
moves a seeded tenth of the **objects** (shelfmarks) of every project into a
sibling project `<project>_eval` before the tree is uploaded:

```
python3 scripts/blb_gt_split.py --tree /mnt/wbkolleg_dh_1/tei/datasets/lassberg-blb-gt --dry-run
python3 scripts/blb_gt_split.py --tree /mnt/wbkolleg_dh_1/tei/datasets/lassberg-blb-gt
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
  `eval_projects`. The 20 others keep their caps, so the BLB set's ~2,400
  handwriting pages are the largest single source, which is the point.
* **kraken is a fine-tune of the 19th-century model**, `kraken-german-xix-v1`
  (the "Kurrent XVI–XIX" of the request is a VLM recipe name; kraken's nearest
  base for 1800–1856 hands is the XIX model; `kraken-early_modern_german` is
  the alternative if the XIX one cannot be loaded — it lost its `promoted` flag
  in an engine outage, not on merit). `resize: union` for characters the base
  codec lacks, early stop on the validation set after a floor of 10 epochs,
  cosine schedule (the only one kraken 7.0.2 steps correctly), augmentation,
  NFD. BLB only: the base already carries the other corpora, and the question
  is what Laßberg's hand adds. ~4,000 pages × ~20 lines ≈ 70k lines, batch 32
  → ~2,000 steps an epoch. `chunk_size: 400` bounds disk on the node.

## Running them

UBELIX is the default (`WHERE_A_RUN_RUNS` in the trainer), two stages:

```
scp training/specs/blb-*.json ubelix:~/ubelix/specs/
ubelix/submit.sh ubelix/prepare.sbatch ~/ubelix/specs/blb-qwen35-page-v1.json
JOB_ID=<id> ubelix/submit.sh ubelix/train.sbatch -- --partition=gpu --qos=job_gratis --cpus-per-task=12 --time=15:00:00
```

(the same for the other two). asteraix via the gateway is the fallback:
`POST /train/jobs` with the spec as body (`agent_a/training_client.py`).

Before submitting: pin `revision` in each spec to the uploaded dataset's commit
(`HfApi().dataset_info("dh-unibe/image-text_lassberg-blb-gt").sha`), and check
that `kraken-german-xix-v1` resolves in the registry the trainer reads.

## What to compare afterwards

All three report CER on `*_eval` of the four handwriting projects. Beside them:
the Qwen3.5 base (27 % on the Kurrent page benchmark, 14.5 % on the
project's eval split), Gemini (4.8 % on the same split), and
`kraken-german-xix-v1` unchanged. Per-project numbers matter more than the
mean: `lassberg` is one hand, `korrespondenten` is dozens.
