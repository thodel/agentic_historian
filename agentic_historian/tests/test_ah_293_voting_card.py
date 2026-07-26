"""#313: the Gate-2 card is a multi-SELECT card — toggle one or more readings,
confirm to combine, then it collapses.

(Supersedes the #293 multi-vote/tally card: at high engine disagreement the
historian picks the reading(s) directly rather than a multi-voter tally deciding.)

Offline — RunState in tmp_path, the Discord interaction is a stub, callbacks driven
directly. Run from the repo root:
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
from runstate import RunState  # noqa: E402

PATHS = {
    "trocr-kurrent-xvi-xvii": "unser fruntlich gruos vor liebe getruwe von der stoesse",
    "vlm": "Infer fremdlichs grue vor liebe getrune von der koffe",
    "kraken-catmus_medieval": "duser feunilite grus vor liebe gerrmreuon de scosse",
}


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "AUTO_RESUME_AFTER_GATE", False)
    return tmp_path


class _Interaction:
    def __init__(self):
        self.user = SimpleNamespace(id=1, display_name="Anna", name="Anna")
        self.edited = []
        self.views = []
        self.response = SimpleNamespace(edit_message=self._edit)

    async def _edit(self, *, content=None, view=None):
        self.edited.append(content)
        self.views.append(view)


def _click(st, sequence):
    """Press buttons by custom_id field, in order, inside one event loop.
    `sequence` = list of field names ("<path>" or "__confirm__")."""
    async def go():
        out = []
        for field in sequence:
            view = path_compare.build_view(st, PATHS)     # rebuild to see current state
            btn = next(b for b in view.children if b.custom_id.endswith(":" + field))
            inter = _Interaction()
            await btn.callback(inter)
            out.append(inter)
        return out
    return asyncio.run(go())


def _state(doc_id="d-sel"):
    return RunState(doc_id=doc_id)


async def _abuild(st):
    return path_compare.build_view(st, PATHS)


# ── toggle selection ──────────────────────────────────────────────────────────

def test_a_toggle_selects_a_reading():
    st = _state()
    _click(st, ["vlm"])
    assert st.gate_decisions["gate2_selected"] == ["vlm"]


def test_toggling_twice_deselects():
    st = _state()
    _click(st, ["vlm", "vlm"])
    assert st.gate_decisions["gate2_selected"] == []


def test_several_readings_can_be_selected():
    st = _state()
    _click(st, ["trocr-kurrent-xvi-xvii", "vlm"])
    assert set(st.gate_decisions["gate2_selected"]) == {"trocr-kurrent-xvi-xvii", "vlm"}


# ── confirm: single vs combine ───────────────────────────────────────────────

def test_confirm_one_reading_uses_it_verbatim(monkeypatch):
    st = _state()
    inter = _click(st, ["trocr-kurrent-xvi-xvii", "__confirm__"])[-1]
    assert st.artifacts["reconcile"] == PATHS["trocr-kurrent-xvi-xvii"]
    assert inter.views[-1] is None                        # collapsed
    assert "gewählt" in inter.edited[-1]


def test_confirm_multiple_combines_them(monkeypatch):
    st = _state()
    inter = _click(st, ["trocr-kurrent-xvi-xvii", "kraken-catmus_medieval",
                        "__confirm__"])[-1]
    # both engines recorded as combined
    assert set(st.gate_decisions["gate2_combined"]) == {
        "trocr-kurrent-xvi-xvii", "kraken-catmus_medieval"}
    assert st.artifacts.get("reconcile")                  # a combined working text
    assert "kombiniert aus" in inter.edited[-1]


def test_confirm_clears_the_selection():
    st = _state()
    _click(st, ["vlm", "__confirm__"])
    assert st.gate_decisions.get("gate2_selected") == []


# ── the card shows what the historian needs ──────────────────────────────────

def test_card_shows_candidate_text_and_cer():
    st = _state()
    card = path_compare.render_vote_card(st, PATHS)
    assert "unser fruntlich gruos" in card                # the reading to judge
    assert "CER" in card                                  # measured disagreement
    assert "ausgewählt" in card.lower()                   # the selection footer


def test_selected_readings_are_checked_on_the_card():
    st = _state()
    st.gate_decisions["gate2_selected"] = ["vlm"]
    card = path_compare.render_vote_card(st, PATHS)
    assert "☑" in card and "☐" in card                   # one checked, others not


# ── persistence: custom_ids unchanged so #150 rebuild still binds ────────────

def test_button_custom_ids_keep_the_gate2_pattern():
    from persistent_views import parse_custom_id
    view = asyncio.run(_abuild(_state("doc-x")))
    fields = set()
    for b in view.children:
        parsed = parse_custom_id(b.custom_id)
        assert parsed is not None
        doc, gate, field = parsed
        assert doc == "doc-x" and gate == "gate2"
        fields.add(field)
    assert set(PATHS) <= fields and "__confirm__" in fields
