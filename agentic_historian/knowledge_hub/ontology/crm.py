"""crm.py — CIDOC CRM 7.1.3 aus dem amtlichen RDFS, als zweite Vokabularquelle.

OntoMe führt von CIDOC nur, was ein Profil ausgewählt hat; die Hierarchie und
die Domain/Range-Angaben der CRM-Properties stehen vollständig im RDFS, das
cidoc-crm.org veröffentlicht (``snapshots/CIDOC_CRM_v7.1.3.rdfs``, 76 Klassen,
309 Properties, geholt 14.10.2026 von
https://cidoc-crm.org/rdfs/7.1.3/CIDOC_CRM_v7.1.3.rdfs).

Eine Feststellung daraus, die den Korpusexporter betrifft: **E40 Legal Body
gibt es in 7.1.3 nicht** (``grep -c E40``: 0). ``rdf_export._ENTITY_CLASS``
bildet ORG darauf ab — ein Begriff, der in der Fassung, auf die wir uns
berufen, nicht existiert (KG-12).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from rdflib import Graph, RDF, RDFS

RDFS_FILE = Path(__file__).resolve().parent / "snapshots" / "CIDOC_CRM_v7.1.3.rdfs"
VERSION = "7.1.3"
LITERAL_RANGE = "http://www.w3.org/2000/01/rdf-schema#Literal"


@dataclass
class Crm:
    classes: dict[str, str] = field(default_factory=dict)        # uri → label
    properties: dict[str, tuple[str, str]] = field(default_factory=dict)  # uri → (domain, range)
    parents: dict[str, set[str]] = field(default_factory=dict)   # uri → direkte Oberklassen

    def ancestors(self, uri: str) -> set[str]:
        out: set[str] = set()
        todo = [str(uri)]
        while todo:
            u = todo.pop()
            for p in self.parents.get(u, ()):
                if p not in out:
                    out.add(p)
                    todo.append(p)
        return out

    def is_a(self, uri: str, ancestor: str) -> bool:
        return str(uri) == str(ancestor) or str(ancestor) in self.ancestors(uri)


@lru_cache(maxsize=1)
def load(path: Path = RDFS_FILE) -> Crm:
    g = Graph()
    g.parse(str(path), format="xml")
    crm = Crm()
    for c in g.subjects(RDF.type, RDFS.Class):
        crm.classes[str(c)] = str(next(g.objects(c, RDFS.label), ""))
        crm.parents[str(c)] = {str(p) for p in g.objects(c, RDFS.subClassOf)}
    for p in g.subjects(RDF.type, RDF.Property):
        dom = next(g.objects(p, RDFS.domain), None)
        rng = next(g.objects(p, RDFS.range), None)
        crm.properties[str(p)] = (str(dom) if dom is not None else "",
                                  str(rng) if rng is not None else "")
    return crm
