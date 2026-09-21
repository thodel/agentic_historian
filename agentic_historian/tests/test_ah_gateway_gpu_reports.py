"""`gateway_models` reports both machines' cards, named for whose they are.

`/train/gpu` was once the only GPU route, and the tool asked it alone. Since
serving-atr-inference#139 `/gpu` answers for the **serving** box's cards while
`/train/gpu` answers for the trainer's — and since 16.09.2026 those are two
machines: idhefix serves, asteraix trains.

Reporting only the trainer's was worse than reporting none. On 2026-09-21 a
batch died with `GPU 1 has 9742 MB free, qwen3.5-4b-german-xix-v2 needs
15848 MB` while this tool showed two idle A40s — the trainer's, idle and
irrelevant — so the one card that mattered could not be seen from here at all.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                   # noqa: E402
from mcp_atr import server as mcp_server        # noqa: E402


class _Resp:
    def __init__(self, status_code, payload, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class FakeClient:
    """A gateway whose two GPU routes answer for two different machines."""

    ROUTES = {
        "/health": {"status": "ok", "model_count": 56},
        "/models": {"models": [{"id": "qwen3.5-4b-german-xix-v2"}]},
        "/gpu": {"host": "idhefix",
                 "cards": [{"index": 1, "memory_free_mib": 9742}]},
        "/train/gpu": {"host": "dhserver03",
                       "cards": [{"index": 0, "memory_free_mib": 45489},
                                 {"index": 1, "memory_free_mib": 45489}]},
    }

    def __init__(self, fail: set[str] = frozenset()):
        self.asked: list[str] = []
        self.fail = fail

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url):
        path = url.replace(config.ATR_GATEWAY_URL, "")
        self.asked.append(path)
        if path in self.fail:
            raise ConnectionError("no route to host")
        return _Resp(200, self.ROUTES[path])


@pytest.fixture
def probe(monkeypatch):
    """Run the probe with a stubbed httpx, returning (result, fake client)."""
    import httpx

    def _run(fail: set[str] = frozenset()):
        fake = FakeClient(fail)
        monkeypatch.setattr(httpx, "Client", lambda *a, **kw: fake)
        return mcp_server.gateway_probe(), fake

    return _run


def test_both_routes_are_asked(probe):
    result, fake = probe()

    assert "/gpu" in fake.asked and "/train/gpu" in fake.asked


def test_the_serving_cards_are_the_ones_a_batch_runs_on(probe):
    result, _ = probe()

    assert result["gpu_serving"]["host"] == "idhefix"
    assert result["gpu_serving"]["cards"][0]["memory_free_mib"] == 9742


def test_the_training_cards_are_reported_separately(probe):
    result, _ = probe()

    assert result["gpu_training"]["host"] == "dhserver03"


def test_the_ambiguous_key_is_gone(probe):
    """`gpu` meant the trainer's cards. A key that silently changes meaning is
    how this was missed, so it is removed rather than redefined."""
    result, _ = probe()

    assert "gpu" not in result


def test_one_unreachable_route_does_not_lose_the_others(probe):
    result, _ = probe(fail={"/gpu"})

    assert "error" in result["gpu_serving"]
    assert result["gpu_training"]["host"] == "dhserver03"
    assert result["health"]["status"] == "ok"
