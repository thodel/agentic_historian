"""#649: an unscored pick must not reach anyone as a number.

`plan_models` gave the VLM pick a hardcoded `1.0` because there is no VLM
selector (#539). The kraken and TrOCR picks beside it carry what the
script/century selector **computed**. Same field, two different kinds of
statement — a measurement, and a placeholder shaped like the best possible
measurement.

The project knew: `select_best`'s docstring says the 1.0 "reads like a perfect
score but only means 'always run the VLM first' — it is not a match score". The
knowledge lived in a docstring and in one `engine == "vlm"` special case, and
not in the value. Three consequences, one group of tests each below.

The fifth instance of the same defect (#119, #595, #631, #633): a value that
claims more than was measured. This time inside the epic (#536) that is itself
about a dataset naming a model that never ran.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import agent_a.ensemble as ens  # noqa: E402
from agent_a.ensemble import ModelPick  # noqa: E402


# ── the value carries the fact ───────────────────────────────────────────────

def test_a_pick_is_unscored_by_default():
    """Nothing measured is the default, so a new producer cannot invent a score
    by forgetting the argument."""
    assert ModelPick("vlm", "qwen3.8-27b").score is None


def test_unscored_is_not_the_same_as_a_measured_zero():
    """The whole reason for three values. `0.0` is "weighed, does not fit";
    `None` is "nobody weighed it" — and a consumer that cannot tell them apart
    is how the placeholder passed as a match for as long as it did."""
    unscored = ModelPick("vlm", "v")
    measured_zero = ModelPick("kraken", "k", 0.0)

    assert unscored.score is None
    assert measured_zero.score == 0.0
    assert unscored.scored is False
    assert measured_zero.scored is True
    assert unscored.score_label() != measured_zero.score_label()


def test_both_rank_as_no_match_but_only_one_says_a_number():
    """They agree where agreeing is safe — ranking — and differ where the
    difference is the information."""
    assert ModelPick("vlm", "v").match() == 0.0
    assert ModelPick("kraken", "k", 0.0).match() == 0.0
    assert ModelPick("vlm", "v").score_label() == "ungewertet"
    assert ModelPick("kraken", "k", 0.0).score_label() == "0.00"


def test_a_measured_score_is_kept_for_ranking():
    """The counter-test: three-valued must not cost the real scores."""
    assert ModelPick("kraken", "k", 0.62).match() == pytest.approx(0.62)
    assert ModelPick("kraken", "k", 0.62).score_label() == "0.62"


def test_a_pick_without_a_score_field_at_all_reads_as_unscored():
    """Duck-typed picks exist — `rank_candidates` reaches for every field with
    `getattr(..., default)`, which is why the helpers are module level. An
    object with no `score` is "I cannot tell", and the safe reading of that is
    unscored, not a measured zero: a `getattr(pick, "score", 0.0)` would make
    the absence of a field claim a measurement."""
    from types import SimpleNamespace

    bare = SimpleNamespace(engine="vlm", model_id="qwen3.8-27b")
    assert ens.pick_match(bare) == 0.0
    assert ens.pick_score_label(bare) == "ungewertet"


# ── the gateway's engine name, missed by one letter ──────────────────────────

def test_an_unscored_gateway_vlm_also_ranks_as_no_match():
    """The hole the old guard had.

    `rank_candidates` recognised the placeholder by `engine == "vlm"`. The ATR
    gateway names its VLM engine **`vllm`** (#540), so a pick from there slipped
    past that test by one letter and would have had a placeholder counted as a
    perfect match — for the engine that collapsed into "uuuu" on u-17__.
    Latent, not live: `plan_models` builds no `vllm` picks today. It goes live
    the moment #539 is answered yes, which is the step where nobody is looking
    for it.
    """
    assert ModelPick("vllm", "qwen3vl-medieval-german-v3").match() == 0.0


@pytest.mark.parametrize(
    "engine", ["vlm", "vllm", "kraken", "trocr", "party", "future"])
def test_no_engine_name_can_make_an_unscored_pick_a_match(engine):
    """Keyed off the value, so the set of engine names does not matter."""
    assert ModelPick(engine, "m").match() == 0.0
    assert ModelPick(engine, "m").score_label() == "ungewertet"


def _code_only(source: str) -> str:
    """Source with comment lines dropped.

    Checked against code rather than text, because the comment *explaining* the
    old engine-name test contains the old engine-name test — and a whole-source
    grep cannot tell an explanation from a reintroduction. The same shape cost
    a wrong answer twice elsewhere today (a `ProtectSystem=strict` that was
    prose, a `top.score` that matched a substring of another object's field).
    """
    return "\n".join(line for line in source.splitlines()
                     if not line.strip().startswith("#"))


def test_the_ranker_no_longer_keys_off_the_engine_name():
    """Structural: recognising the placeholder by engine name is exactly what
    missed `vllm`, so it must not come back as code."""
    code = _code_only(inspect.getsource(ens.rank_candidates))
    assert 'engine == "vlm"' not in code, (
        "the ranker recognises the placeholder by engine name again — that "
        "misses the gateway's `vllm` by one letter")
    assert "pick_match(" in code, "ranking no longer goes through pick_match"


# ── what a human and the dataset get to see ──────────────────────────────────

def _picks_for_display():
    return [ModelPick("vlm", "qwen3.8-27b"),
            ModelPick("kraken", "k0", 0.62),
            ModelPick("trocr", "t0", 0.55)]


def test_the_card_shows_a_word_not_a_perfect_score():
    rendered = "; ".join(f"{p.engine}/{p.model_id} {p.score_label()}"
                         for p in _picks_for_display())
    assert "qwen3.8-27b ungewertet" in rendered
    assert "1.00" not in rendered, (
        "the card reads as a perfect match for a model nothing weighed")
    assert "k0 0.62" in rendered, "the measured scores still show"


def _pool_rendering_lines() -> list[str]:
    """Every orchestrator line that renders the pick pool for a person.

    Found by the shape of the rendering rather than by a remembered spelling:
    naming one spelling is how two mutations survived this file — `{p.score:.2f}`
    became `{p.match():.2f}` and the check did not notice, and a check for
    `score_label()` *anywhere* in the file passed while one of the two sites had
    lost it.
    """
    source = Path("agentic_historian/orchestrator.py").read_text(encoding="utf-8")
    return [line for line in _code_only(source).splitlines()
            if "p.engine}/{p.model_id}" in line]


def test_the_orchestrator_renders_the_pool_in_two_places():
    """The precondition the next two tests rest on — if the rendering moves or
    is rewritten, they must fail loudly rather than pass over nothing."""
    assert len(_pool_rendering_lines()) == 2, (
        "the pick pool is rendered in a different number of places than this "
        "file knows about; check both and update the count")


def test_no_pool_rendering_formats_a_number():
    """Every one of them, not one named spelling."""
    for line in _pool_rendering_lines():
        assert ":.2f" not in line, (
            f"a pick's score is formatted as a number again: {line.strip()!r} — "
            f"unscored would print as a measurement")


def test_every_pool_rendering_goes_through_score_label():
    for line in _pool_rendering_lines():
        assert "score_label()" in line, (
            f"this rendering bypasses score_label: {line.strip()!r}")


def test_the_no_merge_provenance_does_not_format_the_score():
    """The site that matters most: this string is the **provenance** of the
    selected reading, so it goes into the record and not only the log. Written
    as "match score 1.00" it states a measurement nobody took, about the very
    engine whose reading was chosen."""
    code = _code_only(inspect.getsource(ens.recognize_ensemble))
    assert "pick_score_label(" in code, (
        "the no-merge provenance no longer goes through pick_score_label")
    assert ":.2f" not in code.split("match score")[-1][:80], (
        "the provenance formats the match score as a number again")


def test_the_provenance_of_an_unscored_selection_carries_no_number():
    """End to end, because the two checks above read source rather than
    behaviour — and a source check cannot see what the string becomes."""
    texts = {"v": "Infer fremdlichs gruee vor liebe gerninre von der koffe wer",
             "k": "Ganz anderer Text der mit dem ersten nichts zu tun hat hier",
             "t": "Noch eine dritte Lesung die wieder voellig abweicht davon"}

    def rec_fn(pick, image):          # pick first — the ensemble's own order
        return {"engine": pick.engine, "model_id": pick.model_id,
                "text": texts[pick.model_id], "confidence": 0.5, "error": ""}

    picks = [ModelPick("vlm", "v"), ModelPick("kraken", "k", 0.05),
             ModelPick("trocr", "t", 0.20)]
    result = ens.recognize_ensemble("img", None, rec_fn, picks=picks,
                                    min_engines=3, max_loops=0,
                                    no_merge_cer=0.35)

    assert result.provenance, "no-merge should have fired at this disagreement"
    why = str(result.provenance[0])
    assert "1.00" not in why, f"the provenance states a measurement: {why!r}"
    if "vlm/v" in why:
        assert "ungewertet" in why, f"the provenance hides the gap: {why!r}"
