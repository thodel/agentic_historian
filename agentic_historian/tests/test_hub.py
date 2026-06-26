"""
tests/test_hub.py

Tests for knowledge_hub round-trip.
Each test gets a fresh hub to avoid state pollution.
"""

import pytest
from knowledge_hub import hub, Person, Place, Vocabulary


@pytest.fixture
def fresh_hub():
    """Provide a clean hub instance for each test."""
    h = hub.KnowledgeHub()
    return h


def test_person_roundtrip(fresh_hub):
    """Add a person, then find them by name."""
    p = Person(
        name="Hans Müller",
        wikidata_id="Q12345",
        gnd_id="gnd:123456789",
        notes="Schreiber, 1480–1542",
    )
    fresh_hub.add_person(p)
    found = fresh_hub.find_person("hans müller")
    assert found is not None
    assert found.name == "Hans Müller"
    assert found.wikidata_id == "Q12345"


def test_person_not_found(fresh_hub):
    """Unknown person returns None."""
    assert fresh_hub.find_person("Niemand") is None


def test_place_roundtrip(fresh_hub):
    """Add a place, then find it by name."""
    pl = Place(
        name="Bern",
        wikidata_id="Q70174",
        coordinates=(46.9480, 7.4474),
        notes="Hauptstadt der Republik Bern",
    )
    fresh_hub.add_place(pl)
    found = fresh_hub.find_place("Bern")
    assert found is not None
    assert found.name == "Bern"
    assert found.coordinates == (46.9480, 7.4474)


def test_place_not_found(fresh_hub):
    """Unknown place returns None."""
    assert fresh_hub.find_place("Zürich") is None


def test_vocabulary_care_role(fresh_hub):
    """Vocabulary entries are stored and retrievable."""
    v = Vocabulary(
        term="erbar lüt",
        canonical_form="Erbarleut",
        category="care_role",
        notes="Care actors in early modern sources",
    )
    fresh_hub.add_vocabulary(v)
    # Vocabulary list is accessible
    assert any(x.term == "erbar lüt" for x in fresh_hub.vocabulary)


def test_get_hub_returns_singleton():
    """get_hub() returns the module-level singleton."""
    h = hub.get_hub()
    assert isinstance(h, hub.KnowledgeHub)
    h2 = hub.get_hub()
    assert h is h2  # same instance