"""#237 (P2-4): multi-engine fusion — align → vote → bounded LLM arbitration.

Offline — the LLM seam is a spy. Run from the repo root:
    pytest agentic_historian/tests/test_ah_237_fusion.py
"""

import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import fusion  # noqa: E402


def _r(engine, text, error=""):
    return {"engine": engine, "text": text, "error": error}


class Spy:
    def __init__(self, resp=""):
        self.calls = []
        self.resp = resp

    def __call__(self, prompt):
        self.calls.append(prompt)
        return self.resp


# ── degenerate inputs ────────────────────────────────────────────────────────

def test_empty_returns_empty():
    r = fusion.fuse([], llm_fn=Spy())
    assert r.text == "" and r.n_candidates == 0


def test_single_candidate_returned_as_is():
    spy = Spy()
    r = fusion.fuse([_r("vlm", "Wir Hans von Wiler")], llm_fn=spy)
    assert r.text == "Wir Hans von Wiler" and r.provenance[0].source == "single"
    assert spy.calls == []                       # no LLM for a single candidate


def test_error_and_empty_candidates_filtered():
    r = fusion.fuse([_r("vlm", "gut lesbar hier"), _r("kraken", "", "gateway 500"),
                     _r("trocr", "   ")], llm_fn=Spy())
    assert r.n_candidates == 1 and r.text == "gut lesbar hier"


# ── voting (no LLM when there's a majority) ──────────────────────────────────

def test_unanimous_no_llm_call():
    t = "Wir Hans von Wiler tuend kund"
    spy = Spy()
    r = fusion.fuse([_r("vlm", t), _r("kraken", t), _r("party", t)], llm_fn=spy)
    assert r.text == t and r.arbitrated == 0 and spy.calls == []
    assert all(s.source == "consensus" for s in r.provenance)


def test_majority_wins_without_llm():
    spy = Spy()
    r = fusion.fuse([_r("vlm", "Wir Hans von Wiler"),
                     _r("kraken", "Wir Hans von Wiler"),
                     _r("trocr", "Wir Hans von Miler")], llm_fn=spy)   # 2:1 on last token
    assert r.text == "Wir Hans von Wiler" and spy.calls == []          # majority, no arbitration


def test_garbage_engine_is_outvoted():
    good = "Wir Hans von Wiler tuend kund allen"
    spy = Spy()
    r = fusion.fuse([_r("vlm", good), _r("kraken", good),
                     _r("trocr", "xyz 123 voellig andere zeichen ohne sinn hier")], llm_fn=spy)
    assert r.text == good and spy.calls == []


# ── LLM arbitration, scoped to disagreements only ────────────────────────────

def test_llm_arbitrates_only_the_tie_and_preserves_consensus():
    # two candidates differ on ONE token → tie → that slot goes to the LLM;
    # the agreed tokens must be byte-preserved and never sent as choices.
    spy = Spy(json.dumps({"choices": {"1": "Hans"}}))
    r = fusion.fuse([_r("vlm", "Wir Hans von Wiler"),
                     _r("kraken", "Wir Hanns von Wiler")], llm_fn=spy)
    assert r.text == "Wir Hans von Wiler"
    assert r.arbitrated == 1 and len(spy.calls) == 1          # one batched call
    # the arbitrated token is tagged llm; the rest is consensus/vote
    assert any(s.source == "llm" and s.text == "Hans" for s in r.provenance)
    assert any("von" in s.text and s.source in ("consensus", "vote") for s in r.provenance)


def test_llm_fallback_is_deterministic_when_llm_returns_nothing():
    spy = Spy("not json at all")                              # arbitration fails
    r = fusion.fuse([_r("vlm", "Wir Hans"), _r("kraken", "Wir Hanns")], llm_fn=spy)
    # falls back to a candidate reading (no crash), still one call attempted
    assert r.text in ("Wir Hans", "Wir Hanns") and len(spy.calls) == 1


# ── provenance ────────────────────────────────────────────────────────────────

def test_provenance_covers_output_with_sources():
    spy = Spy(json.dumps({"choices": {"1": "Hans"}}))
    r = fusion.fuse([_r("vlm", "Wir Hans von Wiler"),
                     _r("kraken", "Wir Hanns von Wiler")], llm_fn=spy)
    joined = " ".join(s.text for s in r.provenance)
    assert joined == r.text                                   # spans reconstruct the text
    assert {s.source for s in r.provenance} <= {"consensus", "vote", "llm", "single"}


# ── llm_merge strategy ────────────────────────────────────────────────────────

def test_llm_merge_strategy_calls_llm_once_with_all_texts():
    spy = Spy("Wir Hans von Wiler (konsolidiert)")
    r = fusion.fuse([_r("vlm", "Wir Hans von Wiler"), _r("kraken", "Wir Hanns von Wiler")],
                    llm_fn=spy, strategy="llm_merge")
    assert r.strategy == "llm_merge" and r.text == "Wir Hans von Wiler (konsolidiert)"
    assert len(spy.calls) == 1 and "### vlm" in spy.calls[0] and "### kraken" in spy.calls[0]


def test_llm_merge_falls_back_to_longest_on_failure():
    def boom(_):
        raise RuntimeError("down")
    r = fusion.fuse([_r("vlm", "kurz"), _r("kraken", "ein deutlich laengerer text hier")],
                    llm_fn=boom, strategy="llm_merge")
    assert r.text == "ein deutlich laengerer text hier"
