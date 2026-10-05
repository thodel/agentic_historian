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

Two things are deliberately absent from that surface. **The geometry check cannot
be turned off** — a page whose XML geometry disagrees with its image crops the
wrong strip out of every line, and nothing downstream of the dataset would say
so, which is a thing to decide while looking at the pages. And **nothing is
uploaded**: the `pagexml-hf` command is printed for a person to run, because
publishing a dataset is a public act. `--source` and `--archive` are not passed
either, for the reason in "Do not pass `$ATR_PAGE_CACHE`" below.

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

## What the dataset card should say

Three things that are true of this data and not obvious from it:

1. The split is inferred from datelines and will be wrong on some pages.
2. Status DONE counts as ground truth here. This collection used the GT tag on 19
   of 552 pages, so requiring it would have discarded 96 % of the available truth.
3. Two harvested documents (`doc1350777`, `doc1350778`) are pages of a *printed
   edition* of the correspondence rather than letter scans. They never reach the
   export, because they locate nothing — but anyone comparing counts against the
   Transkribus collection will find them missing and should know why.
