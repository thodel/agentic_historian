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

Run: pytest agentic_historian/tests/test_kg_5_sparql_queries.py -v
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import XSD

from knowledge_hub import rdf_export as rx

__all__ = [
    "PREFIX_BLOCK", "CannedQuery", "QUERIES", "QueryError", "Answer",
    "names", "get", "describe_all", "as_text", "run", "format_answer",
    "load_graph", "PERSON_TYPES", "param_terms",
]

#: Entity types that identify a natural person. The same pair
#: ``corpus_build`` uses, so "persons" means the same thing in a published
#: graph and in a query over it.
PERSON_TYPES = ("PERSON", "CARE_ACTOR")

#: Prefix block for every query, built from the exporter's own table — see the
#: module docstring. Sorted, so the rendered query text is stable and diffable.
PREFIX_BLOCK = "\n".join(
    f"PREFIX {short}: <{uri}>" for short, uri in sorted(rx.PREFIXES.items()))


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
  ?document cidoc-crm:P128_carries ?reading .
  ?reading sdhss:readingRole "working" ;
           cidoc-crm:P67_refers_to ?person .
  ?person rdfs:label ?personLabel ;
          sdhss:entityType ?entityType .
  {_person_filter("entityType")}
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
              "hat ein Historiker als die nächste gewählt?"),
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
    body="""
  ?document cidoc-crm:P128_carries ?reading .
  ?reading cidoc-crm:P67_refers_to ?entity .
  OPTIONAL { ?entity owl:sameAs ?link }
  OPTIONAL { ?entity cidoc-crm:P1_is_identified_by ?idNode .
             ?idNode cidoc-crm:P2_has_type ?localSource . }
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
            cidoc-crm:P128_carries ?reading .
  ?reading cidoc-crm:P67_refers_to ?personA, ?personB .
  ?personA rdfs:label ?labelA ; sdhss:entityType ?typeA .
  ?personB rdfs:label ?labelB ; sdhss:entityType ?typeB .
  {_person_filter("typeA")}
  {_person_filter("typeB")}
  # Each pair once, in a fixed order. Without this every pair appears twice
  # (A,B and B,A) and every person appears paired with themselves, which would
  # turn a co-occurrence count into a number nobody can interpret.
  FILTER(STR(?personA) < STR(?personB))
""",
    tail="ORDER BY ?documentLabel ?labelA ?labelB",
)

QUERIES: dict[str, CannedQuery] = {
    q.name: q for q in (_PERSONS_IN_CARE, _READINGS_OF_PAGE,
                        _AUTHORITY_COVERAGE, _CO_OCCURRENCE)
}


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
    lines = ["**Abfragen über den Korpus-Graphen** (KG-5):", ""]
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
    if value is None:
        return "—"
    for short, uri in rx.PREFIXES.items():
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
