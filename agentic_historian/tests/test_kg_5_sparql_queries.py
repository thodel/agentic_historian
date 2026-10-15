"""
test_kg_5_sparql_queries.py — the canned scholarly queries (KG-5, #331).

Offline: a fixture graph built with the real exporter, no QLever and no network.
The endpoint itself is covered by the runbook in deploy/qlever/README.md.

The issue's own test list:

  1. Each canned query parses and runs against a fixture graph, returning the
     expected rows.
  2. EVERY result row carries a document URI — traceability, non-negotiable.
  3. A query returning nothing degrades to an honest "no results", never a
     crash and never a blank.

And the acceptance: four substantive questions answerable without writing
SPARQL, every answer traceable to a document and a reading.

Two of those questions needed facts the graph did not carry — the care flag and
the dating — so the century parser and both new document predicates are tested
here too.

Run: pytest agentic_historian/tests/test_kg_5_sparql_queries.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from rdflib import Graph, Literal, URIRef                        # noqa: E402

from knowledge_hub import queries as q                           # noqa: E402
from knowledge_hub import rdf_export as rx                       # noqa: E402


# ── the fixture corpus ──────────────────────────────────────────────────────

def _doc(doc_id, *, care=True, care_types=("Almosen",), dating="15. Jahrhundert",
         uncertain=False, entities=None, closest="kraken", engines=True):
    """One pipeline record, in the shape `document_to_rdf` reads."""
    datierung = {"wert": dating}
    if uncertain:
        datierung["unsicher"] = True
    care_flag = ({"is_care_related": True, "care_context": "Spital",
                  "care_types": list(care_types), "beteiligte": ["X"]}
                 if care else {"is_care_related": False, "care_context": "",
                               "care_types": [], "beteiligte": []})
    recognitions = ([
        {"engine": "trocr", "model_id": "trocr-v1", "text": "lesung a",
         "confidence": 0.7},
        {"engine": "kraken", "model_id": "kraken-v3", "text": "lesung b",
         "confidence": 0.9},
    ] if engines else [])
    record = {
        "doc_id": doc_id,
        "transcription": "lesung b",
        "description": {"source_description": "d",
                        "source_json": {"Datierung": datierung},
                        "care_flag": care_flag},
        "entities": {"entities": entities if entities is not None else [
            {"text": "Heinrich von Wiler", "type": "PERSON",
             "normalised": "Heinrich von Wiler", "gnd": "12175376X"},
            {"text": "Anna Müller", "type": "PERSON", "normalised": "Anna Müller"},
            {"text": "Thun", "type": "PLACE", "normalised": "Thun"},
        ]},
        "recognitions": recognitions,
        "a_meta": {"fusion_strategy": "no-merge", "fusion_agreement_cer": 1.05},
    }
    if closest == "combined":
        record["closest_reading"] = {"chosen": ["kraken", "trocr"],
                                     "combined": True, "text": "lesung ab",
                                     "editor_pseudonym": "ed-9",
                                     "confirmed_at": "2026-10-01T10:00:00Z"}
    elif closest:
        record["closest_reading"] = {"chosen": [closest], "combined": False,
                                     "editor_pseudonym": "ed-7",
                                     "confirmed_at": "2026-10-01T10:00:00Z"}
    return record


@pytest.fixture
def graph() -> Graph:
    """Four documents: two care-flagged in the 15th c., one 16th, one not care."""
    g = Graph()
    rx.document_to_rdf(g, _doc("care-15-a"))
    rx.document_to_rdf(g, _doc("care-15-b", closest="combined",
                               entities=[
                                   {"text": "Anna Müller", "type": "PERSON",
                                    "normalised": "Anna Müller"},
                                   {"text": "Niklaus Spittler", "type": "CARE_ACTOR",
                                    "normalised": "Niklaus Spittler",
                                    "hls": "012345"},
                               ]))
    rx.document_to_rdf(g, _doc("care-16", dating="1512"))
    rx.document_to_rdf(g, _doc("plain-15", care=False))
    return g


def _rows(graph, name, **params):
    return q.run(name, graph, **params).rows


# ── 1. every query runs and returns what it should ─────────────────────────

def test_every_canned_query_parses_and_runs(graph):
    """The whole surface, in one sweep: a query that does not parse is useless
    however good its question."""
    for name in q.names():
        params = {}
        if "century" in q.get(name).required:
            params["century"] = "15"
        if "document" in q.get(name).required:
            params["document"] = "care-15-a"
        answer = q.run(name, graph, **params)
        assert isinstance(answer.rows, list), name


def test_four_questions_are_offered():
    """The acceptance: at least four substantive questions (seven since KG-11)."""
    assert len(q.names()) >= 4
    for query in q.QUERIES.values():
        assert query.question.strip()
        assert "?" in query.question


# ── 2. traceability ────────────────────────────────────────────────────────

def test_every_query_selects_its_document_first():
    """Structural, not a convention: the constructor refuses otherwise. First
    rather than merely present, because a row is read left to right and gets
    its columns dropped from the right when a message is too long."""
    for query in q.QUERIES.values():
        assert query.columns[0] == "document", query.name


def test_a_query_without_a_document_column_cannot_be_built():
    with pytest.raises(ValueError, match="trace"):
        q.CannedQuery(name="untraceable", question="Wie viele?",
                      columns=("count",), body="?s ?p ?o .")


def test_a_query_that_buries_its_document_cannot_be_built():
    with pytest.raises(ValueError, match="first"):
        q.CannedQuery(name="buried", question="Wie viele?",
                      columns=("count", "document"), body="?s ?p ?o .")


def test_every_row_of_every_query_carries_a_document_uri(graph):
    """The non-negotiable one. An answer nobody can trace is not an answer."""
    for name in q.names():
        params = {}
        if "century" in q.get(name).required:
            params["century"] = "15"
        if "document" in q.get(name).required:
            params["document"] = "care-15-a"
        answer = q.run(name, graph, **params)
        if name in q.BESTAND_ONLY:
            # Asks for what only the Bestandsgraph carries (a dataset per
            # document, E13 roles, C5 memberships); on the corpus fixture the
            # honest answer is no rows. test_kg_11 covers them on their graph.
            assert answer.rows == [], name
            continue
        assert answer.rows, f"{name} returned nothing on the fixture"
        for row in answer.rows:
            assert row["document"], f"{name}: a row without a document"
            assert row["document"].startswith(rx.CORPUS_BASE), row["document"]


def test_the_document_uris_of_an_answer_resolve_into_the_graph(graph):
    """Traceable means the URI is in the graph, not merely URI-shaped."""
    answer = q.run("co-occurrence", graph)
    for uri in answer.documents():
        assert (URIRef(uri), None, None) in graph


def test_a_reading_row_names_the_reading_as_well_as_the_document(graph):
    """#331: traceable to a document **and a reading**."""
    rows = _rows(graph, "readings-of-page", document="care-15-a")
    for row in rows:
        assert row["reading"].startswith(rx.CORPUS_BASE + "reading/")


# ── persons in care-related documents of a century ─────────────────────────

def test_persons_in_care_documents_of_a_century(graph):
    rows = _rows(graph, "persons-in-care-documents", century="15")
    docs = {row["document"].rsplit("/", 1)[-1] for row in rows}
    assert docs == {"care-15-a", "care-15-b"}
    names = {row["personLabel"] for row in rows}
    assert names == {"Heinrich von Wiler", "Anna Müller", "Niklaus Spittler"}


def test_the_century_filter_excludes_other_centuries(graph):
    rows = _rows(graph, "persons-in-care-documents", century="16")
    docs = {row["document"].rsplit("/", 1)[-1] for row in rows}
    assert docs == {"care-16"}


def test_a_document_that_is_not_care_flagged_is_not_in_the_care_answer(graph):
    rows = _rows(graph, "persons-in-care-documents", century="15")
    assert all("plain-15" not in row["document"] for row in rows)


def test_the_authority_id_travels_with_the_person(graph):
    rows = _rows(graph, "persons-in-care-documents", century="15")
    by_name = {row["personLabel"]: row for row in rows}
    assert by_name["Heinrich von Wiler"]["authority"] == \
        "https://d-nb.info/gnd/12175376X"
    import entity_index
    assert by_name["Niklaus Spittler"]["authority"] == \
        entity_index.authority_uri("hls", "012345")


def test_a_person_without_an_authority_id_is_still_returned(graph):
    """Dropping them would make the corpus look better linked than it is."""
    rows = _rows(graph, "persons-in-care-documents", century="15")
    anna = [r for r in rows if r["personLabel"] == "Anna Müller"]
    assert anna
    assert all(r["authority"] is None for r in anna)


def test_places_are_not_persons(graph):
    rows = _rows(graph, "persons-in-care-documents", century="15")
    assert all(row["personLabel"] != "Thun" for row in rows)


def test_a_care_actor_counts_as_a_person(graph):
    """`CARE_ACTOR` is the type this corpus uses for an institution's agent;
    excluding it would hide exactly the people a care question is about."""
    rows = _rows(graph, "persons-in-care-documents", century="15")
    assert any(row["personLabel"] == "Niklaus Spittler" for row in rows)


def test_an_uncertain_dating_is_marked_in_the_answer():
    """A historian narrowing by century has to be able to exclude the guesses."""
    g = Graph()
    rx.document_to_rdf(g, _doc("guessed", dating="um 1450"))
    rows = _rows(g, "persons-in-care-documents", century="15")
    assert rows
    assert all(row["datingUncertain"] == "true" for row in rows)
    assert all(row["dating"] == "um 1450" for row in rows)


def test_a_certain_dating_is_not_marked(graph):
    rows = _rows(graph, "persons-in-care-documents", century="15")
    assert all(row["datingUncertain"] is None for row in rows)


# ── all readings of a page, with their model and the historian's choice ────

def test_all_readings_of_a_page_with_their_models(graph):
    rows = _rows(graph, "readings-of-page", document="care-15-a")
    engines = {row["engine"] for row in rows}
    assert {"trocr", "kraken"} <= engines
    by_engine = {row["engine"]: row for row in rows}
    assert by_engine["kraken"]["modelId"] == "kraken-v3"
    assert by_engine["trocr"]["modelId"] == "trocr-v1"
    assert by_engine["trocr"]["text"] == "lesung a"


def test_the_readings_say_which_one_the_historian_chose(graph):
    """The #326 story in queryable form."""
    rows = _rows(graph, "readings-of-page", document="care-15-a")
    chosen = [row for row in rows if row["closest"] == "true"]
    assert len(chosen) == 1
    assert chosen[0]["engine"] == "kraken"
    assert chosen[0]["editor"] == "ed-7"
    # The caveat travels with the choice: this is an editorial act, not a claim
    # that the text is correct.
    assert chosen[0]["editorialStatus"] == "revisable_editorial_choice"


def test_a_combined_reading_is_attributed_to_the_editor_not_a_model(graph):
    rows = _rows(graph, "readings-of-page", document="care-15-b")
    chosen = [row for row in rows if row["closest"] == "true"]
    assert len(chosen) == 1
    assert chosen[0]["engine"] is None          # no machine produced it
    assert chosen[0]["editor"] == "ed-9"
    assert chosen[0]["text"] == "lesung ab"


def test_the_engine_readings_of_a_combined_page_are_still_listed(graph):
    """All readings of the page — the combination does not replace them."""
    rows = _rows(graph, "readings-of-page", document="care-15-b")
    assert {"trocr", "kraken"} <= {row["engine"] for row in rows}


def test_the_engine_and_the_model_id_come_from_the_same_model(graph):
    """A reading attributed to a model **and** a person must not cross them.

    Two OPTIONALs beside each other each pick their own ``wasAttributedTo``, so
    ``?engine`` could come from the model and ``?modelId`` from another
    attribution of the same reading. The exporter does not produce that today;
    the nested form is why it cannot start producing it unnoticed.
    """
    g = Graph()
    rx.document_to_rdf(g, _doc("crossed"))
    reading = rx.reading_uri("crossed", "kraken")
    editor = rx.agent_uri("ed-7")
    g.add((reading, rx.PROV["wasAttributedTo"], editor))
    second = rx.model_uri("ghost", "ghost-v9")
    g.add((reading, rx.PROV["wasAttributedTo"], second))
    g.add((second, rx.RDF.type, rx.SDHSS["RecognitionModel"]))
    g.add((second, rx.SDHSS["engine"], Literal("ghost")))

    rows = [r for r in _rows(g, "readings-of-page", document="crossed")
            if r["reading"] == str(reading)]
    for row in rows:
        # Whichever model a row picked, its id belongs to that same model.
        if row["engine"] == "kraken":
            assert row["modelId"] == "kraken-v3"
        if row["engine"] == "ghost":
            assert row["modelId"] is None


def test_a_page_nobody_has_judged_has_no_chosen_reading():
    g = Graph()
    rx.document_to_rdf(g, _doc("unjudged", closest=None))
    rows = _rows(g, "readings-of-page", document="unjudged")
    assert rows
    assert all(row["closest"] is None for row in rows)
    assert all(row["editor"] is None for row in rows)


def test_an_unbound_column_is_none_and_not_an_empty_string(graph):
    """`None` and `""` are different findings — an absent authority link is not
    an empty label."""
    rows = _rows(graph, "readings-of-page", document="care-15-a")
    working = [r for r in rows if r["role"] == "working"][0]
    assert working["closest"] is None


# ── corpus health: which source links the most, what stays unlinked ───────

def test_authority_coverage_counts_per_source_and_document(graph):
    rows = _rows(graph, "authority-coverage")
    pairs = {(row["document"].rsplit("/", 1)[-1], row["source"]): int(row["entities"])
             for row in rows}
    assert pairs[("care-15-a", "gnd")] == 1
    assert pairs[("care-15-a", "unlinked")] == 2        # Anna Müller + Thun
    assert pairs[("care-15-b", "hls")] == 1


def test_unlinked_entities_are_a_value_not_a_missing_row(graph):
    """A corpus-health query whose gaps are absent rows reports perfect health."""
    rows = _rows(graph, "authority-coverage")
    assert any(row["source"] == "unlinked" for row in rows)


def test_the_health_query_is_per_document_so_its_rows_stay_traceable(graph):
    """The traceability rule bent this query into a better shape: "which
    documents have unlinked persons" can be acted on, "47 unlinked" cannot."""
    rows = _rows(graph, "authority-coverage")
    assert all(row["document"] for row in rows)
    assert q.get("authority-coverage").columns[0] == "document"


def test_the_most_linking_source_is_first(graph):
    rows = _rows(graph, "authority-coverage")
    counts = [int(row["entities"]) for row in rows]
    assert counts == sorted(counts, reverse=True)


# ── co-occurrence ─────────────────────────────────────────────────────────

def test_persons_mentioned_in_the_same_document(graph):
    rows = _rows(graph, "co-occurrence")
    # Unordered: the pair is ordered by URI (see the next test), and a
    # CARE_ACTOR's URI sits under a different path than a PERSON's, so the
    # labels do not come out alphabetically.
    pairs = {frozenset((row["labelA"], row["labelB"])) for row in rows}
    assert frozenset(("Anna Müller", "Heinrich von Wiler")) in pairs
    assert frozenset(("Anna Müller", "Niklaus Spittler")) in pairs


def test_each_pair_appears_once_and_never_with_itself(graph):
    """Without the ordering filter every pair appears twice and every person is
    paired with themselves — which turns a count into a number nobody can read."""
    rows = _rows(graph, "co-occurrence")
    for row in rows:
        assert row["personA"] != row["personB"]
        assert row["personA"] < row["personB"]
    seen = [(row["document"], row["personA"], row["personB"]) for row in rows]
    assert len(seen) == len(set(seen))


def test_co_occurrence_does_not_pair_a_person_with_a_place(graph):
    rows = _rows(graph, "co-occurrence")
    assert all("Thun" not in (row["labelA"], row["labelB"]) for row in rows)


def test_co_occurrence_spans_the_corpus_not_one_document(graph):
    rows = _rows(graph, "co-occurrence")
    assert len({row["document"] for row in rows}) > 1


# ── 3. nothing found is an answer ────────────────────────────────────────

def test_an_empty_result_is_a_sentence_and_names_the_parameters(graph):
    """"Nothing found" and "nothing found *for the 13th century*" send a reader
    to different next steps."""
    answer = q.run("persons-in-care-documents", graph, century="13")
    assert answer.rows == []
    out = q.format_answer(answer)
    assert "Keine Treffer" in out
    assert "13" in out
    assert "```" not in out              # no empty code block


def test_an_empty_graph_does_not_crash_any_query():
    empty = Graph()
    for name in q.names():
        params = {}
        if "century" in q.get(name).required:
            params["century"] = "15"
        if "document" in q.get(name).required:
            params["document"] = "nothing"
        answer = q.run(name, empty, source="leer", **params)
        assert answer.rows == []
        assert "Keine Treffer" in q.format_answer(answer)


def test_an_unknown_query_name_lists_the_real_ones():
    with pytest.raises(q.QueryError) as caught:
        q.get("persons")
    for name in q.names():
        assert name in str(caught.value)


def test_a_missing_required_parameter_says_which(graph):
    with pytest.raises(q.QueryError, match="century"):
        q.run("persons-in-care-documents", graph)


def test_a_parameter_a_query_does_not_take_is_refused(graph):
    with pytest.raises(q.QueryError, match="document"):
        q.run("co-occurrence", graph, document="care-15-a")


def test_a_century_that_is_not_a_number_is_refused(graph):
    with pytest.raises(q.QueryError, match="Jahrhundert|century|15"):
        q.run("persons-in-care-documents", graph, century="fünfzehntes")


@pytest.mark.parametrize("value", ["0", "99", "-3"])
def test_an_implausible_century_is_refused(graph, value):
    with pytest.raises(q.QueryError):
        q.run("persons-in-care-documents", graph, century=value)


# ── parameters are bound, never interpolated ─────────────────────────────

def test_a_document_id_with_syntax_in_it_cannot_become_query_syntax(graph):
    """The injection case. It must return nothing, not run something else."""
    hostile = '"} UNION { ?document ?p ?o } #'
    answer = q.run("readings-of-page", graph, document=hostile)
    assert answer.rows == []


def test_the_bound_document_term_goes_through_the_exporters_own_slug(graph):
    """Built by hand, every id with a space or an umlaut would silently miss —
    and miss by returning no rows, which reads as "nothing found"."""
    g = Graph()
    rx.document_to_rdf(g, _doc("Brief Nr. 7 (Müller)"))
    rows = _rows(g, "readings-of-page", document="Brief Nr. 7 (Müller)")
    assert rows


def test_a_full_uri_is_accepted_as_well_as_an_id(graph):
    uri = str(rx.document_uri("care-15-a"))
    assert _rows(graph, "readings-of-page", document=uri)


def test_every_parameter_becomes_an_rdf_term_and_never_a_string():
    """The structural guarantee behind both ``run`` and ``as_text``: a
    converter returns a term, so no caller's text is ever concatenated into a
    query. A converter that returned a ``str`` would put it there."""
    from rdflib.term import Identifier

    for name in q.names():
        params = {}
        if "century" in q.get(name).required:
            params["century"] = "15"
        if "document" in q.get(name).required:
            params["document"] = "care-15-a"
        for key, term in q.param_terms(name, **params).items():
            assert isinstance(term, Identifier), f"{name}.{key} is {type(term)}"


def test_the_standalone_text_escapes_a_hostile_term():
    """`as_text` leaves the process. The canned queries' own converters already
    sanitise (a century becomes an int, a document id is slugged), so the
    escaping is tested where it actually operates: on a term that carries the
    hostile text."""
    hostile = q.CannedQuery(
        name="raw", question="Was?", columns=("document",),
        body="?document ?p ?free .",
        params={"free": lambda v: Literal(v)}, required=("free",))
    q.QUERIES["raw"] = hostile
    try:
        text = q.as_text("raw", free='x" . } UNION { ?document ?p ?o } #')
        assert "VALUES" in text
        assert 'x" . }' not in text         # rdflib's escaping, not ours
        Graph().query(text)                 # and it is still one valid query
    finally:
        del q.QUERIES["raw"]


def test_the_standalone_text_is_a_complete_query():
    text = q.as_text("co-occurrence")
    assert text.startswith("PREFIX ")
    assert "SELECT" in text and "WHERE" in text
    # And it parses as SPARQL.
    Graph().query(text)


def test_the_standalone_text_of_a_parameterised_query_parses():
    text = q.as_text("persons-in-care-documents", century="15")
    Graph().query(text)


# ── the prefixes come from the exporter ─────────────────────────────────

def test_every_prefix_a_query_uses_is_one_the_exporter_writes():
    """A query cannot use a prefix the graph was not written with."""
    import re

    known = set(q.PREFIXES)      # both exporters' tables, merged (KG-11)
    for query in q.QUERIES.values():
        used = set(re.findall(r"\b([a-z][a-z0-9-]*):[A-Za-z_]", query.body))
        # String literals in a BIND carry http(s): — not a prefix.
        used -= {"http", "https"}
        assert used <= known, f"{query.name} uses {used - known}"


def test_the_declared_prefixes_match_the_links_that_are_actually_minted():
    """The drift that made the corpus-health query file HLS links as "other".

    ``entity_index._URI_TEMPLATES`` is where a public authority URI comes from.
    A prefix declared in the published Turtle that matches no URI in the file
    tells a consumer that links look a way they do not.
    """
    import entity_index

    assert rx.PREFIXES["hls"] == entity_index.authority_base("hls")
    assert rx.PREFIXES["gnd"] == entity_index.authority_base("gnd")


def test_both_wikidata_forms_count_as_wikidata():
    """``entity_index`` mints the page URI a human clicks, the hub export the
    canonical RDF one. A classifier knowing one files half of them as "other"."""
    import entity_index

    bases = q._authority_bases()["wikidata"]
    assert entity_index.authority_base("wikidata") in bases
    assert str(rx.WD) in bases
    bind = q._source_bind()
    for base in bases:
        assert base in bind


def test_the_source_bind_is_generated_from_the_authority_table():
    """Not written out a second time — that is what went wrong before."""
    import entity_index

    bind = q._source_bind()
    for source in entity_index.authority_sources():
        assert entity_index.authority_base(source) in bind
        assert f'"{source}"' in bind


def test_an_entity_linked_to_an_unknown_authority_is_other_not_unlinked():
    """"We do not recognise this link" and "there is no link" are different
    findings, and only one of them is a gap in the corpus."""
    g = Graph()
    rx.document_to_rdf(g, _doc("odd", entities=[
        {"text": "X", "type": "PERSON", "normalised": "X"}]))
    person = rx.entity_uri("PERSON", "X")
    g.add((person, rx.OWL["sameAs"], URIRef("https://example.org/thing/1")))
    sources = {row["source"] for row in _rows(g, "authority-coverage")}
    assert "other" in sources


def test_the_prefix_block_is_built_from_the_exporter():
    for short, uri in rx.PREFIXES.items():
        assert f"PREFIX {short}: <{uri}>" in q.PREFIX_BLOCK


# ── the century parser (new, and the first query depends on it) ─────────

@pytest.mark.parametrize("text,expected", [
    ("15. Jahrhundert", 15),
    ("15. Jh.", 15),
    ("Mitte 15. Jhdt", 15),
    ("Schrift des 16. Jahrhunderts", 16),     # the German genitive
    ("Anfang des 15. Jahrhunderts", 15),
    ("1431", 15),
    ("1500", 15),                             # the century boundary
    ("1501", 16),
    ("um 1450", 15),
    ("zwischen 1430 und 1440", 15),           # a range inside one century
    ("1431-1435", 15),
    ("XV. Jahrhundert", 15),
    ("saec. XVI", 16),
    ("xiv. Jh.", 14),
])
def test_the_century_is_read_where_it_can_be(text, expected):
    assert rx.century_of(text) == expected


@pytest.mark.parametrize("text", [
    "", "   ", "keine Angabe", "ohne Datum",
    "14./15. Jahrhundert",        # two centuries: picking one is an edit
    "14.-15. Jh.",
    "1498 und 1502",              # a range ACROSS a century boundary
    "xv",                         # a Roman numeral with nothing making it a date
])
def test_an_unreadable_dating_yields_no_century(text):
    """The only safe failure is no number. A guessed century would put a
    document into a historian's result set on the strength of a regex."""
    assert rx.century_of(text) is None


def test_a_document_whose_dating_cannot_be_read_has_no_century_triple():
    g = Graph()
    rx.document_to_rdf(g, _doc("vague", dating="14./15. Jahrhundert"))
    d = rx.document_uri("vague")
    assert (d, rx.SDHSS["dating"], Literal("14./15. Jahrhundert", lang="de")) in g
    assert not list(g.objects(d, rx.SDHSS["century"]))
    # And it is therefore not in a century answer at all — absent, not guessed.
    assert _rows(g, "persons-in-care-documents", century="15") == []
    assert _rows(g, "persons-in-care-documents", century="14") == []


def test_the_dating_and_the_century_are_separate_predicates():
    """One is what the source says, the other is our arithmetic on it."""
    g = Graph()
    rx.document_to_rdf(g, _doc("dated", dating="um 1450"))
    d = rx.document_uri("dated")
    assert (d, rx.SDHSS["dating"], Literal("um 1450", lang="de")) in g
    assert (d, rx.SDHSS["century"], Literal(15)) in g
    assert (d, rx.SDHSS["centuryUncertain"], Literal(True)) in g


@pytest.mark.parametrize("element,expected", [
    ({"wert": "1431"}, False),
    ({"wert": "1431", "unsicher": True}, True),
    ({"wert": "um 1450"}, True),              # hedged prose, flag unset
    ({"wert": "ca. 1450"}, True),
    ({"wert": "wohl 1450"}, True),
    ({"wert": "1450?"}, True),
    ({}, False),
])
def test_an_uncertain_dating_is_recognised(element, expected):
    assert rx.dating_is_uncertain(element) is expected


# ── the care flag: only the positive claim ──────────────────────────────

def test_a_care_flagged_document_carries_the_flag_and_its_types():
    g = Graph()
    rx.document_to_rdf(g, _doc("flagged", care_types=("Almosen", "Spital")))
    d = rx.document_uri("flagged")
    assert (d, rx.SDHSS["careRelated"], Literal(True)) in g
    assert {str(o) for o in g.objects(d, rx.SDHSS["careType"])} == \
        {"Almosen", "Spital"}


def test_a_negative_care_flag_asserts_nothing():
    """`_care_flag`'s exception path returns the same `is_care_related: False`
    as a real negative, so from the record the two cannot be told apart. A
    published `careRelated false` would assert something nobody measured."""
    g = Graph()
    rx.document_to_rdf(g, _doc("not-flagged", care=False))
    d = rx.document_uri("not-flagged")
    assert not list(g.objects(d, rx.SDHSS["careRelated"]))


def test_a_document_never_classified_carries_no_care_triple():
    g = Graph()
    record = _doc("unclassified")
    record["description"].pop("care_flag")
    rx.document_to_rdf(g, record)
    assert not list(g.objects(rx.document_uri("unclassified"),
                              rx.SDHSS["careRelated"]))


def test_the_care_context_prose_is_not_published():
    """Publishing care-flagged documents (owner-approved) is not the same as
    publishing an LLM's prose about who was involved in a welfare case."""
    g = Graph()
    rx.document_to_rdf(g, _doc("flagged"))
    assert "Spital" not in g.serialize(format="turtle")


def test_the_beteiligte_list_is_not_turned_into_entities():
    """Those names are an LLM's reading of a care context, not the NER output
    the rest of the graph's person nodes come from."""
    g = Graph()
    record = _doc("flagged")
    record["description"]["care_flag"]["beteiligte"] = ["Erfundener Name"]
    rx.document_to_rdf(g, record)
    assert "Erfundener Name" not in g.serialize(format="turtle")


# ── rendering ───────────────────────────────────────────────────────────

def test_the_answer_says_which_graph_it_was_computed_from(graph):
    answer = q.run("co-occurrence", graph, source="build/corpus.ttl, gebaut X",
                   triples=len(graph))
    out = q.format_answer(answer)
    assert "build/corpus.ttl" in out
    assert f"{len(graph)} Tripel" in out


def test_the_rendered_answer_fits_in_a_discord_message(graph):
    g = Graph()
    for i in range(40):
        rx.document_to_rdf(g, _doc(f"doc-{i:03d}"))
    answer = q.run("persons-in-care-documents", g, century="15")
    out = q.format_answer(answer)
    assert len(out) <= 2000
    assert "von" in out and "Zeilen" in out      # says it was shortened


def test_a_few_very_long_rows_are_dropped_until_the_message_fits():
    """The row cap alone is not enough: fifteen rows of a long dating are over
    the limit on their own, and Discord refuses the whole message."""
    g = Graph()
    for i in range(12):
        rx.document_to_rdf(g, _doc(
            f"langer-dokumentname-mit-sehr-viel-text-{i:02d}",
            dating="15. Jahrhundert, " + "sehr ausführliche Begründung " * 8))
    answer = q.run("persons-in-care-documents", g, century="15")
    assert len(answer.rows) > 10
    out = q.format_answer(answer)
    assert len(out) <= 2000
    assert "Zeilen" in out


def test_the_rendered_rows_stay_traceable_through_the_base_note(graph):
    """Rows use the short `ah:document/x` form, so the base has to be stated."""
    out = q.format_answer(q.run("co-occurrence", graph))
    assert "ah:document/" in out
    assert rx.CORPUS_BASE in out


def test_an_unbound_cell_is_a_dash_not_a_blank(graph):
    """An empty cell in a code block reads as a formatting accident.

    Asserted on the block, not the message: the question itself contains an em
    dash, so the obvious assertion passes however the cells are rendered.
    """
    answer = q.run("persons-in-care-documents", graph, century="15")
    assert any(row["authority"] is None for row in answer.rows)
    block = q.format_answer(answer).split("```")[1]
    assert "—" in block
    assert " ·  · " not in block


def test_the_listing_names_every_question_and_its_argument():
    out = q.describe_all()
    for name in q.names():
        assert name in out
        assert q.get(name).question in out
    assert "<century>" in out
    assert "<document>" in out


# ── the graph that gets asked ───────────────────────────────────────────

def test_a_built_turtle_is_preferred_and_named(tmp_path):
    """An answer from the built graph answers what the endpoint would."""
    g = Graph()
    rx.document_to_rdf(g, _doc("built"))
    ttl = tmp_path / "corpus.ttl"
    ttl.write_text(g.serialize(format="turtle"), encoding="utf-8")

    loaded, source = q.load_graph(build_path=ttl, outputs_dir=tmp_path)
    assert len(loaded) == len(g)
    assert str(ttl) in source


def test_without_a_build_the_graph_is_built_from_the_records_and_says_so(tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    import json
    (outputs / "x_pipeline.json").write_text(
        json.dumps(_doc("from-records")), encoding="utf-8")

    loaded, source = q.load_graph(build_path=tmp_path / "missing.ttl",
                                  outputs_dir=outputs)
    assert len(loaded) > 0
    assert "nicht der Endpoint-Graph" in source


def test_a_refreshed_build_is_not_served_from_the_cache(tmp_path):
    """A stale answer that looks current is the only cache bug worth fearing."""
    import os

    g1 = Graph()
    rx.document_to_rdf(g1, _doc("one"))
    ttl = tmp_path / "corpus.ttl"
    ttl.write_text(g1.serialize(format="turtle"), encoding="utf-8")
    first, _ = q.load_graph(build_path=ttl, outputs_dir=tmp_path)

    g2 = Graph()
    rx.document_to_rdf(g2, _doc("one"))
    rx.document_to_rdf(g2, _doc("two"))
    ttl.write_text(g2.serialize(format="turtle"), encoding="utf-8")
    os.utime(ttl, (1, 1))                 # a different mtime, deterministically
    second, _ = q.load_graph(build_path=ttl, outputs_dir=tmp_path)

    assert len(second) > len(first)


# ── the bot side ────────────────────────────────────────────────────────

def _source() -> str:
    return (PKG / "bot.py").read_text(encoding="utf-8")


def test_the_command_offers_the_named_queries_only():
    """#331 prefers canned queries over free-text SPARQL from Discord."""
    import bot as bot_module

    commands = {c.name: c for c in bot_module.bot.pending_application_commands}
    assert "sparql" in commands
    options = {o.name: o for o in commands["sparql"].options}
    assert options["frage"].required is False        # no argument → the listing
    assert [c.value for c in options["frage"].choices] == list(q.names())
    # No free-text query option anywhere on the command.
    assert "query" not in options and "sparql" not in options


def test_the_command_is_role_gated():
    source = _source()
    block = source[source.index('name="sparql"'):]
    assert "@require_role" in block[:block.index("async def")]


def test_the_command_runs_the_named_query_and_never_builds_sparql_itself():
    source = _source()
    block = source[source.index("async def sparql_cmd("):]
    block = block[:block.index("\n# ── /train_log")]
    assert "queries.run" in block
    # No query text assembled in the command: it names one and passes params.
    assert "SELECT" not in block and "WHERE" not in block


def test_the_choices_match_the_queries():
    """The decorator's literal cannot drift from the module."""
    import bot as bot_module

    assert bot_module._SPARQL_CHOICES == q.names()
