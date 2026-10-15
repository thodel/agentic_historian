"""
test_kg_11_bestandsgraph_queries.py — Fragen über den Bestandsgraphen (KG-11, #654).

Offline, gegen den Fixture-Graphen aus KG-6 (ein Dokument je Bestand, gebaut mit
den echten Bausteinen) und gegen kleine, von Hand gebaute Graphen, wo das Fixture
die Konstellation nicht hergibt (zwei Unterzeichner, ein Wohnort).

Was das Issue verlangt:

  1. Die vier Fragen aus KG-5 beantworten **beide** Graphen — die Erwähnung geht
     durch ``(P128/P67)|P67``; readings-of-page bleibt korpusspezifisch und sagt es.
  2. Drei neue Fragen, jede mit ``document`` als erster Spalte.
  3. Jede gegen den Fixture-Graphen getestet; jede Zeile trägt ihre Quelle.

Run: pytest agentic_historian/tests/test_kg_11_bestandsgraph_queries.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from rdflib import Graph

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from knowledge_hub import bestandsgraph as bg                   # noqa: E402
from knowledge_hub import queries as q                          # noqa: E402
from knowledge_hub import rdf_export as rx                      # noqa: E402
import bestandsgraph_fixture as fx                              # noqa: E402

NEW = ("persons-of-century-across-sources", "co-signatories", "families-and-places")


@pytest.fixture(scope="module")
def bestand() -> Graph:
    return fx.all_graphs()


@pytest.fixture(scope="module")
def corpus() -> Graph:
    """Zwei Korpus-Dokumente, wie KG-5 sie baut: Lesungen, Personen, Care-Flag."""
    from test_kg_5_sparql_queries import _doc
    g = Graph()
    rx.document_to_rdf(g, _doc("care-15-a"))
    rx.document_to_rdf(g, _doc("care-16", dating="1512"))
    return g


@pytest.fixture(scope="module")
def union(bestand, corpus) -> Graph:
    g = Graph()
    for part in (bestand, corpus):
        for t in part:
            g.add(t)
    return g


def _rows(graph, name, **params):
    return q.run(name, graph, **params).rows


# ── 1. die vier alten Fragen über beide Graphen ────────────────────────────

def test_seven_questions_are_offered():
    assert len(q.names()) == 7
    for name in NEW:
        assert name in q.names()


def test_every_question_uses_the_shared_mention_path_or_says_why_not():
    """Die Erwähnung geht überall durch MENTION — ausser bei den Lesungen, die
    es nur im Korpus-Graphen gibt, und das steht in der Frage selbst.
    co-signatories fragt nicht nach Erwähnungen, sondern nach E13-Rollen."""
    for query in q.QUERIES.values():
        if query.name == "readings-of-page":
            assert "Korpus-Graph" in query.question
            continue
        if "P67_refers_to" in query.body or "P128_carries" in query.body:
            assert q.MENTION in query.body, query.name
            assert "P128_carries ?reading" not in query.body, query.name
    assert q.MENTION in q.get("co-occurrence").body
    assert q.MENTION in q.get("persons-of-century-across-sources").body
    assert q.MENTION in q.get("families-and-places").body


def test_co_occurrence_answers_over_the_corpus_graph(corpus):
    rows = _rows(corpus, "co-occurrence")
    assert rows and all(r["document"].startswith(rx.CORPUS_BASE) for r in rows)


def test_co_occurrence_answers_over_the_bestandsgraph(bestand):
    rows = _rows(bestand, "co-occurrence")
    labels = {(r["labelA"], r["labelB"]) for r in rows}
    assert ("Ruth Fivaz-Silbermann", "Serge Klarsfeld") in labels


def test_co_occurrence_over_a_union_graph_keeps_both_sides(union):
    docs = {r["document"] for r in _rows(union, "co-occurrence")}
    assert any(d.startswith(rx.CORPUS_BASE) for d in docs)
    assert any("/dodis/document/" in d for d in docs)


def test_the_path_yields_one_row_per_mention_not_per_reading(corpus):
    """Nur die Arbeitslesung trägt P67; die Alternative (P128/P67) darf eine
    Person nicht je Lesung wiederholen."""
    rows = _rows(corpus, "co-occurrence")
    assert len(rows) == len({(r["document"], r["personA"], r["personB"]) for r in rows})


def test_authority_coverage_reads_the_bestandsgraph_identifier_source(bestand):
    rows = _rows(bestand, "authority-coverage")
    sources = {r["source"] for r in rows}
    # KF persons carry a local E42 whose type is the E55 «kf» — a label, not a URI.
    assert "kf" in sources
    assert not any(s.startswith("http") for s in sources)


def test_a_name_kind_is_not_an_identifier_source():
    """E41 names hang on P1 as well, typed std/orig; only an E42 is an id."""
    b = bg.Build(bg.Dataset("ssrq", "SSRQ", "test"))
    d = b.document("D", "D")
    p = b.person("per1", "Hans Bürgi")
    b.name(p, "Hans Burgi", kind="orig", key="per1-orig")
    b.mention(d, p)
    rows = _rows(b.g, "authority-coverage")
    assert [r["source"] for r in rows] == ["unlinked"]
    b.identifier(p, "ssrq", "per000001")
    assert [r["source"] for r in _rows(b.g, "authority-coverage")] == ["ssrq"]


def test_authority_coverage_still_works_over_the_corpus_graph(corpus):
    rows = _rows(corpus, "authority-coverage")
    assert {r["source"] for r in rows} & {"gnd", "unlinked"}


def test_persons_in_care_is_unchanged_in_meaning_over_the_corpus_graph(corpus):
    rows = _rows(corpus, "persons-in-care-documents", century="15")
    assert {r["personLabel"] for r in rows} >= {"Heinrich von Wiler", "Anna Müller"}


def test_persons_in_care_finds_nothing_in_the_bestandsgraph_and_says_so(bestand):
    """Kein Care-Flag dort — null Zeilen sind die Antwort, kein Fehler."""
    answer = q.run("persons-in-care-documents", bestand, century="15")
    assert answer.rows == []
    assert "Keine Treffer" in q.format_answer(answer)


# ── 2. die drei neuen Fragen ───────────────────────────────────────────────

def test_the_new_questions_select_their_document_first():
    for name in NEW:
        assert q.get(name).columns[0] == "document"


def test_every_row_of_the_new_questions_carries_its_dataset(bestand):
    """prov:wasDerivedFrom, als Label des Datensatzes — die Abnahme aus #654."""
    for name in NEW:
        params = {"century": "15"} if "century" in q.get(name).required else {}
        rows = _rows(bestand, name, **params)
        assert rows, name
        for row in rows:
            assert row["source"], (name, row)
            assert row["document"], (name, row)


def test_persons_of_a_century_come_with_begin_and_dataset(bestand):
    rows = _rows(bestand, "persons-of-century-across-sources", century="15")
    assert rows
    assert all(r["begin"].startswith("14") for r in rows)
    assert {r["source"] for r in rows} == {fx.KF.label}
    assert "Heinrich Bürgi" in {r["personLabel"] for r in rows}


def test_the_century_is_computed_from_the_creation_date(bestand):
    """1408 → 15, 2005 → 21; und das 14. hat im Fixture niemanden."""
    assert _rows(bestand, "persons-of-century-across-sources", century="14") == []
    twenty_first = _rows(bestand, "persons-of-century-across-sources", century="21")
    assert {r["source"] for r in twenty_first} == {fx.DODIS.label}


def test_authorship_is_a_role_and_a_mere_mention_has_none(bestand):
    rows = _rows(bestand, "persons-of-century-across-sources", century="21")
    assert {r["role"] for r in rows} == {"author"}
    rows_15 = _rows(bestand, "persons-of-century-across-sources", century="15")
    assert all(r["role"] is None for r in rows_15)


def _two_signatories() -> Graph:
    b = bg.Build(bg.Dataset("dodis", "Dodis", "test"))
    d = b.document("1", "Vertrag", begin=1955, end=1955)
    a = b.person("a", "Anna"); c = b.person("c", "Carl"); e = b.person("e", "Eva")
    for p in (a, c, e):
        b.mention(d, p)
    b.role(d, a, "signatory"); b.role(d, c, "signatory"); b.role(d, e, "adressee")
    b.same_as(a, ["https://d-nb.info/gnd/1", "https://dodis.ch/P1"])
    return b.g


def test_co_signatories_pairs_the_signatories_of_a_document_once():
    rows = _rows(_two_signatories(), "co-signatories")
    assert [(r["labelA"], r["labelB"]) for r in rows] == [("Anna", "Carl")]


def test_an_addressee_is_not_a_co_signatory():
    rows = _rows(_two_signatories(), "co-signatories")
    assert "Eva" not in {r["labelA"] for r in rows} | {r["labelB"] for r in rows}


def test_co_signatories_on_the_fixture_find_the_dodis_pair(bestand):
    rows = _rows(bestand, "co-signatories")
    assert rows and all(r["source"] == fx.DODIS.label for r in rows)


def test_the_role_column_names_the_assigned_role():
    rows = _rows(_two_signatories(), "persons-of-century-across-sources", century="20")
    assert {(r["personLabel"], r["role"]) for r in rows} == {
        ("Anna", "signatory"), ("Carl", "signatory"), ("Eva", "adressee")}


def test_only_authority_links_count_as_authority_never_the_sources_own_uri():
    rows = _rows(_two_signatories(), "persons-of-century-across-sources", century="20")
    anna = [r for r in rows if r["personLabel"] == "Anna"]
    assert [r["authority"] for r in anna] == ["https://d-nb.info/gnd/1"]


def _family_with_residence() -> Graph:
    b = bg.Build(bg.Dataset("ssrq", "SSRQ", "test"))
    d = b.document("SSRQ-X", "SSRQ X")
    m = b.person("per1", "Hans Bürgi"); n = b.person("per2", "Uli Bürgi"); o = b.person("per3", "Peter Vogt")
    fam = b.group("org1", "Bürgi", group_type="Familie")
    guild = b.group("org2", "Zunft zum Affen")
    b.membership(m, fam, "Familie"); b.membership(n, fam, "Familie")
    b.membership(o, guild, "memberOf")
    b.residence(m, b.place("loc1", "Zürich"))
    b.residence(n, b.place("loc2", None))
    for p in (m, n, o):
        b.mention(d, p)
    return b.g


def test_families_and_places_lists_members_with_their_residence():
    rows = _rows(_family_with_residence(), "families-and-places")
    got = {(r["familyLabel"], r["memberLabel"], r["placeLabel"]) for r in rows}
    assert got == {("Bürgi", "Hans Bürgi", "Zürich"), ("Bürgi", "Uli Bürgi", None)}


def test_a_place_without_a_label_is_still_a_place():
    rows = _rows(_family_with_residence(), "families-and-places")
    uli = [r for r in rows if r["memberLabel"] == "Uli Bürgi"][0]
    assert uli["place"] and uli["placeLabel"] is None


def test_a_guild_is_not_a_family():
    rows = _rows(_family_with_residence(), "families-and-places")
    assert "Peter Vogt" not in {r["memberLabel"] for r in rows}


def test_families_on_the_fixture_come_from_kf_and_ssrq(bestand):
    rows = _rows(bestand, "families-and-places")
    assert {r["source"] for r in rows} >= {fx.KF.label}
    assert "Bürgi" in {r["familyLabel"] for r in rows}


# ── 3. Präfixe, Text, Discord ──────────────────────────────────────────────

def test_the_prefix_block_knows_both_exporters():
    assert "sdh-slc" in q.PREFIXES and "cidoc-crm" in q.PREFIXES
    assert q.PREFIXES["sdh-slc"] == bg.PREFIXES["sdh-slc"]


def test_one_name_per_namespace_the_corpus_exporters():
    """rdflib bindet je Namensraum ein Präfix; ``crm:`` neben ``cidoc-crm:``
    würde das erste still entbinden — darum nur eines, und zwar das des
    Korpus-Exporters."""
    assert "crm" not in q.PREFIXES
    assert list(q.PREFIXES.values()).count(str(rx.CRM)) == 1


def test_a_disagreeing_prefix_is_refused(monkeypatch):
    monkeypatch.setitem(bg.PREFIXES, "prov", "https://example.org/not-prov#")
    with pytest.raises(ValueError, match="prov"):
        q._merged_prefixes()


def test_every_prefix_a_query_uses_is_declared():
    import re
    for query in q.QUERIES.values():
        used = set(re.findall(r"\b([a-z][a-z0-9-]*):[A-Za-z_]", query.body)) - {"http", "https"}
        assert used <= set(q.PREFIXES), f"{query.name} uses {used - set(q.PREFIXES)}"


def test_the_standalone_text_of_every_new_question_parses(bestand):
    for name in NEW:
        params = {"century": "15"} if "century" in q.get(name).required else {}
        text = q.as_text(name, **params)
        assert "VALUES" in text or not params
        list(bestand.query(text))


def test_an_empty_aggregate_renders_as_a_dash():
    assert q._short("") == "—" and q._short(None) == "—"


def test_the_discord_choices_offer_all_seven():
    import bot as bot_module
    assert bot_module._SPARQL_CHOICES == q.names()


# ── 4. the endpoint path (KG-11 → KG-4) ───────────────────────────────────

import json as _json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs


class _Sparql(BaseHTTPRequestHandler):
    """A SPARQL-protocol stand-in: records the query, answers canned JSON."""
    seen: list[dict] = []
    status = 200
    bindings: list[dict] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode())
        _Sparql.seen.append({"query": form.get("query", [""])[0],
                             "accept": self.headers.get("Accept")})
        body = _json.dumps({"head": {"vars": []},
                            "results": {"bindings": _Sparql.bindings}}).encode()
        self.send_response(_Sparql.status)
        self.send_header("Content-Type", "application/sparql-results+json")
        self.end_headers()
        self.wfile.write(body if _Sparql.status == 200 else b"boom")

    def log_message(self, *args):   # quiet
        pass


@pytest.fixture
def endpoint():
    server = HTTPServer(("127.0.0.1", 0), _Sparql)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _Sparql.seen, _Sparql.status = [], 200
    _Sparql.bindings = [
        {"document": {"type": "uri", "value": "https://tei.dh.unibe.ch/id/kf/document/x"},
         "documentLabel": {"type": "literal", "value": "StAAG X"},
         "source": {"type": "literal", "value": "KF"},
         "begin": {"type": "literal", "datatype": "http://www.w3.org/2001/XMLSchema#date",
                   "value": "1408-01-01"},
         "person": {"type": "uri", "value": "https://tei.dh.unibe.ch/id/kf/person/p"},
         "personLabel": {"type": "literal", "value": "Heinrich Bürgi"}},
    ]
    yield f"http://127.0.0.1:{server.server_port}/sparql"
    server.shutdown()


def test_the_remote_path_sends_the_standalone_text_and_reads_the_rows(endpoint):
    answer = q.run_remote("persons-of-century-across-sources", endpoint, century="15")
    sent = _Sparql.seen[-1]
    assert sent["accept"] == "application/sparql-results+json"
    assert sent["query"] == q.as_text("persons-of-century-across-sources", century="15")
    assert "VALUES (?century)" in sent["query"]
    assert answer.rows == [{"document": "https://tei.dh.unibe.ch/id/kf/document/x",
                            "documentLabel": "StAAG X", "source": "KF",
                            "begin": "1408-01-01",
                            "person": "https://tei.dh.unibe.ch/id/kf/person/p",
                            "personLabel": "Heinrich Bürgi",
                            "role": None, "authority": None}]
    assert answer.source == endpoint and answer.triples is None
    assert answer.took_s is not None


def test_an_unbound_remote_cell_is_none_like_the_local_one(endpoint):
    answer = q.run_remote("persons-of-century-across-sources", endpoint, century="15")
    assert answer.rows[0]["role"] is None
    assert "—" in q.format_answer(answer)


def test_the_remote_answer_names_the_endpoint(endpoint):
    answer = q.run_remote("co-signatories", endpoint)
    assert endpoint in q.format_answer(answer)


def test_a_failing_endpoint_is_an_error_not_a_local_fallback(endpoint):
    _Sparql.status = 500
    with pytest.raises(q.QueryError, match="500"):
        q.run_remote("co-signatories", endpoint)


def test_an_unreachable_endpoint_is_reported_as_such():
    with pytest.raises(q.QueryError, match="antwortet nicht"):
        q.run_remote("co-signatories", "http://127.0.0.1:9/sparql", timeout=2)


def test_the_remote_path_refuses_the_same_parameters_as_the_local_one(endpoint):
    with pytest.raises(q.QueryError):
        q.run_remote("co-signatories", endpoint, century="15")
    assert _Sparql.seen == []        # refused before anything went out


def test_the_command_asks_the_endpoint_when_one_is_configured():
    source = (PKG / "bot.py").read_text(encoding="utf-8")
    block = source[source.index("async def sparql_cmd("):]
    block = block[:block.index("\n# ── /train_log")]
    assert "KG_SPARQL_ENDPOINT" in block and "queries.run_remote" in block
    # The in-process path stays for a deployment without an endpoint.
    assert "queries.load_graph" in block


def test_the_endpoint_knob_defaults_to_none():
    import config
    assert config.KG_SPARQL_ENDPOINT == ""
