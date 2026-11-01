"""#782 — a reward for Agent B from the e-codices comparison, calibration included.

The three "done when" of the issue, and what each closes:

* a label that is always the same scores worse than a calibrated answer with the
  same hit rate — the Brier term on the first script candidate (#775);
* a wide dating scores less than a narrow one that also overlaps;
* a completion that is not the 16-element JSON scores the format penalty.

And around them: a bare label claims full confidence; abstaining costs less than
missing and more than nothing; an element the catalogue does not name scores 0;
the TRL functions have TRL's signature and the format penalty counts once; the
bench report carries the reward and the Brier score.
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agents.source_heuristic import HANDSCHRIFTEN_ELEMENTS  # noqa: E402
from eval import ecodices_bench as eb                        # noqa: E402
from eval import ecodices_reward as er                       # noqa: E402

REF = eb.Reference(id="saa-x", script_families=["bastarda"], hands=2,
                   date_from=1450, date_to=1470, material="papier")


def _sj(schrift=None, p=None, datierung="Mitte 15. Jh.", schreiber="zwei Hände",
        stoff="Papier"):
    sj = {e: None for e in HANDSCHRIFTEN_ELEMENTS}
    sj["Schrift"] = {"wert": schrift}
    if p is not None:
        sj["Schrift"]["kandidaten"] = [{"schrift": schrift, "zuversicht": p, "merkmale": []}]
    sj["Datierung"] = {"wert": datierung}
    sj["Schreiber"] = {"wert": schreiber}
    sj["Beschreibstoff"] = {"wert": stoff}
    return sj


def test_a_constant_label_scores_worse_than_a_calibrated_answer_with_the_same_hits():
    # Ten volumes, half Bastarda, half Textualis; both answerers are right on five.
    refs = [REF] * 5 + [eb.Reference(id="t", script_families=["textualis"])] * 5
    constant = [_sj("Bastarda") for _ in refs]                                # bare label: p = 1
    calibrated = [_sj("Bastarda", 0.5) for _ in refs]
    score = lambda answers: sum(er.total(er.reward(a, r)) for a, r in zip(answers, refs))  # noqa: E731
    hits = lambda answers: sum(eb.judge(r, a)["Schrift"] == eb.HIT for a, r in zip(answers, refs))  # noqa: E731
    assert hits(constant) == hits(calibrated) == 5
    assert score(calibrated) > score(constant)


def test_a_bare_label_claims_full_confidence():
    assert er.first_confidence(_sj("Bastarda")) == 1.0
    assert er.first_confidence(_sj("Bastarda", 0.3)) == 0.3
    wrong_sure = er.reward(_sj("Textura", 0.9), REF)["Schrift_brier"]
    wrong_unsure = er.reward(_sj("Textura", 0.4), REF)["Schrift_brier"]
    assert wrong_sure == pytest.approx(-0.81) and wrong_unsure == pytest.approx(-0.16)


@pytest.mark.parametrize("dating,expected", [
    ("Mitte 15. Jh.", 1.0),            # 50 years
    ("15. Jh.", 0.5),                  # a century
    ("12.–16. Jh.", 0.1),              # five centuries, overlaps anyway
    ("12. Jh.", -1.0),                 # misses
    (None, er.NO_ANSWER_REWARD),
])
def test_a_wide_dating_scores_less_than_a_narrow_one(dating, expected):
    assert er.reward(_sj("Bastarda", datierung=dating), REF)["Datierung"] == pytest.approx(expected)


def test_abstaining_costs_less_than_missing_and_more_than_nothing():
    miss = er.reward(_sj("Bastarda", schreiber="eine Hand"), REF)["Schreiber"]
    none = er.reward(_sj("Bastarda", schreiber=None), REF)["Schreiber"]
    hit = er.reward(_sj("Bastarda"), REF)["Schreiber"]
    assert miss < none < 0 < hit


def test_what_the_catalogue_does_not_name_scores_nothing():
    terms = er.reward(_sj("Textura", 0.9), eb.Reference(id="x"))
    assert all(terms[k] == 0.0 for k in er.WEIGHTS) and terms["format"] == 0.0


@pytest.mark.parametrize("bad", [None, {"Schrift": {"wert": "Bastarda"}}, "Bastarda"])
def test_a_broken_completion_scores_the_format_penalty(bad):
    terms = er.reward(bad, REF)
    assert er.total(terms) == er.FORMAT_PENALTY
    assert all(terms[k] == 0.0 for k in er.WEIGHTS)


# ── TRL ──────────────────────────────────────────────────────────────────────

def test_the_trl_functions_have_trls_signature_and_count_the_format_once():
    funcs, weights = er.trl_reward_funcs()
    assert len(funcs) == len(weights) == len(er.WEIGHTS)
    good = json.dumps(_sj("Bastarda", 0.8)) + "\n\n## Beschreibung\nText."
    conversational = [{"role": "assistant", "content": good}]
    ref = {"id": REF.id, "script_families": REF.script_families, "hands": 2,
           "date_from": 1450, "date_to": 1470, "material": "papier", "unknown_column": 1}
    rows = [f(completions=[good, conversational, "kein JSON"], reference=[ref] * 3,
              prompts=["p"] * 3) for f in funcs]
    per_completion = [sum(w * r[i] for w, r in zip(weights, rows)) for i in range(3)]
    assert per_completion[0] == pytest.approx(per_completion[1]), "string and conversation agree"
    assert per_completion[0] == pytest.approx(er.total(er.reward(_sj("Bastarda", 0.8), REF)))
    assert per_completion[2] == pytest.approx(er.FORMAT_PENALTY), "penalised once, not five times"


# ── the bench report ─────────────────────────────────────────────────────────

def test_the_bench_report_carries_reward_and_brier(tmp_path):
    vol = tmp_path / "saa-x"
    (vol / "runs").mkdir(parents=True)
    (vol / "tei.xml").write_bytes((PKG / "eval" / "fixtures" / "ecodices" /
                                   "paper-two-hands.xml").read_bytes())
    for i, sj in enumerate([_sj("Bastarda", 0.8), _sj("Textura", 0.6)]):
        (vol / "runs" / f"{i:02d}.json").write_text(json.dumps({"source_json": sj}))
    _, report = eb.score(tmp_path)
    reward = json.loads((tmp_path / "scores.json").read_text())["reward"]
    # Brier over the two judged runs: (0.8 - 1)² and (0.6 - 0)², averaged.
    assert reward["brier"] == pytest.approx((0.04 + 0.36) / 2)
    assert "## Belohnung (#782)" in report and "Brier-Score der Schrift-Zuversicht" in report
