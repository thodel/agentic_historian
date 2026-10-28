"""An external model on a published line test set (`external_lineset`, #747).

What has to hold for the number to sit in the trainer's table: the trainer's
CER definition and edit direction, an unanswered line counted as empty, a
sample spread over the set rather than its first shard, and no line paid for
twice under the same settings.
"""

import json
import sys
import types
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import external_lineset as el                 # noqa: E402
from mcp_atr import lineset                   # noqa: E402
from mcp_atr.jobs import JobError             # noqa: E402


def test_a_sample_is_spread_over_the_set():
    picked = el.chosen(2751, 50)
    assert len(picked) == 50 and min(picked) == 0 and max(picked) > 2600
    assert el.chosen(10, None) == set(range(10)) == el.chosen(10, 99)


def test_score_uses_the_trainers_definitions():
    s = el.score([("Bundesrath", "Bundesrat"), ("", "Bern")])
    assert s["chars"] == 13 and s["errors"] == 5 and s["cer"] == round(5 / 13, 4)
    assert s["deletions"] == 1, "a character the hypothesis added is a deletion (kraken's direction)"
    assert s["insertions"] == 4, "a reference character never written is an insertion"
    assert s["truncated_cer"] == round(4 / 13, 4), "clipped to the reference, the extra h is gone"


def test_an_unanswered_line_counts_as_empty():
    refs = {"a": "Bern", "b": "Zürich"}
    rep = el.report({"a": {"text": "Bern", "finish_reason": "stop"}}, refs)
    assert rep["all"]["cer"] == round(6 / 10, 4) and rep["answered_only"]["cer"] == 0.0
    assert rep["answered"] == 1 and rep["finish_reasons"] == {"stop": 1}


def _parquet(path: Path, n: int):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq
    table = pa.table({"image": [{"bytes": b"\xff\xd8", "path": None}] * n,
                      "text": [f"Zeile {i}" for i in range(n)],
                      "filename": [f"{i}_page_1_line_{i}_docId_9.jpg" for i in range(n)]})
    pq.write_table(table, path)


def test_a_run_reads_scores_and_resumes(tmp_path, monkeypatch):
    shard = tmp_path / "s.parquet"
    _parquet(shard, 6)
    monkeypatch.setattr(el, "fetch", lambda dataset, dest, project=None: [shard])
    calls = []

    def read(img, model):
        calls.append(img.name)
        n = int(img.name.split("_")[0])
        if n == 3:
            raise RuntimeError("503")
        return types.SimpleNamespace(text=f"Zeile {n}", truncated=False, prompt_tokens=7,
                                     completion_tokens=2, settings="prompt-x|max8192",
                                     service_version="m@prompt-x", finish_reason="stop")
    read.settings, read.close = "prompt-x|max8192", lambda: None
    fake = types.SimpleNamespace(
        openai_recogniser=lambda **kw: read,
        preflight=lambda m: types.SimpleNamespace(refused=False, line=f"  ok {m}"))
    monkeypatch.setitem(sys.modules, "external_atr", fake)
    monkeypatch.setattr(el.config, "ATR_DATASETS_DIR", None)
    out = tmp_path / "run"
    assert el.main(["--model", "gemini-4-argon", "--out", str(out), "--full", "--workers", "2"]) == 0
    rep = json.loads((out / "report_gemini-4-argon.json").read_text())
    assert rep["lines"] == 6 and rep["answered"] == 5
    assert rep["answered_only"]["cer"] == 0.0 and rep["all"]["cer"] > 0
    assert (out / "errors_gemini-4-argon.jsonl").exists()
    calls.clear()
    el.main(["--model", "gemini-4-argon", "--out", str(out), "--full"])
    assert calls == ["3_page_1_line_3_docId_9.jpg"], "only the failed line is read again"


def test_nothing_runs_without_a_cut(tmp_path):
    with pytest.raises(SystemExit):
        el.main(["--model", "m", "--out", str(tmp_path)])


# ── the MCP side ─────────────────────────────────────────────────────────────

def test_lineset_argv(monkeypatch, tmp_path):
    monkeypatch.setattr(lineset.config, "VLM_TEST_ROOT", tmp_path)
    argv = lineset.lineset_argv("fedmin-argon", "gemini-4-argon", sample=50, reasoning="low")
    assert argv[1] == str(lineset.SCRIPT) and argv[argv.index("--sample") + 1] == "50"
    assert argv[argv.index("--out") + 1] == str(tmp_path / "fedmin-argon")
    assert "--full" in lineset.lineset_argv("r", "m", full=True)
    for kw in ({}, {"sample": 5, "full": True}, {"sample": 5, "dataset": "../x"},
               {"sample": 5, "prompt": "/etc/passwd"}, {"sample": 5, "reasoning": "max"},
               {"sample": 5, "workers": 99}):
        with pytest.raises(JobError):
            lineset.lineset_argv("r", "m", **kw)
    with pytest.raises(JobError):
        lineset.lineset_argv("r", "x;rm", sample=5)


def test_the_line_prompt_ships():
    import external_atr
    text, _ = external_atr.load_prompt("federal_minutes_line.md")
    assert "exactly as written" in text and "one line" in text


# ── a page set: the reference is the PAGE XML (St. Gallen Missiven) ──────────

_PAGE = """<?xml version="1.0" encoding="UTF-8"?>
<PcGts xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"><Page imageFilename="p.jpg" imageWidth="20" imageHeight="10">
<TextRegion id="r1"><TextLine id="l1"><Coords points="0,0 9,0 9,4 0,4"/>
<Word id="w1"><TextEquiv><Unicode>Den</Unicode></TextEquiv></Word>
<TextEquiv><Unicode>Den fromen wysen</Unicode></TextEquiv></TextLine>
<TextLine id="l2"><Coords points="0,5 9,5 9,9 0,9"/><TextEquiv><Unicode>burgermaister</Unicode></TextEquiv></TextLine>
</TextRegion></Page></PcGts>"""


def test_a_page_reference_is_its_lines_own_text():
    assert el.page_text(_PAGE) == "Den fromen wysen\nburgermaister", "the line's TextEquiv, never word 1"


def test_a_large_scan_is_shrunk_and_a_small_one_kept():
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (5000, 2000)).save(buf, "JPEG")
    with Image.open(io.BytesIO(el.shrink(buf.getvalue()))) as im:
        assert max(im.size) == el.PAGE_MAX_SIDE
    small = io.BytesIO()
    Image.new("RGB", (100, 50)).save(small, "JPEG")
    assert el.shrink(small.getvalue()) == small.getvalue()


def test_pages_report_a_median_per_page():
    refs = {"a": "Den fromen", "b": "burgermaister", "c": "Item"}
    rep = el.report({"a": {"text": "Den fromen"}, "b": {"text": "burgermeister"},
                     "c": {"text": "Itam"}}, refs)
    s = rep["per_item_cer"]
    assert s["n"] == 3 and s["best"] == 0.0 and s["worst"] == 0.25
    assert s["median"] == round(1 / 13, 4)


def test_a_page_run_reads_xml_content(tmp_path, monkeypatch):
    pa = pytest.importorskip("pyarrow")
    import io

    import pyarrow.parquet as pq
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 20)).save(buf, "JPEG")
    shard = tmp_path / "data" / "train" / "sg-missiven" / "0000.parquet"
    shard.parent.mkdir(parents=True)
    pq.write_table(pa.table({"image": [{"bytes": buf.getvalue(), "path": None}] * 2,
                             "xml_content": [_PAGE, _PAGE],
                             "filename": ["p1.jpg", "p2.jpg"]}), shard)
    monkeypatch.setattr(el, "fetch", lambda dataset, dest, project=None: [shard])
    monkeypatch.setattr(el.config, "ATR_DATASETS_DIR", None)
    read = lambda img, model: types.SimpleNamespace(
        text="Den fromen wysen\nburgermaister", truncated=False, prompt_tokens=9,
        completion_tokens=4, settings="s", service_version="v", finish_reason="stop")
    read.settings, read.close = "s", lambda: None
    monkeypatch.setitem(sys.modules, "external_atr", types.SimpleNamespace(
        openai_recogniser=lambda **kw: read,
        preflight=lambda m: types.SimpleNamespace(refused=False, line="ok")))
    out = tmp_path / "run"
    assert el.main(["--model", "m", "--out", str(out), "--full", "--pages",
                    "--project", "sg-missiven"]) == 0
    rep = json.loads((out / "report_m.json").read_text())
    assert rep["all"]["cer"] == 0.0 and rep["per_item_cer"]["n"] == 2


def test_lineset_argv_page_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(lineset.config, "VLM_TEST_ROOT", tmp_path)
    argv = lineset.lineset_argv("sg", "gemini-4-argon", sample=20, pages=True,
                                dataset="dh-unibe/image-text_sg-missiven", project="sg-missiven",
                                prompt="sg_missiven_page.md")
    assert "--pages" in argv and argv[argv.index("--project") + 1] == "sg-missiven"
    with pytest.raises(JobError):
        lineset.lineset_argv("sg", "m", sample=2, project="../x")


# ── notation apart from reading (28.10., Missiven: u/v, virgules, superscripts) ─

def test_convention_normalized_cer_separates_notation():
    import textdiff
    assert textdiff.normalize_convention("Vnd ů ä ß ſ, Jr/ HERR.") == "und u a s s ir herr"
    refs = {"a": "und unser rat, burgermaister"}
    rep = el.report({"a": {"text": "vnd vnser rat / burgermaiſter"}}, refs)
    assert rep["all"]["cer"] > 0.1, "the headline keeps the notation"
    assert rep["convention_normalized"]["all"]["cer"] == 0.0
    assert rep["convention_normalized"]["per_item_cer"]["median"] == 0.0


def test_the_worst_items_are_named():
    refs = {"a": "Item", "b": "kurz", "c": "lang genug"}
    rep = el.report({"a": {"text": "Item"}, "b": {"text": "ein ganz anderer text"},
                     "c": {"text": "lang genug"}}, refs)
    assert rep["worst"][0]["key"] == "b" and rep["worst"][0]["cer"] > 1


def test_the_v2_prompt_names_the_gt_conventions():
    import external_atr
    text, digest = external_atr.load_prompt("sg_missiven_page_v2.md")
    assert "by sound" in text and "virgule" in text
    assert digest != external_atr.load_prompt("sg_missiven_page.md")[1]
