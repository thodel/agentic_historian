"""What the arbitration actually did, and whether it had to run (#406).

#406 asked two things. The wasted first attempt is fixed (#407:
`FUSION_ARBITRATE_MAX_TOKENS` is 8192 and the 4096-token call that structurally
could not finish is gone). The second — is the LLM round trip worth ~50 % of a
page — was measured against ground truth on 13 pages of the 2021 Federal
Council minutes:

    best single system, no fusion   3.38 %
    fusion, vote only               4.82 %
    fusion with LLM arbitration     5.01 %

Identical output on 7 of the 13 pages, better on 3, worse on 3, at 3.7 s a page
and 42 s on the tei page that opened the issue.

Two things follow, and this file is both.

**The count was not true.** `FusionResult.arbitrated` was documented as the
slots the LLM decided and set to the number of slots that *disagreed*. When the
call failed, every slot fell to the deterministic vote and the field still
reported the full count — and `rdf_export` publishes it as `sdhss:arbitrated`,
so a knowledge graph asserted a model had arbitrated readings it never saw.

**The decision needs more than 13 pages.** #416 says widening that sample is
the next step, not changing the pipeline, so the default stays on. What was
missing is the quantity the offline measurement had to reconstruct: how often
arbitration chose *against* the vote. Counted per run, production answers the
question itself.
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import fusion  # noqa: E402


def _r(engine, text, error=""):
    return {"engine": engine, "text": text, "error": error}


class Spy:
    """The LLM seam. `resp` is the raw completion; `calls` records the prompts."""

    def __init__(self, resp=""):
        self.calls = []
        self.resp = resp

    def __call__(self, prompt):
        self.calls.append(prompt)
        return self.resp


def choices(mapping: dict) -> str:
    import json

    return json.dumps({"choices": {str(k): v for k, v in mapping.items()}})


#: Three candidates that agree except at one column, where all three differ —
#: no majority, so exactly one arbitration slot. `kraken` and `trocr` offer
#: different readings and `vlm` a third, so the most-backed reading is the first
#: by tally order, which is what the deterministic path picks.
SPLIT = [_r("vlm", "Wir Hans von Wiler tuend kund"),
         _r("kraken", "Wir Hans von Wyler tuend kund"),
         _r("trocr", "Wir Hans von Wilre tuend kund")]


def slot_indices(cands=SPLIT) -> list[int]:
    """The column indices the arbitration was asked about.

    Read out of the prompt it actually sends, which lists them as ``[k]``.
    Not derived from `provenance`: spans merge consecutive same-source tokens,
    so a span's position is not a column index — a confusion that makes a test
    pass against the wrong slot.
    """
    import re

    spy = Spy()
    fusion.fuse(cands, llm_fn=spy, arbitrate=True)
    assert spy.calls, "the fixture must reach arbitration"
    return [int(m) for m in re.findall(r"^\[(\d+)\] Kontext:", spy.calls[0],
                                      re.M)]


def one_slot(cands=SPLIT) -> int:
    """The single column `SPLIT` disagrees on."""
    found = slot_indices(cands)
    assert len(found) == 1, f"fixture no longer splits exactly once: {found}"
    return found[0]


# ── the count has to be true ────────────────────────────────────────────────

def test_a_failed_call_arbitrated_nothing():
    """The bug this file exists for. The LLM returns something unparsable, every
    slot falls to the vote, and the result must not claim otherwise."""
    spy = Spy("I'm afraid I can't help with that.")

    r = fusion.fuse(SPLIT, llm_fn=spy)

    assert spy.calls, "the call was made"
    assert r.disagreed == 1          # there was work to do
    assert r.arbitrated == 0         # and the LLM did none of it
    assert r.changed == 0
    assert all(s.source != "llm" for s in r.provenance)


def test_a_decided_slot_is_counted_as_arbitrated():
    """The other half, so the test above cannot pass by always reporting zero."""
    k = one_slot()
    spy = Spy(choices({k: "Wyler"}))

    r = fusion.fuse(SPLIT, llm_fn=spy)

    assert (r.disagreed, r.arbitrated) == (1, 1)
    assert "Wyler" in r.text
    assert any(s.source == "llm" for s in r.provenance)


def test_an_empty_choice_is_a_decision_and_is_counted():
    """"Nothing stands here" is an answer, not a missing one. It is dropped from
    the text like any empty token, and it still cost a call."""
    k = one_slot()

    r = fusion.fuse(SPLIT, llm_fn=Spy(choices({k: ""})))

    assert (r.disagreed, r.arbitrated) == (1, 1)
    assert "Wiler" not in r.text and "Wyler" not in r.text
    assert r.text == "Wir Hans von tuend kund"


def test_a_partial_answer_counts_only_what_it_answered():
    """Two slots offered, one answered. The other is the vote's."""
    cands = [_r("vlm", "Wir Hans von Wiler tuend kund hie"),
             _r("kraken", "Wir Hans von Wyler tuend kund hye"),
             _r("trocr", "Wir Hans von Wilre tuend kund hÿe")]
    found = slot_indices(cands)
    assert len(found) == 2, f"fixture must split twice: {found}"

    r = fusion.fuse(cands, llm_fn=Spy(choices({found[0]: "Wyler"})))

    assert (r.disagreed, r.arbitrated) == (2, 1)


# ── `changed` is the number the decision needs ──────────────────────────────

def test_an_arbitration_that_echoes_the_vote_changed_nothing():
    """The 7 of 13 pages where the model picked exactly what the vote picked.
    Counted apart, those pages are visibly an LLM confirming a majority at
    3.7 s a page — which is what makes the cost answerable."""
    k = one_slot()
    vote_would_pick = fusion.fuse(SPLIT, llm_fn=Spy(), arbitrate=False)
    # What the vote picks at that column, from the one function both paths use.
    cols, labels = fusion._align_columns(fusion._candidates(SPLIT))
    echoed, _ = fusion._most_backed(
        {lbl: cols[k][lbl] for lbl in labels if lbl in cols[k]})

    r = fusion.fuse(SPLIT, llm_fn=Spy(choices({k: echoed})))

    assert r.arbitrated == 1         # the call was made and answered
    assert r.changed == 0            # and it changed nothing
    assert r.text == vote_would_pick.text


def test_an_arbitration_against_the_vote_is_counted_as_changed():
    k = one_slot()
    deterministic = fusion.fuse(SPLIT, llm_fn=Spy(), arbitrate=False)

    r = fusion.fuse(SPLIT, llm_fn=Spy(choices({k: "Wilre"})))

    assert (r.arbitrated, r.changed) == (1, 1)
    assert r.text != deterministic.text


def test_changed_never_exceeds_arbitrated_which_never_exceeds_disagreed():
    """The three counters nest. Any other ordering means one of them is
    measuring something else."""
    for resp in ("", "junk", choices({0: "x"}), choices({one_slot(): "Wyler"})):
        r = fusion.fuse(SPLIT, llm_fn=Spy(resp))
        assert r.changed <= r.arbitrated <= r.disagreed, resp


# ── the switch ──────────────────────────────────────────────────────────────

def test_arbitrate_false_makes_no_call():
    spy = Spy(choices({0: "Wyler"}))

    r = fusion.fuse(SPLIT, llm_fn=spy, arbitrate=False)

    assert spy.calls == []
    assert (r.disagreed, r.arbitrated, r.changed) == (1, 0, 0)


def test_off_is_the_path_a_failed_call_already_took():
    """"Off" is not a new behaviour — the slot-level fallback for a failed call
    has always resolved disagreements this way. The switch makes a path the code
    already ran reachable deliberately instead of only by failure."""
    failed = fusion.fuse(SPLIT, llm_fn=Spy("not json"))
    off = fusion.fuse(SPLIT, llm_fn=Spy(choices({0: "Wyler"})), arbitrate=False)

    assert off.text == failed.text
    assert [s.source for s in off.provenance] == [s.source for s in failed.provenance]


def test_the_config_default_is_on_and_the_argument_overrides_it(monkeypatch):
    """Default on, because 13 pages of three CTC candidates is the sample whose
    limits #416 cites for not changing the pipeline yet."""
    import config

    assert config.FUSION_ARBITRATE is True

    monkeypatch.setattr(config, "FUSION_ARBITRATE", False)
    spy = Spy(choices({0: "Wyler"}))
    fusion.fuse(SPLIT, llm_fn=spy)
    assert spy.calls == [], "the config switch is read when no argument is given"

    loud = Spy(choices({0: "Wyler"}))
    fusion.fuse(SPLIT, llm_fn=loud, arbitrate=True)
    assert loud.calls, "an explicit argument beats the config"


def test_a_page_that_agrees_still_makes_no_call_either_way():
    """Nothing to arbitrate is not the same as arbitration switched off, and
    neither spends a round trip."""
    t = "Wir Hans von Wiler tuend kund"
    agreeing = [_r("vlm", t), _r("kraken", t), _r("trocr", t)]

    for flag in (True, False):
        spy = Spy()
        r = fusion.fuse(agreeing, llm_fn=spy, arbitrate=flag)
        assert spy.calls == []
        assert (r.disagreed, r.arbitrated, r.changed) == (0, 0, 0)
        assert r.text == t


# ── one implementation of "what the vote would have said" ───────────────────

def test_the_fallback_and_the_changed_baseline_are_the_same_function():
    """`_most_backed` serves both. Two implementations would be two answers to
    "what would the vote have said", and `changed` would be counted against a
    baseline the fallback does not use."""
    opts = {"vlm": "Wiler", "kraken": "Wyler", "trocr": "Wyler"}

    token, backers = fusion._most_backed(opts)

    assert token == "Wyler"
    assert sorted(backers) == ["kraken", "trocr"]


def test_all_singleton_readings_pick_one_but_never_nothing():
    """With no repeated reading the choice is arbitrary — but the deterministic
    path must always yield a token, unlike the arbitration, which may answer
    "". A fallback that could return nothing would drop text on failure."""
    token, backers = fusion._most_backed({"a": "x", "b": "y", "c": "z"})

    assert token in ("x", "y", "z") and token != ""
    assert len(backers) == 1


# ── the no-merge band is untouched by any of this ───────────────────────────

def test_the_no_merge_band_still_short_circuits_before_arbitration():
    """Above the band nothing is fused, so there is nothing to arbitrate and no
    counter to report — whichever way the switch is set (#300)."""
    cands = [_r("vlm", "völlig andere Lesung ohne Bezug zum Rest hier"),
             _r("kraken", "Wir Hans von Wiler tuend kund"),
             _r("trocr", "xyz qrs tuv wxy zab cde fgh")]

    for flag in (True, False):
        spy = Spy(choices({0: "x"}))
        r = fusion.fuse(cands, llm_fn=spy, arbitrate=flag)
        assert "no-merge" in r.strategy
        assert spy.calls == []
        assert (r.disagreed, r.arbitrated, r.changed) == (0, 0, 0)
