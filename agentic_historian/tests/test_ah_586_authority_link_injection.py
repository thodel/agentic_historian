"""Tests for SEC-15 (#586): authority-id link injection in the published index.md.

`_entity_links` interpolates LLM-supplied authority ids (GND, HLS, Wikidata) into
Markdown links. A manipulated id could close the link early and inject a different
one on the public GitHub-Pages page. Ids are now validated against a tight
character set before they reach the link template.
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from utils import publish_github as pg   # noqa: E402


def test_valid_ids_render_links():
    out = pg._entity_links({"gnd": "4074335-4", "hls": "de/articles/012345",
                            "wikidata": "Q42"})
    assert "[GND](https://d-nb.info/gnd/4074335-4)" in out
    assert "[HLS](https://hls-dhs-dss.ch/de/de/articles/012345)" in out
    assert "[WD](https://www.wikidata.org/entity/Q42)" in out


def test_id_suffix_form_is_accepted():
    assert "https://d-nb.info/gnd/118540238" in pg._entity_links({"gnd_id": "118540238"})


def test_a_markdown_breakout_id_is_dropped():
    """The injection the fix exists for: an id that would close the ) and add a
    second link is refused, so nothing is rendered for it."""
    evil = "x) [click here](https://evil.example"
    assert pg._entity_links({"gnd": evil}) == ""


def test_ids_with_spaces_brackets_or_newlines_are_dropped():
    for bad in ["a b", "a[b", "a]b", "a(b", "a)b", "a\nb", "a`b", "x" * 65, ""]:
        assert pg._entity_links({"wikidata": bad}) == "", bad


def test_one_bad_id_does_not_sink_the_valid_ones():
    out = pg._entity_links({"gnd": "evil) [x](http://e", "wikidata": "Q7"})
    assert "evil" not in out
    assert "[WD](https://www.wikidata.org/entity/Q7)" in out
