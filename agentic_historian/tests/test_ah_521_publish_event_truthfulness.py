"""The publish event has to say what happened, not what was attempted.

``_publish_outputs`` returns ``(published, detail)`` and its docstring explains
why: "a bare 'done' when nothing was published would be exactly the false-green
signal V-2 exists to remove" (#288).

It then called ``publish_doc`` and returned ``True, "published to the outputs
repo"`` without looking at the result. ``publish_doc`` returns ``None`` every
time it did *not* publish — no artifacts to publish, or an API call it
swallowed into a warning — so the run emitted a successful publish event for a
document that was never published. The false green the function exists to
remove was produced by the function itself.

This matters beyond tidiness here: the id refusal added alongside it is
reported through the same path. A refused document that the run calls
"published" would be worse than the unchecked publishing it replaces.

Run from the repo root:
    pytest agentic_historian/tests/test_ah_521_publish_event_truthfulness.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import orchestrator  # noqa: E402
from utils import publish_github as pg  # noqa: E402


def test_nothing_published_is_not_reported_as_published(monkeypatch):
    """The case that was wrong: publish_doc declined, the event said done."""
    monkeypatch.setattr(pg, "is_enabled", lambda: True)
    monkeypatch.setattr(pg, "publish_doc", lambda doc_id, source_url=None: None)

    published, detail = orchestrator._publish_outputs("some-doc")

    assert published is False, (
        "publish_doc published nothing and the run reported success; this is "
        "the false-green signal the function's own docstring disclaims")
    assert "published nothing" in detail


def test_a_real_publication_is_reported_as_one(monkeypatch):
    monkeypatch.setattr(pg, "is_enabled", lambda: True)
    monkeypatch.setattr(
        pg, "publish_doc",
        lambda doc_id, source_url=None: "https://github.test/commit/abc")

    published, detail = orchestrator._publish_outputs("some-doc")

    assert published is True
    assert "published to the outputs repo" in detail


def test_publishing_switched_off_says_so(monkeypatch):
    monkeypatch.setattr(pg, "is_enabled", lambda: False)
    published, detail = orchestrator._publish_outputs("some-doc")
    assert published is False
    assert "ENABLE_GITHUB_PUBLISH" in detail


def test_a_refused_id_reaches_the_event_with_its_reason(monkeypatch):
    """What the refusal is worth: the run records why, not just that."""
    monkeypatch.setattr(pg, "is_enabled", lambda: True)

    def refuse(doc_id, source_url=None):
        raise pg.DocumentIdRefused(
            f"document id {doc_id!r} must start and end with a letter or digit")

    monkeypatch.setattr(pg, "publish_doc", refuse)

    published, detail = orchestrator._publish_outputs("u-17__")

    assert published is False
    assert "u-17__" in detail, "the event does not name the id it refused"
    assert "letter or digit" in detail, "the event does not carry the reason"


def test_a_refused_id_does_not_break_the_pipeline(monkeypatch):
    """Non-fatal stays non-fatal: one bad name must not stop a batch."""
    monkeypatch.setattr(pg, "is_enabled", lambda: True)

    def refuse(doc_id, source_url=None):
        raise pg.DocumentIdRefused("nope")

    monkeypatch.setattr(pg, "publish_doc", refuse)
    # Returns rather than raising; the caller emits an event and carries on.
    assert orchestrator._publish_outputs("u-17__")[0] is False
