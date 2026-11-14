"""#868 — the Königsfelden server renders an in-word abbreviation as two whole words.

Until kf_mcp's build_db change (14.11.2026) the edition's indentation came
through as spaces, and a word with an abbreviation inside arrived in three
pieces: ``unn sz₎ szer``, ``hofmeis tˀ ter``. The reading layer kept the split
(``unn szer``, ``hofmeis ter``), the model quoted the whole word, and the
binding check rejected the quote — in both live runs of #812.

The server now writes the raw word and its reading, both whole: ``unnsz₎
unnszer``, ``hofmeistˀ hofmeister``. This pins that the reading layer here
reads such a pair as one abbreviation and its expansion — the same rule as for
``un̄ und`` — so the reading is ``unnszer hofmeister`` and a quote of it is
found. Nothing changes for text built before the server change.

Run from the repo root:
    pytest agentic_historian/tests/test_ah_868_whole_word_layers.py
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import edition_text as et  # noqa: E402


@pytest.mark.parametrize("text, reading", [
    ("unnsz₎ unnszer hofmeister hàt", "unnszer hofmeister hàt"),
    ("min her hofmeistˀ hofmeister, den", "min her hofmeister, den"),
    ("Bremgartē Bremgarten, schulth̄ schultheiss", "Bremgarten, schultheiss"),
    ("wisz₎ wiszen gnaͣdigen", "wiszen gnaͣdigen"),
    ("Oͤstˀ Oͤster rich", "Oͤster rich"),
])
def test_a_whole_word_pair_reads_as_abbreviation_and_expansion(text, reading):
    assert et.reading_layer(text).text == reading


def test_a_quote_of_the_whole_word_is_found_with_the_editions_characters():
    text = "das unnsz₎ unnszer hofmeistˀ hofmeister fùr sich selbs"
    layer = et.reading_layer(text)
    spans = et.locate("unnszer hofmeister fùr", layer, text)
    # the span starts at the first matched character and carries both layers in between
    assert spans and text[spans[0][0]:spans[0][1]] == "unnszer hofmeistˀ hofmeister fùr"


def test_text_built_before_the_server_change_reads_as_before():
    assert et.reading_layer("unn sz₎ szer hofmeis tˀ ter").text == "unn szer hofmeis ter"
