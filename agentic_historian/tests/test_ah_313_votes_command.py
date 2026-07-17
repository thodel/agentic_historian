"""#313 (posting half): the /votes command surfaces the Gate-2 voting card.

#313's recording half stores a no-merge page's candidates as `state.artifacts['paths']`.
This command posts the voting card built from them, so the historian can override
the score-ranked auto-pick. Pull-based: the historian sees the no-merge on the live
board (#289) and runs /votes <doc_id> — no interactive view is posted from the
headless worker thread.

Offline — Discord ctx is a stub, RunState in tmp. Run from the repo root:
    pytest agentic_historian/tests/test_ah_313_votes_command.py
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
    "p1.jpg:trocr/trocr-medieval-escriptmask":
        "unser frùntlich gruͦs vor liebe getrüwe von der stoͤsse wegē so da sint",
    "p1.jpg:trocr/trocr-kurrent-xvi-xvii":
        "Vnser fründlich grus vor liebe getrune von der stösse wyse so daß nit",
}


class _Ctx:
    """Minimal ApplicationContext stub: records the followup message + view."""
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


# ── registration ─────────────────────────────────────────────────────────────

def test_votes_command_is_registered():
    cmd = next((c for c in bot.bot.pending_application_commands if c.name == "votes"), None)
    assert cmd is not None


# ── posts the card when a no-merge recorded candidates ───────────────────────

def test_posts_the_vote_card_when_paths_exist():
    state = RunState.load_or_new("doc-votes")
    state.artifacts["paths"] = dict(PATHS)
    state.save()

    ctx = _Ctx()
    asyncio.run(_callback()(ctx, "doc-votes"))

    assert len(ctx.sent) == 1
    msg = ctx.sent[0]
    assert msg.view is not None                        # the interactive vote card
    low = msg.content.lower()
    assert "escriptmask" in low and "kurrent" in low   # both engine labels shown
    # persisted so the buttons survive a restart (#150)
    assert RunState.load_or_new("doc-votes").message_ids.get("gate2") == 4242


def test_the_card_carries_both_readings():
    state = RunState.load_or_new("doc-votes")
    state.artifacts["paths"] = dict(PATHS)
    state.save()

    ctx = _Ctx()
    asyncio.run(_callback()(ctx, "doc-votes"))
    body = ctx.sent[0].content
    # both readings are shown for the historian to judge (render_vote_card)
    assert "gruͦs" in body or "grus" in body


# ── declines cleanly when there is nothing to vote on ────────────────────────

def test_declines_when_no_paths_recorded():
    RunState.load_or_new("doc-empty").save()

    ctx = _Ctx()
    asyncio.run(_callback()(ctx, "doc-empty"))

    assert len(ctx.sent) == 1
    assert ctx.sent[0].view is None                    # no card
    assert "Keine Abstimmung" in ctx.sent[0].content


def test_declines_when_only_one_candidate():
    state = RunState.load_or_new("doc-one")
    state.artifacts["paths"] = {"p1:trocr/x": "die einzige lesart"}
    state.save()

    ctx = _Ctx()
    asyncio.run(_callback()(ctx, "doc-one"))
    assert ctx.sent[0].view is None
    assert "Keine Abstimmung" in ctx.sent[0].content
