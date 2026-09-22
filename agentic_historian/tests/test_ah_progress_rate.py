"""The progress line's rate, on a resumed run.

The rate was `done / elapsed`, where `done` counts every position walked —
including pages skipped because they were already on disk. Those cost
microseconds, so a resumed run reported a rate inflated by however much it had
already finished.

Measured on 2026-09-22, resuming a corpus run at page 1920 of 6742:

    2210/6742 pages (14 failed) · 18.2 p/min · ETA 248 min
    2250/6742 pages (14 failed) · 16.3 p/min · ETA 276 min

40 pages in 17 minutes is **2.3 p/min**, and the real remaining time was 33
hours, not 248 minutes. The giveaway is in those two lines: the ETA *grows* as
the inflated rate decays toward the true one.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import atr_batch as batch                       # noqa: E402


def outcome(done=0, failed=0, skipped=0):
    return batch.ModelOutcome(model="m", done=done, failed=failed, skipped=skipped)


def test_skipped_pages_do_not_count_towards_the_rate():
    """The corpus run that found this: 1920 skipped, 330 worked in 143 minutes."""
    line = batch._progress_line("m", 2250, 6742,
                                outcome(done=316, failed=14, skipped=1920),
                                elapsed=143 * 60)

    assert "2250/6742" in line
    assert "2.3 p/min" in line                   # not 15.7


def test_the_three_counts_are_stated_separately():
    """"2250 of 6742" and "330 by this process" are both true, and only one of
    them is a speed."""
    line = batch._progress_line("m", 2250, 6742,
                                outcome(done=316, failed=14, skipped=1920),
                                elapsed=143 * 60)

    assert "316 read" in line and "14 failed" in line and "1920 skipped" in line


def test_a_fresh_run_is_unchanged():
    """With nothing skipped, worked == done and the old arithmetic was right."""
    line = batch._progress_line("m", 60, 600, outcome(done=60), elapsed=60 * 60)

    assert "1.0 p/min" in line
    assert "ETA 9.0 h" in line                   # 540 remaining at 1/min


def test_the_eta_assumes_the_rest_still_needs_work():
    """An estimate should be wrong in the direction of finishing early; the
    `skipped` count in the same line is what says to expect that."""
    line = batch._progress_line("m", 100, 200, outcome(done=50, skipped=50),
                                elapsed=50 * 60)

    assert "1.0 p/min" in line
    assert "ETA 100 min" in line                 # 100 remaining, not 50


def test_long_estimates_are_stated_in_hours():
    """`ETA 1953 min` is a number nobody converts in their head."""
    line = batch._progress_line("m", 2250, 6742, outcome(done=330),
                                elapsed=143 * 60)

    assert " h" in line.rsplit("ETA ", 1)[1]


def test_no_rate_before_anything_has_been_worked():
    """All-skipped so far: a rate of 0.0 p/min would read as "stalled"."""
    line = batch._progress_line("m", 500, 600, outcome(skipped=500), elapsed=10)

    assert "p/min" not in line and "ETA" not in line
    assert "500 skipped" in line


def test_failed_pages_are_work_too():
    """A page that failed cost its retries; excluding it would inflate the rate
    the same way skipping did."""
    line = batch._progress_line("m", 20, 100, outcome(done=10, failed=10),
                                elapsed=20 * 60)

    assert "1.0 p/min" in line
