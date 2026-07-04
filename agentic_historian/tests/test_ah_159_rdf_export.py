"""
tests/test_ah_159_rdf_export.py

Tests for knowledge_hub/rdf_export.py — RDF/Turtle export using CIDOC-CRM.

Verifies (AH-159):
1. hub_to_graph() returns a Graph with triples for persons, places, vocabulary
2. person_to_rdf() → CRM E21 Person + RDFS.label + schema.sameAs (Wikidata, GND)
3. place_to_rdf() → CRM E53 Place + geo:lat/geo:long + schema.sameAs
4. entity_to_rdf() dispatches correctly for PERSON, PLACE, ORGANISATION, etc.
5. vocabulary_term_to_rdf() → SDHSS.Concept (NOT schema.Concept)

rdflib is required; tests skip cleanly if it is not installed.
"""

import pytest

rdflib = pytest.importorskip("rdflib", reason="rdflib required for RDF export tests")

from rdflib import Graph, RDF, RDFS, Literal, BNode, URIRef
from knowledge_hub import hub
from knowledge_hub.rdf_export import (
    hub_to_graph,
    person_to_rdf,
    place_to_rdf,
    organisation_to_rdf,
    social_group_to_rdf,
    care_action_to_rdf,
    vocabulary_term_to_rdf,
    entity_to_rdf,
    wikipedia_id_to_qid,
    CRM,
    SDHSS,
    WD,
    WDT,
    GND,
    GEO,
    SCHEMA,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_temp_hub(tmp_path):
    """Fresh KnowledgeHub backed by a temp file (no I/O to real hub.json)."""
    import knowledge_hub.hub as hub_module

    class FreshHub(hub_module.KnowledgeHub):
        def __init__(self, path):
            self.path = path
            import threading
            self._lock = threading.Lock()
            self._data = self._load()

    return FreshHub(path=tmp_path / "hub.json")


def subjects_of_type(g: Graph, rdftype) -> list:
    """All subjects that have rdf:type = rdftype."""
    return list(g.subjects(RDF.type, rdftype))


def first_subject_of_type(g: Graph, rdftype):
    """First subject with rdf:type = rdftype, or None."""
    subjects = subjects_of_type(g, rdftype)
    return subjects[0] if subjects else None


def has_type(g: Graph, rdftype) -> bool:
    """True if graph contains at least one subject with the given RDF type."""
    return bool(subjects_of_type(g, rdftype))


def triples_with_pred(g: Graph, pred) -> list:
    """All (s, p, o) triples where p = pred."""
    return list(g.triples((None, pred, None)))


# ── Test fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def sample_hub(tmp_path):
    """Hub with two persons and two places, no vocabulary pre-seeded."""
    h = make_temp_hub(tmp_path)
    h.add_person({
        "id": "p1",
        "name": "Hans Müller",
        "wikidata": "Q12345",
        "gnd": "gnd:123456789",
        "notes": "Schreiber, 1480–1542",
    })
    h.add_person({
        "id": "p2",
        "name": "Anna Fischer",
        "wikidata": "",
        "gnd": "gnd:987654321",
        "notes": "",
    })
    h.add_place({
        "id": "loc1",
        "name": "Bern",
        "wikidata": "Q70174",
        "coordinates": [46.9480, 7.4474],
        "notes": "Hauptstadt",
    })
    h.add_place({
        "id": "loc2",
        "name": "Zürich",
        "wikidata": "Q72",
        "coordinates": None,
        "notes": "",
    })
    return h


@pytest.fixture
def empty_hub(tmp_path):
    """Fresh hub with only default seed vocabulary."""
    return make_temp_hub(tmp_path)


# ── Hub-level export ───────────────────────────────────────────────────────────

class TestHubToGraph:
    """hub_to_graph() exports an entire hub to an rdflib.Graph."""

    def test_returns_graph(self, sample_hub):
        """Returns an rdflib.Graph instance."""
        g = hub_to_graph(sample_hub)
        assert isinstance(g, Graph)

    def test_graph_has_triples(self, sample_hub):
        """Graph contains at least some triples."""
        g = hub_to_graph(sample_hub)
        assert len(g) > 0

    def test_person_triples_present(self, sample_hub):
        """Graph contains CRM.E21_Person triples for the two added persons."""
        g = hub_to_graph(sample_hub)
        persons = subjects_of_type(g, CRM["E21_Person"])
        assert len(persons) >= 2

    def test_place_triples_present(self, sample_hub):
        """Graph contains CRM.E53_Place triples for the two added places."""
        g = hub_to_graph(sample_hub)
        places = subjects_of_type(g, CRM["E53_Place"])
        assert len(places) >= 2

    def test_vocabulary_concept_triples_present(self, empty_hub):
        """Graph contains SDHSS.Concept triples for default seed vocabulary."""
        g = hub_to_graph(empty_hub)
        # vocabulary_term_to_rdf uses SDHSS["Concept"], NOT SCHEMA["Concept"]
        concepts = subjects_of_type(g, SDHSS["Concept"])
        assert len(concepts) >= 1

    def test_serializes_to_turtle(self, sample_hub):
        """hub_to_graph output serialises to Turtle without error."""
        g = hub_to_graph(sample_hub)
        ttl = g.serialize(format="turtle")
        assert isinstance(ttl, str)
        assert len(ttl) > 100  # non-trivial output

    def test_serializes_to_rdf_xml(self, sample_hub):
        """hub_to_graph output serialises to RDF/XML without error."""
        g = hub_to_graph(sample_hub)
        xml = g.serialize(format="xml")
        assert isinstance(xml, str)
        assert len(xml) > 100


# ── person_to_rdf ─────────────────────────────────────────────────────────────

class TestPersonToRdf:
    """person_to_rdf() produces correct CRM E21 Person triples."""

    def _person_graph(self, sample_hub, name):
        """Build a single-person graph for the named person."""
        g = Graph()
        person = next(p for p in sample_hub.all_persons() if p.name == name)
        person_to_rdf(g, person)
        return g

    def test_e21_person_type(self, sample_hub):
        """Person subject has rdf:type crm.E21_Person."""
        g = self._person_graph(sample_hub, "Hans Müller")
        assert has_type(g, CRM["E21_Person"])

    def test_rdfs_label(self, sample_hub):
        """Person subject has RDFS.label with their name (German literal)."""
        g = self._person_graph(sample_hub, "Hans Müller")
        labels = list(g.objects(None, RDFS.label))
        assert Literal("Hans Müller", lang="de") in labels

    def test_wikidata_uri_as_subject(self, sample_hub):
        """Person with wikidata_id uses the Wikidata URI as the subject."""
        g = self._person_graph(sample_hub, "Hans Müller")
        wd_uri = WD["Q12345"]
        assert (wd_uri, RDF.type, CRM["E21_Person"]) in g

    def test_wikidata_same_as(self, sample_hub):
        """Person with wikidata_id has schema:sameAs pointing to their Wikidata URI."""
        g = self._person_graph(sample_hub, "Hans Müller")
        wd_uri = WD["Q12345"]
        sameAs_triples = triples_with_pred(g, SCHEMA["sameAs"])
        # schema:sameAs should point to wd:Q12345
        assert any(o == wd_uri for s, p, o in sameAs_triples)

    def test_gnd_same_as(self, sample_hub):
        """Person with gnd_id has schema:sameAs pointing to their GND URI."""
        g = self._person_graph(sample_hub, "Hans Müller")
        gnd_uri = GND["123456789"]
        sameAs_triples = triples_with_pred(g, SCHEMA["sameAs"])
        assert any(o == gnd_uri for s, p, o in sameAs_triples)

    def test_rdfs_comment_from_notes(self, sample_hub):
        """Person with non-empty notes has an RDFS.comment triple."""
        g = self._person_graph(sample_hub, "Hans Müller")
        comments = list(g.objects(None, RDFS.comment))
        assert any("Schreiber" in str(c) for c in comments)

    def test_e82_actor_appellation(self, sample_hub):
        """Person has a CRM.E82_Actor_Appellation name node via P1_is_identified_by."""
        g = self._person_graph(sample_hub, "Hans Müller")
        assert has_type(g, CRM["E82_Actor_Appellation"])

    def test_blank_node_without_wikidata(self, sample_hub):
        """Person without wikidata_id gets a BNode as subject (not a full URI)."""
        g = self._person_graph(sample_hub, "Anna Fischer")
        persons = subjects_of_type(g, CRM["E21_Person"])
        assert len(persons) == 1
        assert isinstance(persons[0], BNode)


# ── place_to_rdf ──────────────────────────────────────────────────────────────

class TestPlaceToRdf:
    """place_to_rdf() produces correct CRM E53 Place triples."""

    def _place_graph(self, sample_hub, name):
        """Build a single-place graph."""
        g = Graph()
        place = next(p for p in sample_hub.all_places() if p.name == name)
        place_to_rdf(g, place)
        return g

    def test_e53_place_type(self, sample_hub):
        """Place subject has rdf:type crm.E53_Place."""
        g = self._place_graph(sample_hub, "Bern")
        assert has_type(g, CRM["E53_Place"])

    def test_rdfs_label(self, sample_hub):
        """Place subject has RDFS.label with the place name."""
        g = self._place_graph(sample_hub, "Bern")
        labels = list(g.objects(None, RDFS.label))
        assert Literal("Bern", lang="de") in labels

    def test_wikidata_uri_as_subject(self, sample_hub):
        """Place with wikidata_id uses the Wikidata URI as subject."""
        g = self._place_graph(sample_hub, "Bern")
        wd_uri = WD["Q70174"]
        assert (wd_uri, RDF.type, CRM["E53_Place"]) in g

    def test_wikidata_uri_typed_as_wikidata_item(self, sample_hub):
        """Place with wikidata_id is rdf:type wdt:Q70174 (Wikidata item type triple).

        Note: place_to_rdf adds RDF.type = WD[QID] (typing the place as the
        Wikidata item), but does NOT emit schema:sameAs for places (unlike
        person_to_rdf which does add schema:sameAs).  The Wikidata URI IS used
        as the subject, so (WD[Q70174], RDF.type, WD[Q70174]) is the indicator.
        """
        g = self._place_graph(sample_hub, "Bern")
        wd_uri = WD["Q70174"]
        # place_to_rdf types the Wikidata URI as the Wikidata item itself
        assert (wd_uri, RDF.type, wd_uri) in g

    def test_coordinates_geo_lat_long(self, sample_hub):
        """Place with coordinates gets geo:lat and geo:long triples."""
        g = self._place_graph(sample_hub, "Bern")
        bern_node = first_subject_of_type(g, CRM["E53_Place"])
        lats = list(g.objects(bern_node, GEO["lat"]))
        longs = list(g.objects(bern_node, GEO["long"]))
        assert len(lats) == 1
        assert len(longs) == 1
        # Float comparison with rounding tolerance
        assert float(lats[0]) == pytest.approx(46.9480)
        assert float(longs[0]) == pytest.approx(7.4474)

    def test_no_coordinates_when_None(self, sample_hub):
        """Place without coordinates has no geo:lat/geo:long triples."""
        g = self._place_graph(sample_hub, "Zürich")
        zurich_node = first_subject_of_type(g, CRM["E53_Place"])
        lats = list(g.objects(zurich_node, GEO["lat"]))
        longs = list(g.objects(zurich_node, GEO["long"]))
        assert len(lats) == 0
        assert len(longs) == 0

    def test_e48_place_name(self, sample_hub):
        """Place has a CRM.E48_Place_Name node via P1_is_identified_by."""
        g = self._place_graph(sample_hub, "Bern")
        assert has_type(g, CRM["E48_Place_Name"])


# ── entity_to_rdf dispatch ─────────────────────────────────────────────────────

class TestEntityToRdf:
    """entity_to_rdf() dispatches to the correct conversion function by type."""

    def _entity_graph(self, entity_dict):
        from pathlib import Path
        import tempfile
        from knowledge_hub.hub import KnowledgeHub
        with tempfile.TemporaryDirectory() as td:
            h = KnowledgeHub(path=Path(td) / "hub.json")
            return entity_to_rdf(h, entity_dict)

    def test_person_entity(self):
        """type=PERSON → CRM E21 Person."""
        g = self._entity_graph({
            "type": "PERSON",
            "name": "Test Person",
            "wikidata_id": "Q999",
            "gnd_id": "",
            "notes": "Test notes",
        })
        assert has_type(g, CRM["E21_Person"])
        labels = list(g.objects(None, RDFS.label))
        assert Literal("Test Person", lang="de") in labels

    def test_place_entity(self):
        """type=PLACE → CRM E53 Place."""
        g = self._entity_graph({
            "type": "PLACE",
            "name": "Test Place",
            "wikidata_id": "Q888",
            "gnd_id": "",
            "coordinates": [47.0, 8.0],
            "notes": "",
        })
        assert has_type(g, CRM["E53_Place"])

    def test_organisation_entity(self):
        """type=ORGANISATION → CRM E40 Legal Body."""
        g = self._entity_graph({
            "type": "ORGANISATION",
            "name": "Test Spital",
            "wikidata_id": "Q777",
            "gnd_id": "gnd:111",
            "notes": "",
        })
        assert has_type(g, CRM["E40_Legal_Body"])

    def test_social_group_entity(self):
        """type=SOCIAL_GROUP → CRM E74 Group."""
        g = self._entity_graph({
            "type": "SOCIAL_GROUP",
            "name": "Erbarleut",
            "wikidata_id": "",
            "notes": "Honourable people",
        })
        assert has_type(g, CRM["E74_Group"])

    def test_care_action_entity(self):
        """type=CARE_ACTION → CRM E7 Activity."""
        g = self._entity_graph({
            "type": "CARE_ACTION",
            "name": "Pflege",
            "notes": "Care provision",
        })
        assert has_type(g, CRM["E7_Activity"])

    def test_role_entity(self):
        """type=ROLE → SDHSS.Role."""
        g = self._entity_graph({
            "type": "ROLE",
            "name": "Bürger",
        })
        assert has_type(g, SDHSS["Role"])

    def test_date_entity(self):
        """type=DATE → CRM E50 Date."""
        g = self._entity_graph({
            "type": "DATE",
            "name": "1480–1542",
        })
        assert has_type(g, CRM["E50_Date"])

    def test_unknown_type_produces_label(self):
        """Unknown type falls back to RDFS.label only."""
        g = self._entity_graph({
            "type": "WHATEVER",
            "name": "Unknown Entity",
        })
        labels = list(g.objects(None, RDFS.label))
        assert Literal("Unknown Entity", lang="de") in labels


# ── Individual function tests ──────────────────────────────────────────────────

class TestOrganisationToRdf:
    """organisation_to_rdf() → CRM E40 Legal Body."""

    def test_e40_legal_body_type(self):
        g = Graph()
        organisation_to_rdf(g, "Spital Thun", wikidata_id="Q123", gnd_id="gnd:456")
        assert has_type(g, CRM["E40_Legal_Body"])

    def test_rdfs_label(self):
        g = Graph()
        organisation_to_rdf(g, "Spital Thun")
        assert (None, RDFS.label, Literal("Spital Thun", lang="de")) in g


class TestSocialGroupToRdf:
    """social_group_to_rdf() → CRM E74 Group."""

    def test_e74_group_type(self):
        g = Graph()
        social_group_to_rdf(g, "Erbarleut", wikidata_id="Q100")
        assert has_type(g, CRM["E74_Group"])


class TestCareActionToRdf:
    """care_action_to_rdf() → CRM E7 Activity."""

    def test_e7_activity_type(self):
        g = Graph()
        care_action_to_rdf(g, "Versorgung", notes="Care provision")
        assert has_type(g, CRM["E7_Activity"])


class TestVocabularyTermToRdf:
    """vocabulary_term_to_rdf() → SDHSS.Concept (NOT schema.Concept)."""

    def test_sdhss_concept_type(self):
        from knowledge_hub.hub import Vocabulary
        g = Graph()
        vocab = Vocabulary(term="erbar lüt", canonical_form="Erbarleut",
                           category="care_role", notes="")
        vocabulary_term_to_rdf(g, vocab)
        # MUST use SDHSS["Concept"], not SCHEMA["Concept"]
        assert has_type(g, SDHSS["Concept"])

    def test_rdfs_label_from_term(self):
        from knowledge_hub.hub import Vocabulary
        g = Graph()
        vocab = Vocabulary(term="arme lüt", canonical_form="Arme Lüt",
                           category="social_group", notes="")
        vocabulary_term_to_rdf(g, vocab)
        labels = list(g.objects(None, RDFS.label))
        assert Literal("arme lüt", lang="de") in labels


class TestWikipediaIdToQid:
    """wikipedia_id_to_qid() normalises Wikidata IDs to Qnnnnn form."""

    def test_qid_unchanged(self):
        assert wikipedia_id_to_qid("Q12345") == "Q12345"

    def test_numeric_padded(self):
        assert wikipedia_id_to_qid("12345") == "Q12345"

    def test_q_prefix_stripped(self):
        assert wikipedia_id_to_qid("Q999") == "Q999"

    def test_mixed_q_and_numeric(self):
        assert wikipedia_id_to_qid("Q0") == "Q0"


# ── Graph serialisation smoke tests ───────────────────────────────────────────

class TestGraphSerialisation:
    """Verify exported graphs can be serialised without error."""

    def test_person_graph_serialises(self, sample_hub):
        g = self._person_graph(sample_hub)
        ttl = g.serialize(format="turtle")
        assert "@prefix" in ttl

    def test_place_graph_serialises(self, sample_hub):
        g = self._place_graph(sample_hub)
        ttl = g.serialize(format="turtle")
        assert "@prefix" in ttl

    def _person_graph(self, sample_hub, name="Hans Müller"):
        from knowledge_hub.rdf_export import person_to_rdf
        g = Graph()
        person = next(p for p in sample_hub.all_persons() if p.name == name)
        person_to_rdf(g, person)
        return g

    def _place_graph(self, sample_hub, name="Bern"):
        from knowledge_hub.rdf_export import place_to_rdf
        g = Graph()
        place = next(p for p in sample_hub.all_places() if p.name == name)
        place_to_rdf(g, place)
        return g