"""HL-9 (#830): Verwandtschaft aus Human Lineage in den Bestandsgraphen — mit einem
Client, der die Antworten des MCP vom 06.11.2026 nachspielt (Albrecht I., Rudolf I.,
Gertrud von Hohenberg, Elisabeth von Görz-Tirol).

Run from the repo root:
    pytest agentic_historian/tests/test_kg_hl9_humanlineage.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from rdflib import Graph, URIRef, RDF, RDFS
from rdflib.namespace import OWL

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from knowledge_hub import bestandsgraph as bg          # noqa: E402
from knowledge_hub.sources import humanlineage as hl    # noqa: E402

WD = "https://www.wikidata.org/entity/{}"
HLS = "https://hls-dhs-dss.ch/de/articles/{}/"

ALBRECHT = {"id": "HL2DM2FSQ3XX", "name": "Albrecht I.", "wikidata_id": "Q153956",
            "gnd_id": "118501607", "hls_id": "029202", "url": "https://humanlineage.org/person/HL2DM2FSQ3XX"}
RUDOLF = {"id": "HLJEMQ3JAQGW", "name": "Rudolf I.", "wikidata_id": "Q76956",
          "gnd_id": "11860371X", "hls_id": "029461"}
GERTRUD = {"id": "HLGSAAGEGE7P", "name": "Gertrud von Hohenberg", "wikidata_id": "Q454769",
           "gnd_id": "123579783", "hls_id": None}
ELISABETH = {"id": "HL7RTFQK8YPM", "name": "Elisabeth von Görz und Tirol", "wikidata_id": "Q241653",
             "gnd_id": "119324660", "hls_id": None}
MATHILDE = {"id": "HL9P5RQTX8RF", "name": "Mathilde von Habsburg", "wikidata_id": "Q2635513",
            "gnd_id": "120266318", "hls_id": None}


class FakeClient:
    """Answers shaped like the live MCP (get_by_identifier → status/persons;
    get_relatives → person/parents/children/spouses/siblings_derived/snapshot)."""

    def __init__(self):
        self.calls = []
        self.by_id = {("wikidata", "Q153956"): [ALBRECHT], ("hls", "029202"): [ALBRECHT],
                      ("hls", "029461"): [RUDOLF], ("wikidata", "Q454769"): [GERTRUD],
                      ("wikidata", "Q241653"): [ELISABETH],
                      ("gnd", "ambiguous"): [RUDOLF, ALBRECHT]}
        self.relatives = {
            "HL2DM2FSQ3XX": {"person": ALBRECHT, "parents": [GERTRUD, RUDOLF],
                             "children": [{"id": "HLWCRHPFJKJM", "name": "Rudolf I.", "wikidata_id": "Q168254"}],
                             "spouses": [ELISABETH], "siblings_derived": [MATHILDE], "snapshot": "2026-10-10"},
            "HLJEMQ3JAQGW": {"person": RUDOLF, "parents": [], "children": [ALBRECHT, MATHILDE],
                             "spouses": [GERTRUD], "siblings_derived": [], "snapshot": "2026-10-10"},
            "HLGSAAGEGE7P": {"person": GERTRUD, "parents": [], "children": [ALBRECHT],
                             "spouses": [RUDOLF], "siblings_derived": [], "snapshot": "2026-10-10"},
            "HL7RTFQK8YPM": {"person": ELISABETH, "parents": [], "children": [], "spouses": [ALBRECHT],
                             "siblings_derived": [], "snapshot": "2026-10-10"},
        }

    def get_by_identifier(self, scheme, value):
        self.calls.append(("get_by_identifier", scheme, value))
        persons = self.by_id.get((scheme, value), [])
        status = "match" if len(persons) == 1 else ("ambiguous" if persons else "no_candidates")
        return {"status": status, "scheme": scheme, "value": value, "persons": persons,
                "snapshot": "2026-10-10"}

    def get_relatives(self, pid):
        self.calls.append(("get_relatives", pid))
        return self.relatives.get(pid, {})

    def snapshot_info(self):
        return {"snapshot_date": "2026-10-10", "doi": "10.5281/zenodo.23284944",
                "licence": "CC-BY-SA-4.0"}


def _kf_graph() -> tuple[Graph, dict[str, URIRef]]:
    """A Königsfelden-like build: three Habsburgs with identifiers, one without,
    and a person the graph knows only by GND (the ambiguous case)."""
    b = bg.Build(bg.Dataset(key="kf", label="Königsfelden", version="test"))
    u = {
        "albrecht": b.person("per000001", "Albrecht I.", same_as=[HLS.format("029202")],
                             identifier=("hls", "029202")),
        "rudolf": b.person("per000002", "Rudolf I.", identifier=("hls", "029461")),
        "gertrud": b.person("per000003", "Gertrud von Hohenberg", same_as=[WD.format("Q454769")]),
        "elisabeth": b.person("per000004", "Elisabeth", same_as=[WD.format("Q241653")]),
        "nobody": b.person("per000005", "Hans Müller"),
        "twins": b.person("per000006", "Zwilling", same_as=["https://d-nb.info/gnd/ambiguous"]),
    }
    return b.g, u


def test_identifiers_are_read_from_same_as_and_from_e42():
    g, u = _kf_graph()
    ids = hl.person_identifiers(g)
    assert ids[u["albrecht"]] == {"hls": "029202"}            # sameAs and E42 say the same once
    assert ids[u["rudolf"]] == {"hls": "029461"}              # E42 only
    assert ids[u["gertrud"]] == {"wikidata": "Q454769"}
    assert ids[u["twins"]] == {"gnd": "ambiguous"}
    assert u["nobody"] not in ids


def test_edges_only_between_persons_the_graph_has_and_same_as_on_both_uris():
    g, u = _kf_graph()
    client = FakeClient()
    out, report = hl.build(g, client)
    P152 = bg.CRM["P152_has_parent"]
    parents = {(s, o) for s, _p, o in out.triples((None, P152, None))}
    assert parents == {(u["albrecht"], u["rudolf"]), (u["albrecht"], u["gertrud"])}
    # Albrecht's child Rudolf (Q168254) and Mathilde are not in the graph: no edge, counted
    assert report.withheld["relatives_outside_graph"] >= 2
    assert report.withheld["siblings_derived"] == 1                   # Mathilde, derived, never an edge
    # one marriage, symmetric URI, so Albrecht×Elisabeth and Rudolf×Gertrud, each once
    C9 = bg.SDH_SLC["C9"]
    assert len(list(out.subjects(RDF.type, C9))) == 2
    assert report.counts["partnerships"] == 2
    # sameAs: the Human Lineage URI always, the QID only where the graph lacked it
    assert (u["albrecht"], OWL.sameAs, URIRef(hl.HL_PERSON.format("HL2DM2FSQ3XX"))) in out
    assert (u["albrecht"], OWL.sameAs, URIRef(WD.format("Q153956"))) in out
    assert (u["gertrud"], OWL.sameAs, URIRef(WD.format("Q454769"))) not in out   # already in the graph
    assert report.counts["persons_matched"] == 4
    assert report.withheld["lookup_ambiguous"] == 1 and report.withheld.get("lookup_none", 0) == 0


def test_the_dataset_carries_snapshot_doi_and_the_licence_note():
    g, _u = _kf_graph()
    out, report = hl.build(g, FakeClient())
    ds = URIRef("https://tei.dh.unibe.ch/id/dataset/humanlineage")
    assert (ds, bg.PROV["value"], None) in out
    version = str(out.value(ds, bg.PROV["value"]))
    assert "10.5281/zenodo.23284944" in version and "2026-10-10" in version
    assert "CC BY-SA" in str(out.value(ds, RDFS.comment))
    assert report.validation["licence"].startswith("CC BY-SA 4.0")
    assert report.db.endswith("snapshot 2026-10-10")


def test_wikidata_is_asked_before_hls_and_an_unanswered_person_is_counted():
    g, u = _kf_graph()
    client = FakeClient()
    client.by_id[("wikidata", "Q454769")] = []            # Gertrud now unknown by QID
    out, report = hl.build(g, client)
    assert ("get_by_identifier", "wikidata", "Q454769") in client.calls
    assert report.withheld["lookup_none"] == 1
    assert (u["albrecht"], bg.CRM["P152_has_parent"], u["gertrud"]) not in out


def test_the_result_validates_against_the_ontology():
    g, _u = _kf_graph()
    out, _report = hl.build(g, FakeClient())
    from knowledge_hub.sources._common import validate_graph
    rep = validate_graph(g + out, shacl=True)
    assert rep["ok"], rep
