"""A Gate-2 vote click must give visible feedback (#313 live fix).

With VOTING_MIN_VOTES=1 the first click decides, and if AUTO_RESUME_AFTER_GATE is
on the old callback ran the B/C re-run (slow LLM calls) SYNCHRONOUSLY before
editing the message — past Discord's ~3s interaction window, so the token expired,
the edit failed, and the click looked like it did nothing. The card must be
updated first (the ack), and the re-run must happen after, off the event loop.

Offline — Discord interaction + resume are stubbed. Run from the repo root:
    pytest agentic_historian/tests/test_ah_gate2_vote_feedback.py
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
import path_compare    # noqa: E402
import voting          # noqa: E402
from runstate import RunState  # noqa: E402

PATHS = {
    "p:trocr/escriptmask": "unser frùntlich gruͦs vor liebe getrüwe",
    "p:trocr/kurrent": "Vnser fründlich grus vor liebe getrune",
}


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path)
    monkeypatch.setattr(config, "VOTES_LOG_PATH", tmp_path / "votes.jsonl")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "VOTING_MIN_VOTES", 1)
    return tmp_path


class _Interaction:
    def __init__(self):
        self.user = SimpleNamespace(id=1, display_name="Anna", name="Anna")
        self.edited = []
        self.response = SimpleNamespace(edit_message=self._edit)

    async def _edit(self, *, content=None, view=None):
        self.edited.append(content)


def test_click_updates_the_card_even_when_a_resume_is_triggered(monkeypatch):
    """The reported bug: a decided vote with AUTO_RESUME on gave no visible change.
    The card must update (the ack) regardless."""
    monkeypatch.setattr(config, "AUTO_RESUME_AFTER_GATE", True)
    monkeypatch.setattr(path_compare, "apply_path_choice", lambda s, c, p, **k: p[c])

    order = []

    # resume must run AFTER the ack and off-loop — record when it fires.
    # RunState is a pydantic model, so patch the method on the class.
    monkeypatch.setattr(RunState, "resume",
                        lambda self, runners, **k: order.append("resume"))

    async def go():
        st = RunState(doc_id="d-fb")
        view = path_compare.build_view(st, PATHS, runners={"stub": object()})
        btn = next(b for b in view.children if b.path == "p:trocr/escriptmask")
        inter = _Interaction()
        await btn.callback(inter)
        order.append("ack:" + str(bool(inter.edited)))
        await asyncio.sleep(0.05)      # let the offloaded resume task run
        return inter

    inter = asyncio.run(go())

    assert inter.edited, "the card was never updated — no visible feedback"
    assert "Entschieden" in inter.edited[0]                 # the decision is shown
    # the ack happened before resume ran (resume is scheduled after the edit)
    assert order[0].startswith("ack:True")
    assert "resume" in order                                # resume still ran


def test_a_failing_apply_does_not_leave_the_click_unacked(monkeypatch):
    """Even if vote handling raises, the interaction must be acknowledged."""
    monkeypatch.setattr(config, "AUTO_RESUME_AFTER_GATE", False)
    monkeypatch.setattr(path_compare, "apply_path_choice",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    async def go():
        st = RunState(doc_id="d-fb2")
        view = path_compare.build_view(st, PATHS)
        btn = next(b for b in view.children if b.path == "p:trocr/escriptmask")
        inter = _Interaction()
        await btn.callback(inter)                            # must not raise
        return inter

    inter = asyncio.run(go())
    assert inter.edited, "the card must be updated even when apply fails"
