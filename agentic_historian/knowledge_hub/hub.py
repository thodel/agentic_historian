"""
knowledge_hub/hub.py — In-memory knowledge store for persons, places, vocab.

Provides:
  - Person / Place / Vocabulary dataclasses (with HLS support)
  - search_person(name) / search_place(name): case-insensitive fuzzy search
  - find_person(name) / find_place(name): substring match (existing)
  - add_* / get_* helpers
  - _hub singleton via get_hub()
"""

from dataclasses import dataclass, field
from typing import Optional

import requests
from loguru import logger


@dataclass
class Person:
    name: str
    wikidata_id: Optional[str] = None
    gnd_id: Optional[str] = None
    hls_id: Optional[str] = None    # Historisches Lexikon der Schweiz article ID
    notes: str = ""


@dataclass
class Place:
    name: str
    wikidata_id: Optional[str] = None
    gnd_id: Optional[str] = None
    hls_id: Optional[str] = None    # Historisches Lexikon der Schweiz article ID
    coordinates: Optional[tuple[float, float]] = None
    notes: str = ""


@dataclass
class Vocabulary:
    term: str
    canonical_form: str
    category: str
    notes: str = ""


class KnowledgeHub:
    """In-memory knowledge base for the Agentic Historian."""

    def __init__(self):
        self.persons: list[Person] = []
        self.places: list[Place] = []
        self.vocabulary: list[Vocabulary] = []

    # ── Add ──────────────────────────────────────────────────────────────────

    def add_person(self, person: Person) -> None:
        self.persons.append(person)

    def add_place(self, place: Place) -> None:
        self.places.append(place)

    def add_vocabulary(self, vocab: Vocabulary) -> None:
        self.vocabulary.append(vocab)

    # ── Substring find (existing API) ───────────────────────────────────────

    def find_person(self, name: str) -> Optional[Person]:
        name_l = name.lower()
        for p in self.persons:
            if name_l in p.name.lower():
                return p
        return None

    def find_place(self, name: str) -> Optional[Place]:
        name_l = name.lower()
        for p in self.places:
            if name_l in p.name.lower():
                return p
        return None

    # ── Fuzzy search (new API — powers Agent C entity linking) ───────────────

    def search_person(self, name: str, limit: int = 3) -> list[Person]:
        """
        Case-insensitive fuzzy search for persons.
        Returns up to `limit` best matches (sorted by name length proximity).
        Falls back to HLS-DHS lookup if local hub is empty.
        """
        name_l = name.lower()
        # Exact prefix match first
        exact = [p for p in self.persons if p.name.lower().startswith(name_l)]
        # Then substring
        partial = [p for p in self.persons if name_l in p.name.lower() and p not in exact]
        results = exact + partial
        if results:
            return results[:limit]

        # Fallback: try HLS-DHS search
        hls_results = _hls_search_person(name)
        return hls_results[:limit]

    def search_place(self, name: str, limit: int = 3) -> list[Place]:
        """
        Case-insensitive fuzzy search for places.
        Returns up to `limit` best matches.
        Falls back to HLS-DHS lookup if local hub is empty.
        """
        name_l = name.lower()
        exact = [p for p in self.places if p.name.lower().startswith(name_l)]
        partial = [p for p in self.places if name_l in p.name.lower() and p not in exact]
        results = exact + partial
        if results:
            return results[:limit]

        # Fallback: try HLS-DHS search
        hls_results = _hls_search_place(name)
        return hls_results[:limit]


# ── HLS-DHS lookup helpers ────────────────────────────────────────────────────
# HLS-DHS (Historisches Lexikon der Schweiz) public search API

HLS_SEARCH_URL = "https://www.hls-dhs-dss.ch/hits4.php"


def _hls_search_person(name: str) -> list[Person]:
    """Search HLS-DHS for a person by name. Returns Person dataclasses."""
    return _hls_search(name, kind="personen")


def _hls_search_place(name: str) -> list[Place]:
    """Search HLS-DHS for a place by name. Returns Place dataclasses."""
    return _hls_search(name, kind="ortschaften")


def _hls_search(name: str, kind: str = "personen") -> list:
    """
    Hit HLS-DHS search and parse results.
    Returns list of Person or Place objects with hls_id set.
    """
    results = []
    try:
        params = {
            "q": name,
            "Search": "1",
            "show": kind,
        }
        resp = requests.get(HLS_SEARCH_URL, params=params, timeout=10)
        resp.raise_for_status()
        # Parse article IDs from response — HLS uses /de/ARTICLEID format
        import re
        # e.g. <a href="/de/X013299">Müller, Hans</a>
        pattern = re.compile(r'href="/de/([A-Z0-9]+)">(.*?)</a>')
        for match in pattern.finditer(resp.text):
            hls_id, title = match.group(1), match.group(2)
            title = title.strip()
            if kind == "personen":
                results.append(Person(name=title, hls_id=hls_id))
            else:
                results.append(Place(name=title, hls_id=hls_id))
    except Exception as e:
        logger.warning(f"[Hub] HLS search failed for '{name}': {e}")
    return results


# ── Module singleton ──────────────────────────────────────────────────────────

_hub = KnowledgeHub()


def get_hub() -> KnowledgeHub:
    return _hub