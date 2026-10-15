"""validate.py — ist dieser Graph CIDOC-CRM-valide, mit dem Vokabular, das OntoMe führt?

    python -m knowledge_hub.ontology.validate graph.ttl

Vier Prüfungen, in dieser Reihenfolge, jede mit einem benannten Befund:

1. ``unknown-class`` / ``unknown-property`` — ein Begriff, der weder im Register
   steht noch (bei ``ontome``-Begriffen) im Schnappschuss oder CRM-RDFS. Das ist
   der Befund «erfunden»: 40 Mal im bisherigen Korpusexporter (KG-12).
2. ``domain`` / ``range`` — Subjekt bzw. Objekt eines Tripels ist nicht vom
   Typ, den die Property verlangt, auch nicht über Unterklassen. Range
   ``rdfs:Literal`` verlangt ein Literal, alles andere eine Ressource mit
   passendem Typ. Ein Objekt **ohne** Typ im Graphen ist ``range-untyped``:
   nicht falsch, aber nicht belegt — und das wird gemeldet, nicht
   durchgewinkt.
3. ``provenance`` — jeder Knoten der Kernklassen (E31, E21, E53, E74, E55 und
   die SDHSS-Beziehungen) trägt ``prov:wasDerivedFrom``.
4. SHACL (``shapes.ttl``, pyshacl) — Formen, die eine Ontologie nicht
   ausdrückt: genau zwei Partner an einer C9, ein Text an jedem E42, ein Typ
   als Ressource an P2, ``owl:sameAs`` nur in bekannte Namensräume.

Die Hierarchie kommt aus beiden Quellen: CRM-Klassen aus dem RDFS
(``crm.py``), SDHSS- und andere aus den OntoMe-Schnappschüssen (``ontome.py``),
zusammengeführt über die URI.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from rdflib import Graph, Literal, URIRef, RDF, BNode

from knowledge_hub.ontology import crm as crm_mod
from knowledge_hub.ontology import ontome
from knowledge_hub.ontology import registry

SHAPES = Path(__file__).resolve().parent / "shapes.ttl"
CRM_NS = str(registry.CRM)
RDF_TYPE = str(RDF.type)

#: Knoten dieser Klassen müssen ihre Herkunft tragen.
PROVENANCE_REQUIRED = frozenset({
    str(registry.CRM["E31_Document"]), str(registry.CRM["E21_Person"]),
    str(registry.CRM["E53_Place"]), str(registry.CRM["E74_Group"]),
    str(registry.CRM["E55_Type"]), str(registry.CRM["E13_Attribute_Assignment"]),
    str(registry.CRM["E65_Creation"]),
    str(registry.SDH_SLC["C9"]), str(registry.SDH_SLC["C3"]), str(registry.SDH_SLC["C5"]),
    str(registry.SDH_SLC["C13"]),
})


@dataclass(frozen=True)
class Finding:
    kind: str          # unknown-class | unknown-property | domain | range | range-untyped | provenance | shacl
    subject: str
    predicate: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.kind}] {registry.curie(self.subject)} {registry.curie(self.predicate)} — {self.detail}"


@dataclass
class Report:
    triples: int = 0
    findings: list[Finding] = field(default_factory=list)
    checked_properties: int = 0

    @property
    def ok(self) -> bool:
        return not [f for f in self.findings if f.kind != "range-untyped"]

    def counts(self) -> dict[str, int]:
        return dict(Counter(f.kind for f in self.findings))

    def render(self, limit: int = 40) -> str:
        lines = [f"{self.triples} Tripel, {self.checked_properties} Property-Verwendungen geprüft",
                 "Befunde: " + (", ".join(f"{k}={v}" for k, v in sorted(self.counts().items())) or "keine")]
        for f in self.findings[:limit]:
            lines.append("  " + str(f))
        if len(self.findings) > limit:
            lines.append(f"  … {len(self.findings) - limit} weitere")
        lines.append("VALIDE" if self.ok else "NICHT VALIDE")
        return "\n".join(lines)


class Hierarchy:
    """Oberklassen über beide Quellen; `is_a(uri, ancestor)`.

    OntoMe schreibt CIDOC-URIs kurz (``…/cidoc-crm/E21``), das RDFS lang
    (``…/cidoc-crm/E21_Person``). Beides meint dieselbe Klasse, und ohne
    Abgleich wäre jede SDHSS-Property mit CIDOC-Domain ein falscher Befund.
    ``canon`` führt die Kurzform auf die RDFS-Form zurück; alles andere bleibt.
    """

    def __init__(self, voc: ontome.Vocabulary, crm: crm_mod.Crm):
        self.voc = voc
        self.crm = crm
        self._cache: dict[str, set[str]] = {}
        self._short: dict[str, str] = {}
        for full in crm.classes:
            local = full[len(CRM_NS):]
            self._short[CRM_NS + local.split("_", 1)[0]] = full
        for full in crm.properties:
            local = full[len(CRM_NS):]
            self._short[CRM_NS + local.split("_", 1)[0]] = full

    def canon(self, uri: str) -> str:
        u = str(uri)
        return self._short.get(u, u)

    def ancestors(self, uri: str) -> set[str]:
        uri = self.canon(uri)
        if uri in self._cache:
            return self._cache[uri]
        out: set[str] = set()
        todo = [uri]
        while todo:
            u = todo.pop()
            parents = {self.canon(x) for x in self.crm.parents.get(u, ())}
            # OntoMe kennt die Klasse unter der Kurzform oder der langen; beide nachsehen.
            for key in {u, next((k for k, v in self._short.items() if v == u), u)}:
                c = self.voc.classes.get(key)
                if c is not None:
                    parents |= {self.canon(p.uri) for p in
                                (self.voc.class_by_id(pid) for pid in c.parents) if p is not None}
            for p in parents:
                if p not in out:
                    out.add(p)
                    todo.append(p)
        self._cache[uri] = out
        return out

    def is_a(self, uri: str, ancestor: str) -> bool:
        u, a = self.canon(uri), self.canon(ancestor)
        return u == a or a in self.ancestors(u)

    def known_class(self, uri: str) -> bool:
        u = self.canon(uri)
        return u in self.crm.classes or u in self.voc.classes or str(uri) in self.voc.classes


RDFS_LITERAL = "http://www.w3.org/2000/01/rdf-schema#Literal"
E59 = CRM_NS + "E59_Primitive_Value"


#: Die CRM-Primitivwerte. Das RDFS 7.1.3 führt sie nicht als Klassen (es
#: schreibt rdfs:Literal), OntoMe führt sie als E59…E62/E94/E95 — beides sind
#: Werte, keine Knoten.
_PRIMITIVES = ("E59", "E60", "E61", "E62", "E94", "E95")


def _is_literal_range(r: Optional[str], h: "Hierarchy") -> bool:
    if r is None:
        return False
    if r in (registry.LITERAL, RDFS_LITERAL) or r.startswith("http://www.w3.org/2001/XMLSchema#"):
        return True
    if r.startswith(CRM_NS):
        local = r[len(CRM_NS):].split("_", 1)[0]
        if local in _PRIMITIVES:
            return True
    return h.is_a(r, E59)


def _expected(prop: str, voc: ontome.Vocabulary, crm: crm_mod.Crm,
              term: registry.Term, h: "Hierarchy") -> list[tuple[Optional[str], Optional[str]]]:
    """Die zulässigen (Domain, Range)-Paare einer Property — mehrere, wenn
    OntoMe sie je Domain getrennt führt (P8 für E67 und für E69)."""
    if term.source == "ours" or term.source == "w3c":
        return [(term.domain, term.range)]
    pairs = []
    if prop in crm.properties:
        d, r = crm.properties[prop]
        pairs.append((d or None, r or None))
    for p in voc.properties.values():
        if h.canon(p.uri) == h.canon(prop):
            d = voc.class_by_id(p.domain); r = voc.class_by_id(p.range)
            pairs.append((h.canon(d.uri) if d else None, h.canon(r.uri) if r else None))
    return pairs or [(None, None)]


def validate(g: Graph, *, voc: Optional[ontome.Vocabulary] = None,
             crm: Optional[crm_mod.Crm] = None, shacl: bool = True) -> Report:
    voc = voc or ontome.load()
    crm = crm or crm_mod.load()
    h = Hierarchy(voc, crm)
    rep = Report(triples=len(g))
    types: dict[str, set[str]] = {}
    for s, o in g.subject_objects(RDF.type):
        types.setdefault(str(s), set()).add(str(o))

    # 1. Begriffe
    for cls in sorted({t for ts in types.values() for t in ts}):
        term = registry.term(cls)
        if term is None:
            rep.findings.append(Finding("unknown-class", cls, RDF_TYPE,
                                        "nicht im Register — erfunden oder nicht erklärt"))
        elif term.source == "ontome" and not h.known_class(cls):
            rep.findings.append(Finding("unknown-class", cls, RDF_TYPE,
                                        "im Register als OntoMe-Begriff, aber in keinem Schnappschuss"))
    used_props = sorted({str(p) for p in g.predicates()})
    for prop in used_props:
        if prop == RDF_TYPE:
            continue
        term = registry.term(prop)
        if term is None:
            rep.findings.append(Finding("unknown-property", "*", prop,
                                        "nicht im Register — erfunden oder nicht erklärt"))
        elif term.source == "ontome" and prop not in crm.properties and \
                h.canon(prop) not in {h.canon(p.uri) for p in voc.properties.values()}:
            rep.findings.append(Finding("unknown-property", "*", prop,
                                        "im Register als OntoMe-Begriff, aber in keinem Schnappschuss"))

    # 2. Domain / Range — die zulässigen Paare einmal je Property, nicht je
    # Tripel: auf dem Dodis-Graphen (~3 Mio. Tripel) wäre das sonst eine
    # halbe Milliarde Durchläufe über die Property-Liste.
    for prop in used_props:
        term = registry.term(prop)
        if prop == RDF_TYPE or term is None:
            continue
        pairs = _expected(prop, voc, crm, term, h)
        literal_ok = any(_is_literal_range(r, h) for _, r in pairs)
        only_literal = literal_ok and all(_is_literal_range(r, h) for _, r in pairs if r)
        for s, o in g.subject_objects(URIRef(prop)):
            rep.checked_properties += 1
            s_types = types.get(str(s), set())
            dom_ok = any(d is None or any(h.is_a(t, d) for t in s_types) for d, _ in pairs)
            if not dom_ok and s_types:
                rep.findings.append(Finding("domain", str(s), prop,
                    f"Subjekt ist {', '.join(registry.curie(t) for t in sorted(s_types))}, "
                    f"verlangt {' | '.join(registry.curie(d) for d, _ in pairs if d)}"))
            elif not dom_ok and not s_types and not isinstance(s, BNode):
                rep.findings.append(Finding("domain", str(s), prop, "Subjekt ohne Typ"))
            ranges = [r for _, r in pairs]
            # Ein Literal ist richtig, wo der Range rdfs:Literal ist — oder ein
            # CRM-Primitivwert (E59 und seine Abkömmlinge: E60 Number, E61 Time
            # Primitive, E62 String, E94/E95). OntoMe schreibt für P82a «E61»,
            # das RDFS «rdfs:Literal»; beides heisst: ein Wert, kein Knoten.
            if isinstance(o, Literal):
                if literal_ok or any(r is None for r in ranges):
                    continue
                rep.findings.append(Finding("range", str(s), prop,
                    f"Ressource vom Typ {' | '.join(registry.curie(r) for r in ranges if r)} verlangt, Literal gegeben"))
                continue
            if only_literal:
                rep.findings.append(Finding("range", str(s), prop,
                                            f"Literal verlangt, {registry.curie(str(o))} gegeben"))
                continue
            o_types = types.get(str(o), set())
            if not o_types:
                if any(r is None for r in ranges) or term.source != "ontome":
                    continue
                rep.findings.append(Finding("range-untyped", str(s), prop,
                    f"Objekt {registry.curie(str(o))} hat im Graphen keinen Typ — Range nicht belegt"))
                continue
            if not any(r is None or any(h.is_a(t, r) for t in o_types) for r in ranges):
                rep.findings.append(Finding("range", str(s), prop,
                    f"Objekt ist {', '.join(registry.curie(t) for t in sorted(o_types))}, "
                    f"verlangt {' | '.join(registry.curie(r) for r in ranges if r)}"))

    # 3. Herkunft
    derived = {str(s) for s in g.subjects(registry.PROV["wasDerivedFrom"], None)}
    for node, ts in types.items():
        if ts & PROVENANCE_REQUIRED and node not in derived:
            rep.findings.append(Finding("provenance", node, str(registry.PROV["wasDerivedFrom"]),
                                        "kein prov:wasDerivedFrom — woher stammt dieser Knoten?"))

    # 4. SHACL
    if shacl and SHAPES.exists():
        from pyshacl import validate as shacl_validate
        conforms, results_graph, _ = shacl_validate(g, shacl_graph=Graph().parse(str(SHAPES), format="turtle"),
                                                    inference="none", abort_on_first=False)
        if not conforms:
            SH = "http://www.w3.org/ns/shacl#"
            for res in results_graph.subjects(RDF.type, URIRef(SH + "ValidationResult")):
                focus = next(results_graph.objects(res, URIRef(SH + "focusNode")), "")
                path = next(results_graph.objects(res, URIRef(SH + "resultPath")), "")
                msg = next(results_graph.objects(res, URIRef(SH + "resultMessage")), "")
                rep.findings.append(Finding("shacl", str(focus), str(path), str(msg)))
    rep.findings.sort(key=lambda f: (f.kind, f.subject, f.predicate, f.detail))
    return rep


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("graph", type=Path)
    ap.add_argument("--no-shacl", action="store_true")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args(argv)
    g = Graph()
    g.parse(str(args.graph))
    rep = validate(g, shacl=not args.no_shacl)
    print(rep.render(args.limit))
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
