"""#351: /votes must not report "the engines agreed" for a document that does not exist.

`RunState.load_or_new` invents an empty state for any string, so an unknown doc_id
produced a positive claim about a run that never happened. Observed on tei with a
one-character typo (`prefs-test-BAT66` for `prefs-test-BAT664`), which cost a full
round-trip before the difference was spotted.

Offline — Discord ctx is a stub, RunState in tmp. Run from the repo root:
    pytest agentic_historian/tests/test_ah_351_votes_unknown_doc.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config          # noqa: E402
import bot             # noqa: E402
from runstate import RunState  # noqa: E402

PATHS = {
    "p1.jpg:trocr/trocr-medieval-escriptmask": "unser frùntlich gruͦs vor liebe",
    "p1.jpg:trocr/trocr-kurrent-xvi-xvii": "Vnser fründlich grus vor liebe",
}


class _Ctx:
    def __init__(self):
        self.sent = []
        self.channel = SimpleNamespace(id=1)

        async def _defer():
            pass

        async def _send(content=None, view=None):
            msg = SimpleNamespace(id=4242, content=content, view=view)
            self.sent.append(msg)
            return msg

        self.defer = _defer
        self.followup = SimpleNamespace(send=_send)


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "AUTO_RESUME_AFTER_GATE", False)
    monkeypatch.setattr(config, "VOTES_LOG_PATH", tmp_path / "votes.jsonl")
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path)
    return tmp_path


def _callback():
    cmd = next(c for c in bot.bot.pending_application_commands if c.name == "votes")
    return cmd.callback


def _run(doc_id):
    ctx = _Ctx()
    asyncio.run(_callback()(ctx, doc_id))
    return ctx.sent[0].content


# ── the three states must be distinguishable ─────────────────────────────────

def test_an_unknown_doc_is_reported_as_unknown():
    body = _run("does-not-exist")
    assert "Kein Lauf" in body
    assert "einig" not in body, "must not claim the engines agreed about nothing"


def test_a_real_run_without_candidates_still_says_the_engines_agreed():
    """The existing message is correct HERE — this run happened."""
    RunState.load_or_new("doc-real-empty").save()
    body = _run("doc-real-empty")
    assert "Keine Abstimmung" in body and "einig" in body


def test_a_run_with_candidates_still_posts_the_card():
    st = RunState.load_or_new("doc-with-paths")
    st.artifacts["paths"] = dict(PATHS)
    st.save()

    ctx = _Ctx()
    asyncio.run(_callback()(ctx, "doc-with-paths"))
    assert ctx.sent[0].view is not None


# ── typo recovery ────────────────────────────────────────────────────────────

def test_a_one_character_typo_gets_a_suggestion():
    """The live case: `prefs-test-BAT66` for `prefs-test-BAT664`."""
    st = RunState.load_or_new("prefs-test-BAT664")
    st.artifacts["paths"] = dict(PATHS)
    st.save()

    body = _run("prefs-test-BAT66")
    assert "prefs-test-BAT664" in body
    assert "Meintest du" in body


def test_no_suggestion_block_when_nothing_is_close():
    RunState.load_or_new("completely-different").save()
    assert "Meintest du" not in _run("zzzzzzzz")


def test_suggestions_do_not_crash_on_an_empty_runs_dir():
    assert "Kein Lauf" in _run("anything")


# ── the helpers ──────────────────────────────────────────────────────────────

def test_known_doc_ids_lists_saved_runs_only():
    RunState.load_or_new("saved-one").save()
    RunState.load_or_new("saved-two").save()
    RunState.load_or_new("never-saved")            # not persisted
    known = RunState.known_doc_ids()
    assert "saved-one" in known and "saved-two" in known
    assert "never-saved" not in known


def test_load_or_new_still_does_not_persist():
    """The behaviour that made the bug invisible — a query must not create state."""
    RunState.load_or_new("phantom")
    assert not RunState.exists("phantom")
