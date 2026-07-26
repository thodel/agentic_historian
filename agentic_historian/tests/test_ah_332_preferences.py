"""#332: a Gate-2 selection is recorded as a PREFERENCE, not a reference text.

The historian picks the closest of the options we produced. That is a comparison
(chosen ≻ the alternatives offered), never a claim that the text is correct — so
this log stores comparisons and provenance, and deliberately never stores the
chosen text (#326/#336).

Offline — no Discord, no engines. Run from the repo root:
    pytest agentic_historian/tests/test_ah_332_preferences.py
"""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config          # noqa: E402
import path_compare    # noqa: E402
import preferences     # noqa: E402
from runstate import RunState  # noqa: E402

PAGE = "BAT_664_r_00027.jpg"
ESCRIPT = f"{PAGE}:trocr/trocr-medieval-escriptmask"
KURRENT = f"{PAGE}:trocr/trocr-kurrent-xvi-xvii"
CATMUS = f"{PAGE}:kraken/kraken-catmus_medieval"

PATHS = {
    ESCRIPT: "unser frùntlich gruͦs vor liebe getrüwe von der stoͤsse",
    KURRENT: "Vnser fründlich grus vor liebe getrune von der stösse",
    CATMUS: "duser feunilite grus vor liebe gerrmreuon de scosse",
}


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path)
    monkeypatch.setattr(config, "PREFERENCES_LOG_PATH", tmp_path / "preferences.jsonl")
    monkeypatch.setattr(config, "PSEUDONYM_SALT_PATH", tmp_path / ".salt")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "AUTO_RESUME_AFTER_GATE", False)
    return tmp_path


def _state(doc_id="d-332"):
    """A RunState as the orchestrator leaves it after a no-merge (#313/#332)."""
    st = RunState(doc_id=doc_id)
    st.artifacts["paths"] = dict(PATHS)
    st.gate_decisions["gate2_auto"] = {PAGE: KURRENT}          # the selector's pick
    st.gate_decisions["gate2_context"] = {
        PAGE: {
            "ranks": {KURRENT: 1, ESCRIPT: 2, CATMUS: 3},
            "max_pairwise_cer": 1.059,
            "criteria": {"script": "kurrent", "century": 16, "lang": "de"},
        }
    }
    return st


def _events():
    return preferences.load_preferences()


# ── the event carries the comparison, not the text ───────────────────────────

def test_a_selection_records_offered_chosen_autopick_and_criteria():
    st = _state()
    preferences.record_selection(st, PATHS, [ESCRIPT], voter="1234567890")

    evs = _events()
    assert len(evs) == 1
    ev = evs[0]
    assert ev.doc_id == "d-332" and ev.page == PAGE
    assert ev.chosen == ["trocr/trocr-medieval-escriptmask"]
    assert {o["model_id"] for o in ev.offered} == {
        "trocr-medieval-escriptmask", "trocr-kurrent-xvi-xvii", "kraken-catmus_medieval"}
    assert ev.auto_pick == "trocr/trocr-kurrent-xvi-xvii"      # differs from chosen
    assert ev.criteria == {"script": "kurrent", "century": 16, "lang": "de"}
    assert ev.max_pairwise_cer == pytest.approx(1.059)
    assert ev.combined is False


def test_the_chosen_text_is_never_written_to_the_log(_tmp):
    """The whole point: this file holds comparisons, not a reference text."""
    st = _state()
    preferences.record_selection(st, PATHS, [ESCRIPT], voter="1")

    raw = (_tmp / "preferences.jsonl").read_text(encoding="utf-8")
    for text in PATHS.values():
        assert text not in raw
    assert "gruͦs" not in raw


def test_offered_carries_the_selectors_own_rank():
    st = _state()
    preferences.record_selection(st, PATHS, [ESCRIPT], voter="1")

    ranks = {o["model_id"]: o["auto_rank"] for o in _events()[0].offered}
    assert ranks["trocr-kurrent-xvi-xvii"] == 1                 # the auto pick ranks 1
    assert ranks["trocr-medieval-escriptmask"] == 2


# ── combine is a weaker signal and must be distinguishable ───────────────────

def test_a_combine_is_marked_and_lists_every_chosen():
    st = _state()
    preferences.record_selection(st, PATHS, [ESCRIPT, KURRENT], voter="1")

    ev = _events()[0]
    assert ev.combined is True
    assert set(ev.chosen) == {"trocr/trocr-medieval-escriptmask",
                              "trocr/trocr-kurrent-xvi-xvii"}


# ── the pairs derivation is what #335 consumes ───────────────────────────────

def test_pairs_derive_chosen_over_each_non_chosen():
    st = _state()
    preferences.record_selection(st, PATHS, [ESCRIPT], voter="1")

    pairs = preferences.pairs(_events()[0])
    assert set(pairs) == {
        ("trocr/trocr-medieval-escriptmask", "trocr/trocr-kurrent-xvi-xvii"),
        ("trocr/trocr-medieval-escriptmask", "kraken/kraken-catmus_medieval"),
    }


def test_a_combine_does_not_compare_the_chosen_against_each_other():
    """The historian expressed no preference *between* the ones they combined."""
    st = _state()
    preferences.record_selection(st, PATHS, [ESCRIPT, KURRENT], voter="1")

    pairs = preferences.pairs(_events()[0])
    losers = {l for _w, l in pairs}
    assert losers == {"kraken/kraken-catmus_medieval"}
    assert len(pairs) == 2                                     # 2 chosen × 1 loser


# ── one event per page: comparing across pages is meaningless ────────────────

def test_a_multipage_confirm_emits_one_event_per_page():
    p2 = "page2.jpg"
    paths = dict(PATHS)
    paths[f"{p2}:trocr/trocr-kurrent-xvi-xvii"] = "zweite seite lesart eins"
    paths[f"{p2}:kraken/kraken-mccatmus"] = "zweite seite lesart zwei"

    st = _state()
    st.artifacts["paths"] = paths
    preferences.record_selection(
        st, paths, [ESCRIPT, f"{p2}:trocr/trocr-kurrent-xvi-xvii"], voter="1")

    evs = _events()
    assert {e.page for e in evs} == {PAGE, p2}
    for e in evs:
        # every offered candidate in an event belongs to that event's page
        assert all(o["model_id"] for o in e.offered)
        assert not e.combined                                   # one pick per page


# ── privacy ──────────────────────────────────────────────────────────────────

def test_the_raw_discord_id_is_never_persisted(_tmp):
    st = _state()
    preferences.record_selection(st, PATHS, [ESCRIPT], voter="1234567890")

    raw = (_tmp / "preferences.jsonl").read_text(encoding="utf-8")
    assert "1234567890" not in raw
    assert _events()[0].voter != "1234567890"


def test_the_pseudonym_is_stable_and_salted(_tmp):
    a = preferences.pseudonym("1234567890")
    b = preferences.pseudonym("1234567890")
    assert a == b and a != "1234567890"                        # stable, not the id

    import hashlib
    unsalted = hashlib.sha256(b"1234567890").hexdigest()[:16]
    assert a != unsalted, "must be salted — a bare hash of a Discord id is reversible"


def test_different_voters_get_different_pseudonyms():
    assert preferences.pseudonym("1") != preferences.pseudonym("2")


# ── robustness ───────────────────────────────────────────────────────────────

def test_a_corrupt_line_is_skipped_not_fatal(_tmp):
    st = _state()
    preferences.record_selection(st, PATHS, [ESCRIPT], voter="1")
    with (_tmp / "preferences.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    assert len(_events()) == 1


def test_missing_log_returns_nothing():
    assert preferences.load_preferences() == []


def test_recording_never_raises_on_a_broken_state():
    """Observation must never break the historian's click (#313 lesson)."""
    assert preferences.record_selection(object(), PATHS, [ESCRIPT], voter="1") == []


def test_a_selection_with_no_recorded_context_still_records():
    """A no-merge without gate2_context (older run) degrades, doesn't fail."""
    st = RunState(doc_id="d-nc")
    preferences.record_selection(st, PATHS, [ESCRIPT], voter="1")
    ev = _events()[0]
    assert ev.chosen and ev.criteria == {} and ev.max_pairwise_cer is None


# ── wired into the Gate-2 confirm ────────────────────────────────────────────

class _Interaction:
    def __init__(self):
        self.user = SimpleNamespace(id=999, display_name="Anna", name="Anna")
        self.edited, self.views = [], []
        self.response = SimpleNamespace(edit_message=self._edit)

    async def _edit(self, *, content=None, view=None):
        self.edited.append(content)
        self.views.append(view)


def test_confirming_on_the_card_writes_a_preference_event():
    async def go():
        st = _state()
        st.gate_decisions["gate2_selected"] = [ESCRIPT]
        view = path_compare.build_view(st, PATHS)
        btn = next(b for b in view.children if b.custom_id.endswith(":__confirm__"))
        await btn.callback(_Interaction())

    asyncio.run(go())

    evs = _events()
    assert len(evs) == 1
    assert evs[0].chosen == ["trocr/trocr-medieval-escriptmask"]
    assert evs[0].voter not in ("999", "")                     # pseudonymised
