"""Knowledge Hub — central store for persons, places, vocab, and document types."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Person:
    name: str
    wikidata_id: Optional[str] = None
    gnd_id: Optional[str] = None
    notes: str = ""


@dataclass
class Place:
    name: str
    wikidata_id: Optional[str] = None
    gnd_id: Optional[str] = None
    coordinates: Optional[tuple[float, float]] = None
    notes: str = ""


@dataclass
class Vocabulary:
    term: str
    canonical_form: str
    category: str  # e.g. "care_role", "document_type", "institution"
    notes: str = ""


class KnowledgeHub:
    """In-memory knowledge base for the Agentic Historian."""

    def __init__(self):
        self.persons: list[Person] = []
        self.places: list[Place] = []
        self.vocabulary: list[Vocabulary] = []

    def add_person(self, person: Person) -> None:
        self.persons.append(person)

    def add_place(self, place: Place) -> None:
        self.places.append(place)

    def add_vocabulary(self, vocab: Vocabulary) -> None:
        self.vocabulary.append(vocab)

    def find_person(self, name: str) -> Optional[Person]:
        for p in self.persons:
            if name.lower() in p.name.lower():
                return p
        return None

    def find_place(self, name: str) -> Optional[Place]:
        for p in self.places:
            if name.lower() in p.name.lower():
                return p
        return None


_hub = KnowledgeHub()


def get_hub() -> KnowledgeHub:
    return _hub

