"""A run's machine readings in the shape `pagexml-hf` reads (`run_export`).

The sibling of `test_ah_hf_export.py`, and the tests are about the difference.
That export carries hand-corrected PAGE XML with line polygons, and its central
test is that the geometry matches the image. Here there **is** no geometry: a chat
completion returns text, so the PAGE document is synthesised around the text, and
the questions are different ones.

**Does the converter get the text?** `pagexml-hf` reads a page's text as the join
over its regions' ``full_text``, and a region's ``full_text`` is its own
``TextEquiv/Unicode`` when it has one — so one region, no lines, is a complete
input. That is somebody else's contract, so it is pinned here against the clone's
own parser rule, and the shape of the document is asserted explicitly: a document
that silently stopped carrying its text would produce a dataset of empty strings.

**Are the lines absent on purpose?** Line boxes could be invented by dividing the
page into strips, and they would be indistinguishable downstream from measured
ones. A test asserts there are none, because that is a decision and not an
omission.

**Is the refusal of a defective reading counted rather than silent?** `empty`,
`repetitive` and `deliberation` are `page_quality`'s names for a reading that is
the model's defect rendered as text, and a truncated page is a success that stops
mid-sentence. Each is refused by default, in its own category, with its key.

**Does the tree say what it is?** The upload tool takes no mode, so the mode lives
in the tree's manifest. A tree of machine readings uploaded as `raw_xml` would put
synthesised documents in an `xml_content` column beside fourteen datasets of real
Transkribus output.
"""

import json
import re
import sys
from pathlib import Path

import pytest


PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import hf_export as hf                     # noqa: E402
import run_export as rx                    # noqa: E402

PAGE_NS = "http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"


# ── fixtures ─────────────────────────────────────────────────────────────────

#: A page of a length `page_quality` calls ordinary. Under 100 characters its
#: verdict is `short` — a statement about length, not a defect — and a fixture
#: that trips it tests the threshold instead of the export.
PAGE_TEXT = ("Meersburg, den 3. August 1831\n"
             "Theuerster Freund!\n"
             "Ihr Brief vom vorigen Monat ist mir richtig zugekommen und hat\n"
             "mich sehr erfreut. Die Handschrift, von der Sie schreiben, habe\n"
             "ich endlich erhalten und bereits durchgesehen.")


def _image(path: Path, size=(1200, 1600)) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (255, 255, 255)).save(path, "JPEG", quality=60)
    return path


def _reading(run: Path, model: str, key: str, text: str, **over) -> Path:
    """One page as the runner leaves it: a .txt and the .json that is the record."""
    import page_quality

    d = run / model
    d.mkdir(parents=True, exist_ok=True)
    meta = {"schema": 4, "run": run.name, "model": model, "engine": "gateway",
            "doc_id": "", "source": {"key": key, "name": f"{key}.jpg",
                                     "sha256": "a" * 64, "sha256_of": "original"},
            "text": text, "lines": text.splitlines(),
            "settings": "prompt-9f2c1a+think-low|max8192",
            "truncated": False, "gateway_version": "1.4.2",
            "recognised_at": "2026-10-12T09:14:33+00:00",
            "quality": page_quality.classify(text).as_dict()}
    meta.update(over)
    (d / f"{key}.json").write_text(json.dumps(meta, ensure_ascii=False),
                                   encoding="utf-8")
    (d / f"{key}.txt").write_text(text, encoding="utf-8")
    return d / f"{key}.json"


@pytest.fixture
def corpus(tmp_path):
    """A run with one good page, and a cache holding its image."""
    run = tmp_path / "runs" / "gemini-300-final"
    _reading(run, "gemini-3.8-flash", "Basel__lassberg-letter-1012__p1", PAGE_TEXT)
    cache = tmp_path / "cache"
    _image(cache / "Basel" / "lassberg-letter-1012" / "p1.jpg")
    index = {"Basel__lassberg-letter-1012__p1":
             cache / "Basel" / "lassberg-letter-1012" / "p1.jpg"}
    return run, index


# ── reading the run ──────────────────────────────────────────────────────────

def test_the_json_is_the_record_not_the_txt(corpus):
    """The .txt holds the same string; only the .json says what produced it."""
    run, _ = corpus
    page, = rx.read_run(run)
    assert page.model == "gemini-3.8-flash"
    assert page.meta["settings"] == "prompt-9f2c1a+think-low|max8192"
    assert page.verdict == "ok"
    assert page.lines == 5


def test_an_unparseable_record_is_skipped_loudly_not_guessed_from_the_txt(
        corpus, caplog):
    """An interrupted run is what leaves a half-written file behind."""
    run, _ = corpus
    d = run / "gemini-3.8-flash"
    (d / "broken.json").write_text("{not json", encoding="utf-8")
    (d / "broken.txt").write_text("a reading with no provenance", encoding="utf-8")
    keys = [p.key for p in rx.read_run(run)]
    assert "broken" not in keys


def test_a_model_that_is_not_in_the_run_names_what_is(corpus):
    run, _ = corpus
    with pytest.raises(FileNotFoundError) as exc:
        rx.read_run(run, "qwen3.5-vl")
    assert "gemini-3.8-flash" in str(exc.value)


def test_every_model_of_a_run_is_its_own_project(tmp_path):
    run = tmp_path / "two-readers"
    _reading(run, "gemini-3.8-flash", "k1", "ein Text")
    _reading(run, "qwen3.5-vl", "k1", "ein anderer Text")
    cache = tmp_path / "c"
    _image(cache / "k1.jpg")
    plan = rx.plan(rx.read_run(run), {"k1": cache / "k1.jpg"})
    assert plan.by_project == {"gemini-3.8-flash": 1, "qwen3.5-vl": 1}
    assert len(plan.entries) == 2


# ── the synthesised document ─────────────────────────────────────────────────

def test_the_region_carries_the_text_which_is_where_the_parser_looks(corpus):
    """`pagexml-hf`'s `_get_text_equiv` prefers the region's own TextEquiv and
    falls back to its lines, so a region with text and no lines is sufficient."""
    run, index = corpus
    plan = rx.plan(rx.read_run(run), index)
    e, = plan.entries
    xml = rx.page_xml(e.page, image=e.image, size=e.size)
    assert "Theuerster Freund!" in xml
    assert xml.count("<TextRegion") == 1
    assert "<TextEquiv><Unicode>" in xml


def test_there_are_no_textlines_and_that_is_the_decision(corpus):
    """Invented strips would be indistinguishable downstream from measured ones."""
    run, index = corpus
    e, = rx.plan(rx.read_run(run), index).entries
    xml = rx.page_xml(e.page, image=e.image, size=e.size)
    assert "<TextLine" not in xml
    assert "<Baseline" not in xml


def test_the_only_polygon_is_the_page_itself(corpus):
    run, index = corpus
    e, = rx.plan(rx.read_run(run), index).entries
    xml = rx.page_xml(e.page, image=e.image, size=e.size)
    assert xml.count("points=") == 1
    assert 'points="0,0 1200,0 1200,1600 0,1600"' in xml


def test_the_geometry_it_declares_is_the_image_not_a_claim(corpus):
    """The one size assertion this export can make, and it makes it from the file."""
    run, index = corpus
    e, = rx.plan(rx.read_run(run), index).entries
    xml = rx.page_xml(e.page, image=e.image, size=e.size)
    assert hf.xml_size(xml) == (1200, 1600)
    assert hf.xml_size(xml) == hf.image_size(e.image)


def test_the_document_parses_and_the_namespace_is_the_conventional_one(corpus):
    run, index = corpus
    e, = rx.plan(rx.read_run(run), index).entries
    xml = rx.page_xml(e.page, image=e.image, size=e.size)
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml)
    assert root.tag == f"{{{PAGE_NS}}}PcGts"
    region = root.find(f".//{{{PAGE_NS}}}TextRegion")
    unicode_el = region.find(f"{{{PAGE_NS}}}TextEquiv/{{{PAGE_NS}}}Unicode")
    assert unicode_el.text.strip().startswith("Meersburg")


def test_the_image_the_document_names_is_the_one_beside_it(tmp_path, corpus):
    """`pagexml-hf` pairs by stem, but imageFilename is what a human reads."""
    run, index = corpus
    plan = rx.plan(rx.read_run(run), index)
    written = rx.build(plan, tmp_path / "tree")
    project = Path(written["out_dir"]) / "gemini-3.8-flash"
    xml = next((project / "page").glob("*.xml")).read_text(encoding="utf-8")
    name = re.search(r'imageFilename="([^"]+)"', xml).group(1)
    assert (project / name).is_file()


# ── the provenance comment ───────────────────────────────────────────────────

def test_the_document_says_in_itself_that_it_is_machine_output(corpus):
    """A tree is images and XML; nothing a note beside them would reach."""
    run, index = corpus
    e, = rx.plan(rx.read_run(run), index).entries
    xml = rx.page_xml(e.page, image=e.image, size=e.size)
    head = xml.split("<PcGts", 1)[0]
    assert "MACHINE READING, NOT GROUND TRUTH" in head
    assert "uncorrected" in head
    for said in ("gemini-3.8-flash", "gemini-300-final", "prompt-9f2c1a",
                 "2026-10-12T09:14:33+00:00", "1.4.2"):
        assert said in head, said


def test_a_truncated_reading_says_so_in_its_own_comment(corpus):
    """It is exported only on request, and then it is not quietly ordinary."""
    run, index = corpus
    pages = rx.read_run(run)
    pages[0].meta["truncated"] = True
    plan = rx.plan(pages, index, skip_truncated=False)
    e, = plan.entries
    assert "TRUNCATED" in rx.page_xml(e.page, image=e.image, size=e.size)


def test_a_double_hyphen_cannot_break_the_comment(corpus):
    """XML comments cannot contain `--`, and a model name is somebody else's
    string. A document that does not parse takes its page out of the dataset."""
    run, index = corpus
    pages = rx.read_run(run)
    pages[0].meta["model"] = "weird--model"
    pages[0].model = "weird--model"
    pages[0].meta["settings"] = "prompt-a--b|max8192"
    e, = rx.plan(pages, index).entries
    xml = rx.page_xml(e.page, image=e.image, size=e.size)
    import xml.etree.ElementTree as ET

    ET.fromstring(xml)                       # the point of the test
    assert "- -" in xml.split("<PcGts", 1)[0]


def test_the_text_is_escaped_not_pasted(corpus):
    """A transcription containing `<` or `&` is ordinary; a broken document is not."""
    run, index = corpus
    pages = rx.read_run(run)
    pages[0].text = "Herr <von> Laßberg & Sohn\nzweite Zeile"
    e, = rx.plan(pages, index).entries
    xml = rx.page_xml(e.page, image=e.image, size=e.size)
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml)
    got = root.find(f".//{{{PAGE_NS}}}TextEquiv/{{{PAGE_NS}}}Unicode").text
    assert got == "Herr <von> Laßberg & Sohn\nzweite Zeile"


def test_control_characters_are_removed_before_they_break_the_document():
    """A chat API's output is not a file this project wrote."""
    assert rx.clean_text("a\x0cb\r\nc\x00") == "a b\nc".replace(" ", "")


def test_line_breaks_survive_but_the_kind_is_normalised():
    assert rx.clean_text("eins\r\nzwei\rdrei\n") == "eins\nzwei\ndrei"


# ── what is refused ──────────────────────────────────────────────────────────

def test_an_empty_reading_is_refused_and_named(tmp_path):
    """One of 19 'empty' pages turned out to be a written page with 24 lines
    (gateway cross-check, 2026-10-19). An empty answer is not a blank page."""
    run = tmp_path / "r"
    _reading(run, "m", "k1", "")
    cache = tmp_path / "c"
    _image(cache / "k1.jpg")
    plan = rx.plan(rx.read_run(run), {"k1": cache / "k1.jpg"})
    assert not plan.entries
    assert [r.category for r in plan.rejected] == ["empty"]
    assert plan.rejected[0].key == "k1"


@pytest.mark.parametrize("verdict", ["repetitive", "deliberation"])
def test_a_defect_of_the_reading_is_refused_by_default(tmp_path, verdict):
    run = tmp_path / "r"
    _reading(run, "m", "k1", "ein Text",
             quality={"verdict": verdict, "chars": 8, "lines": 1})
    cache = tmp_path / "c"
    _image(cache / "k1.jpg")
    index = {"k1": cache / "k1.jpg"}
    plan = rx.plan(rx.read_run(run), index)
    assert plan.by_category == {verdict: 1}
    assert plan.seen == {verdict: 1}


def test_the_refusal_is_a_default_and_not_a_law(tmp_path):
    """Somebody studying the deliberations wants exactly those pages."""
    run = tmp_path / "r"
    _reading(run, "m", "k1", "ich bin unsicher, aber",
             quality={"verdict": "deliberation", "chars": 21, "lines": 1})
    cache = tmp_path / "c"
    _image(cache / "k1.jpg")
    plan = rx.plan(rx.read_run(run), {"k1": cache / "k1.jpg"}, skip_verdicts=[])
    assert len(plan.entries) == 1


def test_a_truncated_page_is_its_own_category_not_a_verdict(tmp_path):
    """It comes back as an ordinary success: a 200, a text, a verdict of ok."""
    run = tmp_path / "r"
    _reading(run, "m", "k1", "ein Text, der mitten im", truncated=True)
    cache = tmp_path / "c"
    _image(cache / "k1.jpg")
    index = {"k1": cache / "k1.jpg"}
    assert rx.plan(rx.read_run(run), index).by_category == {"truncated": 1}
    assert len(rx.plan(rx.read_run(run), index, skip_truncated=False).entries) == 1


def test_a_page_with_no_image_is_refused_not_invented(tmp_path):
    """The cache evicts after two days (#487); the cold tier is where they are."""
    run = tmp_path / "r"
    _reading(run, "m", "k1", "ein Text")
    plan = rx.plan(rx.read_run(run), {})
    assert plan.by_category == {"no-image": 1}


def test_every_refusal_is_in_the_table_not_only_the_counts(tmp_path):
    run = tmp_path / "r"
    _reading(run, "m", "k1", "")
    _reading(run, "m", "k2", "ein Text")
    plan = rx.plan(rx.read_run(run), {})
    table = rx.rejected_table(plan).splitlines()
    assert table[0].split("\t") == ["key", "category", "reason"]
    assert len(table) == 1 + 2
    assert {r.split("\t")[1] for r in table[1:]} == {"empty", "no-image"}


def test_the_verdicts_of_everything_seen_are_counted_including_the_refused(
        tmp_path):
    """The shape of what was thrown away, beside what was kept."""
    run = tmp_path / "r"
    _reading(run, "m", "k1", PAGE_TEXT)
    _reading(run, "m", "k2", "")
    cache = tmp_path / "c"
    _image(cache / "k1.jpg")
    _image(cache / "k2.jpg")
    plan = rx.plan(rx.read_run(run),
                   {"k1": cache / "k1.jpg", "k2": cache / "k2.jpg"})
    assert plan.seen == {"ok": 1, "empty": 1}
    assert len(plan.entries) == 1


# ── the tree, and what it says about itself ──────────────────────────────────

def test_the_tree_is_the_layout_the_converter_pairs_by_stem(tmp_path, corpus):
    run, index = corpus
    written = rx.build(rx.plan(rx.read_run(run), index), tmp_path / "tree")
    project = Path(written["out_dir"]) / "gemini-3.8-flash"
    images = [f for f in project.iterdir() if f.suffix == ".jpg"]
    xmls = list((project / "page").glob("*.xml"))
    assert len(images) == len(xmls) == 1
    assert images[0].stem == xmls[0].stem


def test_the_tree_records_the_mode_because_the_upload_tool_takes_none(
        tmp_path, corpus):
    run, index = corpus
    rx.build(rx.plan(rx.read_run(run), index), tmp_path / "tree")
    said = hf.read_manifest(tmp_path / "tree")
    assert said["mode"] == "text"
    assert said["kind"] == "run"
    assert hf.tree_mode(tmp_path / "tree") == "text"


def test_the_upload_of_a_run_tree_is_in_text_mode(tmp_path, corpus):
    """The failure this prevents: synthesised documents in an `xml_content`
    column beside fourteen datasets of real Transkribus output."""
    run, index = corpus
    rx.build(rx.plan(rx.read_run(run), index), tmp_path / "tree")
    argv = hf.upload_argv(tmp_path / "tree", "dh-unibe/x")
    assert argv[argv.index("--mode") + 1] == "text"
    assert "--private" in argv


def test_a_tree_written_before_manifests_still_uploads_as_ground_truth(tmp_path):
    """Every tree written before 2026-10-22 is `raw_xml`, and the default says so."""
    (tmp_path / "old").mkdir()
    assert hf.tree_mode(tmp_path / "old") == "raw_xml"
    assert hf.read_manifest(tmp_path / "old") == {}


def test_the_ground_truth_tree_names_itself_too(tmp_path):
    (tmp_path / "gt").mkdir()
    hf.write_manifest(tmp_path / "gt", mode="raw_xml", kind="ground-truth")
    plan = hf.inspect_upload(tmp_path / "gt", "dh-unibe/x")
    assert plan.kind == "ground-truth"
    assert plan.mode == "raw_xml"


def test_the_plan_says_whether_a_human_has_read_this(tmp_path, corpus):
    """The one line of the upload plan that distinguishes the two datasets."""
    run, index = corpus
    rx.build(rx.plan(rx.read_run(run), index), tmp_path / "tree")
    text = hf.format_upload(hf.inspect_upload(tmp_path / "tree", "dh-unibe/x"))
    assert "MACHINE READING, uncorrected" in text
    assert "mode      : text" in text


def test_the_plan_a_person_reads_names_every_refusal(tmp_path):
    run = tmp_path / "r"
    _reading(run, "m", "k1", "ein Text")
    _reading(run, "m", "k2", "")
    cache = tmp_path / "c"
    _image(cache / "k1.jpg")
    said = rx.format_plan(rx.plan(rx.read_run(run), {"k1": cache / "k1.jpg"}))
    assert "pages     : 1" in said
    assert "left out  : 1" in said
    assert "k2" in said


def test_the_dataset_name_cannot_be_mistaken_for_the_corrected_one():
    """`image-text_lassberg-correspondence_xix` is 254 hand-corrected pages. A
    name is what survives into somebody else's notebook."""
    assert rx.DEFAULT_REPO_ID != hf.DEFAULT_REPO_ID
    assert "uncorrected" in rx.DEFAULT_REPO_ID
