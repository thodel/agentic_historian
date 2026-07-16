"""#293: the Gate-2 card is a multi-vote card with a live tally.

Offline — the votes log goes to tmp_path and the Discord interaction is a stub; the
real button callbacks are driven directly. Run from the repo root:
    pytest agentic_historian/tests/test_ah_293_voting_card.py
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
    "trocr-kurrent-xvi-xvii": "unser fruntlich gruos vor liebe getruwe von der stoesse",
    "vlm": "Infer fremdlichs grue vor liebe getrune von der koffe",
    "kraken-catmus_medieval": "duser feunilite grus vor liebe gerrmreuon de scosse",
}


@pytest.fixture(autouse=True)
def _tmp_votes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path)
    monkeypatch.setattr(config, "VOTES_LOG_PATH", tmp_path / "votes.jsonl")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "AUTO_RESUME_AFTER_GATE", False)
    return tmp_path


class _Interaction:
    """Minimal stand-in for a discord Interaction."""
    def __init__(self, user_id, name):
        self.user = SimpleNamespace(id=user_id, display_name=name, name=name)
        self.edited = []
        self.response = SimpleNamespace(edit_message=self._edit)

    async def _edit(self, *, content=None, view=None):
        self.edited.append(content)


def _clicks(st, sequence):
    """Build the Gate-2 view and press buttons in order, all inside ONE event loop
    (discord.ui.View requires a running loop at construction).

    `sequence` = [(path, user_id, display_name), …]; returns the interactions.
    """
    async def go():
        view = path_compare.build_view(st, PATHS)
        out = []
        for path, uid, name in sequence:
            btn = next(b for b in view.children if getattr(b, "path", None) == path)
            inter = _Interaction(uid, name)
            await btn.callback(inter)
            out.append(inter)
        return out
    return asyncio.run(go())


def _build(st):
    async def go():
        return path_compare.build_view(st, PATHS)
    return asyncio.run(go())


def _state(doc_id="d-vote"):
    return RunState(doc_id=doc_id)


# ── Gate-2 parity: the safe default ──────────────────────────────────────────

def test_single_vote_applies_immediately_gate2_parity(monkeypatch):
    """VOTING_MIN_VOTES=1 must behave exactly like the old single-click gate."""
    monkeypatch.setattr(config, "VOTING_MIN_VOTES", 1)
    applied = {}
    monkeypatch.setattr(path_compare, "apply_path_choice",
                        lambda s, c, p, **k: applied.setdefault("choice", c) or p[c])

    st = _state()
    inter = _clicks(st, [("trocr-kurrent-xvi-xvii", 1, "Anna")])[0]

    assert applied["choice"] == "trocr-kurrent-xvi-xvii"
    assert "Entschieden" in inter.edited[0]


# ── multi-vote: record, don't apply, until quorum ────────────────────────────

def test_vote_below_quorum_records_but_does_not_apply(monkeypatch):
    monkeypatch.setattr(config, "VOTING_MIN_VOTES", 3)
    monkeypatch.setattr(path_compare, "apply_path_choice",
                        lambda *a, **k: pytest.fail("must not apply below quorum"))

    st = _state()
    inter = _clicks(st, [("vlm", 1, "Anna")])[0]

    assert voting.tally(voting.load_votes(st.doc_id)) == {"vlm": 1}
    card = inter.edited[0]
    assert "1/3" in card and "Anna" in card          # live tally, display name


def test_quorum_applies_the_majority_winner_once(monkeypatch):
    monkeypatch.setattr(config, "VOTING_MIN_VOTES", 3)
    calls = []
    monkeypatch.setattr(path_compare, "apply_path_choice",
                        lambda s, c, p, **k: calls.append(c) or p[c])

    st = _state()
    inters = _clicks(st, [("trocr-kurrent-xvi-xvii", 1, "Anna"),
                          ("vlm", 2, "Hans"),
                          ("trocr-kurrent-xvi-xvii", 3, "Tobias")])
    inter = inters[-1]

    assert calls == ["trocr-kurrent-xvi-xvii"]        # applied exactly once, at quorum
    assert "Entschieden" in inter.edited[0]


def test_revote_changes_not_adds(monkeypatch):
    """A voter clicking a second time changes their vote; the card must not double-count."""
    monkeypatch.setattr(config, "VOTING_MIN_VOTES", 3)
    monkeypatch.setattr(path_compare, "apply_path_choice", lambda s, c, p, **k: p[c])

    st = _state()
    inter = _clicks(st, [("vlm", 1, "Anna"),
                         ("trocr-kurrent-xvi-xvii", 1, "Anna")])[-1]   # same voter

    assert voting.tally(voting.load_votes(st.doc_id)) == {"trocr-kurrent-xvi-xvii": 1}
    assert "1/3" in inter.edited[0]                   # one voter, one vote


def test_tie_stays_undecided(monkeypatch):
    monkeypatch.setattr(config, "VOTING_MIN_VOTES", 2)
    monkeypatch.setattr(path_compare, "apply_path_choice",
                        lambda *a, **k: pytest.fail("must not apply on a tie"))

    st = _state()
    inter = _clicks(st, [("vlm", 1, "Anna"),
                         ("trocr-kurrent-xvi-xvii", 2, "Hans")])[-1]

    assert "Gleichstand" in inter.edited[0]


# ── the card shows what voters need to judge ─────────────────────────────────

def test_card_shows_candidate_text_and_cer(monkeypatch):
    monkeypatch.setattr(config, "VOTING_MIN_VOTES", 3)
    st = _state()
    card = path_compare.render_vote_card(st, PATHS)
    assert "unser fruntlich gruos" in card            # the actual reading to judge
    assert "CER" in card                              # measured disagreement
    assert "Noch keine Stimmen" in card


# ── persistence: custom_ids unchanged so #150 rebuild still binds ────────────

def test_button_custom_ids_keep_the_gate2_pattern():
    from persistent_views import parse_custom_id
    view = _build(_state("doc-x"))
    for b in view.children:
        parsed = parse_custom_id(b.custom_id)
        assert parsed is not None
        doc, gate, field = parsed
        assert doc == "doc-x" and gate == "gate2" and field in PATHS
