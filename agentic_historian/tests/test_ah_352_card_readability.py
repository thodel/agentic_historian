"""Gate-2 card readability — labels, quoting, page prefix.

Pins what the live 9-candidate card on tei got wrong: every button read
``Bat 664 R 00027.Jpg:Trocr/Trocr-Medieval-Escriptmask`` — the page repeated nine
times, the model identifier title-cased into something that no longer matches the
preference log, and only the first line of each excerpt actually quoted.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_352_card_readability.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import path_compare as pc          # noqa: E402
from runstate import RunState      # noqa: E402

PAGE = "BAT_664_r_00027.jpg"
ESCRIPT = f"{PAGE}:trocr/trocr-medieval-escriptmask"
KRAKEN = f"{PAGE}:kraken/kraken-early_modern_german_16"
P2 = "BAT_664_r_00028.jpg"


# ── the identifier must survive ──────────────────────────────────────────────

def test_the_model_id_is_not_title_cased():
    """It is an identifier, not prose. Title-casing it means the label can no
    longer be matched against what the preference log stores (#332)."""
    label = pc._card_label(ESCRIPT, show_page=False)
    assert "medieval-escriptmask" in label
    assert "Medieval-Escriptmask" not in label


def test_underscores_in_a_model_id_are_preserved():
    """`kraken-early_modern_german_16` is the real gateway id; the old fallback
    turned it into "Early Modern German 16", which matches nothing."""
    assert "early_modern_german_16" in pc._card_label(KRAKEN, show_page=False)


def test_the_engine_gets_its_display_name():
    assert pc._card_label(ESCRIPT, show_page=False).startswith("TrOCR")
    assert pc._card_label(KRAKEN, show_page=False).startswith("Kraken")


def test_the_engine_is_not_repeated_from_the_model_id():
    """"trocr/trocr-medieval-escriptmask" says trocr twice; a button has 80 chars."""
    assert pc._card_label(ESCRIPT, show_page=False).lower().count("trocr") == 1


# ── the page prefix ──────────────────────────────────────────────────────────

def test_the_page_is_dropped_when_every_candidate_shares_it():
    """Nine buttons carrying the same page prefix crowd out the part that differs."""
    assert PAGE not in pc._card_label(ESCRIPT, show_page=False)


def test_the_page_is_kept_when_the_card_spans_several_pages():
    """There it is the only thing distinguishing two identical engine/model rows."""
    assert PAGE in pc._card_label(ESCRIPT, show_page=True)


def test_multi_page_is_detected_from_the_candidate_names():
    assert pc._multi_page([ESCRIPT, KRAKEN]) is False
    assert pc._multi_page([ESCRIPT, f"{P2}:trocr/trocr-medieval-escriptmask"]) is True


# ── the canonical short names still behave ───────────────────────────────────

def test_canonical_names_are_unchanged():
    assert pc._label_for("vlm") == "VLM"
    assert pc._label_for("kraken") == "Kraken"
    assert pc._label_for("party") == "PARTY"


def test_an_unknown_bare_engine_name_is_still_title_cased():
    """The case the fallback was originally written for — prose-ish, not an id."""
    assert pc._label_for("custom_engine") == "Custom Engine"


# ── quoting ──────────────────────────────────────────────────────────────────

def test_every_line_of_an_excerpt_is_quoted():
    """`"> " + text` quotes only line 1; the rest rendered as loose body text and
    destroyed the visual separation between candidates on the live card."""
    quoted = pc._quote("erste zeile\nzweite zeile\ndritte zeile")
    assert quoted.splitlines() == ["> erste zeile", "> zweite zeile", "> dritte zeile"]


def test_quoting_an_empty_excerpt_does_not_crash():
    assert pc._quote("") == "> "


# ── the rendered card ────────────────────────────────────────────────────────

def _state_and_paths():
    st = RunState(doc_id="prefs-test-BAT664")
    paths = {ESCRIPT: "unser fründtlich grus\nvon der stösse wegen",
             KRAKEN: "luser femitlich gens\nUan de scosse io e"}
    st.artifacts["paths"] = paths
    return st, paths


def test_the_card_names_the_page_once_in_the_header():
    st, paths = _state_and_paths()
    card = pc.render_vote_card(st, paths)
    assert card.count(PAGE) == 1, "page should appear once, not on every candidate"
    assert PAGE in card.splitlines()[0]


def test_the_card_body_lines_are_all_quoted():
    st, paths = _state_and_paths()
    card = pc.render_vote_card(st, paths)
    assert "> von der stösse wegen" in card


def test_the_card_still_fits_discords_limit():
    st, paths = _state_and_paths()
    assert len(pc.render_vote_card(st, paths)) <= 2000
