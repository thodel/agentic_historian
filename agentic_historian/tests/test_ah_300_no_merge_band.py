"""#300: above the agreement threshold, select the best reading — never blend.

Majority-voting assumes engines make independent errors around a shared signal.
When they genuinely disagree there is no shared signal to recover, so the vote
returns noise. Measured on tei 2026-07-16 (BAT_664, 70.19% pairwise CER): TrOCR
read real Early New High German and the *fused* text was that reading with its good
parts voted out by three garbage candidates — worse than the best single input.

Offline — no engines, no I/O. Run from the repo root:
    pytest agentic_historian/tests/test_ah_300_no_merge_band.py
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a.ensemble import (ModelPick, recognize_ensemble,  # noqa: E402
                              select_best)

# ── the real BAT_664 candidates, verbatim from the tei trace (#298) ──────────

TROCR_GOOD = ("Vnser fründlich grus vor liebe getrune von der stösse wyse so daß "
              "nit zwüschent hemin , fast vnd Cuͦnratoͤffen , darumb and die selbe")
KRAKEN_CATMUS = ("duser feunilite grus vor liebe gerrmreuon de scosse roepse di "
                 "fuitsriousthont homn vast uud emraro oigon darmus ue bee selbe")
KRAKEN_MCCATMUS = ("aInfer fommlurg deuis For lb grninv (Dan de Rof) avar Coda Gnit "
                   "(Lionfgon Gemin (Vaft and Coivate auFen) Daommn6 aur Dle Pelle")
VLM_POOR = ("Infer fremdlichs grüe vor liebe gerninre Von der koffe wer So da mit "
            "zwisstient hönn kraft und Eimrath neffon Darmmb und die sellic")

BAT_TEXTS = {
    "dh-unibe/trocr-kurrent-XVI-XVII": TROCR_GOOD,
    "10.5281/zenodo.7516057": KRAKEN_CATMUS,
    "10.5281/zenodo.6542744": KRAKEN_MCCATMUS,
    "internvl3-8b-instruct": VLM_POOR,
}

# The plan as it really came out (blind, Phase 1) — note the VLM's 1.0, which is a
# placeholder from plan_models ("always run VLM first"), NOT a match score.
BAT_PICKS = [
    ModelPick("vlm", "internvl3-8b-instruct", 1.00),
    ModelPick("kraken", "10.5281/zenodo.7516057", 0.05),
    ModelPick("trocr", "dh-unibe/trocr-kurrent-XVI-XVII", 0.20),
    ModelPick("kraken", "10.5281/zenodo.6542744", 0.05),
]


def _rec_fn(texts, fail=()):
    def fn(pick, image):
        if pick.model_id in fail:
            return None
        return {"engine": pick.engine, "model_id": pick.model_id,
                "text": texts.get(pick.model_id, ""), "error": "",
                "confidence": 0.5}
    return fn


# ── the regression, against the real trace ───────────────────────────────────

def test_bat664_returns_the_trocr_reading_not_a_blend():
    """The #298 evidence, as a test: 4 candidates at ~70% CER, one of them good."""
    r = recognize_ensemble("img", None, _rec_fn(BAT_TEXTS), picks=BAT_PICKS,
                           min_engines=4, max_loops=0, no_merge_cer=0.35)

    assert r.max_pairwise_cer > 0.35
    assert r.text == TROCR_GOOD                 # byte-identical to the best engine
    assert "fründlich grus" in r.text           # the words the vote destroyed
    assert "no-merge" in r.provenance[0]


def test_the_vlm_placeholder_score_does_not_win():
    """plan_models gives the VLM a hardcoded 1.0 meaning "always run it", not "best
    match". Ranking on it naively would select the VLM every time — and the VLM is
    the engine that collapsed into "uuuu" on u-17__."""
    r = recognize_ensemble("img", None, _rec_fn(BAT_TEXTS), picks=BAT_PICKS,
                           min_engines=4, max_loops=0, no_merge_cer=0.35)
    assert r.text != VLM_POOR


def test_selection_is_by_match_score_not_length():
    """The garbage candidates are the LONG ones — length is not quality."""
    long_garbage = "xxx " * 200
    texts = {"t0": "kurz aber richtig gelesen", "k0": long_garbage}
    picks = [ModelPick("trocr", "t0", 0.80), ModelPick("kraken", "k0", 0.05)]

    r = recognize_ensemble("img", None, _rec_fn(texts), picks=picks,
                           min_engines=2, max_loops=0, no_merge_cer=0.35)
    assert r.text == "kurz aber richtig gelesen"


def test_below_the_band_still_merges():
    """Consensus is real below the threshold — merging helps there, keep it."""
    agree = "Wir Hans von Wiler tuend kund allen die disen brief ansehent"
    texts = {"vlm": agree, "k0": agree, "t0": agree}
    picks = [ModelPick("vlm", "vlm", 1.0), ModelPick("kraken", "k0", 0.8),
             ModelPick("trocr", "t0", 0.4)]

    r = recognize_ensemble("img", None, _rec_fn(texts), picks=picks,
                           min_engines=3, max_loops=0, no_merge_cer=0.35)
    assert r.max_pairwise_cer <= 0.35
    assert r.text.startswith("Wir Hans von Wiler")
    assert not r.provenance or "no-merge" not in str(r.provenance[0])


# ── select_best in isolation ─────────────────────────────────────────────────

def test_errored_and_empty_candidates_are_not_eligible():
    recs = [{"text": "", "error": "gateway 502", "confidence": 0.0},
            {"text": "   ", "error": "", "confidence": 0.9},
            {"text": "die einzige echte lesart", "error": "", "confidence": 0.4}]
    picks = [ModelPick("kraken", "k0", 0.9), ModelPick("kraken", "k1", 0.9),
             ModelPick("trocr", "t0", 0.2)]

    rec, pick = select_best(recs, picks)
    assert rec["text"] == "die einzige echte lesart"   # low score, but the only text


def test_confidence_breaks_a_score_tie():
    recs = [{"text": "lesart A", "error": "", "confidence": 0.3},
            {"text": "lesart B", "error": "", "confidence": 0.9}]
    picks = [ModelPick("trocr", "t0", 0.20), ModelPick("trocr", "t1", 0.20)]

    rec, _ = select_best(recs, picks)
    assert rec["text"] == "lesart B"


def test_vlm_wins_only_when_it_is_the_only_reading():
    recs = [{"text": "nur der VLM hat gelesen", "error": "", "confidence": 0.5},
            {"text": "", "error": "502", "confidence": 0.0}]
    picks = [ModelPick("vlm", "internvl3", 1.0), ModelPick("kraken", "k0", 0.9)]

    rec, pick = select_best(recs, picks)
    assert pick.engine == "vlm"


def test_no_eligible_candidate_returns_none():
    recs = [{"text": "", "error": "502", "confidence": 0.0}]
    assert select_best(recs, [ModelPick("kraken", "k0", 0.9)]) is None


def test_provenance_states_the_cer_that_triggered_selection():
    r = recognize_ensemble("img", None, _rec_fn(BAT_TEXTS), picks=BAT_PICKS,
                           min_engines=4, max_loops=0, no_merge_cer=0.35)
    prov = r.provenance[0]
    assert "CER" in prov and "35" in prov
    assert "trocr" in prov and "not blended" in prov


def test_threshold_comes_from_config(monkeypatch):
    import config
    monkeypatch.setattr(config, "ENSEMBLE_NO_MERGE_CER", 0.99)   # effectively never

    r = recognize_ensemble("img", None, _rec_fn(BAT_TEXTS), picks=BAT_PICKS,
                           min_engines=4, max_loops=0)           # no explicit arg
    assert "no-merge" not in str(r.provenance)                   # merged instead


def test_a_single_candidate_is_never_a_no_merge_decision():
    texts = {"t0": "die einzige lesart"}
    r = recognize_ensemble("img", None, _rec_fn(texts),
                           picks=[ModelPick("trocr", "t0", 0.8)],
                           min_engines=1, max_loops=0, no_merge_cer=0.35)
    assert r.text == "die einzige lesart"


# ── the same guard in fusion.fuse (the direct-caller safety net) ─────────────

def test_fuse_refuses_to_blend_above_the_band():
    """run_full_pipeline's Phase 3 calls fuse() directly under
    ENABLE_MULTI_ENGINE_FUSION. Without this guard, flipping that flag on brings
    the BAT_664 behaviour straight back."""
    from fusion import fuse

    recs = [
        {"engine": "trocr", "text": TROCR_GOOD, "error": "", "confidence": 0.95},
        {"engine": "kraken", "text": KRAKEN_CATMUS, "error": "", "confidence": 0.30},
        {"engine": "vlm", "text": VLM_POOR, "error": "", "confidence": 0.40},
    ]
    fr = fuse(recs, no_merge_cer=0.35)

    assert fr.text == TROCR_GOOD                 # verbatim, not blended
    assert "no-merge" in fr.strategy
    assert "fründlich grus" in fr.text


def test_fuse_still_merges_below_the_band():
    from fusion import fuse
    agree = "Wir Hans von Wiler tuend kund allen die disen brief ansehent"
    recs = [{"engine": "trocr", "text": agree, "error": "", "confidence": 0.9},
            {"engine": "kraken", "text": agree, "error": "", "confidence": 0.8}]

    fr = fuse(recs, no_merge_cer=0.35)
    assert "no-merge" not in fr.strategy
    assert fr.text.startswith("Wir Hans von Wiler")


def test_fuse_single_candidate_is_unchanged():
    from fusion import fuse
    fr = fuse([{"engine": "trocr", "text": "eine lesart", "error": "", "confidence": 0.9}])
    assert fr.text == "eine lesart" and "no-merge" not in fr.strategy
