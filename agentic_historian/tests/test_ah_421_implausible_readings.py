"""A reading that cannot be of this page is kept, and kept out of the numbers.

#421: `kraken-bohemian_19th` returned 203 characters of "u\\nuuu\\nuu\\nuu" for a
page every other engine read as ~3200 — measured live on saa-0428, three pages.
`_usable()` counted it (`t.strip()` is non-empty), so the page was filed as
checked; it drove the spread to 19.86, which decides #300's no-merge band; and it
sat on the Gate-2 card as a transcription to choose from.

That "uuuu" is literally the failure this module's docstring says the ensemble
was built to prevent.

The candidate stays in `recognitions` and on the Gate-2 card — a misconfigured
model is evidence — and is held out of `usable`, the spread, the consensus and
the fusion vote.

Offline — engine execution is a mock recognize_fn, CER and fusion are real.
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a.ensemble import (  # noqa: E402
    ModelPick,
    _partition,
    implausible_reading,
    rank_candidates,
    recognize_ensemble,
)

# A page's worth of text and three near-identical readings of it, against the
# fragment measured on saa-0428/015v. The proportions are the measured ones:
# ~640 characters of transcription against ~32 of "u", a factor of 20. The live
# page was 3198 against 203, a factor of 15.
_L1 = "Wir Hans von Wiler tuend kund allen die disen brief ansehent oder "
_L2 = "hoerent lesen daz wir mit guotem willen und wolbedachtem muote "
PAGE = (_L1 + _L2) * 5
PAGE_B = (_L1.replace("tuend", "tuond") + _L2.replace("hoerent", "horent")) * 5
PAGE_C = (_L1 + _L2.replace("guotem", "gutem").replace("muote", "mute")) * 5
UUUU = "\nu\nuuu\nuu\nuu\n\n\n\n\n\nu\nuu\nuuu\nuuuu\n"

PICKS = [ModelPick("vlm", "v0", 1.0), ModelPick("kraken", "k0", 0.9),
         ModelPick("trocr", "t0", 0.8), ModelPick("kraken", "k1", 0.7),
         ModelPick("trocr", "t1", 0.6)]


def _rec(engine, model, text):
    return {"engine": engine, "model_id": model, "text": text, "error": ""}


def _fn(text_map):
    calls = []

    def fn(pick, image):
        calls.append(pick.model_id)
        return _rec(pick.engine, pick.model_id, text_map.get(pick.model_id, ""))
    fn.calls = calls
    return fn


# ── the gate itself ──────────────────────────────────────────────────────────

def test_the_fragment_measured_on_saa_0428_is_rejected():
    why = implausible_reading(UUUU, [PAGE, PAGE_B, PAGE_C, UUUU])
    assert why and "distinct letters" in why


def test_a_plausible_reading_is_not_rejected():
    others = [PAGE, PAGE_B, PAGE_C]
    assert implausible_reading(PAGE_B, others) == ""


def test_the_bat_664_lesson_is_respected():
    """select_best's docstring: on BAT_664 the LONGEST candidates were garbage.

    586 chars of noise against 645 of real text — a ratio of 1.1. Length is
    worthless for ranking two plausible readings, and this gate must stay far
    away from that call: it only tells a reading from a non-reading.
    """
    noise = ("Infer fremdlichs gruee der wirt sich nit gehalten hab und "
             "wolt im das nit zugeben noch gestatten in kainem weg mer ") * 5
    real = ("Item als vil die sach berueren tut ist mein guetlich bit an "
            "euch ir wellet mir schriftlich antwurt geben in disen sachen") * 5
    assert 0.85 < len(noise) / len(real) < 1.2      # die gemessenen Proportionen
    assert implausible_reading(noise, [noise, real, real]) == ""


def test_a_short_reading_needs_a_set_to_be_short_against():
    """With two candidates there is no set to be an outlier in.

    The alphabet test still applies — it looks at the text alone — so this uses a
    varied short text, which is the case the LENGTH arm exists for.
    """
    short = "Item als vil die sach"
    assert implausible_reading(short, [PAGE, short]) == ""
    assert implausible_reading(short, [PAGE, PAGE_B, short])


def test_the_gate_does_not_invert_when_fragments_are_the_majority():
    """One real reading against three fragments: the reading must survive.

    A first version measured against the median, which IS the fragment once they
    outnumber the readings — so it set aside the only true transcription. The
    length arm measures against the longest candidate, which cannot invert.
    """
    others = [PAGE, UUUU, UUUU, UUUU]
    assert implausible_reading(PAGE, others) == ""
    assert implausible_reading(UUUU, others)


# ── the numbers ──────────────────────────────────────────────────────────────

def test_the_fragment_is_kept_but_held_out_of_the_measures():
    recs = [_rec("vlm", "v0", PAGE), _rec("kraken", "k0", PAGE_B),
            _rec("trocr", "t0", PAGE_C), _rec("kraken", "k1", UUUU)]
    kept, aside = _partition(recs)
    assert len(kept) == 3
    assert [lbl for lbl, _ in aside] == ["kraken/k1"]
    assert recs[3] in recs, "the candidate itself is not discarded"


def test_usable_no_longer_counts_a_non_reading():
    """#367 asked whether a page was checked at all. "uuuu" is not a check."""
    fn = _fn({"v0": PAGE, "k0": UUUU, "t0": UUUU, "k1": UUUU, "t1": UUUU})
    r = recognize_ensemble("img", None, fn, picks=list(PICKS), min_engines=2,
                           max_loops=3, agreement_cer=0.30, concurrency=1)
    assert r.usable == 1, "only the VLM produced a reading of this page"
    assert r.fast_path is False
    assert "unchecked" in r.path


def test_the_spread_that_decides_the_no_merge_band_ignores_it():
    """#300 keys on max_pairwise_cer; a fragment must not decide the path."""
    fn = _fn({"v0": PAGE, "k0": PAGE_B, "t0": PAGE_C, "k1": UUUU, "t1": UUUU})
    r = recognize_ensemble("img", None, fn, picks=list(PICKS), min_engines=3,
                           max_loops=2, agreement_cer=0.30, concurrency=1)
    assert r.max_pairwise_cer < 0.30, "three close readings, one ignored fragment"
    assert r.no_merge is False


def test_the_fragment_never_reaches_the_fusion_vote():
    """Holding it out of the spread makes fusion MORE likely — so it must also
    be out of the vote, or this change would blend in what it just excluded."""
    fn = _fn({"v0": PAGE, "k0": PAGE_B, "t0": PAGE_C, "k1": UUUU, "t1": UUUU})
    r = recognize_ensemble("img", None, fn, picks=list(PICKS), min_engines=3,
                           max_loops=2, agreement_cer=0.30, concurrency=1)
    assert "uuu" not in r.text


# ── it stays visible ─────────────────────────────────────────────────────────

def test_it_is_reported_not_silently_dropped():
    fn = _fn({"v0": PAGE, "k0": PAGE_B, "t0": PAGE_C, "k1": UUUU, "t1": UUUU})
    r = recognize_ensemble("img", None, fn, picks=list(PICKS), min_engines=4,
                           max_loops=1, agreement_cer=0.30, concurrency=1)
    assert r.set_aside, "the operator has to be told a number saw fewer candidates"
    assert "held out of the numbers" in r.path


def test_it_still_reaches_the_gate_2_card():
    """#313: a person should see what every engine did — including this."""
    fn = _fn({"v0": PAGE, "k0": PAGE_B, "t0": PAGE_C, "k1": UUUU, "t1": UUUU})
    r = recognize_ensemble("img", None, fn, picks=list(PICKS), min_engines=4,
                           max_loops=1, agreement_cer=0.30, concurrency=1)
    assert any(UUUU == c["text"] for c in r.recognitions)


def test_it_can_never_become_the_automatic_pick():
    """#358's rule, extended: implausible sorts below every plausible candidate."""
    recs = [_rec("kraken", "k1", UUUU), _rec("trocr", "t0", PAGE),
            _rec("vlm", "v0", PAGE_B)]
    picks = [ModelPick("kraken", "k1", 0.99),      # the highest match score
             ModelPick("trocr", "t0", 0.5), ModelPick("vlm", "v0", 1.0)]
    ranked = rank_candidates(recs, picks)
    assert ranked[0][0]["text"] != UUUU
    assert ranked[-1][0]["text"] == UUUU
