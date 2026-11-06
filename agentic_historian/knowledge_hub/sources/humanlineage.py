"""humanlineage.py — HL-9 (#830): Verwandtschaft aus Human Lineage in den Bestandsgraphen.

    python -m knowledge_hub.sources.humanlineage --graph build/kf.nt build/ssrq.nt \\
        --out build/humanlineage.nt --validate

Human Lineage (humanlineage.org, Zenodo-Schnappschuss, CC BY-SA 4.0) kennt für
2,7 Mio. verstorbene Personen mit persistenter Kennung deren Eltern, Kinder und
Ehepartner. Der MCP dazu (``knowledge_hub/mcp_registry.py``, ``humanlineage``,
idhefix :8025) beantwortet ``get_by_identifier(scheme, value)`` für Wikidata-,
HLS- und GND-Ids und ``get_relatives(pid)``; jede Antwort trägt das
Schnappschuss-Datum.

**Was diese Quelle tut, und was nicht.** Sie liest einen gebauten
Bestandsgraphen (die ``.nt`` der anderen Quellen), sammelt jede E21 Person, die
eine Kennung trägt, die Human Lineage auflöst — ``owl:sameAs`` auf
``wikidata.org/entity/Q…``, ``hls-dhs-dss.ch/de/articles/…`` oder
``d-nb.info/gnd/…``, oder eine E42 mit Quelle ``hls``/``gnd`` — und fragt sie
nach. Für jede Antwort mit genau **einem** Treffer kommt ``owl:sameAs`` auf die
Human-Lineage-URI und (wo noch nicht da) auf die QID. Eltern-Kind-Paare werden
CRM ``P152_has_parent``, Ehen ``sdh-slc:C9 Relationship`` «Ehe» — genau wie bei
ssrq (``bestandsgraph.Build.parent`` / ``partnership``) — **aber nur, wenn beide
Enden Personen des Graphen sind.** Eine Verwandte, die der Graph nicht kennt,
wird nicht erfunden (``withheld.relatives_outside_graph``); so hält es der
Dump selbst. Abgeleitete Geschwister (``siblings_derived``) sind keine Kante.

Provenienz: der Datensatz-Knoten trägt die Schnappschuss-DOI und das Datum,
jede Partnerschaft ``prov:wasDerivedFrom`` darauf; die Elternkanten sind, wie
bei ssrq, blanke P152 und erben die Provenienz über den Datensatz (das Manifest
nennt ihn). Mehrdeutige Treffer (``status`` nicht ``match``, oder mehr als eine
Person) werden gezählt und nicht geraten.

**Lizenz.** CC BY-SA 4.0: der Graph ist abgeleitet. Nennung «Human Lineage»
mit Profil-URL steht an jedem ``sameAs``-Ziel; ob ShareAlike mit der
Graph-Lizenz verträglich ist, ist vor einem Export zu entscheiden und im
Manifest unter ``licence`` festgehalten — dieses Modul entscheidet es nicht.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Protocol

from rdflib import Graph, Literal, URIRef, RDF, RDFS
from rdflib.namespace import OWL

from knowledge_hub.bestandsgraph import CRM, Build, Dataset
from knowledge_hub.sources._common import (ExportReport, git_commit, now,
                                           validate_graph, write_manifest, write_sorted_ntriples)

HL_PERSON = "https://humanlineage.org/person/{}"
WD = "https://www.wikidata.org/entity/{}"
LICENCE_NOTE = ("CC BY-SA 4.0 — attribution «Human Lineage, https://humanlineage.org»; "
                "ShareAlike against the graph's licence to be decided before export (#830)")

_WD_RE = re.compile(r"wikidata\.org/entity/(Q\d+)$")
_HLS_RE = re.compile(r"hls-dhs-dss\.ch/\w+/articles/(\d+)/?$")
_GND_RE = re.compile(r"d-nb\.info/gnd/([\w-]+)$")
#: Order of preference when a person carries several: Wikidata is what Human
#: Lineage is built on, HLS and GND are its bridges.
SCHEMES = ("wikidata", "hls", "gnd")


class Client(Protocol):
    def get_by_identifier(self, scheme: str, value: str) -> dict: ...
    def get_relatives(self, pid: str) -> dict: ...
    def snapshot_info(self) -> dict: ...


class MCPClient:
    """The registry's ``humanlineage`` source over ``utils.mcp_client``."""

    def __init__(self, source_name: str = "humanlineage"):
        from knowledge_hub import mcp_registry as reg
        self.source = reg.get_source(source_name)

    def _call(self, tool: str, arguments: dict) -> Any:
        from utils import mcp_client
        return asyncio.run(mcp_client._call_tool(self.source, tool, arguments))

    def get_by_identifier(self, scheme: str, value: str) -> dict:
        return self._call("get_by_identifier", {"scheme": scheme, "value": value}) or {}

    def get_relatives(self, pid: str) -> dict:
        return self._call("get_relatives", {"pid": pid}) or {}

    def snapshot_info(self) -> dict:
        return self._call("snapshot_info", {}) or {}


# ── the graph's persons and their identifiers ────────────────────────────────

def person_identifiers(g: Graph) -> dict[URIRef, dict[str, str]]:
    """Every E21 Person of the graph with the identifiers Human Lineage can
    resolve: ``{person_uri: {"wikidata": "Q…", "hls": "029202", "gnd": "…"}}``.
    Persons without any such identifier are not in the result."""
    out: dict[URIRef, dict[str, str]] = {}
    for p in g.subjects(RDF.type, CRM["E21_Person"]):
        ids: dict[str, str] = {}
        for same in g.objects(p, OWL["sameAs"]):
            s = str(same)
            for scheme, rx in (("wikidata", _WD_RE), ("hls", _HLS_RE), ("gnd", _GND_RE)):
                m = rx.search(s)
                if m and scheme not in ids:
                    ids[scheme] = m.group(1)
        for ident in g.objects(p, CRM["P1_is_identified_by"]):
            value = g.value(ident, CRM["P190_has_symbolic_content"])
            kind = g.value(ident, CRM["P2_has_type"])
            label = str(g.value(kind, RDFS.label) or "") if kind else ""
            if value and label in ("hls", "gnd") and label not in ids:
                ids[label] = str(value)
        if ids:
            out[p] = ids
    return out


def _lookup(client: Client, ids: dict[str, str]) -> tuple[Optional[dict], str]:
    """One Human Lineage person for a graph person, or why not: ``(person, outcome)``
    with outcome in ``match`` / ``ambiguous`` / ``none``. The schemes are tried
    in ``SCHEMES`` order; the first unambiguous answer wins."""
    saw_ambiguous = False
    for scheme in SCHEMES:
        if scheme not in ids:
            continue
        answer = client.get_by_identifier(scheme, ids[scheme]) or {}
        persons = answer.get("persons") or []
        if answer.get("status") == "match" and len(persons) == 1:
            return persons[0], "match"
        if persons:
            saw_ambiguous = True
    return None, ("ambiguous" if saw_ambiguous else "none")


def _hl_keys(rec: dict) -> list[tuple[str, str]]:
    """The identifiers a Human Lineage record carries, as ``(scheme, value)``."""
    keys = [("hl", rec.get("id") or "")]
    for scheme, field in (("wikidata", "wikidata_id"), ("hls", "hls_id"), ("gnd", "gnd_id")):
        if rec.get(field):
            keys.append((scheme, str(rec[field])))
    return [k for k in keys if k[1]]


# ── the build ────────────────────────────────────────────────────────────────

def build(graph: Graph, client: Client, *, snapshot: Optional[dict] = None,
          progress: Optional[Callable[[str], None]] = None) -> tuple[Graph, ExportReport]:
    """The Human Lineage layer over ``graph``: sameAs and kinship edges for the
    persons both sides know. Returns the new triples (not ``graph``) and the report."""
    t0 = time.time()
    snap = snapshot or client.snapshot_info() or {}
    date = snap.get("snapshot_date") or snap.get("snapshot") or "?"
    doi = snap.get("doi") or snap.get("concept_doi") or ""
    ds = Dataset(key="humanlineage", label="Human Lineage",
                 version=f"Zenodo {doi}, snapshot {date}".strip(),
                 uri="https://humanlineage.org/")
    b = Build(ds)
    b.g.add((b.dataset_uri, RDFS.comment, Literal(LICENCE_NOTE)))   # prov:Entity is no E1: no P3

    counts: Counter = Counter()
    held: Counter = Counter()
    persons = person_identifiers(graph)
    counts["persons_with_identifier"] = len(persons)

    # pass 1: who is in Human Lineage — and under which keys the graph knows them
    matched: dict[URIRef, dict] = {}
    by_key: dict[tuple[str, str], URIRef] = {}
    for i, (uri, ids) in enumerate(sorted(persons.items(), key=lambda kv: str(kv[0]))):
        rec, outcome = _lookup(client, ids)
        if rec is None:
            held[f"lookup_{outcome}"] += 1
            continue
        matched[uri] = rec
        for key in _hl_keys(rec):
            by_key.setdefault(key, uri)
        for scheme, value in ids.items():
            by_key.setdefault((scheme, value), uri)
        if progress and i % 100 == 0:
            progress(f"{i}/{len(persons)} looked up, {len(matched)} matched")
    counts["persons_matched"] = len(matched)

    def resolve(rec: dict) -> Optional[URIRef]:
        for key in _hl_keys(rec):
            if key in by_key:
                return by_key[key]
        return None

    # pass 2: sameAs and the edges whose both ends the graph has
    seen_pairs: set[tuple[str, str]] = set()
    for uri, rec in matched.items():
        same = [HL_PERSON.format(rec["id"])]
        if rec.get("wikidata_id") and not any(_WD_RE.search(str(s)) for s in graph.objects(uri, OWL["sameAs"])):
            same.append(WD.format(rec["wikidata_id"]))
        b.same_as(uri, same)
        counts["same_as"] += len(same)
        rel = client.get_relatives(rec["id"]) or {}
        for parent in rel.get("parents") or []:
            pu = resolve(parent)
            if pu is None:
                held["relatives_outside_graph"] += 1
                continue
            b.parent(uri, pu)
            counts["parents"] += 1
        for child in rel.get("children") or []:
            cu = resolve(child)
            if cu is None:
                held["relatives_outside_graph"] += 1
                continue
            b.parent(cu, uri)
            counts["parents"] += 1
        for spouse in rel.get("spouses") or []:
            su = resolve(spouse)
            if su is None:
                held["relatives_outside_graph"] += 1
                continue
            pair = tuple(sorted((str(uri), str(su))))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            b.partnership(uri, su, "Ehe", note=f"Human Lineage, Schnappschuss {date}")
            counts["partnerships"] += 1
        held["siblings_derived"] += len(rel.get("siblings_derived") or rel.get("siblings") or [])
    # P152 is added twice for a pair seen from both sides; the graph keeps one triple
    counts["parents"] = sum(1 for _ in b.g.triples((None, CRM["P152_has_parent"], None)))

    report = ExportReport(source="humanlineage", built_at=now(),
                          db=f"Human Lineage MCP, snapshot {date}", commit=git_commit(),
                          triples=len(b.g), counts=dict(counts), withheld=dict(held),
                          seconds=round(time.time() - t0, 1))
    report.validation = {"licence": LICENCE_NOTE}
    return b.g, report


def load_graphs(paths: Iterable[str | Path]) -> Graph:
    g = Graph()
    for p in paths:
        g.parse(str(p), format="nt")
    return g


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graph", nargs="+", required=True, help="the built .nt files to enrich")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--validate", action="store_true", help="run the KG-6 check (SHACL) on the result")
    args = ap.parse_args(argv)
    graph = load_graphs(args.graph)
    g, report = build(graph, MCPClient(), progress=lambda s: print(s, file=sys.stderr))
    report.destination = str(args.out)
    report.triples = write_sorted_ntriples(g, args.out)
    if args.validate:
        report.validation.update(validate_graph(graph + g, shacl=True))
    write_manifest(report, args.out)
    print(report.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
