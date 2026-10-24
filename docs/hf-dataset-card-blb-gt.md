<!-- FRONTMATTER: `pagexml-hf` schreibt beim Upload selbst ein README mit
     `dataset_info`/`configs`. Dieser Block wird nach dem Upload wörtlich von
     dort übernommen (`upload_hf_card`), nie von Hand erfunden. -->

# Laßberg papers at the Badische Landesbibliothek — hand-transcribed pages

Some **2,700 pages** of the Laßberg holdings of the Badische Landesbibliothek
Karlsruhe (shelfmark series K 2911–K 2915, K 3123 and others), each a page
image paired with the complete PAGE XML of a **human transcription**, converted
from the library's ALTO full text. German Kurrent and Latin, c. 1800–1856:
letters by and to **Joseph von Laßberg (1770–1855)**, his copies of medieval
manuscripts, notes, documents — and 896 pages of a printed almanac, kept in
a project of their own.

The images, the metadata and the full text are published by the BLB under
**Public Domain** ([Digitale Sammlungen](https://digital.blb-karlsruhe.de/),
OAI-PMH set `blbknajvl`, "Joseph von Laßberg"). This dataset is the same
material in the shape the project's trainers read, built with
[`pagexml-hf`](https://github.com/The-Flow-Project/pagexml-hf) **0.9.6+fork.1**
in `raw_xml` mode from a tree assembled by
[`agentic_historian`](https://github.com/thodel/agentic_historian)
(`scripts/blb_gt_dataset.py`).

| `project_name` | what it is | how it was decided |
|---|---|---:|
| `lassberg` | letters **by** Laßberg | the BLB's title: *Brief von Joseph von Laßberg an …* |
| `korrespondenten` | letters **to** Laßberg | the title: *Brief von X an Joseph von Laßberg* |
| `unbestimmt` | letters whose title names no direction | *Korrespondenz zwischen …*, *Briefgedicht* |
| `manuskripte` | copies, excerpts, notes, documents — handwriting, hand not stated | everything that is not a letter and not printed |
| `drucke` | the printed *Fürstenbergischer Landkalender* (K 2914) | the series |

Each of the five has a sibling **`<project>_eval`** holding a seeded tenth of
its *objects* (shelfmarks), moved there before the upload by
`scripts/blb_gt_split.py` so that no page of an evaluation object has a
sibling page in training; `K 2911,17`, `K 2911,107` and `K 2911,110` — the
objects of the project's own evaluation manifest the BLB holds — are always on
the evaluation side. Train on `<project>`, measure on `<project>_eval`.

Columns: `image`, `xml_content`, `filename`, `project_name`.

## What is true of this data and not obvious from it

**1. The text is a transcription, and it is the same transcription this project
already holds for part of the material.** The BLB does not state who produced
its full text. Measured against the hand-corrected Transkribus pages of this
project on 23 October 2026, over the six pages both sides hold, it is
**character-identical on five and 2.8 % apart on the sixth** — where two
people's conventions differ, not where a machine erred. Nothing a machine has
read in this corpus comes near that (the best engine measured here reads
Laßberg's own hand at 27–45 % CER). Where the text came from — delivered by
the editing project, or transcribed by the library — is a question put to the
project; the answer does not change what the text is.

**2. The hand labels come from the catalogue, not from a guess.** In the
project's first Laßberg dataset the writer was inferred from each letter's
dateline and inherited along the letter. Here it is read off the BLB's own
title, which names sender and recipient. The price is the fourth project:
copies, excerpts and notes carry no writer in their title, and although most of
them are very probably in Laßberg's hand, "very probably" is not a label.
Split by `project_name` **and by shelfmark** (`filename` begins with it:
`K2911-47_0001`): two pages of one object are the same hand, paper and ink.

**3. The line geometry is converted, not measured.** ALTO gives a box per line;
the PAGE `TextLine` is that box as a rectangle, scaled onto the image, and the
`Baseline` is the box's **lower edge** — a usable crop boundary for a line
recogniser, not a baseline a segmenter was trained on. The region is the whole
page. Every PAGE document says this in its first comment, with the shelfmark,
title, date, MODS genre, the BLB's object id and the URLs of the image and the
ALTO it was made from.

**4. Pages without text are not here — and that is a third of them.** The BLB
holds 4,123 page images in the objects with full text; 1,419 of them have an
ALTO without a single line — blank versos, address panels, covers, and in the
almanac whole runs of pages the library did not transcribe. They are left out
rather than carried as empty rows; the BLB holds the images. Objects without
any full text (10 of 401 with pages) are left out entirely. By project, pages
with text: `lassberg` 305 (70 objects), `korrespondenten` 370 (81),
`unbestimmt` 111 (36), `manuskripte` 1,022 (137), `drucke` 896 (66).

**5. This overlaps with the project's other datasets.** 54 objects of the
K 2911 series are also in the corpus the project reads, and some of their pages
are among the 254 of
[`image-text_lassberg-correspondence_xix`](https://huggingface.co/datasets/dh-unibe/image-text_lassberg-correspondence_xix)
and among the 276 pages the project scores engines against. A held-out
evaluation on either of those must exclude the matching shelfmarks from any
training on this one.

**6. `drucke` is not handwriting.** 896 pages of a printed almanac with a
transcription are OCR ground truth for Fraktur, not Kurrent. They are in the
dataset because the BLB's set has them and the label makes them easy to leave
out — which a handwriting model should do.

## Usage

```python
from datasets import load_dataset

ds = load_dataset("dh-unibe/image-text_lassberg-blb-gt", split="train")
HANDS = ("lassberg", "korrespondenten", "unbestimmt", "manuskripte")
train = ds.filter(lambda r: r["project_name"] in HANDS)
evalu = ds.filter(lambda r: r["project_name"] in {h + "_eval" for h in HANDS})
shelf = lambda r: r["filename"].rsplit("_", 1)[0]          # K2911-47_0001 -> K2911-47
```

The Dataset Viewer is unavailable for this repository: private datasets are
served by it only for PRO users and Enterprise Hub organizations.

## License

The images, metadata and full text are published by the Badische
Landesbibliothek under **Public Domain**; the conversion and the structure are
MIT. **This dataset is private** while the question in caveat 1 is open and
until the editing project has decided how its own transcriptions travel.
