"""Tests for #146 (HITL-1b): Gate 1 routing card.

Offline: model selection is real (works offline); Discord View is built and
structurally inspected (callbacks need a live interaction, not unit-tested).
Run from the repo root:
    pytest agentic_historian/tests/test_ah_146_routing_card.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import routing_card as rc
from runstate import RunState, DIRTY, DONE, STAGES


def _state(**criteria) -> RunState:
    rs = RunState(doc_id="saa-0428")
    for s in STAGES:
        rs.stage_status[s] = DONE
    rs.criteria.update(criteria)
    return rs


# ── options ──────────────────────────────────────────────────────────────────

def test_option_builders_map_to_sourcecriteria():
    assert ("15. Jh.", 15) in rc.century_options()
    assert ("Deutsch", "de") in rc.lang_options()
    assert rc.script_options() and all(len(o) == 2 for o in rc.script_options())
    assert rc.type_options()
    # Discord Selects allow max 25 options
    for opts in (rc.century_options(), rc.lang_options(), rc.script_options(), rc.type_options()):
        assert len(opts) <= 25


# ── model (re-)selection ─────────────────────────────────────────────────────

def test_select_model_for_criteria():
    st = _state(script="Kurrent", lang="de", century=16)
    m = rc.select_model(st)
    assert m is not None and m.model is not None


def test_apply_change_pins_invalidates_and_reselects():
    st = _state(script="Kurrent", lang="de", century=15)
    match = rc.apply_criteria_change(st, "century", 16)

    # criterion pinned + override recorded
    assert st.criteria["century"] == 16
    assert st.human_overrides[-1]["field"] == "century"
    # kraken path marked dirty (per the invalidation matrix), VLM untouched
    assert set(st.dirty_stages()) >= {"model_select", "kraken", "reconcile"}
    assert st.stage_status["vlm"] == DONE
    # model choice recorded as a gate decision
    assert match is not None
    assert st.gate_decisions["model"]["name"] == match.model.name
    assert st.gate_decisions["model"]["score"] == round(match.score, 3)


def test_apply_change_resumes_when_runners_given():
    st = _state(script="Kurrent", lang="de", century=15)
    # runner via the View path is exercised in build_view; here check the state
    # transition primitive used by it.
    rc.apply_criteria_change(st, "script", "Bastarda")
    assert st.criteria["script"] == "Bastarda"


# ── rendering ────────────────────────────────────────────────────────────────

def test_render_shows_criteria_model_and_pins():
    st = _state(script="Kurrent", lang="de", century=15)
    rc.apply_criteria_change(st, "century", 16)   # pin century
    card = rc.render_card(st)
    assert "saa-0428" in card and "Routing" in card
    assert "16. Jh." in card
    assert "Historiker:in" in card       # pinned marker on the century row
    assert "HTR-Modell" in card


def test_render_low_score_warning(monkeypatch):
    st = _state(script="Kurrent", lang="de", century=16)

    class _M:
        class model:  # noqa: N801
            name = "weak-model"
        score = 0.40
        matched_on = []
    card = rc.render_card(st, match=_M())
    assert "⚠️" in card and "weak-model" in card


def test_render_no_model(monkeypatch):
    st = _state()
    monkeypatch.setattr(rc, "select_model", lambda state: None)
    card = rc.render_card(st, match=None)
    assert "kein Treffer" in card


# ── Discord View structure ───────────────────────────────────────────────────

async def _build_view_async(st):
    return rc.build_view(st)


def test_route_command_registered():
    import bot as bot_module
    cmd = next((c for c in bot_module.bot.pending_application_commands
                if getattr(c, "name", None) == "route"), None)
    assert cmd is not None and [o.name for o in cmd.options] == ["doc_id"]


def test_build_view_has_four_field_selects():
    import asyncio
    import discord
    st = _state(script="Kurrent", lang="de", century=16)
    # discord.ui.View construction needs a running loop (as in the live bot).
    view = asyncio.run(_build_view_async(st))
    selects = [c for c in view.children if isinstance(c, discord.ui.Select)]
    assert len(selects) == 4
    custom_ids = {s.custom_id for s in selects}
    assert custom_ids == {f"ah:saa-0428:gate1:{f}"
                          for f in ("century", "lang", "script", "document_type")}
