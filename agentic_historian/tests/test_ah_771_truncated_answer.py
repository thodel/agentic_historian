"""#771/#774 — a multi-page answer the token budget cut off is asked for again.

The e-codices bench with four pages per call (12.11.2026): 4 of 5 answers on
saa-0428 broke off inside the 16-element JSON at 3500 tokens, and the bench
scored them as "no answer". The client's own retry covers only an EMPTY answer
(a reasoning model that spent the budget before writing). These pin:

* an answer with ``finish_reason == "length"`` whose JSON does not parse is
  asked for once more, with twice the budget, on the same pages;
* a complete answer is not asked again, nor a broken one the budget did not
  cut (a second call would not help);
* the client records the finish reason beside the usage.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                     # noqa: E402
from agents import source_description as agent_b  # noqa: E402
from utils import gpustack_client as gs           # noqa: E402

FULL = ('{"Schrift": {"wert": "Bastarda"}, "Datierung": {"wert": "15. Jh."}}'
        "\n\n## Beschreibung\nText.")
CUT = '{"Schrift": {"wert": "Bastarda"}, "Datierung": {"wert": "15. Jh.", "notiz": "Bild 4 zei'


@pytest.fixture
def vlm(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DESCRIPTIONS_DIR", tmp_path)
    monkeypatch.setattr(config, "AGENT_B_MAX_TOKENS", 3500)
    calls, answers = [], []

    def vision(prompt, image_source=None, system=None, **kw):
        calls.append({"images": image_source, **kw})
        text, finish = answers.pop(0)
        gs._usage.finish = finish
        return text
    monkeypatch.setattr(agent_b.gs, "chat_vision", vision)
    pages = []
    for i in range(3):
        p = tmp_path / f"p{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff" + bytes([i]))
        pages.append(str(p))
    return calls, answers, pages


def test_an_answer_cut_off_inside_the_json_is_asked_for_again_with_twice_the_budget(vlm):
    calls, answers, pages = vlm
    answers += [(CUT, "length"), (FULL, "stop")]
    result = agent_b._describe_from_image("saa-0428-r1", pages, None, 10)
    assert [c["max_tokens"] for c in calls] == [3500, 7000]
    assert calls[1]["images"] == pages, "the same pages, not the first alone"
    assert result["source_json"]["Schrift"]["wert"] == "Bastarda"
    assert result["pages_seen"] == [Path(p).name for p in pages]


def test_a_complete_answer_is_not_asked_again(vlm):
    """The budget cut the markdown after a complete JSON: nothing to ask for."""
    calls, answers, pages = vlm
    answers += [(FULL, "length")]
    result = agent_b._describe_from_image("saa-0428-r0", pages, None, 10)
    assert len(calls) == 1 and result["source_json"]["Schrift"]["wert"] == "Bastarda"


def test_a_broken_answer_the_budget_did_not_cut_is_not_asked_again(vlm):
    """finish_reason "stop" with unparsable JSON: a bigger budget would not help."""
    calls, answers, pages = vlm
    answers += [(CUT, "stop")]
    agent_b._describe_from_image("saa-0428-r2", pages, None, 10)
    assert len(calls) == 1


def test_the_retry_follows_the_single_page_fallback(vlm):
    """The backend refuses several images, the single page's answer is cut off:
    the second budget goes to that single page."""
    calls, answers, pages = vlm
    answers += [(CUT, "length"), (FULL, "stop")]
    original = agent_b.gs.chat_vision

    def refusing(prompt, image_source=None, **kw):
        if isinstance(image_source, list):
            raise RuntimeError("At most 1 image(s) may be provided in one prompt.")
        return original(prompt, image_source=image_source, **kw)
    agent_b.gs.chat_vision = refusing
    result = agent_b._describe_from_image("x", pages, None, 10)
    assert [(c["images"], c["max_tokens"]) for c in calls] == [(pages[0], 3500), (pages[0], 7000)]
    assert result["pages_seen"] == [Path(pages[0]).name]


def test_the_client_records_the_finish_reason(monkeypatch):
    def create(**request):
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="x"), finish_reason="length")], usage=None)
    monkeypatch.setattr(gs, "get_client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    assert gs.chat("p", model="m", max_tokens=2000) == "x"
    assert gs.last_finish() == "length"
