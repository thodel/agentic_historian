"""#335: engine strength from historian preferences (Bradley–Terry) → routing prior.

The learning signal the project has never had: which engine a historian actually
prefers for a given script/century, derived from pairwise comparisons and **no
reference text** (#326).

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_335_engine_strength.py
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config          # noqa: E402
from agent_a import preference_strength as ps  # noqa: E402
from preferences import PreferenceEvent  # noqa: E402

BUCKET = ("kurrent", 16, "de")

# local (registry) ids — what the model selector actually uses
KURRENT_LOCAL = "dh-unibe/trocr-kurrent-XVI-XVII"
ESCRIPT_LOCAL = "dh-unibe/trocr-medieval-escriptmask"
KRAKEN_LOCAL = "10.5281/zenodo.15030337"


def _offered(*specs):
    """specs = (engine, gateway_id, local_id)"""
    return [{"engine": e, "model_id": g, "local_model_id": l} for e, g, l in specs]


ALL_THREE = _offered(
    ("trocr", "trocr-kurrent-xvi-xvii", KURRENT_LOCAL),
    ("trocr", "trocr-medieval-escriptmask", ESCRIPT_LOCAL),
    ("kraken", "kraken-early_modern_german", KRAKEN_LOCAL),
)


def _ev(chosen_gateway, offered=None, *, combined=False, rejected=False,
        script="kurrent", century=16, lang="de"):
    return PreferenceEvent(
        doc_id="d", page="p.jpg", offered=offered if offered is not None else ALL_THREE,
        chosen=list(chosen_gateway), combined=combined, rejected=rejected,
        criteria={"script": script, "century": century, "lang": lang},
    )


CHOOSE_ESCRIPT = "trocr/trocr-medieval-escriptmask"
CHOOSE_KURRENT = "trocr/trocr-kurrent-xvi-xvii"


# ── the ranking ──────────────────────────────────────────────────────────────

def test_a_consistently_preferred_engine_ranks_first():
    events = [_ev([CHOOSE_ESCRIPT]) for _ in range(10)]
    info = ps.compute_strengths(events)[BUCKET]

    assert info["sufficient"] is True
    ranked = sorted(info["models"].values(), key=lambda s: s.strength, reverse=True)
    assert ranked[0].model_id == ESCRIPT_LOCAL


def test_strengths_are_keyed_by_the_LOCAL_id_not_the_gateway_id():
    """The prior matches registry models. A kraken DOI shares no substring with its
    gateway id, so aggregating on the gateway id would make the prior permanently inert."""
    events = [_ev([CHOOSE_ESCRIPT]) for _ in range(10)]
    models = ps.compute_strengths(events)[BUCKET]["models"]

    assert ESCRIPT_LOCAL in models and KRAKEN_LOCAL in models
    assert "trocr/trocr-medieval-escriptmask" not in models     # not the display key


# ── the property a naive win rate gets wrong ─────────────────────────────────

def test_a_rarely_offered_but_usually_chosen_engine_outranks_a_frequently_offered_loser():
    """`routing_prior`'s legacy rate is wins / ALL entries in the bucket, so a
    specialist offered rarely scores low however often it wins. Bradley–Terry
    accounts for who each model faced, so being offered rarely costs nothing —
    and the ensemble offers specialists rarely by construction."""
    pair_specialist = _offered(
        ("trocr", "trocr-medieval-escriptmask", ESCRIPT_LOCAL),
        ("kraken", "kraken-early_modern_german", KRAKEN_LOCAL),
    )
    pair_generalist = _offered(
        ("trocr", "trocr-kurrent-xvi-xvii", KURRENT_LOCAL),
        ("kraken", "kraken-early_modern_german", KRAKEN_LOCAL),
    )
    events = (
        # specialist: offered 4x, chosen every time
        [_ev([CHOOSE_ESCRIPT], pair_specialist) for _ in range(4)]
        # generalist: offered 16x, chosen only 4x
        + [_ev([CHOOSE_KURRENT], pair_generalist) for _ in range(4)]
        + [_ev(["kraken/kraken-early_modern_german"], pair_generalist) for _ in range(12)]
    )
    models = ps.compute_strengths(events)[BUCKET]["models"]
    specialist, generalist = models[ESCRIPT_LOCAL], models[KURRENT_LOCAL]

    # Both won exactly 4 times, so a win COUNT cannot tell them apart at all, and
    # the legacy rate (wins / all bucket entries) ties them too. Bradley–Terry
    # separates them decisively, because the specialist won every comparison it
    # entered while the generalist lost three quarters of its own.
    assert specialist.wins == generalist.wins
    assert specialist.comparisons < generalist.comparisons
    assert specialist.strength > generalist.strength * 10


# ── weighting and exclusions ─────────────────────────────────────────────────

def test_a_combine_counts_less_than_a_single_pick():
    """"Both are usable" is a weaker statement than "this beats that"."""
    single = ps.compute_strengths([_ev([CHOOSE_ESCRIPT]) for _ in range(10)],
                                  min_comparisons=0.0)[BUCKET]
    combined = ps.compute_strengths(
        [_ev([CHOOSE_ESCRIPT, CHOOSE_KURRENT], combined=True) for _ in range(10)],
        min_comparisons=0.0)[BUCKET]

    assert combined["comparisons"] < single["comparisons"] * 2   # weighted down
    assert ps.COMBINE_WEIGHT < 1.0


def test_a_rejection_contributes_no_comparison():
    """"None of these is usable" is a statement about the pool, not a preference
    between its members (#333)."""
    events = [_ev([], rejected=True) for _ in range(10)]
    assert ps.comparisons_from(events) == {}


# ── sufficiency ──────────────────────────────────────────────────────────────

def test_a_thin_bucket_reports_insufficient_data_not_a_number():
    """A confident-looking strength from three clicks would steer selection on
    nothing — worse than no prior at all."""
    info = ps.compute_strengths([_ev([CHOOSE_ESCRIPT])])[BUCKET]
    assert info["sufficient"] is False and info["models"] == {}


def test_a_thin_bucket_is_visible_not_silently_absent():
    info = ps.compute_strengths([_ev([CHOOSE_ESCRIPT])])
    assert BUCKET in info                       # distinguishable from an unseen bucket


def test_no_prior_is_published_for_a_thin_bucket():
    assert ps.prior_scores([_ev([CHOOSE_ESCRIPT])]) == {}


# ── the prior: nudge, never override ─────────────────────────────────────────

def test_the_prior_is_capped_at_015():
    events = [_ev([CHOOSE_ESCRIPT]) for _ in range(30)]
    scores = ps.prior_scores(events)[BUCKET]
    assert all(0.0 < v <= 0.15 for v in scores.values())


def test_the_prior_cannot_flip_a_strong_criteria_match():
    """A script match contributes 0.4; the cap is 0.15, so a promoted weak model
    (0.05 + 0.15 = 0.20) still cannot pass it."""
    events = [_ev([CHOOSE_ESCRIPT]) for _ in range(30)]
    best_nudge = max(ps.prior_scores(events)[BUCKET].values())
    weak_model_with_prior = 0.05 + best_nudge
    script_match = 0.4
    assert weak_model_with_prior < script_match


def test_only_above_average_engines_are_promoted():
    """Nothing is demoted on this evidence — a losing engine simply gets no nudge."""
    events = [_ev([CHOOSE_ESCRIPT]) for _ in range(30)]
    scores = ps.prior_scores(events)[BUCKET]
    assert ESCRIPT_LOCAL in scores
    assert KRAKEN_LOCAL not in scores           # never chosen → no nudge, no penalty


# ── integration with routing_prior + the flag ────────────────────────────────

def test_get_prior_uses_the_preference_strengths(monkeypatch, tmp_path):
    import agent_a.routing_prior as rp
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path)
    monkeypatch.setattr(rp, "get_routing_prior", lambda: {})          # no legacy data
    monkeypatch.setattr(ps, "prior_scores",
                        lambda *a, **k: {BUCKET: {ESCRIPT_LOCAL: 0.12}})

    out = rp.get_prior("Kurrent", 16, "de", [ESCRIPT_LOCAL, KRAKEN_LOCAL])
    assert out[ESCRIPT_LOCAL] == 0.12 and out[KRAKEN_LOCAL] == 0.0


def test_a_broken_preference_store_never_breaks_selection(monkeypatch, tmp_path):
    import agent_a.routing_prior as rp
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(rp, "get_routing_prior", lambda: {})
    monkeypatch.setattr(ps, "prior_scores",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    out = rp.get_prior("Kurrent", 16, "de", [ESCRIPT_LOCAL])   # must not raise
    assert not any(out.values())                                # and applies no prior


def test_flag_off_leaves_selection_untouched(monkeypatch):
    """ENABLE_ROUTING_PRIOR=false → byte-identical scoring, whatever the data."""
    from agent_a.model_selector import SourceCriteria, select_kraken_model
    monkeypatch.setattr(config, "ENABLE_ROUTING_PRIOR", False)
    monkeypatch.setattr(ps, "prior_scores",
                        lambda *a, **k: {BUCKET: {KRAKEN_LOCAL: 0.15}})

    matches = select_kraken_model(
        SourceCriteria(script="kurrent", century=16, lang="de"), top_k=5)
    assert all("prior" not in m.matched_on for m in matches)
