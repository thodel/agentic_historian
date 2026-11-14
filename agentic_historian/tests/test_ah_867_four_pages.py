"""#867 — Agent B saw one page in production: AGENT_B_PAGES=5 against vLLM's four.

tei's vLLM takes at most four images per prompt ("At most 4 image(s) may be
provided in one prompt", 12.11.2026). Agent B sent five since #771, the call was
refused, and the fallback showed the first page alone — in every production run
and in all 90 calls of the third e-codices run. ``pages_seen`` said so; nobody
read it.

Two things change: the default is four pages, and a refusal that names its
limit is answered with that many pages (first and last among them, as the
sample was made) before the fallback to one. A refusal that names no limit, or
a second refusal, still gets page 1, as before.

Offline — the VLM is mocked. Run from the repo root:
    pytest agentic_historian/tests/test_ah_867_four_pages.py
"""

import importlib
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                     # noqa: E402
from agents import source_description as agent_b  # noqa: E402

ANSWER = '{"Schrift": {"wert": "Bastarda"}}\n\n## B\nText.'
REFUSAL = ("Error code: 400 - {'error': {'message': "
           "'At most 4 image(s) may be provided in one prompt.'}}")


@pytest.fixture
def pages(tmp_path):
    out = []
    for i in range(5):
        p = tmp_path / f"page-{i:04d}.jpg"
        p.write_bytes(b"\xff\xd8\xff")
        out.append(str(p))
    return out


@pytest.fixture
def vlm(monkeypatch):
    """Refuses more than ``state['limit']`` images with ``state['message']``."""
    calls, state = [], {"limit": 4, "message": REFUSAL}

    def vision(prompt, image_source=None, system=None, **kw):
        imgs = image_source if isinstance(image_source, list) else [image_source]
        calls.append(list(imgs))
        if len(imgs) > state["limit"]:
            raise RuntimeError(state["message"])
        return ANSWER
    monkeypatch.setattr(agent_b.gs, "chat_vision", vision)
    monkeypatch.setattr(agent_b.gs, "last_finish", lambda: "stop")
    monkeypatch.setattr(agent_b.gs, "last_usage", lambda: {})
    return calls, state


def test_the_default_is_four_pages_because_tei_takes_four(monkeypatch):
    monkeypatch.delenv("AGENT_B_PAGES", raising=False)
    try:
        assert importlib.reload(config).AGENT_B_PAGES == 4
    finally:
        importlib.reload(config)


def test_a_refusal_that_names_its_limit_is_answered_with_that_many_pages(vlm, pages):
    calls, _ = vlm
    raw, sent, cost = agent_b._vision_call("p", pages, "d")
    assert raw == ANSWER
    assert sent == [pages[0], pages[1], pages[3], pages[4]], "first and last among them"
    assert calls == [pages, sent] and cost["seiten"] == 4


def test_a_refusal_without_a_number_still_gets_page_one(vlm, pages):
    calls, state = vlm
    state["message"] = "image input not supported"
    _, sent, _ = agent_b._vision_call("p", pages, "d")
    assert sent == pages[:1] and calls == [pages, pages[:1]]


def test_a_second_refusal_gets_page_one(vlm, pages):
    calls, state = vlm
    state["limit"] = 1                          # says four, takes one
    _, sent, _ = agent_b._vision_call("p", pages, "d")
    assert sent == pages[:1]
    assert [len(c) for c in calls] == [5, 4, 1]


def test_the_limit_is_read_from_the_backends_words():
    assert agent_b._image_limit(RuntimeError(REFUSAL)) == 4
    assert agent_b._image_limit(RuntimeError("at most 2 images per request")) == 2
    assert agent_b._image_limit(RuntimeError("too many images")) is None
