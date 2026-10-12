"""One run from several attempts at the same pages (#624).

An empty answer turned out to be, on some pages, a property of the *attempt*
rather than of the page (#623). Measured 2026-10-11 on the 25 pages
`gemini-300-strict2` flagged, re-read twice with the same prompt and settings:
the second attempt read three of them — among them two complete letters of ~950
characters that five earlier readings had answered with nothing — and the third
added two scraps and no letter.

So the corpus wants the best of the attempts, and the attempts sit in three run
directories while `publish-batch` publishes one. Hence a merge, and two
decisions in it worth testing rather than assuming:

* **Ranking is by verdict, never by length.** The `deliberation` pages carry
  800–900 characters of the model arguing with itself and would win any
  comparison by size against the transcription that replaced them.
* **`repetitive` and `deliberation` rank below `empty`.** They carry text that
  is not a reading of the page; published in an edition that is attributed
  invention, and nothing is better than something fabricated.
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import atr_batch as batch                   # noqa: E402

#: Eight distinct lines. Doubling a four-line block instead would make the
#: fixture `repetitive` at a repeat ratio of exactly 0.5 — which the classifier
#: catches, and which would have had this test asserting that an empty page
#: beats a letter. A fixture that trips a verdict it was not written for tests
#: the verdict, not the merge.
LETTER = ("Eppishausen am 21 Januar 1831.\n"
          "Hochgeschäzter Herr!\n"
          "Da ich das vergnügen nicht haben soll, Sie in meiner Waldklause\n"
          "zu bewirten; so bliebt mir freilich nichts anders übrig, als Inen\n"
          "die Siegel zu schicken. Die erlaubnis die urkunden länger zu\n"
          "behalten, habe ich zwar selbst anticipirt, doch dank ich herzlich.\n"
          "Mit vollkomener hochachtung verharre ich\n"
          "                                        Ir ergebenster JvLaßberg.\n")
ARGUING = "\n".join(["Wait, is it an 'f'?", "LOOK AT WORD 3:",
                     "Line 3: 'u' with an arc", "YES!"] * 3)
PADDED = "Beiliegend die Urkunde.\n10 bis 12" + "0" * 3000
STAMP = "[stempel: Schillermuseum Marbach] [fremde hand: 11551]"


def write(root: Path, run: str, key: str, text: str, *, model="gemini-3.8-flash",
          settings: str = "prompt-x") -> None:
    d = root / run / model
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.txt").write_text(text, encoding="utf-8")
    (d / f"{key}.json").write_text(json.dumps({
        "schema": batch.SCHEMA, "run": run, "model": model, "text": text,
        "lines": [], "timing_ms": 6400, "truncated": False,
        "settings": f"{settings}|{run}",
        "gateway_version": f"gemini@{run}",
        "recognised_at": "2026-10-11T18:00:00+00:00",
        "source": {"name": f"{key}.jpg", "key": key, "sha256": "x", "bytes": 1},
    }), encoding="utf-8")


@pytest.fixture
def runs(tmp_path: Path) -> Path:
    """A canonical run and two retries, as the real ones stood."""
    write(tmp_path, "canon", "p-read", LETTER)
    write(tmp_path, "canon", "p-blank", "")
    write(tmp_path, "canon", "p-arguing", ARGUING)
    write(tmp_path, "canon", "p-padded", PADDED)

    write(tmp_path, "retry", "p-read", "kurz")          # worse
    write(tmp_path, "retry", "p-blank", LETTER)         # the Winterthur case
    write(tmp_path, "retry", "p-arguing", LETTER)
    write(tmp_path, "retry", "p-padded", "")            # nothing beats padding

    write(tmp_path, "third", "p-blank", "")
    write(tmp_path, "third", "p-only-here", LETTER)
    return tmp_path


def merged(runs: Path, order=("canon", "retry", "third"), **kw):
    return batch.merge_runs([runs / r for r in order], runs / "merged", **kw)


def read(runs: Path, key: str) -> dict:
    path = runs / "merged" / "gemini-3.8-flash" / f"{key}.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ── what wins ────────────────────────────────────────────────────────────────

def test_a_later_attempt_wins_where_the_first_was_empty(runs):
    """The case this exists for: 978 characters of Zellweger where five earlier
    readings answered with nothing."""
    merged(runs)

    assert read(runs, "p-blank")["text"] == LETTER
    assert read(runs, "p-blank")["merged_from"]["run"] == "retry"


def test_nothing_beats_text_that_is_not_a_reading(runs):
    """`repetitive` and `deliberation` rank below `empty`. The padded page's
    retry is *empty*, and the empty one still wins — because 3 000 zeros
    published as a transcription of Laßberg's hand is worse than a gap."""
    merged(runs)

    assert read(runs, "p-padded")["text"] == ""
    assert read(runs, "p-padded")["merged_from"]["run"] == "retry"


def test_ranking_is_not_by_length(runs):
    """The arguing page is 180 characters of self-address; the letter that
    replaces it is longer here, so make the point where length would mislead:
    against a *short* reading the long nonsense must still lose."""
    write(runs, "retry", "p-arguing", STAMP)
    merged(runs)

    chosen = read(runs, "p-arguing")
    assert chosen["text"] == STAMP
    assert len(ARGUING) > len(STAMP), "the loser is the longer text"


def test_the_canonical_run_wins_a_tie(runs):
    """Order is how a caller says which run is canonical, and `ok` vs `ok` is
    a tie — so a retry that read the page differently does not displace it."""
    write(runs, "retry", "p-read", LETTER.replace("Januar", "Januer"))
    merged(runs)

    assert read(runs, "p-read")["merged_from"]["run"] == "canon"
    assert read(runs, "p-read")["text"] == LETTER


def test_a_worse_retry_does_not_displace_a_reading(runs):
    merged(runs)
    assert read(runs, "p-read")["merged_from"]["verdict"] == "ok"
    assert read(runs, "p-read")["merged_from"]["run"] == "canon"


# ── provenance ───────────────────────────────────────────────────────────────

def test_the_page_keeps_the_payload_of_the_attempt_that_produced_it(runs):
    """`settings` and `gateway_version` must stay true to the attempt, or the
    corpus can no longer say what produced any reading in it."""
    merged(runs)
    page = read(runs, "p-blank")

    assert page["settings"] == "prompt-x|retry"
    assert page["gateway_version"] == "gemini@retry"
    assert page["run"] == "merged", "but the run is the merged one"


def test_the_page_says_what_the_other_attempts_answered(runs):
    merged(runs)

    assert read(runs, "p-blank")["merged_from"]["considered"] == [
        {"run": "canon", "verdict": "empty"},
        {"run": "retry", "verdict": "ok"},
        {"run": "third", "verdict": "empty"},
    ]


def test_a_merged_page_still_reads_as_complete(runs):
    """`merged_from` is additive and SCHEMA is not bumped — otherwise every page
    of every earlier run would read as unfinished (#613)."""
    merged(runs)
    path = runs / "merged" / "gemini-3.8-flash" / "p-blank.json"

    assert batch.is_complete(path)
    assert json.loads(path.read_text())["schema"] == batch.SCHEMA


# ── the report ───────────────────────────────────────────────────────────────

def test_a_page_found_only_later_is_not_counted_as_an_improvement(runs):
    """It was not improved by the third run, it was only found there — and
    inflating that number would spoil the one thing this merge reports."""
    report = merged(runs)

    assert [c.key for c in report.improved] == ["p-arguing", "p-blank",
                                                "p-padded"]
    assert [c.key for c in report.only_later] == ["p-only-here"]


def test_the_report_says_what_came_from_where(runs):
    report = merged(runs)

    assert report.pages == 5
    assert report.per_run() == {"canon": 1, "retry": 3, "third": 1}
    assert "pages taken" in batch.format_merge_report(report)


def test_a_dry_run_writes_nothing_and_still_counts(runs):
    report = merged(runs, dry_run=True)

    assert report.pages == 5
    assert not (runs / "merged").exists()


# ── refusals ─────────────────────────────────────────────────────────────────

def test_merging_one_run_with_itself_changes_nothing(runs):
    """The identity case, which is what makes the order safe to express."""
    batch.merge_runs([runs / "canon"], runs / "merged")

    for key in ("p-read", "p-blank", "p-arguing", "p-padded"):
        original = (runs / "canon" / "gemini-3.8-flash" / f"{key}.txt")
        assert read(runs, key)["text"] == original.read_text(encoding="utf-8")


def test_no_runs_is_refused(tmp_path):
    with pytest.raises(ValueError, match="at least one run"):
        batch.merge_runs([], tmp_path / "merged")


def test_a_missing_run_is_refused_rather_than_skipped(runs):
    with pytest.raises(FileNotFoundError, match="not a run directory"):
        batch.merge_runs([runs / "canon", runs / "nope"], runs / "merged")


# ── through MCP: named, never pathed ─────────────────────────────────────────

@pytest.fixture
def stack(runs, monkeypatch):
    import config
    monkeypatch.setattr(config, "VLM_TEST_ROOT", runs)
    return runs


def test_the_merge_runs_from_a_session(stack):
    from mcp_atr import jobs

    out = jobs.merge_runs(["canon", "retry", "third"], "merged")

    assert out["pages"] == 5
    assert out["per_run"] == {"canon": 1, "retry": 3, "third": 1}
    assert [c["key"] for c in out["improved"]] == ["p-arguing", "p-blank",
                                                   "p-padded"]
    assert out["only_later"] == ["p-only-here"]
    assert out["code"]["stale"] is False


def test_the_reply_says_which_code_merged(stack):
    """Same reason as #621: these numbers are computed in the server process."""
    from mcp_atr import jobs

    out = jobs.merge_runs(["canon", "retry"], "merged", dry_run=True)
    assert set(out["code"]) == {"loaded", "checkout", "stale", "started_at"}
    assert out["dry_run"] is True


@pytest.mark.parametrize("runs_arg,message", [
    ([], "at least one run"),
    (["canon", "canon"], "given twice"),
    (["canon", "../../etc"], "invalid run name"),
    (["canon", "nope"], "no run directory"),
])
def test_what_the_session_tool_refuses(stack, runs_arg, message):
    from mcp_atr import jobs

    with pytest.raises(jobs.JobError, match=message):
        jobs.merge_runs(runs_arg, "merged")


def test_the_destination_cannot_be_an_input(stack):
    """Merging a run into itself would read and overwrite the same files."""
    from mcp_atr import jobs

    with pytest.raises(jobs.JobError, match="also an input run"):
        jobs.merge_runs(["canon", "retry"], "canon")


# ── the one recomputed field (#625) ──────────────────────────────────────────
#
# A merged page carried the `quality` block of the run that read it, and on
# 2026-10-12 a page published into the edition held two contradictory verdicts:
#
#     merged_from.verdict : ok           (the merge, with the current rules)
#     quality.verdict     : repetitive   (the reading, before #623)
#
# `quality` is the only part of the payload that is not a fact about the call.

def test_the_stored_verdict_cannot_contradict_the_chosen_one(runs):
    """By construction, not by coincidence: one classification, used for both."""
    merged(runs)

    for key in ("p-read", "p-blank", "p-arguing", "p-padded", "p-only-here"):
        page = read(runs, key)
        assert page["quality"]["verdict"] == page["merged_from"]["verdict"], key


def test_a_stale_verdict_from_the_reading_run_is_not_carried_forward(runs):
    """The Marbach case, reconstructed: a page read under an older classifier
    that called it `repetitive`, which the current rules do not."""
    d = runs / "canon" / "gemini-3.8-flash"
    stale = json.loads((d / "p-read.json").read_text(encoding="utf-8"))
    stale["quality"] = {"verdict": "repetitive", "chars": 416, "lines": 8,
                        "longest_char_run": 80, "repeat_ratio": 0.0,
                        "mean_line_chars": 52.0}
    (d / "p-read.json").write_text(json.dumps(stale), encoding="utf-8")

    merged(runs)
    page = read(runs, "p-read")

    assert page["quality"]["verdict"] == "ok"
    # And it carries the measures the current classifier stores, so the page
    # can be re-judged from its own numbers rather than read again.
    assert "run_share" in page["quality"]
    assert "deliberation_ratio" in page["quality"]


def test_what_the_call_did_is_never_rewritten(runs):
    """The reason `quality` is the *only* recomputed field: everything else
    records what the API was asked and what it answered."""
    merged(runs)
    page = read(runs, "p-blank")

    assert page["settings"] == "prompt-x|retry"
    assert page["gateway_version"] == "gemini@retry"
    assert page["timing_ms"] == 6400
    assert page["recognised_at"] == "2026-10-11T18:00:00+00:00"
