<!-- FRONTMATTER: `pagexml-hf` schreibt beim Upload selbst ein README mit
     `dataset_info`/`configs`. Dieser Block wird nach dem Upload wörtlich von
     dort übernommen und ersetzt diesen Kommentar — wie bei der Karte des
     handkorrigierten Datensatzes. Nicht von Hand erfinden: die Zeilenzahl und
     die Bytes stehen dort gemessen. -->

# Laßberg correspondence, 19th century — uncorrected machine readings

**Nobody has read this text.** 281 pages of the correspondence of **Joseph von
Laßberg (1770–1855)**, each a page image paired with the transcription a
commercial vision-language model produced from it in October 2026. No human has
checked a single character of it, and for these pages no reference transcription
exists — which is why they were read by a machine in the first place.

The hand-corrected counterpart is
[`image-text_lassberg-correspondence_xix`](https://huggingface.co/datasets/dh-unibe/image-text_lassberg-correspondence_xix):
254 pages whose text a human did check, with real line geometry. If you need
ground truth, that is the dataset. This one is a record of what a reader said.

Built with [`pagexml-hf`](https://github.com/The-Flow-Project/pagexml-hf)
**0.9.6+fork.1** in `text` mode, from an export tree assembled by
[`agentic_historian`](https://github.com/thodel/agentic_historian) —
`docs/HF_DATASET.md` there, part *Der zweite Datensatz*, is the runbook.

| | |
|---|---:|
| Pages | **281** |
| Readings in the run | 300 |
| Refused (model returned nothing) | 19 |
| `project_name` | `gemini-3.8-flash` |

Columns: `image`, `text`, `filename`, `project_name`.

## Six things that are true of this data and not obvious from it

**1. The error rate of these pages is unknown, and errors are not spread
evenly.** There is no reference for them, so no CER can be quoted. What *was*
measured on the hand-corrected part of the same corpus, with the same engine
family, is the shape of the problem: **6–20 % character error rate on Laßberg's
correspondents and 27–45 % on Laßberg's own hand** — a factor of four to five
inside one model on one corpus. A page in this dataset is therefore not "about
90 % right"; it is somewhere in a range that depends on who held the pen, and
nothing in the data says who that was.

**2. There is no line geometry, and that is deliberate.** A chat completion
returns text, not polygons. Each page is one `TextRegion` covering the whole page
with the text in its `TextEquiv` and **no `TextLine` elements**. Line boxes could
have been invented — divide the page into as many strips as the model returned
lines — and they would have been indistinguishable from measured ones while
shifting every target by an unknown amount. Consequence, measured rather than
assumed: a line-level export of this material does not produce a misaligned
dataset, it **fails** (`ZeroDivisionError` over zero lines). The `text` column is
page-level, and that is all it is.

**3. `project_name` is the model, not the writing hand.** In the hand-corrected
dataset that column is the hand, inferred from each letter's dateline. Inferring
it here would mean reading a dateline out of an *unverified* transcription and
inheriting it along a letter — an inference on an inference. The holding archive
and the letter id are in `filename`, which is the page key
(`<archive>__<folder>__<page>`). **Split by letter** — the
`lassberg-letter-NNNN` segment — if a held-out number has to mean something: two
pages of one letter are the same writer, the same paper and the same ink.

**4. The 19 missing pages are the model's silence, not blank leaves.** They are
absent because the model returned no text at all, and one of them is
**written**: `blb lassberg__K 2911,93__page_0001` carries 24 readable lines,
which two models of other families transcribed in a cross-check on
19 October 2026 — this model, in this run, said nothing about it four times
over. Repeating the same reader does not recover them: four passes of this model
over those 19 pages yielded 3, then 2, then 0 additional pages. An empty answer
is a statement about the reader, and treating it as a statement about the page
was a mistake this project made once already.

**5. 29 of the 281 pages hold fewer than 100 characters.** They are included:
`page_quality` calls them `short`, which is a statement about length, and a half
written page or an address panel is legitimately short. But a silent failure
would look exactly like this, so filter on `len(text)` before using the dataset
as if every row were a full page. The remaining 252 are verdict `ok`. Zero pages
in this run were repetition loops, self-deliberation in the transcription field
or truncated at the model's output ceiling — all three are refused by the
exporter, and on this run the guard did not have to fire.

**6. Every row says in its own document what produced it.** The export is
image + text, but the synthesised PAGE document that produced the text carries a
comment naming the model, the run, the timestamp, the prompt digest and
recognition settings, the quality verdict and the source image's sha256. It is
not in a column — a dataset is rows, and a note beside them reaches nobody — so
if you need the provenance of a single page, ask the project for the run record
under that `filename`.

## What this is for, and what it is not for

It is a **record of a reading**: for searching an otherwise unsearchable corpus,
for comparing engines on the same pages, for finding where a page worth
transcribing actually is, and for reading a letter whose content was unknown
until October 2026.

It is **not training data for handwriting recognition**, and least of all for the
family of models that produced it: training on your own uncorrected output
teaches the model its own errors and is measurable only against ground truth this
subset does not have. It is also not an edition. Where a sentence here matters to
an argument, read the image.

## Usage

```python
from datasets import load_dataset

ds = load_dataset(
    "dh-unibe/image-text_lassberg-correspondence_xix_uncorrected-atr",
    split="train")
print(ds.features)                      # image, text, filename, project_name

# the letter a page belongs to, which is the axis to split on
import re
letter = lambda r: (re.search(r"lassberg-letter-\d+", r["filename"]) or [None])[0]

# pages long enough to be a full page of prose (see caveat 5)
full = ds.filter(lambda r: len(r["text"]) >= 100)
```

Reading the text without downloading 1.5 GB of pixels:

```python
from huggingface_hub import HfFileSystem
import pyarrow.parquet as pq

fs = HfFileSystem()                     # needs HF_TOKEN; the dataset is private
base = ("datasets/dh-unibe/image-text_lassberg-correspondence_xix_"
        "uncorrected-atr/data/train")
for shard in sorted(fs.glob(base + "/*/*.parquet")):
    t = pq.read_table(shard, columns=["filename", "text"], filesystem=fs)
    print(shard.split("/")[-2], t.num_rows)
```

The Dataset Viewer is unavailable for this repository: private datasets are
served by it only for PRO users and Enterprise Hub organizations.

## License

MIT for the conversion and the structure. The page images are digitisations held
by the contributing archives and libraries; the transcriptions are machine output
commissioned by the editing project. **This dataset is private.** Clear the
rights with the holding institutions before anything here is published — and if
any of this text is published, publish it as what it is: unread by a human.
