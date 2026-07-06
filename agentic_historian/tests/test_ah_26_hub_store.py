"""#26: the Knowledge-Hub store is a swappable interface (HubStore + get_store).

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_26_hub_store.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config  # noqa: E402
from knowledge_hub import hub  # noqa: E402
from knowledge_hub.store import HubStore  # noqa: E402


def test_json_backend_satisfies_the_protocol():
    store = hub.get_store()
    assert isinstance(store, HubStore)          # runtime_checkable Protocol


def test_default_backend_is_json_singleton():
    assert config.KH_BACKEND == "json"
    assert hub.get_store() is hub._hub
    assert hub.get_hub() is hub.get_store()     # historical name routes through


def test_unknown_backend_raises(monkeypatch):
    monkeypatch.setattr(config, "KH_BACKEND", "qlever")
    try:
        hub.get_store()
        assert False, "expected ValueError for an unimplemented backend"
    except ValueError as e:
        assert "qlever" in str(e)


def test_module_helpers_route_through_the_store(monkeypatch):
    """Swapping the backend swaps what hub.* returns — without touching agents."""
    class FakeStore:
        def find_person(self, name): return {"id": "fake", "name": name}
    monkeypatch.setattr(hub, "get_store", lambda: FakeStore())
    assert hub.find_person("Johann")["id"] == "fake"


def test_all_protocol_methods_exist_on_json_store():
    store = hub.get_store()
    for m in ("search_person", "search_place", "find_person", "find_place",
              "match_vocabulary", "get_persons", "get_places", "all_persons",
              "all_places", "all_vocabulary", "add_person", "add_place",
              "add_keyword", "add_document_type", "summary"):
        assert callable(getattr(store, m)), m
