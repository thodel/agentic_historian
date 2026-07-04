"""
tests/test_hub.py

Tests for knowledge_hub round-trip with the dict-based API (current main).
Each test gets a fresh hub backed by a temp file.
"""

import pytest
from knowledge_hub import hub, Person, Place, Vocabulary


@pytest.fixture
def fresh_hub(tmp_path, monkeypatch):
    """Provide a clean hub instance backed by a temp file for each test."""
    import knowledge_hub.hub as hub_module
    fake_path = tmp_path / "hub.json"

    class FreshHub(hub_module.KnowledgeHub):
        def __init__(self, path):
            self.path = path
            import threading
            self._lock = threading.Lock()
            self._data = self._load()

    return FreshHub(path=fake_path)


# ── Person round-trip ──────────────────────────────────────────────────────────

class TestPersonRoundtrip:
    """Person add + find round-trip using the dict-based API."""

    def test_person_roundtrip(self, fresh_hub):
        """add_person(dict) → find_person(name) returns the person dict."""
        fresh_hub.add_person({
            "id": "p1",
            "name": "Hans Müller",
            "wikidata": "Q12345",
            "gnd": "gnd:123456789",
            "notes": "Schreiber, 1480–1542",
        })
        found = fresh_hub.find_person("hans müller")
        assert found is not None
        assert found["name"] == "Hans Müller"
        assert found["wikidata"] == "Q12345"
        assert found["gnd"] == "gnd:123456789"

    def test_person_not_found(self, fresh_hub):
        """Unknown person returns None."""
        assert fresh_hub.find_person("Niemand") is None

    def test_person_update_idempotent(self, fresh_hub):
        """Re-adding same id replaces the existing entry."""
        fresh_hub.add_person({
            "id": "p1",
            "name": "Hans Müller",
            "wikidata": "Q12345",
            "gnd": "",
            "notes": "",
        })
        fresh_hub.add_person({
            "id": "p1",
            "name": "Hans Müller (aktualisiert)",
            "wikidata": "Q99999",
            "gnd": "",
            "notes": "",
        })
        found = fresh_hub.find_person("Hans Müller")
        assert found["wikidata"] == "Q99999"
        assert found["name"] == "Hans Müller (aktualisiert)"

    def test_search_person_finds_variant(self, fresh_hub):
        """search_person finds by name variant."""
        fresh_hub.add_person({
            "id": "p1",
            "name": "Hans Müller",
            "variants": ["Johannes Mueller", "Hans Müler"],
            "wikidata": "Q1",
            "gnd": "",
            "notes": "",
        })
        results = fresh_hub.search_person("Johannes Mueller")
        assert any(r["name"] == "Hans Müller" for r in results)


# ── Place round-trip ───────────────────────────────────────────────────────────

class TestPlaceRoundtrip:
    """Place add + find round-trip using the dict-based API."""

    def test_place_roundtrip(self, fresh_hub):
        """add_place(dict) → find_place(name) returns the place dict."""
        fresh_hub.add_place({
            "id": "loc1",
            "name": "Bern",
            "wikidata": "Q70174",
            "coordinates": [46.9480, 7.4474],
            "notes": "Hauptstadt der Republik Bern",
        })
        found = fresh_hub.find_place("Bern")
        assert found is not None
        assert found["name"] == "Bern"
        assert found["coordinates"] == [46.9480, 7.4474]

    def test_place_not_found(self, fresh_hub):
        """Unknown place returns None."""
        assert fresh_hub.find_place("Zürich") is None


# ── Vocabulary ─────────────────────────────────────────────────────────────────

class TestVocabulary:
    """Controlled vocabulary via add_keyword / get_controlled_vocabulary."""

    def test_add_keyword_appears_in_vocabulary(self, fresh_hub):
        """add_keyword() makes the term retrievable via get_controlled_vocabulary()."""
        # Use a unique term so it definitely wasn't pre-seeded
        unique_term = "test_keyword_unique_123xyz"
        fresh_hub.add_keyword(unique_term)
        assert unique_term in fresh_hub.get_controlled_vocabulary()

    def test_add_keyword_idempotent(self, fresh_hub):
        """Adding the same keyword twice doesn't duplicate it."""
        unique_term = "test_idempotent_unique"
        fresh_hub.add_keyword(unique_term)
        fresh_hub.add_keyword(unique_term)
        count = fresh_hub.get_controlled_vocabulary().count(unique_term)
        assert count == 1

    def test_all_vocabulary_returns_dataclass(self, fresh_hub):
        """all_vocabulary() returns a list of Vocabulary dataclass instances."""
        fresh_hub.add_keyword("test_vocab_adapter")
        vocabs = fresh_hub.all_vocabulary()
        assert all(isinstance(v, Vocabulary) for v in vocabs)

    def test_vocabulary_dataclass_attributes(self, fresh_hub):
        """Vocabulary dataclass exposes term, canonical_form, category, notes."""
        fresh_hub.add_keyword("Erbar lüt")
        vocabs = fresh_hub.all_vocabulary()
        erbar = next((v for v in vocabs if v.term == "Erbar lüt"), None)
        assert erbar is not None
        assert hasattr(erbar, "term")
        assert hasattr(erbar, "canonical_form")
        assert hasattr(erbar, "category")
        assert hasattr(erbar, "notes")

    def test_match_vocabulary_exact(self, fresh_hub):
        """match_vocabulary returns stored form on exact match (stored as-is)."""
        fresh_hub.add_keyword("Erbar lüt")
        result = fresh_hub.match_vocabulary("Erbar lüt")
        # match_vocabulary returns the internally-stored canonical form (lowercase here)
        assert result == "erbar lüt"

    def test_match_vocabulary_case_insensitive(self, fresh_hub):
        """match_vocabulary is case-insensitive."""
        fresh_hub.add_keyword("Bürger")
        result = fresh_hub.match_vocabulary("bürger")
        assert result == "Bürger"

    def test_match_vocabulary_partial_match(self, fresh_hub):
        """match_vocabulary returns canonical form for partial match (≥4 chars)."""
        fresh_hub.add_keyword("arme lüt")
        result = fresh_hub.match_vocabulary("arme lüte")  # "arme lüt" is substring
        assert result == "arme lüt"


# ── Hub singleton ──────────────────────────────────────────────────────────────

class TestHubSingleton:
    """Hub singleton + module-level API."""

    def test_get_hub_returns_singleton(self):
        """get_hub() returns the same instance on repeated calls."""
        h1 = hub.get_hub()
        h2 = hub.get_hub()
        assert h1 is h2

    def test_module_level_find_person(self, fresh_hub, monkeypatch):
        """Module-level find_person() delegates to the hub singleton."""
        import knowledge_hub.hub as hub_module
        monkeypatch.setattr(hub_module, "_hub", fresh_hub)
        fresh_hub.add_person({
            "id": "p_mod",
            "name": "Peter von Nidau",
            "wikidata": "",
            "gnd": "",
            "notes": "",
        })
        found = hub_module.find_person("Peter von Nidau")
        assert found is not None
        assert found["name"] == "Peter von Nidau"

    def test_module_level_search_person(self, fresh_hub, monkeypatch):
        """Module-level search_person() delegates to the hub singleton."""
        import knowledge_hub.hub as hub_module
        monkeypatch.setattr(hub_module, "_hub", fresh_hub)
        fresh_hub.add_person({
            "id": "p_search",
            "name": "Kaspar Bitster",
            "wikidata": "",
            "gnd": "",
            "notes": "",
        })
        results = hub_module.search_person("Kaspar")
        assert any(r["name"] == "Kaspar Bitster" for r in results)


# ── Dataclass adapters (for RDF export compatibility) ─────────────────────────

class TestDataclassAdapters:
    """Person / Place / Vocabulary dataclass adapters used by rdf_export.py."""

    def test_person_adapter_fields(self):
        """Person dataclass exposes name, wikidata_id, gnd_id, notes."""
        p = Person(name="Anna Bärsin", wikidata_id="Q987",
                   gnd_id="gnd:654321", notes="Test")
        assert p.name == "Anna Bärsin"
        assert p.wikidata_id == "Q987"
        assert p.gnd_id == "gnd:654321"
        assert p.notes == "Test"

    def test_person_adapter_empty_wikidata(self):
        """Person with empty wikidata_id still has a name."""
        p = Person(name="Nobody", wikidata_id="", gnd_id="", notes="")
        assert p.name == "Nobody"

    def test_place_adapter_fields(self):
        """Place dataclass exposes name, wikidata_id, coordinates, notes."""
        pl = Place(name="Zürich", wikidata_id="Q72",
                   coordinates=(47.3769, 8.5417), notes="Test")
        assert pl.name == "Zürich"
        assert pl.coordinates == (47.3769, 8.5417)

    def test_place_adapter_none_coordinates(self):
        """Place without coordinates has None for coordinates."""
        pl = Place(name="Unknown", wikidata_id="", coordinates=None, notes="")
        assert pl.coordinates is None

    def test_vocabulary_adapter_fields(self):
        """Vocabulary dataclass exposes term, canonical_form, category, notes."""
        v = Vocabulary(term="arme lüt", canonical_form="Arme Lüt",
                       category="social_group", notes="")
        assert v.term == "arme lüt"
        assert v.canonical_form == "Arme Lüt"
        assert v.category == "social_group"