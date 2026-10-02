"""Reading a named list of pages (`--keys-from`, `score-gt --keys-out`).

Ground truth arrived for sixteen pages of a 6742-page corpus. To find out which
of eight candidate models reads this hand best, each has to read *those sixteen*:
over the whole corpus, at the 19.3 s a page the first run measured, one model is
some 36 GPU-hours and eight of them is a fortnight of a card that is also serving
everything else.

So scoring writes the page keys it matched and the runner reads them back. The
pair has one rule worth testing on both sides: a key that names nothing is said
out loud. The list is produced somewhere else — a scoring run over a different
corpus, a hand-edited file — and a runner that silently dropped three of sixteen
pages would be measured as if it had read them.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import atr_batch as batch                   # noqa: E402

KEYS = ["Briefe UB Freiburg__lassberg-letter-0083__001",
        "Briefe UB Freiburg__lassberg-letter-0083__002",
        "Briefe UB Freiburg__lassberg-letter-0084__001"]


def _pages(keys):
    """PageRefs with these keys, built the way the runner's own discovery does."""
    return batch.pages_from_paths([k.replace("__", "/") + ".jpg" for k in keys])


# ── reading the file ─────────────────────────────────────────────────────────

def test_one_key_per_line(tmp_path):
    f = tmp_path / "keys.txt"
    f.write_text("\n".join(KEYS) + "\n", encoding="utf-8")
    assert batch.read_keys(f) == KEYS


def test_comments_and_blank_lines_are_dropped(tmp_path):
    """`--keys-out` writes a header saying how many of how many, and that header
    must not be read back as a page."""
    f = tmp_path / "keys.txt"
    f.write_text(f"# page keys of the ground-truth pages\n#   3 of 4 scored\n\n"
                 f"{KEYS[0]}\n\n{KEYS[1]}  # the one with the zeros\n",
                 encoding="utf-8")
    assert batch.read_keys(f) == KEYS[:2]


def test_a_key_listed_twice_is_read_once(tmp_path):
    """Two ground-truth files can match the same page. Reading it twice would
    double its weight in every average computed afterwards."""
    f = tmp_path / "keys.txt"
    f.write_text(f"{KEYS[0]}\n{KEYS[1]}\n{KEYS[0]}\n", encoding="utf-8")
    assert batch.read_keys(f) == KEYS[:2]


def test_a_file_of_only_comments_is_an_error(tmp_path):
    f = tmp_path / "keys.txt"
    f.write_text("# nothing here\n\n", encoding="utf-8")
    with pytest.raises(ValueError) as err:
        batch.read_keys(f)
    assert "no page keys" in str(err.value)


# ── selecting the pages ──────────────────────────────────────────────────────

def test_only_the_named_pages_are_kept():
    pages = _pages(KEYS + ["Marbach__letter-01__001", "Zürich__letter-09__003"])
    chosen, missing = batch.select_keys(list(pages), KEYS[:2])
    assert [p.key for p in chosen] == KEYS[:2]
    assert missing == []


def test_the_corpus_order_is_kept_not_the_file_order():
    """Processing order is corpus order everywhere else in this runner, because
    that is what makes a resumed run walk the sequence the first one did."""
    pages = _pages(KEYS)
    chosen, _ = batch.select_keys(list(pages), [KEYS[2], KEYS[0], KEYS[1]])
    assert [p.key for p in chosen] == KEYS


def test_a_key_that_names_no_page_is_reported():
    """The whole reason this returns a pair."""
    pages = _pages(KEYS[:1])
    chosen, missing = batch.select_keys(list(pages), KEYS)
    assert [p.key for p in chosen] == KEYS[:1]
    assert missing == KEYS[1:]


def test_every_key_missing_leaves_nothing_chosen():
    pages = _pages(["Marbach__letter-01__001"])
    chosen, missing = batch.select_keys(list(pages), KEYS)
    assert chosen == [] and missing == KEYS


def test_a_key_listed_twice_selects_one_page():
    pages = _pages(KEYS)
    chosen, missing = batch.select_keys(list(pages), [KEYS[0], KEYS[0]])
    assert [p.key for p in chosen] == KEYS[:1]
    assert missing == []


# ── through the CLI, both ends ───────────────────────────────────────────────

def _cli():
    """The CLI module, loaded by path — `import __main__` under pytest is
    pytest's own entry point (see test_ah_472_batch_preflight)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("ah_cli_keys", PKG / "__main__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source(tmp_path, keys):
    """A directory of page images with these keys."""
    root = tmp_path / "pages"
    for key in keys:
        p = root / (key.replace("__", "/") + ".jpg")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\xff\xd8\xff")       # not read: --dry-run never opens one
    return root


def _args(**over):
    from types import SimpleNamespace
    args = SimpleNamespace(
        source="", models="trocr-kurrent", run="r", out_root=None, limit=None,
        sample=None, seed=1, concurrency=1, retries=0, cache_dir=None,
        no_listing_cache=False, dry_run=True, no_preflight=True, keys_from=None)
    for k, v in over.items():
        setattr(args, k, v)
    return args


def test_a_dry_run_plans_only_the_listed_pages(tmp_path, capsys):
    root = _source(tmp_path, KEYS + ["Marbach__letter-01__001"])
    keyfile = tmp_path / "keys.txt"
    keyfile.write_text("\n".join(KEYS[:2]) + "\n", encoding="utf-8")

    code = _cli().atr_batch(_args(source=str(root), keys_from=str(keyfile),
                                 out_root=str(tmp_path / "out")))

    out = capsys.readouterr().out
    assert code == 0
    assert "pages     : 2" in out
    assert "Marbach" not in out


def test_a_missing_key_is_named_on_stderr(tmp_path, capsys):
    """Said out loud, because a run that quietly read 13 of 16 ground-truth pages
    would be reported as if it had read all of them."""
    root = _source(tmp_path, KEYS[:1])
    keyfile = tmp_path / "keys.txt"
    keyfile.write_text("\n".join(KEYS) + "\n", encoding="utf-8")

    code = _cli().atr_batch(_args(source=str(root), keys_from=str(keyfile),
                                 out_root=str(tmp_path / "out")))

    captured = capsys.readouterr()
    assert code == 0                                    # one page is still a run
    assert "2 of 3 key(s) are not in this source" in captured.err
    assert KEYS[1] in captured.err


def test_no_key_matching_anything_is_an_error_not_an_empty_run(tmp_path, capsys):
    root = _source(tmp_path, ["Marbach__letter-01__001"])
    keyfile = tmp_path / "keys.txt"
    keyfile.write_text("\n".join(KEYS) + "\n", encoding="utf-8")

    code = _cli().atr_batch(_args(source=str(root), keys_from=str(keyfile),
                                 out_root=str(tmp_path / "out")))

    assert code == 1
    assert "none of the 3 key(s)" in capsys.readouterr().err


def test_a_key_file_that_is_not_there_is_an_argument_error(tmp_path, capsys):
    root = _source(tmp_path, KEYS)
    code = _cli().atr_batch(_args(source=str(root), keys_from=str(tmp_path / "nope"),
                                 out_root=str(tmp_path / "out")))
    assert code == 2


# ── the other end: score-gt writes the file atr-batch reads ──────────────────

NS = 'xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"'

LETTER_LINES = [
    "Eppishausen am 21 Januar 1831.",
    "Hochgeschäzter Herr!",
    "Da ich das vergnügen nicht haben soll, Sie in meiner Waldklause zu",
    "bewirten; so bliebt mir freilich nichts anders übrig, als Inen die Siegel",
    "die erlaubnis die urkunden länger zu behalten, habe ich zwar selbst anti¬",
]
DECOY = ("Völlig anderer Text über ganz andere Dinge, ohne jede Beziehung zu "
         "diesem Brief, nur als Ablenkung vorhanden und lang genug dafür. ") * 3


def _gt(tmp_path, name, page_id="61849396"):
    body = "".join(f"<TextLine id='tl_{i}'><TextEquiv><Unicode>{t}</Unicode>"
                   f"</TextEquiv></TextLine>" for i, t in enumerate(LETTER_LINES))
    path = tmp_path / name
    path.write_text(
        (f"<?xml version='1.0' encoding='UTF-8'?><PcGts {NS}>"
         f"<Metadata><TranskribusMetadata docId='1682295' pageId='{page_id}' "
         f"pageNr='1' status='FINAL'/></Metadata>"
         f"<Page imageFilename='{page_id}.tif'><TextRegion id='tr'>{body}"
         f"<TextEquiv><Unicode>{chr(10).join(LETTER_LINES)}</Unicode></TextEquiv>"
         f"</TextRegion></Page></PcGts>").replace("'", '"'), encoding="utf-8")
    return path


def _run_dir(tmp_path, pages):
    d = tmp_path / "run" / "qwen3.5-4b-german-xix-v2"
    d.mkdir(parents=True, exist_ok=True)
    for key, text in pages.items():
        (d / f"{key}.txt").write_text(text, encoding="utf-8")
    return tmp_path / "run"


def _score_args(tmp_path, **over):
    from types import SimpleNamespace
    args = SimpleNamespace(gt=[], run_dir=[], out=None, keys_out=None)
    for k, v in over.items():
        setattr(args, k, v)
    return args


def test_score_gt_writes_the_matched_keys(tmp_path, capsys):
    """The handover: these are the pages the next eight models have to read."""
    gt = _gt(tmp_path, "doc1682295_page1_ts1.xml")
    run = _run_dir(tmp_path, {KEYS[0]: "\n".join(LETTER_LINES), KEYS[1]: DECOY})
    keyfile = tmp_path / "gt-keys.txt"

    code = _cli().score_gt(_score_args(
        tmp_path, gt=[str(gt)], run_dir=[str(run)], keys_out=str(keyfile)))

    assert code == 0
    assert batch.read_keys(keyfile) == [KEYS[0]]
    assert "keys:" in capsys.readouterr().out


def test_the_written_header_survives_being_read_back(tmp_path):
    """The two commands are a pair, so the file one writes is parsed by the other
    in the same test rather than asserted by eye."""
    gt = _gt(tmp_path, "doc1682295_page1_ts1.xml")
    run = _run_dir(tmp_path, {KEYS[0]: "\n".join(LETTER_LINES), KEYS[1]: DECOY})
    keyfile = tmp_path / "gt-keys.txt"
    _cli().score_gt(_score_args(tmp_path, gt=[str(gt)], run_dir=[str(run)],
                                keys_out=str(keyfile)))

    assert keyfile.read_text(encoding="utf-8").startswith("# page keys")
    pages = _pages(KEYS)
    chosen, missing = batch.select_keys(list(pages), batch.read_keys(keyfile))
    assert [p.key for p in chosen] == [KEYS[0]] and missing == []


def test_a_doubtful_match_contributes_no_key(tmp_path, capsys):
    """A match that is not confident names a page the run does not have. Feeding
    it to another model would read the wrong page and score it as this one."""
    gt = _gt(tmp_path, "doc1682295_page1_ts1.xml")
    # Two near-identical decoys and no real reading: nothing stands out.
    run = _run_dir(tmp_path, {KEYS[0]: DECOY, KEYS[1]: DECOY.replace("Dinge", "Sachen")})
    keyfile = tmp_path / "gt-keys.txt"

    code = _cli().score_gt(_score_args(
        tmp_path, gt=[str(gt)], run_dir=[str(run)], keys_out=str(keyfile)))

    assert code == 1
    assert not keyfile.exists()
    assert "no page was matched confidently" in capsys.readouterr().err
