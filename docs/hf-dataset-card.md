---
dataset_info:
  config_name: default
  features:
  - name: image
    dtype:
      image:
        decode: false
  - name: xml_content
    dtype: string
  - name: filename
    dtype: string
  - name: project_name
    dtype: string
  splits:
  - name: train
    num_examples: 254
    num_bytes: 1535886572
  download_size: 1535886572
  dataset_size: 1535886572
configs:
- config_name: default
  data_files:
  - split: train
    path: data/train/**/*.parquet
tags:
  - image-to-text
  - htr
  - trocr
  - transcription
  - pagexml
license: mit
---

# Laßberg correspondence, 19th century — hand-corrected pages

254 pages of the correspondence of **Joseph von Laßberg (1770–1855)**: a page
image paired with the complete Transkribus PAGE XML of its hand-corrected
transcription. German Kurrent, c. 1808–1855, letters and enclosures held across
thirteen archives and libraries.

Built with [`pagexml-hf`](https://github.com/The-Flow-Project/pagexml-hf)
**0.9.6+fork.1** (Python 3.12.3) on 21 October 2026, in `raw_xml` mode, from an
export tree assembled by
[`agentic_historian`](https://github.com/thodel/agentic_historian) —
`docs/HF_DATASET.md` there is the runbook.

| | |
|---|---:|
| Pages | **254** |
| `korrespondenten` | 131 |
| `lassberg` | 89 |
| `unbestimmt` | 34 |
| Size | 1.46 GB |

## Four things that are true of this data and not obvious from it

**1. `project_name` is the writing hand, and it is inferred.** The folder a page
comes from is the *holding archive*, which holds both sides of a correspondence,
so it cannot serve as the axis: measured on this corpus, one engine reads
Laßberg's correspondents at 6–20 % character error rate and Laßberg himself at
27–45 %. The hand is recorded nowhere in the data, so it is read off the
**dateline** of a letter's first page and inherited by that letter's other pages
(a dateline cannot stand on a continuation page). Where the edition's letter
register names a sender, the register wins — it is a record, the dateline is a
guess. **It will be wrong on some pages.** 146 of the 254 come from the register,
44 from their own dateline, 30 are inherited; in five letters register and
dateline disagree, and in one letter two dated pages disagree with each other.

**And one letter crosses the boundary.** Where two dated pages of a letter
disagree, nothing is inherited and each page keeps its own reading — so
`lassberg-letter-2921` has one page under `korrespondenten` and one under
`lassberg`. If you split train and test by `project_name`, that letter lands on
both sides: one page left, one right, same writer, same paper, same ink. It is
one letter of 96, so the effect is small, but it is the shape of a leak in a
held-out measurement, and it is a different fault from the one above — here the
hand may be right and the boundary is porous. Split by letter (the
`lassberg-letter-NNNN` segment of `filename`) if a held-out number has to mean
something.

**2. `unbestimmt` means nothing said anything.** 34 pages have no recognisable
dateline and no dated page in their letter. They are **not a third hand** — do
not train a three-way split on this column without deciding what to do with
them. A rescued page (see 4) is usually a continuation page, and the shards show
how strongly: **7 of the 13 rescaled pages sit in `unbestimmt`**, 21 % of that
shard against 2.3 % and 3.4 % of the other two. This project is both the least
labelled and the most repaired part of the dataset.

**3. Status DONE counts as ground truth here.** This Transkribus collection used
the GT tag on 19 of 552 pages, so requiring it would have discarded 96 % of the
available truth. A page marked DONE has been corrected by a human. Pages below
DONE are refused loudly; 27 pages whose status is unreadable were admitted on
trust, because the harvest only ever fetches corrected pages.

**4. Thirteen pages carry rescaled line coordinates, and they say so in their own
XML.** The PAGE polygons are in the pixel coordinates of *Transkribus's* copy of
the scan; the image beside them is a working copy of the *share's* copy. Where
the two sizes disagree the page is **refused**, not rescaled — 27 pages of this
ground truth are two different photographs of one leaf (two fixed-size
derivatives, one scanned opening against a half leaf, one landscape against a
portrait page) and are absent from this dataset. Where one factor relates both
axes to within 0.11 %, the two are the same scan at two resolutions: those 13
pages are included with their coordinates divided by that factor, outlines and
baselines alike, `imageWidth`/`imageHeight` rewritten to the image, and a comment
as the first line of the document:

```xml
<!-- agentic_historian: line coordinates divided by 2.254993; the ground truth
     describes 1770x2598, this image is 785x1152. imageWidth/imageHeight
     rewritten to the image. The pixels are the same scan at two resolutions:
     both axes agreed on the factor to within 0.019 %. -->
```

Filter on that string to find or exclude them — verified in the uploaded parquet:
13 rows carry it, 3 + 3 + 7 across the three projects. Their factors are 1.58 to
3.31 and vary **within a single letter**, so they are not one derivative
pipeline.

## What is not in here, and why

- **Two documents of the Transkribus collection are a printed edition**, not
  letter scans: `doc1350777` (32 pages) and `doc1350778` (53) carry 45–51 lines
  and 2000–2900 characters per page, their first lines are folio numbers, and
  they open with `BRIEFWECHSEL ZWISCHEN J VON LASZBERG UND JOHANN ADAM PUPIKOFER`
  above an editor's introduction. They also carry uncorrected PyLaia output under
  statuses `Final`/`FINAL`. They never reach this export because they locate no
  page of the manuscript corpus — but anyone comparing counts against the
  collection will find 85 pages missing and should know why.
- **195 ground-truth files located no page confidently enough** to be paired with
  an image, and **76 are second copies** of a page already included (the same
  page appears under several Transkribus document ids). Exporting a page twice
  would hand a trainer one page with twice the weight.
- **35 pages whose transcription exists have no image.** Two folders vanished
  from the source share in October 2026; whether they were deleted, moved or
  renamed is a question for its custodians.

## What has not been checked

**Whether the rescaled polygons sit on the ink.** That the XML and its image
agree on their size is verified three ways, in the plan, in the writing, and in
the written files. Consistency is not correctness: nobody has looked at a line
crop from one of those 13 pages.

## Usage

```python
from datasets import load_dataset

ds = load_dataset("dh-unibe/image-text_lassberg-correspondence_xix", split="train")
print(ds.features)                       # image, xml_content, filename, project_name
lassberg = ds.filter(lambda r: r["project_name"] == "lassberg")
```

Reading one text column without downloading the images — the whole dataset is
1.46 GB and almost all of it is pixels:

```python
from huggingface_hub import HfFileSystem
import pyarrow.parquet as pq

fs = HfFileSystem()                       # needs HF_TOKEN; the dataset is private
base = "datasets/dh-unibe/image-text_lassberg-correspondence_xix/data/train"
for shard in sorted(fs.glob(base + "/*/*.parquet")):
    t = pq.read_table(shard, columns=["filename", "xml_content"], filesystem=fs)
    print(shard.split("/")[-2], t.num_rows)
```

The Dataset Viewer is unavailable for this repository: private datasets are
served by it only for PRO users and Enterprise Hub organizations. Rows have to be
downloaded to be read.

## License

MIT for the conversion and the structure. The page images are digitisations held
by the contributing archives and libraries; the transcriptions are the work of
the editing project. **This dataset is private.** Clear the rights with the
holding institutions before anything here is published.
