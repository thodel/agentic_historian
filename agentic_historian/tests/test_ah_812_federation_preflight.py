"""Agent F against what the federation really sends (#812, before the live run).

The payloads below are verbatim answers of the two full-text servers, asked on
2026-11-06 through their MCP connectors: Königsfelden ``search_fulltext``
("elemosina", "spital") and HGB/EOS ``search_text`` ("almosen"). Three things
they showed that the synthetic fixtures had not:

* the matched term comes wrapped in highlight markup — ``<mark>`` (KF),
  ``<b>`` (EOS) — so a verbatim quote of the words failed the binding check;
* every hit carries a ``year``, and KF a shelfmark ``title``; the client
  dropped both, so every edition passage was undated to the period check;
* KF answers full-text search but was not registered for it, so the question
  #812 uses as its example (Königsfelden) found no edition text at all.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_812_federation_preflight.py
"""

import asyncio
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agents import argument_agent as af
from knowledge_hub import mcp_registry as reg
from utils import mcp_client as mc

KF_SOURCE = "Editionsprojekt «Die Urkunden und Akten des Klosters und der Hofmeisterei Königsfelden (1300–1658)»"

KF_ELEMOSINA = {"result": [
    {"id": "U-17_0289", "title": "StAAG U.17/0289", "short_id": "StAAG U.17/0289",
     "year": 1359, "source": KF_SOURCE,
     "snippet": "…dtˀ dert jar und dar nach in dem nùnden un̄ und fùnfzigostem jare. ✳ de "
                "el̄ia <mark>elemosina</mark> pro infirmis unùtz und langest abgeloͤst ✳ K ✳ T "
                "✳ 76 ✳ von dem hoff zu Sar mēs…"},
    {"id": "U-17_0546a", "title": "StAAG U.17/0546a", "short_id": "StAAG U.17/0546a",
     "year": 1425, "source": KF_SOURCE,
     "snippet": "…ꝝ rum , vigi¬lia ꝝ rum , ieiunio ꝝ rum , absti nē nen tia ꝝ rum , ✳ "
                "<mark>elemosina</mark> ꝝ rum , disciplina ꝝ rum ce tˀo tero ꝝ rum qƺ que "
                "spū spiritu ali ū…"},
]}

EOS_ALMOSEN = {"result": [
    {"id": "6e10c2d5-75e6-46e5-aa31-e8cb558b1a66_20260528", "dossier_id": "HGB_1_024_088",
     "year": 1604, "source": "Wurstisen",
     "location": "POINT(2611371.6350586987 1267062.6128145475)",
     "snippet": "Die Mächen Schule auf dem Barfüsserplatz neben dem <b>Almosen</b> ewird "
                "angelegt."},
    {"id": "b7f49120-f68a-408b-8d4e-ff91d7aa620b_20260528", "dossier_id": "HGB_1_024_052",
     "year": 1589, "source": "St. Leonhard",
     "location": "POINT(2611311.9655639525 1267150.2388157153)",
     "snippet": "Cristofel Wiest der <b>Almosen</b> Schaf ner von dem Haus Babenberg uf "
                "Martini 1ß 1 Heierer."},
]}


async def _recorded(source, tool, args):
    return {"kf": KF_ELEMOSINA, "eos": EOS_ALMOSEN}.get(source.name, {"result": []})


def test_kf_is_searched_for_full_text_and_hls_is_not():
    names = {s.name for s in reg.sources_for_kind("fulltext")}
    assert {"eos", "kf"} <= names
    assert "hls" not in names, "a lexicon is secondary literature, not a source to quote"


def test_the_client_keeps_year_and_title():
    hits = asyncio.run(mc.search_fulltext("elemosina", call_tool=_recorded))
    by_id = {h.doc_id: h for h in hits}
    assert by_id["U-17_0289"].year == "1359"
    assert by_id["U-17_0289"].title == "StAAG U.17/0289"
    assert by_id["b7f49120-f68a-408b-8d4e-ff91d7aa620b_20260528"].title == "HGB_1_024_052"


def test_edition_items_carry_the_words_not_the_markup():
    items = af.federation_fulltext("elemosina", call_tool=_recorded)
    quotes = " ".join(i.quote for i in items)
    assert "<mark>" not in quotes and "<b>" not in quotes
    kf = next(i for i in items if i.doc_id == "U-17_0289")
    assert "de el̄ia elemosina pro infirmis unùtz" in kf.quote
    assert kf.source["datierung"] == "1359"
    assert kf.source["title"] == "StAAG U.17/0289"


def test_a_verbatim_quote_of_a_highlighted_term_now_binds():
    items = af.federation_fulltext("almosen", call_tool=_recorded)
    pool = {i.evidence_id: i for i in items}
    eos = next(i for i in items if "Wiest" in i.quote)
    ok, bad = af.check_bindings([{"claim": "Ein Almosenschaffner zinst an St. Leonhard.",
                                  "evidence": [{"id": eos.evidence_id,
                                                "quote": "Cristofel Wiest der Almosen Schaf ner"}]}],
                                pool)
    assert ok and not bad


def test_the_period_check_reads_the_year_the_source_sent():
    items = af.federation_fulltext("elemosina", call_tool=_recorded)
    by_id = {i.doc_id: i for i in items}
    period = {"from": 1300, "to": 1399}
    assert af.period_check(by_id["U-17_0289"], period) == "inside"
    assert af.period_check(by_id["U-17_0546a"], period) == "outside"


def test_only_highlight_tags_go_editorial_brackets_stay():
    assert af.plain_snippet("der <b>spital</b> &amp; <mark class='x'>hof</mark>") == \
        "der spital & hof"
    # an edition's own angle brackets (supplied text) are the source's words
    assert af.plain_snippet("an <sant> Johans <tag>") == "an <sant> Johans <tag>"


def test_the_citation_names_the_shelfmark():
    items = af.federation_fulltext("elemosina", call_tool=_recorded)
    kf = next(i for i in items if i.doc_id == "U-17_0289")
    assert af._describe(kf).startswith("StAAG U.17/0289 · 1359 · Edition kf")
    assert "StAAG U.17/0289" in "\n".join(af._cite(kf, "elemosina pro infirmis"))
