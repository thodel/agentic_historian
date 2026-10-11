"""A reading that argues with itself is not a reading of the page (#619).

Measured 2026-10-11. `gemini-300-strict` reported four pages as `cut off`, and
reading them showed that **three carried no transcription at all** — the model
spelling letters out and talking itself through them until the token ceiling
stopped it. The fourth was a genuine page cut off mid-word.

Nothing else moved. These pages are not `empty`, not `repetitive` (every line
different, no character run over three) and not `short` (816–928 characters). The
only reason anybody saw them is that the arguing hit a ceiling; a page that
argues for four hundred characters and stops cleanly is simply part of the
corpus.

It also makes the `cut off` advice wrong for exactly these pages: raising the
ceiling buys more arguing.

The fixtures are the readings themselves, verbatim from the run, and the clean
ones are the *same pages* read under `lassberg_atr_strict2.md` — so the
separation this module claims is measured against the case it was built for and
against its own counter-example, not against invented text.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import page_quality as pq                   # noqa: E402

# ── the three leaked pages, verbatim ─────────────────────────────────────────

WEIMAR_250 = """- e - n - k - t !
LOOK AT WORD 3:
g - e - s - c - h - e - n - k - t !
Wait, does it have 'sch'?
Look at Word 3:
'g', 'e', then a tall letter!
Wait, is that a 'd' or 'sch'?
It's a tall loop ('d' or 'ch')!
Then 'a' or 'e'?
Then 'n' or 'r'?
Then 'k' (with loop) and 't'!
"geschenkt"!
Wait, what about Word 2?
If Word 3 is "geschenkt", what is Word 2?
"Von Gott geschenkt"?
Wait, is Word 1 "Von"?
Look at Word 1:
V - o - n:
Look at the first letter:
In 19th-century Kurrent, 'V' is often written:
Start with an entry curve from the left, downstroke, rounded bottom, upstroke to the right, looped at the top!
Wait, look at Word 1:
Entry stroke, downstroke, loop up to the right... THAT'S A 'V'!
Then 'o', then 'n'!
"Von Gott geschenkt u. ..."!
WAIT! Does Word 1 say "Von"?
Let's look at the letters of Word 1:
First stroke: curve up, down.
Then goes up into a loop."""

DONAUESCHINGEN_388 = '''Leben! abgehend laßen."
Wait, "ließ ich mein Süßes Leben! abgehen"?
Wait! Look at line 2 after "Gruß":
"... Gruß an Dich mein"
Wait, is it "an Dich mein" or "und ich mein"?
Look at "an Dich":
Is it "an Dich"?
The first word has: a - n ? Or u . ?
Look at "u." in line 1: "u. Kühe".
In line 2: it is "u." !
Then: "dich"? or "ich"?
i - c - h !
Line 3: "Süßes Leben! abgehend laßen."
Wait, look at line 2:
Wait, if word 1 is "konnt"?
Look at word 1:
No, it's very short: 3 letters.
Could it be "Ist"?
Wait, what if it's "ließ" misspelled or abbreviated?
Wait, is there a dot?
Yes, there is a dot above it!'''

FREIBURG_3030 = '''Line 10: "Herr und Göner"
Line 11: "W. v. Zimern und Vanotti"
Line 15: "u Euphemias"
In line 23: it is "u" with an arc over it!
Wait, but look at what's before "Gemahl":
Is it "u Gemahl"?
Wait, what if it's:
"Wilhelm v. Montfort der Rothe u Gemahl (nach Vanotti Taf. B)"?
Wait, who is "Gemahl ... mit der Kunigund"?
A man is "Gemahl mit der Kunigund"!
Wait, was Wilhelm von Montfort married to Kunigunde von Werdenberg?
YES!
Wait! Then why does it say:
Wait! Let's re-read line 22-24 carefully!
Wait, are there two names?'''

# ── the same pages under the prompt that fixed them ──────────────────────────

FREIBURG_3030_CLEAN = '''Nun aber hat namentlich die Mühe, welche Bergman in seiner
neuesten Schrift "Das Wappen v. Bregenz u[nd] die Grafen v. Montfort Bregenz"
an die Vanottischen Stamtafeln verwandte [durchgestrichen: und die] er an die
Rheinthaler Werdenberge noch verwenden will, mich angetrieben, den
Werdenberg-Heiligenberger Stambaum ganz auszuführen, so weit er
auf den mir zugänglichen Urkunden und gleichzeitigen Stellen fusst.
Für jetzt nur eine kurze Beantwortung Ihrer Fragen, verehrtester
Herr und Göner!
Die Albrechte von W. H. sind bei W. v. Zimern und Vanotti theils
verwechselt, theils mangelhaft.'''

WLB_32_CLEAN = """mit 800 fl. in S. Gallen recht wol leben koenne; bescheidne
wonungen würden Sie in der nähe des stifts, und ser billige
kost in der in dem stifte selbst befindlichen restauration finden,
welche von merern angestellten benutzt wird.
Nun mein lieber Auletes! wie gefällt Inen dieser vorschlag?
da Sie sich schon früher gegen mich geaeuffert haben, Sie
wünschten vor allem, wieder in der väterländischen Schweiz
vesten fuss zu fassen; so schient mir dies anerbieten die
beste gelegenheit dazu und ich meine Sie würden nicht
übel tun die fronte capillata, beim schopf zu nemen.
Entschliessen Sie sich dazu; so rate ich, one langen verzug,
[durchgestrichen: an Herren Decan und Pfarr-Rector Greith Hochwürden]
nach St. Gallen zu schreiben; denn mir scheint er wünscht
in dieser sache begrüsst zu werden; also carpe diem,
dum fervet olla."""

LEAKED = pytest.mark.parametrize("text", [WEIMAR_250, DONAUESCHINGEN_388,
                                          FREIBURG_3030])
CLEAN = pytest.mark.parametrize("text", [FREIBURG_3030_CLEAN, WLB_32_CLEAN])


# ── the verdict ──────────────────────────────────────────────────────────────

@LEAKED
def test_a_page_the_model_argued_itself_through_is_named(text):
    assert pq.classify(text).verdict == "deliberation"


@CLEAN
def test_a_transcription_of_the_same_page_is_untouched(text):
    assert pq.classify(text).verdict == "ok"
    assert pq.classify(text).deliberation_ratio == 0.0


@LEAKED
def test_no_other_column_would_have_moved(text):
    """Why this needed a verdict of its own rather than a wider threshold."""
    q = pq.classify(text)

    assert q.chars > pq.SHORT_CHARS, "not short: 816–928 characters"
    assert q.longest_char_run < pq.MAX_CHAR_RUN, "no character run"
    assert q.repeat_ratio < pq.MAX_REPEAT_RATIO, "every line different"
    assert not pq.looks_repetitive(q), "and so: not a loop"


def test_the_separation_is_wide_on_both_sides():
    """0.38–0.70 against 0.00, and the threshold sits at 0.2."""
    leaked = [pq.classify(t).deliberation_ratio
              for t in (WEIMAR_250, DONAUESCHINGEN_388, FREIBURG_3030)]
    clean = [pq.classify(t).deliberation_ratio
             for t in (FREIBURG_3030_CLEAN, WLB_32_CLEAN)]

    assert min(leaked) > pq.DELIBERATION_SHARE
    assert max(clean) < pq.DELIBERATION_SHARE
    assert min(leaked) - max(clean) > 0.15


# ── what must not trip it ────────────────────────────────────────────────────

def test_one_cue_line_is_not_an_argument():
    """A page with a single leaked line is still a reading of the page.

    The floor is what makes that true: one cue line in six is 0.17, under the
    0.2 the measured pages cleared by twice over.
    """
    page = "\n".join(["Mein lieber Freund, die Handschrift ist angekommen.",
                      "Wait, is that an 'f'?",
                      "Mit herzlichem Grusse, den 4 Maerz 1831.",
                      "JvLassberg.", "Eppishausen.", "Beiliegend zwei Bogen."])

    q = pq.classify(page)
    assert q.deliberation_ratio > 0.0, "the cue is seen"
    assert q.deliberation_ratio < pq.DELIBERATION_SHARE, "and outvoted"
    assert q.verdict == "ok"


def test_a_cue_is_read_at_the_start_of_a_line_not_inside_one():
    """Deliberation addresses itself at the head of a line. A letter that
    happens to contain English mid-sentence is not arguing with itself, and
    anchoring is what keeps the two apart without a word list."""
    inside = "Er schrieb mir: look at the seal, dear friend, und sandte es."
    assert pq.deliberation_ratio([inside]) == 0.0
    assert pq.deliberation_ratio(["Look at the seal:"]) == 1.0


def test_too_few_lines_for_a_share_to_mean_anything():
    """An address panel of two lines cannot establish a ratio."""
    assert pq.classify("Wait, is it?\nLook at word 1:").verdict != "deliberation"


def test_the_marks_of_the_strict_prompt_are_not_deliberation():
    """`[unlesbar]` and `[stempel: …]` are the prompt's own answers for a page
    the model declines or a page carrying only a stamp. They are `short`, which
    is what they are, and never mistaken for arguing."""
    for text in ("[unlesbar]",
                 "[stempel: Schillermuseum Marbach] [fremde hand: 11551]"):
        assert pq.classify(text).verdict == "short"


# ── the threshold stays arithmetic ───────────────────────────────────────────

def test_the_ratio_is_stored_so_a_change_needs_no_rereading(tmp_path):
    q = pq.classify(WEIMAR_250)

    assert q.as_dict()["deliberation_ratio"] == q.deliberation_ratio
    # Raise the bar past the measured page and it reclassifies, from the stored
    # measures alone.
    assert not pq.looks_deliberated(q, deliberation_share=0.9)
