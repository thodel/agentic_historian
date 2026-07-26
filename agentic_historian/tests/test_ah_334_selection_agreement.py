"""#334: did the automatic selection pick what the historian picked?

This measures the SELECTOR (#300 ranks by model-match score, a prior on
fit-to-source, not output quality — live on BAT_664 the 0.20-scored engine read
better than the 0.80 one). Both sides are candidates we produced, so no reference
text is involved and nothing here claims accuracy.

`regret_cer` is the distance between two candidate texts — bounded by the pool,
never an accuracy score (#326/#336).

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_334_selection_agreement.py
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config          # noqa: E402
import routing_report  # noqa: E402
from preferences import PreferenceEvent  # noqa: E402
from runstate import RunState  # noqa: E402

AUTO = "trocr/trocr-kurrent-xvi-xvii"
HUMAN = "trocr/trocr-medieval-escriptmask"
THIRD = "kraken/kraken-catmus_medieval"

AUTO_TEXT = "Vnser fründlich grus vor liebe getrune von der stösse wyse so daß nit"
HUMAN_TEXT = "unser frùntlich gruͦs vor liebe getrüwe von der stoͤsse wegē so da sint"


def _ev(chosen, auto=AUTO, *, page="p1.jpg", doc_id="d", script="kurrent",
        century=16, lang="de", combined=False):
    return PreferenceEvent(
        doc_id=doc_id, page=page,
        offered=[{"engine": "trocr", "model_id": "trocr-kurrent-xvi-xvii", "auto_rank": 1},
                 {"engine": "trocr", "model_id": "trocr-medieval-escriptmask", "auto_rank": 2},
                 {"engine": "kraken", "model_id": "kraken-catmus_medieval", "auto_rank": 3}],
        chosen=chosen, combined=combined, auto_pick=auto,
        criteria={"script": script, "century": century, "lang": lang},
    )


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path)
    return tmp_path


# ── agreement ────────────────────────────────────────────────────────────────

def test_agreement_when_the_auto_pick_is_what_the_human_chose():
    out = routing_report.compute_selection_agreement([_ev([AUTO])])
    assert out["overall"] == {"decided": 1, "agreed": 1, "rate": 1.0}


def test_disagreement_when_the_human_chose_otherwise():
    out = routing_report.compute_selection_agreement([_ev([HUMAN])])
    assert out["overall"]["agreed"] == 0 and out["overall"]["rate"] == 0.0


def test_a_combine_agrees_when_the_auto_pick_is_among_the_chosen():
    """The historian kept the auto pick — they just kept another one too."""
    out = routing_report.compute_selection_agreement(
        [_ev([AUTO, HUMAN], combined=True)])
    assert out["overall"]["agreed"] == 1


def test_a_combine_disagrees_when_the_auto_pick_was_dropped():
    out = routing_report.compute_selection_agreement(
        [_ev([HUMAN, THIRD], combined=True)])
    assert out["overall"]["agreed"] == 0


# ── what must NOT count ──────────────────────────────────────────────────────

def test_a_rejection_is_excluded_not_counted_as_disagreement():
    """Nothing chosen (Q-2) → there was no human pick to agree with. An undecided
    page is unknown, not bad."""
    out = routing_report.compute_selection_agreement([_ev([]), _ev([AUTO])])
    assert out["overall"]["decided"] == 1


def test_an_event_without_an_auto_pick_is_excluded():
    out = routing_report.compute_selection_agreement([_ev([HUMAN], auto="")])
    assert out["overall"]["decided"] == 0
    assert out["overall"]["rate"] is None


# ── where the selector fails ─────────────────────────────────────────────────

def test_the_worst_bucket_is_identifiable():
    events = [
        _ev([AUTO], script="kurrent", century=16),          # agrees
        _ev([AUTO], script="kurrent", century=16),          # agrees
        _ev([HUMAN], script="bastarda", century=15),        # fails
        _ev([HUMAN], script="bastarda", century=15),        # fails
    ]
    by_bucket = routing_report.compute_selection_agreement(events)["by_bucket"]
    assert by_bucket[("kurrent", 16, "de")]["rate"] == 1.0
    assert by_bucket[("bastarda", 15, "de")]["rate"] == 0.0


def test_failures_are_attributed_to_the_auto_engine():
    out = routing_report.compute_selection_agreement(
        [_ev([HUMAN], auto="kraken/kraken-catmus_medieval")])
    assert out["by_auto_engine"]["kraken"]["rate"] == 0.0


# ── regret: a distance between two candidates, never accuracy ────────────────

def _state_with_texts(doc_id="d", page="p1.jpg"):
    st = RunState(doc_id=doc_id)
    st.artifacts["paths"] = {f"{page}:{AUTO}": AUTO_TEXT, f"{page}:{HUMAN}": HUMAN_TEXT}
    st.save()
    return st


def test_regret_is_the_distance_between_the_two_picks():
    _state_with_texts()
    out = routing_report.compute_regret([_ev([HUMAN])])
    assert out["disagreements_measured"] == 1
    assert 0.0 < out["median_regret_cer"] < 1.0


def test_agreements_contribute_no_regret():
    _state_with_texts()
    out = routing_report.compute_regret([_ev([AUTO])])
    assert out["disagreements_measured"] == 0
    assert out["median_regret_cer"] is None


def test_missing_texts_degrade_to_unmeasurable_not_to_a_wrong_number():
    """The log holds no text by design (#332); when the RunState no longer has the
    candidates, regret is skipped — agreement still works."""
    out = routing_report.compute_regret([_ev([HUMAN], doc_id="never-existed")])
    assert out["disagreements_measured"] == 0
    assert out["disagreements_unmeasurable"] == 1
    assert out["median_regret_cer"] is None


def test_regret_reports_a_distribution_not_a_mean():
    _state_with_texts()
    out = routing_report.compute_regret([_ev([HUMAN]) for _ in range(3)])
    assert {"median_regret_cer", "p90_regret_cer", "max_regret_cer"} <= set(out)


# ── the report ───────────────────────────────────────────────────────────────

def test_the_report_states_agreement_and_names_regret_honestly():
    _state_with_texts()
    text = routing_report.format_selection_stats([_ev([HUMAN]), _ev([AUTO])])
    assert "1/2" in text or "50%" in text
    assert "regret_cer" in text
    assert "kein" in text.lower()               # explicitly NOT an accuracy measure


def test_the_report_is_honest_when_there_is_no_data():
    assert "noch keine" in routing_report.format_selection_stats([]).lower()


def test_regret_is_never_labelled_accuracy_or_reference():
    """The guard #334 asks for: this number must never be presented as accuracy —
    it is bounded by the candidate pool and both sides are our own output.

    Asserted on the OUTPUT and the returned keys, not by scanning the source for
    the word "accuracy": the source legitimately contains it in the disclaimer, so
    a substring scan there would be either vacuous or self-defeating.
    """
    _state_with_texts()
    report = routing_report.format_selection_stats([_ev([HUMAN])])
    keys = set(routing_report.compute_regret([_ev([HUMAN])]))

    # every reported number carries the regret_cer name, not a bare accuracy claim
    assert all("regret_cer" in k for k in keys if k.endswith("_cer"))
    assert "regret_cer" in report
    # and the report says out loud what it is not
    assert "kein" in report.lower() and "genauigkeitsmass" in report.lower()
