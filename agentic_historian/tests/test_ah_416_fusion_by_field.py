"""Does fusion beat the best single engine? The answer depends on the field — #416.

Three measurements, in two directions:

* 13 reconstructed Federal-Council pages, HTR+ at 3.38 % against rivals at
  7–12 %: voting **lost** 1.44 points to the best single system;
* #390's judge sample on Inzigkofen: character voting 0.304 against 0.218 for
  the strongest engine alone — lost again;
* the published 150-line Federal-minutes sample, candidates of comparable
  strength with uncorrelated errors: voting **won**, 0.123 against 0.145.

So fusion's value is not a property of the method but of the field it is given,
and the two regimes move in opposite directions. A single number over all pages
is the average of two opposite effects — which is precisely the number that
would let someone conclude from a bench without seeing the mechanism.

This file is about the **measurement**, not about the pipeline. #416 says in as
many words: "Widening the sample is the next step, not changing the pipeline."
The conditional rule it sketches — fuse only when the candidates are close in
quality — stays unimplemented, and must: it would need the field's quality at
recognition time, and #313 records that the match score is not a quality signal.
A bench has ground truth by definition, which is why the measurement is sound
exactly where the rule is not.

Offline. Run from the repo root.
"""

from __future__ import annotations

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from eval import vlm_bench as vb      # noqa: E402


def _scores():
    """One scored candidate. ``format_report`` short-circuits on an empty list —
    rightly, since nothing is measured then — so a test about the fusion section
    has to get past that."""
    return [vb.CandidateScore(model_id="m", level="page", attempted=3,
                              answered=3, scored=3, median_cer=0.1)]

TRUTH = "Dem Edlen und Vesten Herrn Hauptmann zu Sanct Gallen"
#: One character wrong — a strong reading.
STRONG = "Dem Edlen und Vesten Herrn Hauptmann zu Sanct Gallon"
#: Many characters wrong, and **wrong in the same places** as its twin below:
#: that correlation is what lets two weak readings outvote a good one.
WEAK_A = "Dem Edlan und Vastan Harrn Hauptmann zu Sanct Gallan"
WEAK_B = "Dem Edlan und Vastan Harrn Hauptmann zu Sanct Gallan"
#: Comparable to STRONG, wrong somewhere else.
PEER_A = "Dem Edlen und Vesten Herrn Hauptmaun zu Sanct Gallen"
PEER_B = "Dem Edlen und Vesten Herrn Hauptmann zu Sanct Gellen"


def _effect(readings, refs=None, **kw):
    return vb.fusion_effect(readings, refs or {"p1": TRUTH}, **kw)


# ── the two regimes are reported apart ──────────────────────────────────────

def test_a_dominant_leader_and_a_field_of_equals_are_separate_rows():
    """The finding of #416, as two rows instead of one average."""
    effect = _effect(
        {"htr+": {"led": TRUTH, "equal": STRONG},
         "pylaia": {"led": WEAK_A, "equal": PEER_A},
         "transkribus": {"led": WEAK_B, "equal": PEER_B}},
        {"led": TRUTH, "equal": TRUTH})

    led = effect.stratum("ein Kandidat führt klar")
    equal = effect.stratum("Kandidaten vergleichbar stark")
    assert led.pages == 1 and equal.pages == 1
    # And the direction is the measured one: the vote costs where a leader leads.
    assert led.points_lost > 0
    assert equal.points_lost <= 0.0001


def test_each_stratum_counts_its_own_wins():
    """The gap a mutation found: the strata were checked on their medians only,
    so counting every page as a win went unnoticed. The win count is the other
    half of the finding — "fusion lost 1.44 points" and "fusion lost on every
    page" are different claims, and the issue makes the first one.
    """
    effect = _effect(
        {"a": {"led": TRUTH, "helped": WEAK_A},
         "b": {"led": WEAK_A, "helped": PEER_A},
         "c": {"led": WEAK_B, "helped": PEER_B}},
        {"led": TRUTH, "helped": TRUTH},
        # One page where fusion is handed the truth, one where it is not, so a
        # "count everything as a win" bug cannot hide behind a stratum that
        # genuinely won.
        fuse_fn=lambda texts: TRUTH if PEER_A in texts.values() else WEAK_A)

    led = effect.stratum("ein Kandidat führt klar")
    assert led.pages == 1 and led.fusion_wins == 0 and led.win_share == 0.0
    helped = effect.stratum("Kandidaten vergleichbar stark")
    assert helped.pages == 1 and helped.fusion_wins == 1 and helped.win_share == 1.0


def test_a_strata_win_count_never_exceeds_its_pages():
    effect = _effect({"a": {"p1": STRONG}, "b": {"p1": WEAK_A}})
    for found in effect.strata + effect.by_count:
        assert found.fusion_wins <= found.pages


def test_the_lead_is_measured_against_the_nearest_rival_not_the_worst():
    """A leader is voted down by the candidates that can outvote it, which is
    the field it is *near*. ``worst − best`` would call a page led whenever one
    candidate collapsed, however close the other two were."""
    effect = _effect({"a": {"p1": STRONG}, "b": {"p1": PEER_A},
                      "c": {"p1": "völliger Unsinn ohne jede Ähnlichkeit"}})
    [row] = [s for s in effect.strata if s.pages]
    # STRONG and PEER_A are one edit apart; the third is far. The lead is small.
    assert row.median_lead < 0.02
    assert row.label == "Kandidaten vergleichbar stark"


def test_a_stratum_with_no_pages_says_so_rather_than_nought_percent():
    """"Not measured here" and "measured, and fusion never won" are different
    facts, and telling them apart is the whole point of cutting the pages up."""
    effect = _effect({"a": {"p1": STRONG}, "b": {"p1": PEER_A}})
    empty = effect.stratum("ein Kandidat führt klar")
    assert empty.pages == 0
    assert empty.win_share == 0.0          # the number is nought …
    table = "\n".join(vb._stratum_table(effect.strata))
    assert "| ein Kandidat führt klar | 0 | — | — | — | — | — |" in table   # … and it is not printed


def test_the_cost_carries_its_sign():
    """The sign is the answer to the issue's title, so it is a property rather
    than something each reader subtracts and occasionally inverts."""
    costly = vb.FusionStratum(pages=1, median_fused_cer=0.05,
                              median_best_single_cer=0.0338)
    helpful = vb.FusionStratum(pages=1, median_fused_cer=0.123,
                               median_best_single_cer=0.145)
    assert round(costly.points_lost, 4) == 0.0162      # the measured 1.44 pt shape
    assert helpful.points_lost < 0


def test_the_threshold_is_a_parameter_and_moving_it_moves_the_cut():
    """It is a number from one measurement, not a law: on the 13 pages the
    leader led by ~3.6 points and voting lost, so "comparable" sits well below
    that. A wider sample moves it rather than an argument settling it."""
    readings = {"a": {"p1": STRONG}, "b": {"p1": WEAK_A}}
    strict = _effect(readings, comparable_lead=0.0)
    loose = _effect(readings, comparable_lead=0.9)
    assert strict.stratum("ein Kandidat führt klar").pages == 1
    assert loose.stratum("Kandidaten vergleichbar stark").pages == 1


def test_the_effect_carries_the_threshold_it_was_cut_at():
    """So a report cannot describe one cut and print another."""
    effect = _effect({"a": {"p1": STRONG}, "b": {"p1": WEAK_A}},
                     comparable_lead=0.07)
    assert effect.comparable_lead == 0.07
    report = vb.format_report(_scores(), effect)
    assert "lead ≤ 7% counts as comparable" in report


# ── two candidates have no majority ────────────────────────────────────────

def test_pages_are_also_cut_by_how_many_candidates_answered():
    """Two candidates have no majority at all — a tie is broken by something
    other than a vote — so mixing them with three-candidate pages measures two
    mechanisms as one. #416 asks for "three or more candidates" for this reason.
    """
    effect = _effect(
        {"a": {"two": STRONG, "three": STRONG},
         "b": {"two": PEER_A, "three": PEER_A},
         "c": {"three": PEER_B}},
        {"two": TRUTH, "three": TRUTH})

    by_count = {s.label: s.pages for s in effect.by_count}
    assert by_count == {"2 Kandidaten": 1, "3+ Kandidaten": 1}


def test_the_strata_account_for_every_counted_page():
    """A page in neither stratum would be a page quietly dropped from the answer."""
    readings = {"a": {"p1": STRONG, "p2": TRUTH, "p3": STRONG},
                "b": {"p1": PEER_A, "p2": WEAK_A, "p3": WEAK_B},
                "c": {"p1": PEER_B, "p2": WEAK_B, "p3": PEER_A}}
    refs = {"p1": TRUTH, "p2": TRUTH, "p3": TRUTH}
    effect = _effect(readings, refs)
    assert sum(s.pages for s in effect.strata) == effect.pages
    assert sum(s.pages for s in effect.by_count) == effect.pages


# ── the report may not let the aggregate stand as the answer ───────────────

def test_the_report_refuses_to_let_the_aggregate_be_the_answer():
    effect = _effect(
        {"htr+": {"led": TRUTH, "equal": STRONG},
         "pylaia": {"led": WEAK_A, "equal": PEER_A},
         "transkribus": {"led": WEAK_B, "equal": PEER_B}},
        {"led": TRUTH, "equal": TRUTH})
    report = vb.format_report(_scores(), effect)

    assert "That aggregate is not the answer" in report.replace("**", "")
    # Both measured directions are named, so a reader sees the mechanism rather
    # than a single number they can conclude from.
    assert "1.44" in report and "0.123" in report
    assert "ein Kandidat führt klar" in report
    assert "Kandidaten vergleichbar stark" in report


def test_the_report_still_works_without_any_fusable_page():
    """No page had two candidates, so there is nothing to stratify — and the
    section says that rather than printing two empty strata as a finding."""
    report = vb.format_report(_scores(), vb.FusionEffect())
    assert "Not measurable" in report
    assert "ein Kandidat führt klar" not in report


# ── what this deliberately does not do ────────────────────────────────────

def test_measuring_does_not_gate_the_pipeline():
    """#416: "Widening the sample is the next step, not changing the pipeline."

    The conditional rule it sketches would have to know the field's quality at
    recognition time, and #313 records that the match score is not a quality
    signal — so the gap would have to be closed first. A guard, because the
    tempting next commit is the one that reads this bench's table and wires a
    threshold into `fuse`.
    """
    import config
    import fusion

    source = Path(fusion.__file__).read_text(encoding="utf-8")
    assert "COMPARABLE_LEAD" not in source, \
        "fusion must not gate on the bench's threshold while #313's gap is open"
    assert not hasattr(config, "FUSION_COMPARABLE_LEAD")
    # And the bench's own threshold is not read from config either: it is a
    # measurement parameter, and a config switch would invite exactly that wiring.
    bench = Path(vb.__file__).read_text(encoding="utf-8")
    assert "COMPARABLE_LEAD = 0.02" in bench


def test_the_measurement_needs_ground_truth_and_says_so():
    """The lead is a *quality* gap, so it cannot be computed without truth.

    This is the line between the measurement and the rule #416 defers: the
    bench has the reference by definition; the pipeline does not.
    """
    doc = vb.fusion_effect.__doc__ or ""
    assert "ground truth" in doc
    assert "#313" in doc


def test_the_default_fuser_still_makes_no_llm_call(monkeypatch):
    """Unchanged by the stratification, and it has to stay that way: a bench
    that needs a model to score a model is not reusable in CI."""
    import utils.gpustack_client as gs

    def forbidden(*a, **kw):
        raise AssertionError("the bench called an LLM")

    monkeypatch.setattr(gs, "chat", forbidden)
    monkeypatch.setattr(gs, "chat_text", forbidden)
    effect = _effect({"a": {"p1": STRONG}, "b": {"p1": WEAK_A}})
    assert effect.pages == 1
