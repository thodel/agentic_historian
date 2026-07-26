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
        self.response = SimpleNamespace(edit_message=self._edit)

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
