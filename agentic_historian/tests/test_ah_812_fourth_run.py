"""What the fourth run on tei showed (08.11.2026, #812).

``ask "Wie wird Almosen in den Königsfelder Urkunden begründet?"`` now found
the charters (43 passages, 12 shown) and still held 0 of 4 claims, every one
"quote not in the cited passage". Read against the edition:

* two quotes were verbatim and failed only on the ``…`` the model copied
  from the edge of the document window it was shown (U.17/0320, U.17/0156);
* one left out a stretch with ``…`` — standard in a scholarly quote — and
  also wrote "da si" where the charter has "daz si";
* one ("absti nen tia rum , elemosina rum , disciplina") is not in StAAG
  AA/0429 at all.

The first two must bind; the last two must not. The texts are excerpts of
``get_entry`` for the three entries, verbatim, 08.11.2026.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_812_fourth_run.py
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import evidence as ev
from agents import argument_agent as af

#: StAAG U.17/0320 (1363), Queen Agnes's gift of Birmenstorf.
U0320 = ("mit soͤlichem gedinge, ✳ daz si alle jar eweclich ✳ von unsern wêgen ✳ "
         "luterlich durch got ✳ zuͦ einem rehten almuͦsen ✳ da von richten und gèben "
         "soͤllen ✳ unsern getrùwen, ✳ andaͤhtigen, ✳ ✳ dem gardian und dem convent ✳ "
         "der Minren Bruͦder ✳ dez closters ze Kùngsvèlt, ✳ oͧch unserr stift, ✳ von "
         "allen den vorgenanten guͤtern ✳ acht mark gèltes nach dizz landes rèht und "
         "gewonheit, ✳ vollenclich ✳ als si von den selben guͤtern vallent, ✳ aun alle "
         "gevaͤrde, ✳ zuͦ besserung irs gewandes ✳ und zuͦ an derr iro notdurft. ✳ und "
         "waz die vorgenan tē ten guͤter allù gemeinlich")
#: StAAG U.17/0156 (1337), Duke Albrecht's anniversary for Katharina.
U0156 = ("das si da von eweclich began sùllent den jartag ✳ unserr lieben swester ✳ "
         "Katherinen , ✳ unsers lieben bruͦders hˀ her tzog Lup̄ Lupolt , seligen, wite "
         "bē ben , ✳ mit messen, ✳ mit vigilien, ✳ mit spenden, mit kertzen ✳ und mit "
         "andren dingen in alle die wi¬se, als unsers bruͦders hˀ her tzog Lup̄ Lupolt , "
         "seli gē gen , ir wirtes, jartag verschriben ist. ✳ wir wel lē len oͧch")
#: StAAG AA/0429, fol. 24r–28r (1467), the passage the invented quote leaned on.
AA0429 = ("ut in tˀ ter alia claustralis abstinentie nexi bꝰ bus relegate puritate "
          "fulgea tꝭ tis observantie rˀ re gularis et volunta tꝭ tis libitum coartantes")


def _window(doc_id, text):
    """A passage as the model is shown it: a window of its document, cut at
    both ends."""
    return ev.edition_item("kf", doc_id, f"…{text}…")


def _bind(item, quote):
    ok, bad = af.check_bindings([{"claim": "x", "evidence": [
        {"id": item.evidence_id, "quote": quote}]}], {item.evidence_id: item})
    return (ok or bad)[0].bindings[0]


# ── the run's four quotes, as the model wrote them ───────────────────────────

def test_a_verbatim_quote_with_the_windows_cut_marks_binds():
    b = _bind(_window("U-17_0320", U0320),
              "…durch got zuͦ einem rehten almuͦsen da von richten und gèben…")
    assert b.ok and b.layer == "reading"
    assert b.quote == "durch got zuͦ einem rehten almuͦsen da von richten und gèben"
    assert b.source_span.startswith("durch got ✳ zuͦ einem rehten almuͦsen")


def test_the_anniversary_quote_binds_across_separators_and_a_line_break():
    b = _bind(_window("U-17_0156", U0156),
              "…mit spenden, mit kertzen und mit andren dingen in alle die wise, als "
              "unsers bruͦders her tzog Lupolt , seli gen , ir wirtes, jartag "
              "verschriben ist.")
    assert b.ok
    assert "wi¬se" in b.source_span and "Lup̄ Lupolt" in b.source_span


def test_an_omission_does_not_excuse_a_misread_word():
    b = _bind(_window("U-17_0320", U0320),
              "…da si alle jar eweclich von unsern wêgen luterlich … zuͦ besserung irs "
              "gewandes und zuͦ an derr iro notdurft.")
    assert not b.ok and b.reason == "quote not in the cited passage"


def test_an_invented_quote_is_still_refused():
    b = _bind(_window("AA_0429_0740b", AA0429),
              "…absti nen tia rum , elemosina rum , disciplina …")
    assert not b.ok and b.reason == "quote not in the cited passage"


# ── omissions ────────────────────────────────────────────────────────────────

def test_an_omission_binds_and_shows_what_was_left_out():
    b = _bind(_window("U-17_0320", U0320),
              "daz si alle jar eweclich von unsern wêgen luterlich … zuͦ besserung irs "
              "gewandes und zuͦ an derr iro notdurft.")
    assert b.ok
    # the reader sees whom the alms went to, which the quote left out
    assert "dem gardian und dem convent ✳ der Minren Bruͦder" in b.source_span
    assert b.source_span.endswith("iro notdurft.")
    line = "\n".join(af._cite(_window("U-17_0320", U0320), b.quote, b.source_span))
    assert "Im Zusammenhang, mit dem Ausgelassenen:" in line


def test_three_dots_are_an_omission_too_and_are_written_as_one():
    b = _bind(_window("U-17_0320", U0320),
              "luterlich durch got ... einem rehten almuͦsen")
    assert b.ok and b.quote == "luterlich durch got … einem rehten almuͦsen"


def test_the_pieces_must_stand_in_the_passages_order():
    b = _bind(_window("U-17_0320", U0320),
              "zuͦ besserung irs gewandes … luterlich durch got")
    assert not b.ok and b.reason == "quote not in the cited passage"


def test_a_piece_of_one_word_is_too_short_to_check():
    b = _bind(_window("U-17_0320", U0320), "durch got … almuͦsen")
    assert not b.ok and b.reason == "omission leaves a piece too short to check"


def test_an_omission_cannot_splice_distant_text(monkeypatch):
    monkeypatch.setattr(af, "OMISSION_MAX_CHARS", 100)
    b = _bind(_window("U-17_0320", U0320),
              "daz si alle jar … iro notdurft.")
    assert not b.ok and b.reason == "omission spans more than 100 characters"


@pytest.mark.parametrize("quote", ["…", " … ", "...", "… …"])
def test_cut_marks_alone_are_an_empty_quote(quote):
    b = _bind(_window("U-17_0320", U0320), quote)
    assert not b.ok and b.reason == "empty quote"


def test_the_model_is_told_what_the_cut_mark_means():
    assert "abgeschnitten ist; es\ngehört nicht ins Zitat" in af._BIND_PROMPT
    assert "Auslassung" not in af._BIND_PROMPT.split("Behaupte nur")[0]
