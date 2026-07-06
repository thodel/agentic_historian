"""Script-aware kraken selection (#191 follow-up).

A kraken model trained on the wrong script produces garbage even when language
and century agree, so a correct-script model must outrank a wrong-script one —
and a known-script mismatch is penalised, not merely un-rewarded. When the
script is unknown, scoring is unchanged.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_script_aware_selection.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a import model_selector as ms
from agent_a.model_selector import score_model, SourceCriteria
from agent_a.models import KrakenModel


def _m(mid, script, lang, centuries):
    return KrakenModel(model_id=mid, name=mid, lang=lang, script=script, centuries=centuries)


def test_script_match_outranks_wrong_script_with_lang_century():
    crit = dict(script="Kurrent", lang="de", century=16)          # cursive doc
    kurrent = score_model(_m("kraken-kurrent", "Kurrent", "de", [16, 17]), **crit)
    textura = score_model(_m("kraken-textura", "Textura", "de", [15, 16]), **crit)
    assert kurrent.score > textura.score
    assert "script" in kurrent.matched_on
    assert "script-mismatch" in textura.matched_on


def test_mismatch_penalised_below_script_agnostic():
    crit = dict(script="Kurrent", lang="de", century=16)
    wrong = score_model(_m("t", "Textura", "de", [16]), **crit)    # contradicts script
    agnostic = score_model(_m("u", None, "de", [16]), **crit)      # no script info
    assert agnostic.score > wrong.score


def test_unknown_script_leaves_scoring_unchanged():
    m = score_model(_m("t", "Textura", "de", [15]), lang="de", century=15)  # no script
    assert "script-mismatch" not in m.matched_on
    assert abs(m.score - (0.3 + 0.2)) < 1e-9                       # lang + century only


def test_exact_script_dominates_via_selector(monkeypatch):
    live = {
        "kraken-early_modern_german": _m("kraken-early_modern_german", "Kurrent", "de", [16, 17]),
        "kraken-late_medieval_german": _m("kraken-late_medieval_german", "Textura", "de", [14, 15, 16]),
    }
    monkeypatch.setattr(ms, "KRAKEN_MODELS_LIVE", live)
    matches = ms.select_kraken_model(SourceCriteria(script="Kurrent", lang="de", century=16))
    assert matches[0].model.model_id == "kraken-early_modern_german"
