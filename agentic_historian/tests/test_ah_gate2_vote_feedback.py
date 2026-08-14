"""A Gate-2 selection click must give visible feedback and the card must collapse
after confirm (#313 live fixes).

Two live findings, both fixed here:
1. A confirm that decides ran the B/C re-run SYNCHRONOUSLY before editing the
   message — past Discord's ~3s interaction window, so the token expired, the edit
   failed, and the click looked inert. The card must be ACKed/updated first and the
   re-run offloaded.
2. The card stayed visible after selection — it must collapse (buttons removed) on
   confirm.

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
from runstate import RunState  # noqa: E402

PATHS = {
    "p:trocr/escriptmask": "unser frùntlich gruͦs vor liebe getrüwe",
    "p:trocr/kurrent": "Vnser fründlich grus vor liebe getrune",
}


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


class _Interaction:
    def __init__(self):
        self.user = SimpleNamespace(id=1, display_name="Anna", name="Anna")
        self.edited = []
        self.views = []
        # Model the real interaction lifecycle: a callback defers (marking the
        # response done) and then edits via edit_original_response. A mock that
        # only offers edit_message cannot catch a callback responding too late,
        # which is exactly the 10062 "Unknown interaction" seen live on tei.
        self._done = False
        self.response = SimpleNamespace(
            edit_message=self._edit,
            defer=self._defer,
            is_done=lambda: self._done,
        )

    async def _defer(self):
        self._done = True

    async def edit_original_response(self, *, content=None, view=None):
        await self._edit(content=content, view=view)

    async def _edit(self, *, content=None, view=None):
        self.edited.append(content)
        self.views.append(view)


def _btn(state, field, runners=None):
    view = path_compare.build_view(state, PATHS, runners=runners)
    return next(b for b in view.children if b.custom_id.endswith(":" + field))


# ── a toggle gives immediate visible feedback ────────────────────────────────

def test_a_toggle_updates_the_card():
    async def go():
        st = RunState(doc_id="d-fb")
        inter = _Interaction()
        await _btn(st, "p:trocr/escriptmask").callback(inter)
        return st, inter

    st, inter = asyncio.run(go())
    assert inter.edited, "toggle produced no visible update"
    assert "☑" in inter.edited[-1]                       # the toggled item is checked
    assert st.gate_decisions["gate2_selected"] == ["p:trocr/escriptmask"]


# ── confirm collapses the card and applies, ack-first ────────────────────────

def test_confirm_collapses_the_card_and_combines(monkeypatch):
    monkeypatch.setattr(config, "AUTO_RESUME_AFTER_GATE", True)
    order = []
    monkeypatch.setattr(RunState, "resume",
                        lambda self, runners, **k: order.append("resume"))

    async def go():
        st = RunState(doc_id="d-fb")
        await _btn(st, "p:trocr/escriptmask").callback(_Interaction())
        await _btn(st, "p:trocr/kurrent").callback(_Interaction())
        inter = _Interaction()
        await _btn(st, "__confirm__", runners={"stub": object()}).callback(inter)
        order.append("ack:" + str(bool(inter.edited)))
        for _ in range(100):               # wait for the offloaded resume (≤2s)
            if "resume" in order:
                break
            await asyncio.sleep(0.02)
        return st, inter

    st, inter = asyncio.run(go())

    assert inter.views[-1] is None                       # card collapsed — buttons gone
    assert "kombiniert aus" in inter.edited[-1]          # combined the two readings
    assert st.artifacts.get("reconcile")                 # working transcription set
    assert st.gate_decisions.get("gate2_combined")       # provenance recorded
    assert order[0].startswith("ack:True")               # acked before resume ran
    assert "resume" in order                             # resume still ran (off-loop)


def test_confirm_with_nothing_selected_is_harmless():
    async def go():
        st = RunState(doc_id="d-fb2")
        inter = _Interaction()
        await _btn(st, "__confirm__").callback(inter)     # no selection
        return st, inter

    st, inter = asyncio.run(go())
    assert inter.views[-1] is None
    assert "abgebrochen" in inter.edited[-1]
    assert not st.artifacts.get("reconcile")


def test_a_failing_confirm_still_acks(monkeypatch):
    monkeypatch.setattr(config, "AUTO_RESUME_AFTER_GATE", False)
    monkeypatch.setattr(path_compare, "apply_combined_choice",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    async def go():
        st = RunState(doc_id="d-fb3")
        st.gate_decisions["gate2_selected"] = ["p:trocr/escriptmask"]
        inter = _Interaction()
        await _btn(st, "__confirm__").callback(inter)     # must not raise
        return inter

    inter = asyncio.run(go())
    assert inter.edited, "confirm must ack even when apply fails"


# ── the 3-second budget: ack BEFORE the work (live 10062 on tei) ─────────────

class _OrderTrackingInteraction(_Interaction):
    """Records the order of defer vs. the callback's side effects."""
    def __init__(self):
        super().__init__()
        self.events = []

    async def _defer(self):
        self.events.append("defer")
        await super()._defer()

    async def _edit(self, *, content=None, view=None):
        self.events.append("edit")
        await super()._edit(content=content, view=view)


def _first_event(field, state=None):
    async def go():
        st = state or RunState(doc_id="d-ack")
        inter = _OrderTrackingInteraction()
        await _btn(st, field).callback(inter)
        return inter.events
    return asyncio.run(go())


def test_a_reject_acks_before_recording():
    """Live on tei the reject WAS recorded ("0 chosen of 9 offered") and the card
    update still died with 10062 Unknown interaction — Discord kills the token 3s
    after the click, and all the work sat inside that budget. The historian saw
    "This component is no longer valid" for a click that had in fact been recorded,
    and the natural response is to click again and double-record."""
    assert _first_event(path_compare._REJECT_FIELD)[0] == "defer"


def test_a_toggle_acks_before_re_rendering():
    assert _first_event(list(PATHS)[0])[0] == "defer"


def test_a_confirm_acks_before_applying():
    """Confirm does the heaviest work of the three (fuse + record + resume)."""
    st = RunState(doc_id="d-ack-confirm")
    st.gate_decisions["gate2_selected"] = [list(PATHS)[0]]
    assert _first_event(path_compare._CONFIRM_FIELD, st)[0] == "defer"


def test_the_card_is_still_updated_after_deferring():
    """Deferring must not cost the visible update — it moves to
    edit_original_response, which the historian sees identically."""
    async def go():
        st = RunState(doc_id="d-ack-visible")
        inter = _OrderTrackingInteraction()
        await _btn(st, list(PATHS)[0]).callback(inter)
        return inter
    inter = asyncio.run(go())
    assert "edit" in inter.events and inter.edited, "no visible update after defer"


# ── #368: the click must be visibly acknowledged BEFORE the work ─────────────

class _OrderedInteraction(_OrderTrackingInteraction):
    """Records each edit's content so the pending repaint is identifiable."""
    async def _edit(self, *, content=None, view=None):
        self.events.append(f"edit:{'pending' if (content or '').startswith('⏳') else 'final'}")
        self.edited.append(content)
        self.views.append(view)


def _sequence(field, state=None):
    async def go():
        st = state or RunState(doc_id="d-368")
        inter = _OrderedInteraction()
        await _btn(st, field).callback(inter)
        return inter
    return asyncio.run(go())


def test_a_confirm_repaints_before_applying():
    """#353 made the ack invisible (a component defer shows nothing) while the
    visible change still waited on apply_combined_choice — so the click looked like
    it had done nothing, and a second click double-records a preference (#332)."""
    st = RunState(doc_id="d-368-confirm")
    st.gate_decisions["gate2_selected"] = [list(PATHS)[0]]
    inter = _sequence(path_compare._CONFIRM_FIELD, st)

    assert inter.events[0] == "defer"
    assert inter.events[1] == "edit:pending"


def test_a_reject_repaints_before_recording():
    inter = _sequence(path_compare._REJECT_FIELD)
    assert inter.events[:2] == ["defer", "edit:pending"]


def test_the_pending_card_removes_the_buttons():
    """A disabled card cannot be submitted twice — that matters more than the
    reassurance itself."""
    st = RunState(doc_id="d-368-view")
    st.gate_decisions["gate2_selected"] = [list(PATHS)[0]]
    inter = _sequence(path_compare._CONFIRM_FIELD, st)
    assert inter.views[0] is None


def test_the_final_card_still_replaces_the_pending_one():
    st = RunState(doc_id="d-368-final")
    st.gate_decisions["gate2_selected"] = [list(PATHS)[0]]
    inter = _sequence(path_compare._CONFIRM_FIELD, st)

    assert inter.events[-1] == "edit:final"
    assert not (inter.edited[-1] or "").startswith("⏳")


def test_a_failing_confirm_still_reaches_a_terminal_card(monkeypatch):
    """A card stuck on "wird angewendet" is worse than one showing a bad outcome."""
    monkeypatch.setattr(path_compare, "apply_combined_choice",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    st = RunState(doc_id="d-368-boom")
    st.gate_decisions["gate2_selected"] = [list(PATHS)[0]]
    inter = _sequence(path_compare._CONFIRM_FIELD, st)
    assert inter.events[-1] == "edit:final"


def test_the_pending_card_names_what_is_being_applied():
    """"wird angewendet" with no subject leaves the historian unsure which of ten
    candidates was taken."""
    st = RunState(doc_id="d-368-name")
    st.gate_decisions["gate2_selected"] = [list(PATHS)[0]]
    inter = _sequence(path_compare._CONFIRM_FIELD, st)
    assert path_compare._card_label(list(PATHS)[0], show_page=False) in inter.edited[0]
