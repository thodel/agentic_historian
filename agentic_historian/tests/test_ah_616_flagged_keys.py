"""The pages a report flags are a selection the next run can take (#616).

A report already named the pages that came back empty and the pages the model
padded, and counted — without naming — the pages that were cut off at the token
ceiling. All three are pages whose reading is not worth keeping, and all three
wanted the same next step: read them again, under a larger ceiling, a lower
thinking level or another prompt.

That step was manual. The keys lived in prose inside `report.md`, capped at
twenty per section *as they were collected*, so the twenty-first empty page was
counted and then dropped — no file held it. Getting the pages back out meant
reading Markdown and typing keys by hand, and for a cut-off page it meant
grepping the run log, which names the image file (`002.jpg`) rather than the
page key a selection is made of.

Measured on the run that opened this: `gemini-300-strict`, 300 pages of the
Lassberg corpus read by `gemini-3.8-flash`, came back with 28 empty, 1 padded
and 4 cut off — 33 pages to re-read, of which `report.md` named 21.
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import atr_batch as batch                   # noqa: E402
import config                               # noqa: E402
from mcp_atr import jobs                    # noqa: E402


def write_result(run_dir: Path, model: str, key: str, text: str, *,
                 truncated=False) -> None:
    d = run_dir / model
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.txt").write_text(text, encoding="utf-8")
    (d / f"{key}.json").write_text(json.dumps({
        "schema": batch.SCHEMA,
        "run": run_dir.name,
        "model": model,
        "text": text,
        "lines": [],
        "timing_ms": 6400,
        "truncated": truncated,
        "recognised_at": "2026-10-10T21:40:00+00:00",
        "source": {"name": f"{key}.jpg", "key": key, "sha256": "x", "bytes": 1},
    }), encoding="utf-8")


@pytest.fixture
def stack(tmp_path, monkeypatch):
    """One run whose output holds each of the three defects, and a GT root."""
    runs = tmp_path / "vlm_test"
    runs.mkdir(parents=True)
    monkeypatch.setattr(config, "VLM_TEST_ROOT", runs)
    gt = tmp_path / "gt"
    gt.mkdir(parents=True)
    monkeypatch.setattr(config, "GT_ROOT", gt)

    run = runs / "gemini-300-strict"
    write_result(run, "gemini-3.8-flash", "UB-Bern__read", "Eppishausen am 21 Januar")
    write_result(run, "gemini-3.8-flash", "UB-Bern__blank", "")
    write_result(run, "gemini-3.8-flash", "UB-Bern__padded", "[...]\n" * 40)
    write_result(run, "gemini-3.8-flash", "UB-Bern__cutoff",
                 "Durchlauchtigste gnädigste Fürstin, ich bin", truncated=True)
    return tmp_path


# ── what the selection contains ──────────────────────────────────────────────

def test_the_three_defects_come_back_as_one_selection(stack):
    out = jobs.flagged_keys("gemini-300-strict")

    assert out["flagged"] == 3
    assert out["keys"] == ["UB-Bern__blank", "UB-Bern__cutoff", "UB-Bern__padded"]
    assert out["per_model"] == [{"model": "gemini-3.8-flash", "empty": 1,
                                "repetitive": 1, "truncated": 1, "flagged": 3}]


def test_a_page_that_read_is_not_in_it(stack):
    out = jobs.flagged_keys("gemini-300-strict")
    assert "UB-Bern__read" not in out["keys"]


# ── the handover: keys_out → keys_from ───────────────────────────────────────
#
# A *name* on both ends, never a path, exactly as `score_ground_truth` writes
# one: the file this writes is the file `start_batch` reads, both under the
# ground-truth root, so a tool that chooses pages never becomes one that
# chooses files.

def test_the_keys_file_is_written_under_the_gt_root(stack):
    out = jobs.flagged_keys("gemini-300-strict", keys_out="strict-flagged.txt")

    dest = Path(config.GT_ROOT) / "strict-flagged.txt"
    assert dest.is_file()
    assert out["keys_file"] == "strict-flagged.txt"
    assert out["keys_path"] == str(dest)


def test_what_is_written_is_what_start_batch_reads_back(stack):
    jobs.flagged_keys("gemini-300-strict", keys_out="strict-flagged.txt")

    keys = batch.read_keys(Path(config.GT_ROOT) / "strict-flagged.txt")
    assert keys == ["UB-Bern__blank", "UB-Bern__cutoff", "UB-Bern__padded"]


def test_no_keys_file_unless_asked(stack):
    out = jobs.flagged_keys("gemini-300-strict")
    assert "keys_file" not in out


def test_a_keys_out_name_cannot_escape_the_root(stack):
    with pytest.raises(jobs.JobError) as err:
        jobs.flagged_keys("gemini-300-strict", keys_out="../../etc/passwd")
    assert "invalid run name" in str(err.value)


def test_an_unknown_run_is_refused_rather_than_answered_empty(stack):
    with pytest.raises(jobs.JobError) as err:
        jobs.flagged_keys("no-such-run")
    assert "no run directory" in str(err.value)


# ── the counts come from the results, not from report.md ─────────────────────

def test_a_run_whose_report_disagrees_with_its_output_is_counted_from_output(stack):
    """A resumed run's `report.md` counts most of its corpus as skipped and says
    nothing about whether those pages came back empty. Parsing the file would
    inherit that gap; walking the results cannot."""
    stale = Path(config.VLM_TEST_ROOT) / "gemini-300-strict" / "report.md"
    stale.write_text("| `gemini-3.8-flash` | 1 | 299 | 0 | 0 | 0 | 0 |",
                     encoding="utf-8")

    out = jobs.flagged_keys("gemini-300-strict")
    assert out["flagged"] == 3


# ── an empty selection is an answer, not a failure ───────────────────────────

def test_a_clean_run_writes_an_empty_file_rather_than_none(stack, tmp_path):
    clean = Path(config.VLM_TEST_ROOT) / "gemini-clean"
    write_result(clean, "gemini-3.8-flash", "p1", "Mein lieber Freund")

    out = jobs.flagged_keys("gemini-clean", keys_out="clean.txt")

    assert out["flagged"] == 0
    dest = Path(config.GT_ROOT) / "clean.txt"
    # Written, and empty: "nothing to re-read" is a result worth having on disk,
    # and a caller that finds no file cannot tell it from a tool that failed.
    assert dest.is_file()
    assert dest.read_text(encoding="utf-8") == ""
