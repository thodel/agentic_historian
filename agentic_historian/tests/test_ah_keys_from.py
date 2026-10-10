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


def test_the_limit_cuts_what_the_filter_left_not_the_corpus(tmp_path, capsys):
    """#531, the one that cost a smoke run.

    `--keys-from` filters and `--limit` cuts, and the order decides whether the
    command works at all. Cutting first took the alphabetically first three pages
    of a 6742-page corpus, matched them against 276 ground-truth keys, found none,
    and reported "none of the 276 key(s) name a page under dav:digitalisate" — a
    message that reads like a key-spelling problem and is not one.
    """
    others = [f"Aarau__letter-{i:02d}__001" for i in range(20)]   # sort first
    root = _source(tmp_path, others + KEYS)
    keyfile = tmp_path / "keys.txt"
    keyfile.write_text("\n".join(KEYS) + "\n", encoding="utf-8")

    code = _cli().atr_batch(_args(source=str(root), keys_from=str(keyfile), limit=2,
                                 out_root=str(tmp_path / "out")))

    out = capsys.readouterr().out
    assert code == 0
    assert "pages     : 2" in out
    assert KEYS[0] in out and KEYS[1] in out
    assert "Aarau" not in out


def test_a_limit_above_the_key_count_keeps_every_named_page(tmp_path, capsys):
    root = _source(tmp_path, KEYS + ["Marbach__letter-01__001"])
    keyfile = tmp_path / "keys.txt"
    keyfile.write_text("\n".join(KEYS) + "\n", encoding="utf-8")

    code = _cli().atr_batch(_args(source=str(root), keys_from=str(keyfile), limit=10,
                                 out_root=str(tmp_path / "out")))

    assert code == 0
    assert "pages     : 3" in capsys.readouterr().out


def test_a_sample_draws_from_the_named_pages(tmp_path, capsys):
    """Same ordering rule for `--sample`: drawn from the 276 wanted pages, not from
    the corpus and then intersected (which would usually draw none)."""
    others = [f"Aarau__letter-{i:02d}__001" for i in range(50)]
    root = _source(tmp_path, others + KEYS)
    keyfile = tmp_path / "keys.txt"
    keyfile.write_text("\n".join(KEYS) + "\n", encoding="utf-8")

    code = _cli().atr_batch(_args(source=str(root), keys_from=str(keyfile), sample=2,
                                 out_root=str(tmp_path / "out")))

    out = capsys.readouterr().out
    assert code == 0
    assert "pages     : 2" in out
    assert "Aarau" not in out


def test_limit_and_sample_together_are_still_refused_with_a_key_list(tmp_path, capsys):
    root = _source(tmp_path, KEYS)
    keyfile = tmp_path / "keys.txt"
    keyfile.write_text("\n".join(KEYS) + "\n", encoding="utf-8")

    code = _cli().atr_batch(_args(source=str(root), keys_from=str(keyfile),
                                 limit=1, sample=1, out_root=str(tmp_path / "out")))

    assert code == 2
    assert "alternatives" in capsys.readouterr().err


# ── which pages the source does not have ─────────────────────────────────────
#
# 2026-10-04: the share had stopped holding a whole holding, and 35 of the 276
# pages with hand-corrected text went with it. The run said "35 of 276" and named
# five. Which 35 is the list to send to the people who keep the corpus, and it
# could not be got out of the runner at all.

def test_every_missing_key_is_named_not_five(tmp_path, capsys):
    present = [f"Aarau__letter-{i:02d}__001" for i in range(3)]
    absent = [f"Briefe UB Freiburg__lassberg-letter-{i:04d}__001" for i in range(12)]
    root = _source(tmp_path, present)
    keyfile = tmp_path / "keys.txt"
    keyfile.write_text("\n".join(present + absent) + "\n", encoding="utf-8")

    code = _cli().atr_batch(_args(source=str(root), keys_from=str(keyfile),
                                 out_root=str(tmp_path / "out")))

    err = capsys.readouterr().err
    assert code == 0
    assert "12 of 15 key(s) are not in this source" in err
    for key in absent:
        assert key in err, "a key left out of the warning is a page nobody knows about"


def test_a_cap_keeps_an_unrelated_key_list_from_filling_the_terminal(tmp_path, capsys):
    cli = _cli()
    present = ["Aarau__letter-00__001"]
    absent = [f"Elsewhere__letter-{i:04d}__001"
              for i in range(cli.MISSING_KEYS_SHOWN + 5)]
    root = _source(tmp_path, present)
    keyfile = tmp_path / "keys.txt"
    keyfile.write_text("\n".join(present + absent) + "\n", encoding="utf-8")

    cli.atr_batch(_args(source=str(root), keys_from=str(keyfile),
                        out_root=str(tmp_path / "out")))

    err = capsys.readouterr().err
    assert "5 more (use --missing-out" in err


def test_missing_out_writes_the_list_to_send_on(tmp_path, capsys):
    present = ["Aarau__letter-00__001"]
    absent = ["Briefe UB Freiburg__lassberg-letter-0077__001",
              "Briefe UB Freiburg__lassberg-letter-0077__002"]
    root = _source(tmp_path, present)
    keyfile = tmp_path / "keys.txt"
    keyfile.write_text("\n".join(present + absent) + "\n", encoding="utf-8")
    dest = tmp_path / "missing.txt"

    code = _cli().atr_batch(_args(source=str(root), keys_from=str(keyfile),
                                 missing_out=str(dest),
                                 out_root=str(tmp_path / "out")))

    assert code == 0
    written = dest.read_text(encoding="utf-8")
    body = [ln for ln in written.splitlines() if not ln.startswith("#")]
    assert body == absent
    assert "the ground truth for them exists, the image does not" in written
    assert str(keyfile) in written          # where the keys came from
    assert "missing keys:" in capsys.readouterr().err


def test_nothing_missing_still_writes_the_check(tmp_path):
    """A dated file saying "all 276 resolved" is evidence. A file that is simply
    absent could also mean nobody looked, which is the thing this answers."""
    present = ["Aarau__letter-00__001"]
    root = _source(tmp_path, present)
    keyfile = tmp_path / "keys.txt"
    keyfile.write_text(present[0] + "\n", encoding="utf-8")
    dest = tmp_path / "missing.txt"

    _cli().atr_batch(_args(source=str(root), keys_from=str(keyfile),
                           missing_out=str(dest), out_root=str(tmp_path / "out")))

    written = dest.read_text(encoding="utf-8")
    assert "all 1 key(s) resolved; nothing is missing" in written
    assert [ln for ln in written.splitlines() if not ln.startswith("#")] == []


def test_the_flag_is_on_the_real_parser():
    parser = _cli().build_parser()
    args = parser.parse_args(["atr-batch", "--source", "/x", "--models", "m",
                              "--run", "r", "--keys-from", "/k",
                              "--missing-out", "/m"])
    assert args.missing_out == "/m"


def test_the_mcp_tool_writes_it_under_the_ground_truth_root(monkeypatch, tmp_path):
    """A name, not a path — the same rule as `keys_from`, so a tool that chooses
    pages cannot become one that chooses files."""
    from mcp_atr import jobs

    monkeypatch.setattr(jobs.config, "GT_ROOT", tmp_path)
    dest = jobs.keys_file_to_write("missing.txt")

    assert dest == tmp_path / "missing.txt"
    with pytest.raises(jobs.JobError):
        jobs.keys_file_to_write("../../etc/passwd")

    argv = jobs.batch_argv(Path("dav:digitalisate"), ["trocr-kurrent"], "r",
                           keys_from=tmp_path / "gt-keys.txt", missing_out=dest,
                           dry_run=True)
    assert argv[argv.index("--missing-out") + 1] == str(dest)


# ── asking what the share holds *now* ────────────────────────────────────────

def test_the_mcp_tool_can_ask_for_a_fresh_listing():
    """The listing is cached for 48 h because a full walk is twenty minutes. On
    2026-10-04 a whole holding had been removed from the share, every fetch for
    those pages answered 404, and the cached list — written the day before —
    still promised them. Without this flag the stale list was the only list an
    MCP caller could reach.
    """
    from mcp_atr import jobs

    argv = jobs.batch_argv(Path("dav:digitalisate"), ["trocr-kurrent"], "r",
                           keys_from=Path("/gt/gt-keys.txt"),
                           no_listing_cache=True, dry_run=True)

    assert "--no-listing-cache" in argv
    assert argv.index("--no-listing-cache") < argv.index("--dry-run")
    assert "--keys-from" in argv


def test_the_fresh_listing_is_off_by_default():
    """A twenty-minute walk is not something a caller should get by accident."""
    from mcp_atr import jobs

    argv = jobs.batch_argv(Path("dav:digitalisate"), ["trocr-kurrent"], "r")

    assert "--no-listing-cache" not in argv


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


# ── the arguments a human can actually type ──────────────────────────────────
#
# Pasted into tei's shell on 2026-10-02:
#
#     --gt "$GT_ROOT" --run-dir "$VLM_TEST_ROOT/atr_corpus_qwen35_line"
#     → Error: not a run directory: /atr_corpus_qwen35_line
#
# Both variables live in `.env`, which dotenv loads for Python and an interactive
# bash has never seen. The run name collapsed to an absolute path and the empty
# `--gt` was worse: `Path("")` is `Path(".")`, so it walked the whole checkout
# and offered `setuptools/command/launcher manifest.xml` as ground truth, then
# failed later on the run directory. Same trap as #503.

def test_an_empty_gt_path_is_refused_not_walked(tmp_path, monkeypatch):
    """The #503 trap: an unset variable must not become a walk of the checkout."""
    import gt_score as gs
    monkeypatch.chdir(tmp_path)
    (tmp_path / "junk.xml").write_text("<x/>", encoding="utf-8")
    with pytest.raises(gs.GroundTruthError) as err:
        gs.expand_gt_paths([""])
    assert "GT_ROOT" in str(err.value)


def test_a_blank_gt_path_is_refused_too(tmp_path, monkeypatch):
    import gt_score as gs
    monkeypatch.chdir(tmp_path)
    with pytest.raises(gs.GroundTruthError):
        gs.expand_gt_paths(["   "])


def test_a_bare_run_name_resolves_under_the_run_root(tmp_path, monkeypatch):
    import config
    root = tmp_path / "vlm_test"
    (root / "atr_corpus_qwen35_line" / "m").mkdir(parents=True)
    monkeypatch.setattr(config, "VLM_TEST_ROOT", root)
    assert _cli().resolve_run_dir("atr_corpus_qwen35_line") == \
        root / "atr_corpus_qwen35_line"


def test_a_collapsed_variable_still_resolves(tmp_path, monkeypatch):
    """`$VLM_TEST_ROOT/atr_corpus_qwen35_line` with the variable unset. The
    leading slash is what bash left behind, not something the user typed."""
    import config
    root = tmp_path / "vlm_test"
    (root / "atr_corpus_qwen35_line" / "m").mkdir(parents=True)
    monkeypatch.setattr(config, "VLM_TEST_ROOT", root)
    assert _cli().resolve_run_dir("/atr_corpus_qwen35_line") == \
        root / "atr_corpus_qwen35_line"


def test_a_real_path_still_wins(tmp_path, monkeypatch):
    """Nothing that worked before changes: an existing directory is used as given,
    even if a run of the same name exists under the root."""
    import config
    root = tmp_path / "vlm_test"
    (root / "run" / "m").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere" / "run"
    (elsewhere / "m").mkdir(parents=True)
    monkeypatch.setattr(config, "VLM_TEST_ROOT", root)
    assert _cli().resolve_run_dir(str(elsewhere)) == elsewhere


def test_an_unknown_name_is_reported_as_given(tmp_path, monkeypatch):
    """The error has to name what the user typed, not a path they never wrote."""
    import config
    monkeypatch.setattr(config, "VLM_TEST_ROOT", tmp_path / "vlm_test")
    assert _cli().resolve_run_dir("nope") == Path("nope")


def test_score_gt_without_gt_uses_the_configured_root(tmp_path, monkeypatch, capsys):
    """`--gt` can be left out entirely, which is the point."""
    import config
    gt_root = tmp_path / "gt"
    gt_root.mkdir()
    _gt(gt_root, "doc1682295_page1_ts1.xml")
    _run_dir(tmp_path, {KEYS[0]: "\n".join(LETTER_LINES), KEYS[1]: DECOY})
    monkeypatch.setattr(config, "GT_ROOT", gt_root)
    monkeypatch.setattr(config, "VLM_TEST_ROOT", tmp_path)

    code = _cli().score_gt(_score_args(tmp_path, gt=None, run_dir=["run"]))

    assert code == 0
    assert "1 ground-truth page(s)" in capsys.readouterr().out


# ── the pages nobody has read yet ────────────────────────────────────────────
#
# "300 not yet transcribed documents" is a subtraction, not a judgement: the
# corpus minus every key that has ground truth (`score-gt --keys-out`) minus
# every key an earlier run already read. Both lists exist already.
#
# The ordering lesson of #531 applies from the other side. Cutting to 300 first
# and then removing what is already read leaves however many of those 300 happen
# to be unread — a number nobody chose.

def test_the_pages_without_these_keys_come_back():
    pages = _pages(["a", "b", "c", "d"])

    kept, absent = batch.drop_keys(pages, ["b", "d"])

    assert [p.key for p in kept] == ["a", "c"]
    assert absent == []


def test_an_excluded_key_that_names_no_page_is_reported():
    """The same honesty as select_keys. 35 unmatched keys meant the share had
    lost pages we have ground truth for (#535); here the shape means the
    opposite — a reading of a page the source no longer offers."""
    kept, absent = batch.drop_keys(_pages(["a", "b"]), ["b", "gone"])

    assert [p.key for p in kept] == ["a"]
    assert absent == ["gone"]


def test_excluding_nothing_keeps_everything():
    kept, absent = batch.drop_keys(_pages(["a", "b"]), ["x", "y"])

    assert [p.key for p in kept] == ["a", "b"]
    assert absent == ["x", "y"]


def test_a_repeated_exclusion_key_is_counted_once():
    kept, absent = batch.drop_keys(_pages(["a", "b"]), ["b", "b"])

    assert [p.key for p in kept] == ["a"]
    assert absent == []


def test_the_result_is_in_corpus_order():
    """A resumed run has to walk the same sequence it did the first time."""
    kept, _ = batch.drop_keys(_pages(["d", "b", "c", "a"]), ["c"])

    assert [p.key for p in kept] == ["a", "b", "d"]


def test_the_cut_comes_after_the_exclusion():
    """The ordering of #531, from the other side: 300 first and then removing
    what is read gives however many of those 300 were unread."""
    pages = _pages([f"p{i:03d}" for i in range(10)])

    kept, _ = batch.drop_keys(pages, ["p000", "p001", "p002"])
    chosen = batch.narrow(kept, limit=5)

    assert len(chosen) == 5
    assert [p.key for p in chosen] == ["p003", "p004", "p005", "p006", "p007"]


# ── the join the register makes possible ─────────────────────────────────────
#
# "Every page Laßberg wrote, none of them transcribed yet" cannot be answered
# from the pages: the hand is read off a transcription and these have none. The
# edition's register names a sender per *letter*, and the letter id is in the
# page key — so it is a lookup.

def test_every_page_of_the_named_letters_is_selected():
    pages = _pages(["Basel__lassberg-letter-1737__a_1",
                    "Basel__lassberg-letter-1737__a_2",
                    "Basel__lassberg-letter-1280__a_1"])

    chosen, missing = batch.select_letters(pages, ["lassberg-letter-1737"])

    assert [p.key for p in chosen] == ["Basel__lassberg-letter-1737__a_1",
                                       "Basel__lassberg-letter-1737__a_2"]
    assert missing == []


def test_a_letter_with_no_page_here_is_named():
    """The register is somebody else's data and covers letters this corpus may
    not hold — 279 letter ids against a share that has lost folders before."""
    chosen, missing = batch.select_letters(
        _pages(["Basel__lassberg-letter-1737__a_1"]),
        ["lassberg-letter-1737", "lassberg-letter-0626"])

    assert len(chosen) == 1
    assert missing == ["lassberg-letter-0626"]


def test_a_page_loose_in_an_archive_root_belongs_to_no_letter():
    """`Winterthur__101-MsBRH_466-56-071` has no letter segment, so no letter
    id can select it."""
    chosen, missing = batch.select_letters(
        _pages(["Winterthur__101-MsBRH"]), ["lassberg-letter-1737"])

    assert chosen == []
    assert missing == ["lassberg-letter-1737"]


def test_the_letter_is_identified_by_the_exports_own_rule():
    """Through `hf_export.letter_of`, so a page and its dataset label can never
    disagree about which letter the page belongs to — including the folder
    fallback for `blb lassberg__K 2911,104`."""
    import hf_export as hf

    pages = _pages(["Aarau__upload__lassberg-letter-0892__00012"])

    chosen, _ = batch.select_letters(pages, ["lassberg-letter-0892"])

    assert len(chosen) == 1
    assert hf.letter_of(chosen[0].key).name == "lassberg-letter-0892"


def test_the_selection_is_in_corpus_order():
    pages = _pages(["Basel__lassberg-letter-1737__a_2",
                    "Basel__lassberg-letter-1737__a_1"])

    chosen, _ = batch.select_letters(pages, ["lassberg-letter-1737"])

    assert [p.key for p in chosen] == ["Basel__lassberg-letter-1737__a_1",
                                       "Basel__lassberg-letter-1737__a_2"]


def test_a_repeated_letter_id_does_not_duplicate_its_pages():
    pages = _pages(["Basel__lassberg-letter-1737__a_1"])

    chosen, _ = batch.select_letters(pages, ["lassberg-letter-1737"] * 3)

    assert len(chosen) == 1
