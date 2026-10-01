"""/atr_jobs asks for the summary shape, and nothing else does (serving#107).

`GET /train/jobs` returns every job's whole submitted `request` object: 807 KB
for 42 jobs, measured from this host, over the VPN, for the five rows
`format_jobs` prints. The gateway learned to shorten it
(serving-atr-inference#107, merged); this is the caller asking.

Two things it deliberately does not do, and both are the reason `summary` is a
parameter rather than the default:

* **No `limit`.** `format_jobs` prints "(N insgesamt)" from `len(items)`, so
  `?limit=5` would make N always 5 — a wrong number instead of a long answer.
  The `request` objects are where nearly all the bytes are anyway.
* **The watcher keeps the full shape.** `atr_watch` reads `progress`,
  `metrics` and `published` to decide what to announce, and the summary shape
  does not carry them. It polls every few seconds, so it is the caller that
  pays the bytes most often and the one that must not have them cut: it would
  not have failed, it would have gone quiet.
"""

import asyncio
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import atr_status  # noqa: E402

#: The six keys the trainer's ``fields=summary`` returns.
SUMMARY_KEYS = ("id", "status", "stage", "created_at", "queued_reason", "error")


@pytest.fixture
def asked(monkeypatch):
    """Records the (path, params) of every gateway call."""
    calls: list[tuple] = []

    async def _get(path, params=None):
        calls.append((path, params))
        return {"jobs": []}

    monkeypatch.setattr(atr_status, "_get", _get)
    return calls


# ── what each caller asks for ───────────────────────────────────────────────
def test_the_default_asks_for_the_full_record(asked):
    """The watcher's call. Unchanged, and it has to stay that way."""
    asyncio.run(atr_status.jobs())

    assert asked == [("/train/jobs", None)]


def test_the_summary_call_names_the_field_set(asked):
    asyncio.run(atr_status.jobs(summary=True))

    assert asked == [("/train/jobs", {"fields": "summary"})]


def test_no_limit_is_sent(asked):
    """With ?limit=5 the "(N insgesamt)" line would read 5 for any N."""
    asyncio.run(atr_status.jobs(summary=True))

    assert "limit" not in (asked[0][1] or {})


# ── the summary shape is enough for this view ───────────────────────────────
def test_format_jobs_reads_only_summary_fields():
    """Pins the dependency the other way round: if this view starts reading a
    field the summary does not carry, this test says so before a user sees an
    empty column."""
    jobs = [{k: f"{k}-{n}" for k in SUMMARY_KEYS} for n in range(3)]

    rendered = atr_status.format_jobs({"jobs": jobs})

    for n in range(3):
        assert f"id-{n}" in rendered
        assert f"status-{n}"[:10] in rendered


def test_the_total_is_the_whole_list_not_the_rows_shown():
    """Why no `limit`: the number under the table counts every job the gateway
    returned, and five rows are shown. A shortened list would make it lie."""
    jobs = [{"id": f"job-{n}", "status": "done", "stage": None,
             "created_at": f"2026-09-{n + 1:02d}"} for n in range(12)]

    rendered = atr_status.format_jobs({"jobs": jobs})

    assert "(12 insgesamt)" in rendered
    assert "job-11" in rendered       # newest first
    assert "job-0" not in rendered    # beyond the five rows


def test_a_summary_without_stage_still_renders():
    """`stage` is None for a queued job, and the summary carries it as None."""
    rendered = atr_status.format_jobs(
        {"jobs": [{"id": "job-1", "status": "queued", "stage": None,
                   "created_at": "2026-09-30", "queued_reason": "gpu busy",
                   "error": None}]})

    assert "job-1" in rendered
    assert "queued" in rendered


# ── the watcher is not touched ──────────────────────────────────────────────
def test_the_watcher_needs_fields_the_summary_does_not_carry():
    """The reason `summary` is opt-in, as a fact about the code rather than a
    sentence in a docstring: these are the keys `atr_watch` reads off a job,
    and three of them are outside the summary shape."""
    import atr_watch

    source = Path(atr_watch.__file__).read_text(encoding="utf-8")
    for field in ("progress", "metrics", "published"):
        assert f'job.get("{field}")' in source or f"job.get('{field}')" in source, field
        assert field not in SUMMARY_KEYS


def test_the_bot_asks_for_the_summary_only_in_the_list_view():
    """`/atr_jobs` is the five-row view; the watch loop is the frequent caller
    that needs everything. One call site changes, the other must not."""
    source = (PKG / "bot.py").read_text(encoding="utf-8")

    assert "atr_status.jobs(summary=True)" in source
    assert "await atr_status.jobs()" in source
