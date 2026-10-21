"""knowledge_hub/queries.py — KG-5 (#331): the questions, not the endpoint.

A bare SPARQL endpoint is useless to a historian who does not write SPARQL, and
the point of this graph is that it answers questions no single source can. So
the surface is a small set of **named queries**, each one a real research
question, and each one tested against a fixture graph.

Four things this module insists on, each because the obvious version fails:

**Every row carries the document it came from.** Not a convention but a
constructor check: a :class:`CannedQuery` whose columns lack ``document``
cannot be built. An answer a historian cannot trace back to a source is not
usable in scholarship, and a canned-query surface makes it very easy to publish
a plausible aggregate with nothing behind it. That rule bent the corpus-health
query into a better shape: "which **documents** have unlinked persons" instead
of "47 entities are unlinked" — the first can be acted on, the second cannot.

**Parameters are bound, never interpolated.** A century or a document id goes
in as an RDF term through ``initBindings``, so no caller's text ever becomes
query syntax. The standalone form (:func:`as_text`, for pasting into the public
endpoint) renders the same terms through ``n3()``, which is rdflib's own
escaping — a quote in a document id must not be able to end a literal.

**The prefixes come from the exporter.** ``rdf_export.PREFIXES`` is the single
source, so a query cannot use a prefix the graph was not written with, and a
namespace change cannot leave the queries silently matching nothing.

**No rows is an answer.** It is reported as a sentence naming the question and
the parameters it was asked with — never a blank, and never a crash.

**One question, two graphs** (KG-11, #654). The corpus graph (KG-2) hangs a
mention on the *working reading* of a page — ``P128_carries`` → ``P67``;
the Bestandsgraph (KG-7/8/9/13: Dodis, Königsfelden, SSRQ, MLS) has no readings, the
record itself refers — ``P67`` from the document. :data:`MENTION` is that
path, ``(P128/P67)|P67``, and every question that asks "who is named in this
document" goes through it, so the same canned query answers over both graphs
and over a union of them. ``readings-of-page`` is the exception and says so
in its question: it asks about readings, which only the corpus graph has.

Run: pytest agentic_historian/tests/test_kg_5_sparql_queries.py \
            agentic_historian/tests/test_kg_11_bestandsgraph_queries.py -v
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import XSD

from knowledge_hub import rdf_export as rx
from knowledge_hub import bestandsgraph as bg

__all__ = [
    "PREFIX_BLOCK", "PREFIXES", "MENTION", "BESTAND_ONLY", "CannedQuery", "QUERIES", "QueryError",
    "Answer", "names", "get", "describe_all", "as_text", "run", "format_answer",
    "load_graph", "PERSON_TYPES", "param_terms", "run_remote",
]

#: Entity types that identify a natural person. The same pair
#: ``corpus_build`` uses, so "persons" means the same thing in a published
#: graph and in a query over it.
PERSON_TYPES = ("PERSON", "CARE_ACTOR")

def _merged_prefixes() -> dict[str, str]:
    """The two exporters' prefix tables, as one — refusing a disagreement.

    ``rdf_export`` writes the corpus graph with ``cidoc-crm:``, ``bestandsgraph``
    writes the Bestandsgraph with ``crm:``; both mean the same namespace and a
    query may use either. What may not happen is one short name meaning two
    URIs — that would make a query match one graph and silently miss the other.
    """
    merged: dict[str, str] = dict(rx.PREFIXES)
    for short, uri in bg.PREFIXES.items():
        if short in merged and merged[short] != uri:
            raise ValueError(f"prefix {short}: is {merged[short]} in rdf_export "
                             f"and {uri} in bestandsgraph")
        if uri in merged.values():
            # One name per namespace, the corpus exporter's: rdflib keeps a
            # single prefix per URI, and a second declaration of the same
            # namespace (``crm:`` beside ``cidoc-crm:``) silently unbinds the
            # first — every ``cidoc-crm:`` term then fails to resolve. The
            # Bestandsgraph's ``crm:`` is the same namespace and needs no
            # second name here.
            continue
        merged[short] = uri
    return merged


#: Every prefix a query may use: the union of both exporters' tables.
PREFIXES: dict[str, str] = _merged_prefixes()

#: Prefix block for every query, built from the exporters' own tables — see the
#: module docstring. Sorted, so the rendered query text is stable and diffable.
PREFIX_BLOCK = "\n".join(
    f"PREFIX {short}: <{uri}>" for short, uri in sorted(PREFIXES.items()))

#: How a document refers to an entity, in both graphs (KG-11): through its
#: working reading in the corpus graph, directly in the Bestandsgraph. Only
#: the working reading carries ``P67`` (``rdf_export``), so the path yields
#: one row per mention there too — never one per reading.
MENTION = "(cidoc-crm:P128_carries/cidoc-crm:P67_refers_to)|cidoc-crm:P67_refers_to"


class QueryError(ValueError):
    """An unknown query name, or a parameter it does not take."""


@dataclass(frozen=True)
class CannedQuery:
    """One research question, as a named and tested query."""

    name: str
    #: The question in words, as a historian would ask it. This is what the
    #: Discord listing shows; the SPARQL is an implementation detail.
    question: str
    #: Result variables, in display order. ``document`` must be among them.
    columns: tuple[str, ...]
    #: The WHERE body, without prefixes and without SELECT.
    body: str
    #: What goes after the body: GROUP BY / ORDER BY / LIMIT.
    tail: str = ""
    #: Aggregate expressions, ``variable -> expression``, e.g.
    #: ``{"entities": "COUNT(DISTINCT ?entity)"}``.
    aggregates: dict[str, str] = field(default_factory=dict)
    #: Accepted parameters → how to turn the caller's string into an RDF term.
    params: dict[str, Callable[[str], Any]] = field(default_factory=dict)
    #: Parameters without which the question is not a question.
    required: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.columns or self.columns[0] != "document":
            # Structural, not a review rule: an untraceable canned query is the
            # failure mode this surface is most prone to, so it may not be
            # possible to write one (#331). First, not merely present: a row is
            # read left to right, and the thing it is evidence about belongs at
            # the start of it — including when the message has to drop columns.
            raise ValueError(
                f"{self.name}: every canned query must select ?document first — "
                f"a row a historian cannot trace to a source is not an answer")
        for key in self.required:
            if key not in self.params:
                raise ValueError(f"{self.name}: required parameter {key!r} is "
                                 f"not in params")

    def select(self) -> str:
        """The SELECT clause, aggregates rendered as ``(expr AS ?var)``."""
        parts = []
        for column in self.columns:
            expression = self.aggregates.get(column)
            parts.append(f"({expression} AS ?{column})" if expression
                         else f"?{column}")
        return "SELECT " + " ".join(parts)

    def sparql(self) -> str:
        """The query as sent, with prefixes and without parameter values."""
        pieces = [PREFIX_BLOCK, "", self.select(), "WHERE {",
                  self.body.strip("\n"), "}"]
        if self.tail.strip():
            pieces.append(self.tail.strip())
        return "\n".join(pieces) + "\n"


# ── the terms a parameter may become ────────────────────────────────────────

def _century(value: str) -> Literal:
    """A century as an integer literal, matching what ``add_dating`` emits."""
    text = str(value).strip().rstrip(".")
    try:
        number = int(text)
    except (TypeError, ValueError):
        raise QueryError(
            f"{value!r} is not a century — give a number like 15") from None
    if not 1 <= number <= 21:
        raise QueryError(f"{number} is not a plausible century")
    return Literal(number, datatype=XSD.integer)


def _document(value: str) -> URIRef:
    """A document id as its corpus URI.

    Through ``rdf_export.document_uri``, so the id is slugified exactly as the
    exporter slugified it when the node was minted. Building the URI by string
    concatenation here would quietly fail on every id with a space or an
    umlaut in it — and fail by returning *no rows*, which reads as "nothing
    found" rather than "you asked for a node that cannot exist".
    """
    text = str(value).strip()
    if not text:
        raise QueryError("give a document id")
    if text.startswith(rx.CORPUS_BASE):
        return URIRef(text)
    return rx.document_uri(text)


def _authority_bases() -> dict[str, tuple[str, ...]]:
    """source → the URI bases a link from it can have, longest first.

    Two bases per source are possible and both are legitimate. ``entity_index``
    mints the one a *human* clicks (``wikidata.org/wiki/Q42``); the hub export
    mints the canonical RDF one (``wikidata.org/entity/Q42``). A classifier that
    knew only one would file half the Wikidata links under "other" — which is
    how a corpus-health number starts lying in the direction of "unlinked".

    Neither base is written out here: they come from
    ``entity_index._URI_TEMPLATES`` and from the exporter's own namespaces, so a
    template change cannot leave this matching nothing.
    """
    from entity_index import authority_base, authority_sources

    extra = {"gnd": str(rx.GND), "hls": str(rx.HLS), "wikidata": str(rx.WD)}
    out: dict[str, tuple[str, ...]] = {}
    for source in authority_sources():
        bases = {b for b in (authority_base(source), extra.get(source)) if b}
        out[source] = tuple(sorted(bases, key=len, reverse=True))
    return out


def _source_bind(link: str = "link", local: str = "localSource",
                 target: str = "source") -> str:
    """The ``BIND`` that reads the authority source off a link URI.

    Generated from :func:`_authority_bases` rather than written out, for the
    reason given there.
    """
    expression = '"other"'
    for source, bases in reversed(list(_authority_bases().items())):
        test = " || ".join(f'STRSTARTS(STR(?{link}), "{b}")' for b in bases)
        expression = f'IF({test}, "{source}",\n          {expression})'
    return (f'  BIND(IF(BOUND(?{link}),\n          {expression},\n'
            f'          COALESCE(?{local}, "unlinked")) AS ?{target})')


def _person_filter(variable: str) -> str:
    """``FILTER`` restricting an entity-type variable to the person types."""
    listed = ", ".join(f'"{t}"' for t in PERSON_TYPES)
    return f"FILTER(?{variable} IN ({listed}))"


def _authority_filter(link: str = "link") -> str:
    """``FILTER`` keeping only links into a known authority (GND, HLS, Wikidata).

    The Bestandsgraph writes ``owl:sameAs`` for the source's *own* URI as well
    (``dodis.ch/P123``, the SSRQ register) — a link, not a Normdaten id. The
    bases come from the same table as :func:`_source_bind`, so a new authority
    reaches both without a second list.
    """
    tests = [f'STRSTARTS(STR(?{link}), "{b}")'
             for bases in _authority_bases().values() for b in bases]
    return "FILTER(" + " || ".join(tests) + ")"


#: The century a document belongs to, in the Bestandsgraph: read off the
#: begin of its E65 Creation's time-span (KG-7/8/9 date the creation, not
#: the document — P4 wants a temporal entity). ``FLOOR((year-1)/100)+1`` is
#: the same arithmetic ``rdf_export.century_of`` applies to a year. The
#: corpus graph carries ``sdhss:century`` ready-made; the UNION in the care
#: question takes whichever the graph has.
_CENTURY_OF_CREATION = """
  ?creation cidoc-crm:P94_has_created ?document ;
            cidoc-crm:P4_has_time-span/cidoc-crm:P82a_begin_of_the_begin ?begin .
  BIND(FLOOR((YEAR(?begin) - 1) / 100) + 1 AS ?computedCentury)
  FILTER(?computedCentury = ?century)"""

#: Where a row's document came from: the dataset it was derived from, by
#: label (``prov:wasDerivedFrom`` is on every Bestandsgraph node, KG-6). For
#: the MLS that is the article's lexicon (KG-13), labelled «Musiklexikon der
#: Schweiz — <Lexikon>», so the column names both the holding and the edition;
#: a lexicon article is dated by its lexicon's year of publication.
_SOURCE_OF_DOCUMENT = """
  ?document prov:wasDerivedFrom ?dataset .
  ?dataset rdfs:label ?source ."""


# ── the four questions ──────────────────────────────────────────────────────

_PERSONS_IN_CARE = CannedQuery(
    name="persons-in-care-documents",
    question=("Welche Personen kommen in care-relevanten Dokumenten eines "
              "Jahrhunderts vor — mit ihren GND-/HLS-Ids?"),
    columns=("document", "documentLabel", "person", "personLabel",
             "authority", "dating", "datingUncertain"),
    params={"century": _century},
    required=("century",),
    body=f"""
  ?document a cidoc-crm:E31_Document ;
            rdfs:label ?documentLabel ;
            sdhss:careRelated true ;
            sdhss:century ?century ;
            sdhss:dating ?dating .
  # The care flag exists only in the corpus graph, so this question answers
  # from there; the mention still goes through the shared path (KG-11), so
  # a union graph is asked the same way as a single one.
  ?document {MENTION} ?person .
  ?person a cidoc-crm:E21_Person ;
          rdfs:label ?personLabel .
  # The authority link is OPTIONAL on purpose: a person without a GND id is
  # still in the result, marked as unlinked rather than dropped. Dropping them
  # would make the corpus look better linked than it is.
  OPTIONAL {{ ?person owl:sameAs ?authority }}
  # Carried so a historian can exclude the datings this pipeline guessed at.
  OPTIONAL {{ ?document sdhss:centuryUncertain ?datingUncertain }}
""",
    tail="ORDER BY ?documentLabel ?personLabel",
)

_READINGS_OF_PAGE = CannedQuery(
    name="readings-of-page",
    question=("Welche Lesungen hat eine Seite, von welchem Modell — und welche "
              "hat ein Historiker als die nächste gewählt? (Nur der Korpus-Graph "
              "hat Lesungen; der Bestandsgraph antwortet hier nicht.)"),
    columns=("document", "reading", "engine", "modelId", "role", "closest",
             "editor", "editorialStatus", "text"),
    params={"document": _document},
    required=("document",),
    body="""
  ?document cidoc-crm:P128_carries ?reading .
  ?reading cidoc-crm:P190_has_symbolic_content ?text .
  # One OPTIONAL with the model inside it, not two beside each other: a
  # reading a historian combined is attributed to the *editor* as well, so two
  # independent OPTIONALs could take ?engine from the model and ?modelId from
  # a different attribution of the same reading.
  OPTIONAL { ?reading prov:wasAttributedTo ?model .
             ?model a sdhss:RecognitionModel .
             OPTIONAL { ?model sdhss:engine ?engine }
             OPTIONAL { ?model sdhss:modelId ?modelId } }
  OPTIONAL { ?reading sdhss:readingRole ?role }
  OPTIONAL { ?reading sdhss:closestReading ?closest }
  OPTIONAL { ?reading sdhss:editorialStatus ?editorialStatus }
  # Who chose it. Reached by either path, because an endorsement and a
  # combination attach the selection differently (KG-3): a verbatim choice is
  # `selectedIn`, a combined text `wasGeneratedBy`. The type guard keeps the
  # recognition run — also a `wasGeneratedBy` target — out of this.
  OPTIONAL { ?reading (sdhss:selectedIn|prov:wasGeneratedBy) ?selection .
             ?selection a sdhss:EditorialSelection ;
                        prov:wasAssociatedWith ?agent .
             ?agent rdfs:label ?editor . }
""",
    tail="ORDER BY ?engine ?reading",
)

_AUTHORITY_COVERAGE = CannedQuery(
    name="authority-coverage",
    question=("Welche Normdatenquelle liefert die meisten Verknüpfungen — und "
              "welche Dokumente haben unverknüpfte Entitäten?"),
    columns=("document", "source", "entities"),
    aggregates={"entities": "COUNT(DISTINCT ?entity)"},
    body=f"""
  ?document {MENTION} ?entity .
  OPTIONAL {{ ?entity owl:sameAs ?link }}
  # The local id's source is a literal in the corpus graph and an E55 with a
  # label in the Bestandsgraph (KG-6: identifier-source types); the label is
  # taken where there is one, the term itself otherwise.
  # E42 only: names are P1_is_identified_by as well (E41, typed std/orig in
  # the Bestandsgraph), and a name's kind is not an identifier's source —
  # measured on the real builds: 13 957 «orig» and 3 344 «std» rows before.
  # As FILTER EXISTS, not as a type triple in the pattern: on 3.7 M triples
  # Oxigraph answers this form in 3.6 s and the triple form not within 240 s.
  OPTIONAL {{ ?entity cidoc-crm:P1_is_identified_by ?idNode .
             ?idNode cidoc-crm:P2_has_type ?localType .
             FILTER EXISTS {{ ?idNode a cidoc-crm:E42_Identifier }}
             OPTIONAL {{ ?localType rdfs:label ?localTypeLabel }}
             BIND(COALESCE(?localTypeLabel, ?localType) AS ?localSource) }}
  # The authority is read off the URI it points at, because that is where the
  # exporter put it: a public id becomes owl:sameAs into the authority's own
  # namespace, and one without a public URI stays a local E42 Identifier.
  # "unlinked" is a value here, not a missing row — a corpus-health query whose
  # gaps are absent rows is a query that reports perfect health.
""" + _source_bind() + """
""",
    tail="GROUP BY ?document ?source\nORDER BY DESC(?entities) ?document ?source",
)

_CO_OCCURRENCE = CannedQuery(
    name="co-occurrence",
    question=("Welche Personen werden im selben Dokument genannt — über das "
              "ganze Korpus?"),
    columns=("document", "documentLabel", "personA", "labelA", "personB",
             "labelB"),
    body=f"""
  ?document rdfs:label ?documentLabel ;
            {MENTION} ?personA, ?personB .
  ?personA a cidoc-crm:E21_Person ; rdfs:label ?labelA .
  ?personB a cidoc-crm:E21_Person ; rdfs:label ?labelB .
  # Each pair once, in a fixed order. Without this every pair appears twice
  # (A,B and B,A) and every person appears paired with themselves, which would
  # turn a co-occurrence count into a number nobody can interpret.
  FILTER(STR(?personA) < STR(?personB))
""",
    tail="ORDER BY ?documentLabel ?labelA ?labelB",
)

# ── the three questions over the Bestandsgraph (KG-11, #654) ────────────────

_PERSONS_OF_CENTURY = CannedQuery(
    name="persons-of-century-across-sources",
    question=("Welche Personen werden in Dokumenten eines Jahrhunderts genannt — "
              "über Dodis, Königsfelden, SSRQ und das Musiklexikon der Schweiz "
              "hinweg, mit Bestand, Rolle und Normdaten-URI?"),
    columns=("document", "documentLabel", "source", "begin", "person",
             "personLabel", "role", "authority"),
    params={"century": _century},
    required=("century",),
    body=f"""
  ?document a cidoc-crm:E31_Document ;
            rdfs:label ?documentLabel .{_SOURCE_OF_DOCUMENT}{_CENTURY_OF_CREATION}
  ?document {MENTION} ?person .
  ?person a cidoc-crm:E21_Person ;
          rdfs:label ?personLabel .
  # The role is what the record assigned (E13, KG-7: signatory, adressee,
  # address_of_copy) or authorship, which is the creation's P14 (KG-6 mapping).
  # Both OPTIONAL: a person merely mentioned is still in the answer.
  OPTIONAL {{ ?assignment cidoc-crm:P140_assigned_attribute_to ?document ;
                          cidoc-crm:P141_assigned ?person ;
                          cidoc-crm:P177_assigned_property_of_type/rdfs:label ?assigned }}
  OPTIONAL {{ ?creation cidoc-crm:P14_carried_out_by ?person .
             BIND("author" AS ?authored) }}
  BIND(COALESCE(?authored, ?assigned) AS ?role)
  # Normdaten only — the source's own URI is also owl:sameAs, see
  # _authority_filter. A person with a GND *and* an HLS id is two rows, one per
  # link: honest, and no GROUP_CONCAT — rdflib raises on aggregating an
  # unbound OPTIONAL, and a canned query must run on both engines.
  OPTIONAL {{ ?person owl:sameAs ?authority . {_authority_filter("authority")} }}
""",
    tail="ORDER BY ?source ?begin ?documentLabel ?personLabel ?role ?authority",
)

_CO_SIGNATORIES = CannedQuery(
    name="co-signatories",
    question=("Wer unterzeichnet mit wem? — die signatory-Rolle (Dodis) als Kante "
              "zwischen zwei Personen desselben Dokuments"),
    columns=("document", "documentLabel", "source", "personA", "labelA",
             "personB", "labelB"),
    body=f"""
  ?document a cidoc-crm:E31_Document ;
            rdfs:label ?documentLabel .{_SOURCE_OF_DOCUMENT}
  # Two E13 assignments of the same role type on the same document. The type
  # node is shared per build (KG-6 ``type_``), so joining on it is exact.
  ?a cidoc-crm:P140_assigned_attribute_to ?document ;
     cidoc-crm:P141_assigned ?personA ;
     cidoc-crm:P177_assigned_property_of_type ?signing .
  ?b cidoc-crm:P140_assigned_attribute_to ?document ;
     cidoc-crm:P141_assigned ?personB ;
     cidoc-crm:P177_assigned_property_of_type ?signing .
  ?signing rdfs:label "signatory" .
  ?personA rdfs:label ?labelA .
  ?personB rdfs:label ?labelB .
  # Each pair once and nobody with themselves — as in co-occurrence.
  FILTER(STR(?personA) < STR(?personB))
""",
    tail="ORDER BY ?documentLabel ?labelA ?labelB",
)

_FAMILIES_AND_PLACES = CannedQuery(
    name="families-and-places",
    question=("Welche Familien (E74) haben welche Mitglieder, und welche Orte hängt "
              "das Register an die Mitglieder? — SSRQ und Königsfelden, je mit dem "
              "Dokument, das das Mitglied nennt"),
    columns=("document", "documentLabel", "source", "family", "familyLabel",
             "member", "memberLabel", "place", "placeLabel"),
    body=f"""
  ?document a cidoc-crm:E31_Document ;
            rdfs:label ?documentLabel .{_SOURCE_OF_DOCUMENT}
  ?document {MENTION} ?member .
  # Family membership is the C5 with the type «Familie» (KG-6 mapping); P107
  # on the group is written alongside, but the type is what says *family*
  # rather than citizenship or office.
  ?membership sdh-slc:P1 ?member ;
              sdh-slc:P2 ?family ;
              sdh-slc:P3/rdfs:label "Familie" .
  ?family a cidoc-crm:E74_Group ;
          rdfs:label ?familyLabel .
  ?member rdfs:label ?memberLabel .
  # The place is the register's residence (P74). Its label is OPTIONAL in
  # turn: the SSRQ register's places arrive as URIs without a name (KG-9).
  OPTIONAL {{ ?member cidoc-crm:P74_has_current_or_former_residence ?place .
             OPTIONAL {{ ?place rdfs:label ?placeLabel }} }}
""",
    tail="ORDER BY ?source ?familyLabel ?memberLabel ?placeLabel",
)

QUERIES: dict[str, CannedQuery] = {
    q.name: q for q in (_PERSONS_IN_CARE, _READINGS_OF_PAGE,
                        _AUTHORITY_COVERAGE, _CO_OCCURRENCE,
                        _PERSONS_OF_CENTURY, _CO_SIGNATORIES,
                        _FAMILIES_AND_PLACES)
}

#: Questions that ask for what only the Bestandsgraph carries — a dataset per
#: document (``prov:wasDerivedFrom``), E13 role assignments, C5 memberships.
#: Over the corpus graph alone they return no rows, which is the true answer.
BESTAND_ONLY = frozenset({_PERSONS_OF_CENTURY.name, _CO_SIGNATORIES.name,
                          _FAMILIES_AND_PLACES.name})


def names() -> tuple[str, ...]:
    return tuple(QUERIES)


def get(name: str) -> CannedQuery:
    try:
        return QUERIES[str(name).strip()]
    except KeyError:
        raise QueryError(
            f"Keine Abfrage {name!r}. Es gibt: " + ", ".join(names())) from None


def describe_all() -> str:
    """The questions, for somebody deciding which to ask."""
    lines = ["**Abfragen über den Korpus-Graphen und den Bestandsgraphen** (KG-5, KG-11):", ""]
    for query in QUERIES.values():
        args = "".join(f" <{p}>" for p in query.required)
        lines.append(f"`/sparql {query.name}{args}` — {query.question}")
    return "\n".join(lines)


def _bind(query: CannedQuery, params: dict) -> dict:
    """Caller strings → RDF terms, refusing anything the query does not take."""
    unknown = set(params) - set(query.params)
    if unknown:
        raise QueryError(f"{query.name} kennt die Parameter "
                         f"{sorted(unknown)} nicht; erlaubt sind "
                         f"{sorted(query.params) or ['keine']}")
    missing = [k for k in query.required if not params.get(k)]
    if missing:
        raise QueryError(f"{query.name} braucht {', '.join(missing)}")
    return {key: query.params[key](value)
            for key, value in params.items() if value not in (None, "")}


def param_terms(name: str, **params) -> dict:
    """The bound RDF terms for one call — the seam the escaping rests on.

    Exposed because it is the actual guarantee: every converter in ``params``
    returns an rdflib term, so no caller's text is ever concatenated into a
    query. A test asserts that for every parameter of every canned query.
    """
    return _bind(get(name), params)


def as_text(name: str, **params) -> str:
    """The query as standalone SPARQL, for the public endpoint or a paste.

    The bound terms are rendered with rdflib's ``n3()`` into a ``VALUES`` line
    rather than pasted in, because this text leaves the process: a document id
    with a quote in it would otherwise close the literal and the rest of the id
    would be query syntax.
    """
    query = get(name)
    terms = _bind(query, params)
    text = query.sparql()
    if not terms:
        return text
    variables = " ".join(f"?{k}" for k in terms)
    row = " ".join(term.n3() for term in terms.values())
    values = f"  VALUES ({variables}) {{ ({row}) }}\n"
    head, body = text.split("WHERE {\n", 1)
    return head + "WHERE {\n" + values + body


@dataclass
class Answer:
    """What a query returned, and enough to say where it came from."""

    query: CannedQuery
    params: dict
    rows: list[dict]
    #: Where the graph came from and how big it is — so an answer can say what
    #: it was computed from rather than appearing out of the air (#621).
    source: str = ""
    triples: Optional[int] = None
    took_s: Optional[float] = None

    @property
    def columns(self) -> tuple[str, ...]:
        return self.query.columns

    def documents(self) -> list[str]:
        """The distinct documents behind this answer, in order of appearance."""
        seen: list[str] = []
        for row in self.rows:
            value = row.get("document")
            if value and value not in seen:
                seen.append(value)
        return seen


def run(name: str, graph: Graph, *, source: str = "", triples: Optional[int] = None,
        **params) -> Answer:
    """Run one canned query against *graph* and return its rows as strings.

    Values come back as plain strings (and ``None`` for an unbound OPTIONAL),
    because every consumer here is a message or a page. ``None`` is kept
    distinct from ``""``: an unbound authority link and an empty label are
    different findings.
    """
    query = get(name)
    terms = _bind(query, params)
    started = time.monotonic()
    try:
        result = graph.query(query.sparql(), initBindings=terms)
    except Exception as exc:                        # noqa: BLE001
        raise QueryError(f"{query.name} ist nicht ausführbar: {exc}") from exc
    rows = []
    for row in result:
        rows.append({column: _plain(getattr(row, column, None))
                     for column in query.columns})
    return Answer(query=query, params=dict(params), rows=rows, source=source,
                  triples=triples, took_s=time.monotonic() - started)


def _plain(term: Any) -> Optional[str]:
    if term is None:
        return None
    return str(term)


def run_remote(name: str, endpoint: str, *, timeout: float = 60.0, **params) -> Answer:
    """Run one canned query against a SPARQL endpoint (KG-11, #654).

    The query goes out as the same standalone text ``as_text`` renders for a
    paste — bound terms as a ``VALUES`` line through ``n3()``, so nothing a
    caller typed becomes syntax on the wire either. SPARQL 1.1 protocol:
    ``POST`` with ``query=`` as a form, ``application/sparql-results+json``
    back; an unbound variable is ``None``, exactly as in :func:`run`, so a
    consumer cannot tell the two paths apart except by ``Answer.source``,
    which names the endpoint.

    No retries, no fallback to the local graph: an endpoint that does not
    answer is reported as such. Answering from a different graph than the one
    the reader was promised is the failure this must not have.
    """
    import requests

    query = get(name)
    text = as_text(name, **params)
    started = time.monotonic()
    try:
        response = requests.post(
            endpoint, data={"query": text},
            headers={"Accept": "application/sparql-results+json"},
            timeout=timeout)
    except requests.RequestException as exc:
        raise QueryError(f"{query.name}: der Endpunkt {endpoint} antwortet nicht "
                         f"({type(exc).__name__})") from exc
    if response.status_code != 200:
        raise QueryError(f"{query.name}: der Endpunkt antwortet {response.status_code}: "
                         f"{response.text[:200]}")
    try:
        bindings = response.json()["results"]["bindings"]
    except (ValueError, KeyError, TypeError) as exc:
        raise QueryError(f"{query.name}: keine SPARQL-JSON-Antwort vom Endpunkt") from exc
    rows = [{column: (cell["value"] if (cell := binding.get(column)) else None)
             for column in query.columns} for binding in bindings]
    return Answer(query=query, params=dict(params), rows=rows, source=endpoint,
                  triples=None, took_s=time.monotonic() - started)


# ── the graph to ask ────────────────────────────────────────────────────────

#: Parsed builds, keyed by (path, mtime, size). A refreshed build has a new
#: key, so the cache cannot serve the graph the endpoint no longer has — which
#: is the only failure mode worth caring about here: a stale answer that looks
#: current.
_PARSED: dict[tuple, Graph] = {}


def load_graph(build_path: str | Path = "build/corpus.ttl",
               outputs_dir: Optional[str | Path] = None,
               *, cache: bool = True) -> tuple[Graph, str]:
    """``(graph, where_it_came_from)``.

    The built Turtle when there is one — that is the artefact the public
    endpoint serves, so a query answered from it answers the same thing the
    endpoint would. Otherwise the graph is built from the pipeline records, and
    the returned description says so: an answer computed from a different graph
    than the endpoint serves must not look identical to one that was not.
    """
    path = Path(build_path)
    if path.exists():
        stat = path.stat()
        key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
        graph = _PARSED.get(key) if cache else None
        if graph is None:
            graph = Graph()
            graph.parse(path, format="turtle")
            if cache:
                _PARSED.clear()                     # one build at a time
                _PARSED[key] = graph
        try:
            from knowledge_hub import corpus_build
            manifest = corpus_build.read_manifest(path)
        except Exception:                           # noqa: BLE001
            manifest = None
        built = (manifest or {}).get("built_at")
        when = f", gebaut {built}" if built else ""
        return graph, f"{path}{when}"
    if outputs_dir is None:
        import config
        outputs_dir = config.OUTPUTS_DIR
    # Deliberately not cached: there is no single mtime for a directory of
    # pipeline records, and a cache that cannot tell when it went stale is how
    # a query starts answering from last week's corpus.
    return (rx.corpus_to_graph(outputs_dir),
            f"frisch aus {outputs_dir} gebaut (nicht der Endpoint-Graph)")


# ── rendering ──────────────────────────────────────────────────────────────

#: Discord hard-limits a message to 2000 characters.
LIMIT = 1800

#: Written once in the header so every row can use the short form and still be
#: traceable: `ah:document/x` resolves against this.
BASE_NOTE = f"`ah:` = `{rx.CORPUS_BASE}`"


def _short(value: Optional[str]) -> str:
    """A URI as its CURIE, anything else as itself, nothing as a marked dash.

    ``—`` for an unbound OPTIONAL: the cell has to say "no value here", and an
    empty cell in a code block reads as a formatting accident.
    """
    if value is None or value == "":
        # ``""`` is what GROUP_CONCAT yields over nothing — the same finding
        # as an unbound OPTIONAL, and it must read the same.
        return "—"
    for short, uri in PREFIXES.items():
        if value.startswith(uri) and uri:
            return f"{short}:{value[len(uri):]}"
    return value


def format_answer(answer: Answer, *, limit: int = LIMIT,
                  max_rows: int = 15) -> str:
    """One answer as a Discord message: header, rows, and where it came from.

    No rows is a sentence, not a blank — and it names the parameters, because
    "nothing found" and "nothing found *for the 15th century*" send a reader to
    different next steps.
    """
    asked = ", ".join(f"{k}={v}" for k, v in answer.params.items() if v) or "—"
    head = [f"**{answer.query.name}** — {answer.query.question}",
            f"_Parameter: {asked}_"]
    if not answer.rows:
        head.append("Keine Treffer. Der Graph enthält dazu nichts — das ist "
                    "eine Antwort, kein Fehler.")
        head.append(_provenance(answer))
        return "\n".join(head)

    shown = answer.rows[:max_rows]
    body = "\n".join(
        " · ".join(_short(row.get(column)) for column in answer.columns)
        for row in shown)
    while len(body) > limit and shown:
        shown = shown[:-1]
        body = "\n".join(
            " · ".join(_short(row.get(column)) for column in answer.columns)
            for row in shown)
    head.append("`" + " · ".join(answer.columns) + "`")
    head.append("```\n" + body + "\n```")
    if len(shown) < len(answer.rows):
        head.append(f"_{len(shown)} von {len(answer.rows)} Zeilen._")
    head.append(BASE_NOTE)
    head.append(_provenance(answer))
    return "\n".join(head)


def _provenance(answer: Answer) -> str:
    bits = []
    if answer.source:
        bits.append(f"Graph: {answer.source}")
    if answer.triples is not None:
        bits.append(f"{answer.triples} Tripel")
    if answer.took_s is not None:
        bits.append(f"{answer.took_s * 1000:.0f} ms")
    return "_" + " · ".join(bits) + "_" if bits else ""
