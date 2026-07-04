"""Knowledge Hub — persistent store for persons, places, vocabulary, document types.

Fixes #14: previously this was an in-memory dataclass store that lost data on
restart and exposed no `search_person()` / `search_place()` (Agent C crashed with
AttributeError). It is now JSON-backed (`config.KH_DIR/hub.json`), seeded with
domain defaults on first run, and returns plain dicts that Agent C can enrich.

Stable interface (Agents B/C depend on it). A future RDF / SDHss / QLEVER backend
(proposal WP4 — see AH-41/AH-42) can implement the same methods without touching
the agents:
    search_person(name) -> list[dict]      find_person(name) -> Optional[dict]
    search_place(name)  -> list[dict]      find_place(name)  -> Optional[dict]
    add_person(dict) / add_place(dict) / add_keyword(str) / add_document_type(str)
    get_controlled_vocabulary() -> list[str]
    get_document_types() -> list[str]

Person/place dicts use the keys Agent C reads: id, name, variants, wikidata, gnd,
hls, role, region, coordinates, notes.
"""

import copy
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests
from loguru import logger

import config


# ── Dataclass adapters for RDF export compatibility (origin/master compat) ───
@dataclass
class Person:
    """Dataclass adapter for RDF export. Wraps a person dict."""
    name: str
    wikidata_id: Optional[str] = None
    gnd_id: Optional[str] = None
    notes: str = ""


@dataclass
class Place:
    """Dataclass adapter for RDF export. Wraps a place dict."""
    name: str
    wikidata_id: Optional[str] = None
    gnd_id: Optional[str] = None
    coordinates: Optional[tuple[float, float]] = None
    notes: str = ""


@dataclass
class Vocabulary:
    """Dataclass adapter for vocabulary terms (RDF export)."""
    term: str
    canonical_form: str = ""
    category: str = ""
    notes: str = ""

HUB_PATH = config.KH_DIR / "hub.json"

# Domain defaults (Swiss/German administrative sources, 14th–16th c.). Historians
# extend these via the Discord /hub commands or by editing hub.json directly.
DEFAULTS: dict = {
    "document_types": [
        "Missive", "Ratsprotokoll", "Urteilsbrief", "Bürgschaftsbrief",
        "Schuldbrief", "Kaufbrief", "Steuerregister", "Verhörprotokoll",
        "Mandate", "Rechnung", "Inventar", "Testament", "Pfandbrief",
        "Instruktion", "Supplikation", "Satzung / Ordnung", "Urbar", "Rodel",
    ],
    "controlled_vocabulary": [
        # Taxonomien des Sozialen
        "arme lüt", "erbar lüt", "Bürger", "Burger", "Hintersässe",
        "Juden", "Zigeuner", "Vaganten", "Söldner", "Dienstbot",
        "Knecht", "Magd", "Junckfrow", "Witwe", "Waise",
        "gesellen", "meister", "lehrling",
        # Praxis und Preis der Care
        "versorgung", "pflege", "dienst", "erziehung", "hut",
        "spital", "almosen", "fürsorge", "lohn", "pfand",
        # Verwaltung
        "vogt", "schultheiss", "rat", "amtmann", "richter",
        "steuer", "zins", "schuld", "erbe", "gut",
        # Konflikt / soziale Ordnung
        "friede", "fehde", "klage", "urteil", "strafe", "buss",
        "ehre", "unehrlich", "scham", "treue",
    ],
    # Seed examples demonstrating the schema — replace with curated data.
    # External IDs left blank on purpose (entity linking is AH-32/AH-33).
    "persons": [
        {
            "id": "hub_p_example", "name": "Heinrich von Wiler",
            "variants": ["Hainricus de Villa", "H. Wiler"],
            "role": "Vogt", "active_period": "1430–1450", "location": "Thun",
            "wikidata": "", "gnd": "", "hls": "", "notes": "example — replace",
        },
    ],
    "places": [
        {
            "id": "hub_loc_example", "name": "Thun",
            "variants": ["Tun", "Thunum"], "modern_name": "Thun",
            "region": "Bern", "wikidata": "", "gnd": "", "hls": "",
            "coordinates": None, "notes": "example — replace",
        },
    ],
    "organisations": [],
}


def _matches(query: str, entry: dict) -> bool:
    """Case-insensitive substring match against name + variants."""
    q = (query or "").strip().lower()
    if not q:
        return False
    names = [entry.get("name", "")] + list(entry.get("variants", []))
    return any(q in (n or "").lower() or (n or "").lower() in q for n in names if n)


class KnowledgeHub:
    """JSON-persistent knowledge base for the Agentic Historian."""

    def __init__(self, path: Path = HUB_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = self._load()

    # ── persistence ─────────────────────────────────────────────────────────
    def _load(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                # ensure all top-level keys exist
                for k, v in DEFAULTS.items():
                    data.setdefault(k, copy.deepcopy(v) if not isinstance(v, list) else [])
                return data
            except (json.JSONDecodeError, OSError):
                pass
        data = copy.deepcopy(DEFAULTS)
        self._write(data)
        return data

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _save(self) -> None:
        with self._lock:
            self._write(self._data)

    # ── persons ─────────────────────────────────────────────────────────────
    def get_persons(self) -> list[dict]:
        return self._data["persons"]

    def search_person(self, name: str) -> list[dict]:
        return [p for p in self._data["persons"] if _matches(name, p)]

    def find_person(self, name: str) -> Optional[dict]:
        matches = self.search_person(name)
        return matches[0] if matches else None

    def add_person(self, person: dict) -> None:
        persons = self._data["persons"]
        for i, p in enumerate(persons):
            if p.get("id") and p.get("id") == person.get("id"):
                persons[i] = person
                self._save()
                return
        persons.append(person)
        self._save()

    def all_persons(self) -> list[Person]:
        """Return all persons as dataclass instances (for RDF export)."""
        return [
            Person(
                name=p.get("name", ""),
                wikidata_id=p.get("wikidata"),
                gnd_id=p.get("gnd"),
                notes=p.get("notes", ""),
            )
            for p in self._data["persons"]
        ]

    def all_places(self) -> list[Place]:
        """Return all places as dataclass instances (for RDF export)."""
        result = []
        for p in self._data["places"]:
            coords = p.get("coordinates")
            if isinstance(coords, (list, tuple)) and len(coords) == 2:
                coords = tuple(float(c) for c in coords)
            elif coords is None:
                coords = None
            else:
                coords = None
            result.append(Place(
                name=p.get("name", ""),
                wikidata_id=p.get("wikidata"),
                gnd_id=p.get("gnd"),
                coordinates=coords,
                notes=p.get("notes", ""),
            ))
        return result

    def all_vocabulary(self) -> list[Vocabulary]:
        """Return controlled-vocabulary terms as Vocabulary dataclasses."""
        return [
            Vocabulary(term=t, canonical_form=t, category="")
            for t in self._data["controlled_vocabulary"]
        ]

    # ── places ──────────────────────────────────────────────────────────────
    def get_places(self) -> list[dict]:
        return self._data["places"]

    def search_place(self, name: str) -> list[dict]:
        return [p for p in self._data["places"] if _matches(name, p)]

    def find_place(self, name: str) -> Optional[dict]:
        matches = self.search_place(name)
        return matches[0] if matches else None

    def add_place(self, place: dict) -> None:
        places = self._data["places"]
        for i, p in enumerate(places):
            if p.get("id") and p.get("id") == place.get("id"):
                places[i] = place
                self._save()
                return
        places.append(place)
        self._save()

    # ── vocabulary / document types ──────────────────────────────────────────
    def get_controlled_vocabulary(self) -> list[str]:
        return self._data["controlled_vocabulary"]

    def add_keyword(self, term: str) -> None:
        if term and term not in self._data["controlled_vocabulary"]:
            self._data["controlled_vocabulary"].append(term)
            self._save()

    def match_vocabulary(self, term: str) -> Optional[str]:
        """Return the controlled-vocabulary entry matching `term` (the taxonomy
        link for SOCIAL_GROUP / CARE_ACTION / CARE_ACTOR / ROLE), else None.

        Matches case-insensitively: exact, or a vocab term of length >= 4 that is
        a substring of the mention (or vice versa). The length floor avoids tiny
        tokens (rat, gut, hut) producing false positives.
        """
        t = (term or "").strip().lower()
        if not t:
            return None
        vocab = self._data["controlled_vocabulary"]
        for v in vocab:
            if t == v.lower():
                return v
        for v in vocab:
            vl = v.lower()
            if len(vl) >= 4 and (vl in t or t in vl):
                return v
        return None

    def get_document_types(self) -> list[str]:
        return self._data["document_types"]

    def add_document_type(self, dtype: str) -> None:
        if dtype and dtype not in self._data["document_types"]:
            self._data["document_types"].append(dtype)
            self._save()

    # ── summary ──────────────────────────────────────────────────────────────
    def summary(self) -> str:
        return (
            "📚 **Knowledge Hub**\n"
            f"• Document types: {len(self._data['document_types'])}\n"
            f"• Controlled vocabulary: {len(self._data['controlled_vocabulary'])} terms\n"
            f"• Persons: {len(self._data['persons'])}\n"
            f"• Places: {len(self._data['places'])}\n"
            f"• Organisations: {len(self._data['organisations'])}"
        )


# Singleton + module-level convenience API (Agent C calls hub.search_person(...)).
_hub = KnowledgeHub()


def get_hub() -> KnowledgeHub:
    return _hub


def search_person(name: str) -> list[dict]:
    return _hub.search_person(name)


def search_place(name: str) -> list[dict]:
    return _hub.search_place(name)


def find_person(name: str) -> Optional[dict]:
    return _hub.find_person(name)


def find_place(name: str) -> Optional[dict]:
    return _hub.find_place(name)


def match_vocabulary(term: str) -> Optional[str]:
    return _hub.match_vocabulary(term)


def add_person(person: dict) -> None:
    _hub.add_person(person)


def add_place(place: dict) -> None:
    _hub.add_place(place)


def all_persons() -> list[Person]:
    return _hub.all_persons()


def all_places() -> list[Place]:
    return _hub.all_places()


def all_vocabulary() -> list[Vocabulary]:
    return _hub.all_vocabulary()


def add_keyword(term: str) -> None:
    _hub.add_keyword(term)


def add_document_type(dtype: str) -> None:
    _hub.add_document_type(dtype)


def summary() -> str:
    return _hub.summary()


# ── HLS-DHS lookup against a local data dump (offline; no web calls) ─────────
#
# The live hits4.php endpoint is dead, so HLS linking uses an internal dump at
# config.HLS_DATA_PATH (default knowledge_hub/data/hls.json). Accepted formats:
#
#   1. a flat list:   [{"hls_id": "012345", "name": "Müller, Hans",
#                       "variants": ["Hans Müller"], "type": "person"|"place"}, ...]
#   2. split dict:    {"persons": [ {hls_id,name,variants}, ... ],
#                      "places":  [ {hls_id,name,variants}, ... ]}
#
# Set ENABLE_HLS_LOOKUP=true once the file is in place.

_hls_index: Optional[list[dict]] = None


def _load_hls() -> list[dict]:
    """Lazy-load + cache the HLS dump as a flat list of typed entries."""
    global _hls_index
    if _hls_index is not None:
        return _hls_index
    _hls_index = []
    path = config.HLS_DATA_PATH
    if not Path(path).exists():
        logger.warning(f"[Hub] HLS data file not found: {path} (HLS linking disabled)")
        return _hls_index
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            entries = (
                [{**e, "type": "person"} for e in data.get("persons", [])]
                + [{**e, "type": "place"} for e in data.get("places", [])]
            )
        else:
            entries = list(data)
        _hls_index = [e for e in entries if e.get("hls_id") or e.get("id")]
        logger.info(f"[Hub] Loaded {len(_hls_index)} HLS entries from {path}")
    except Exception as e:
        logger.warning(f"[Hub] Failed to load HLS data {path}: {e}")
    return _hls_index


def hls_search_person(name: str) -> list[dict]:
    return _hls_search(name, kind="personen")


def hls_search_place(name: str) -> list[dict]:
    return _hls_search(name, kind="ortschaften")


def _hls_search(name: str, kind: str = "personen") -> list[dict]:
    """Match `name` against the local HLS dump. Returns list of {hls_id, name}."""
    if not config.ENABLE_HLS_LOOKUP:
        return []
    want = "place" if kind == "ortschaften" else "person"
    q = (name or "").strip().lower()
    if not q:
        return []
    out = []
    for e in _load_hls():
        etype = e.get("type")
        if etype and etype != want:
            continue
        names = [e.get("name", "")] + list(e.get("variants", []))
        for n in names:
            nl = (n or "").lower()
            if nl and (q == nl or (len(nl) >= 4 and (nl in q or q in nl))):
                out.append({"hls_id": e.get("hls_id") or e.get("id"), "name": e.get("name", "")})
                break
        if len(out) >= 5:
            break
    return out
