"""#301: when the transcription is unreadable, describe from the IMAGE.

The #274 degeneracy detector and the #276 Agent B guard both work correctly — and
together they formed a trap. Measured on tei 2026-07-16:

    [Agent A] Fertig: BAT_664 (source=kraken, QA: 0.10)   → "uuuu uuuu uuuu"
    [Agent B] Transkription degeneriert — keine LLM-Beschreibung   ← guard, correctly
    [model_selector] Best match: kraken-catmus_medieval (score=0.00, no match)  ← starved

bad transcription → no description → no criteria → generic model → bad transcription.

Refusing to describe from "uuuu" is right (that is the hallucination #276 fixed).
Refusing *entirely* is what breaks the run: a VLM can see "Kurrent, 16th c." from
the page without reading a word, and that alone lets #299 pick a real model.

Offline — the VLM is mocked; no GPUStack. Run from the repo root:
    pytest agentic_historian/tests/test_ah_301_image_only_description.py
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                    # noqa: E402
from agents import source_description as agent_b  # noqa: E402

DEGENERATE = "uuuu uuuu uuuu uuuu\nuuuuuuuuuuuu\niuuuuuie uuuu"
GOOD_TEXT = ("Wir Hans von Wiler tuend kund allen die disen brief ansehent "
             "oder hoerent lesen dass wir mit guotem willen")

VLM_JSON = (
    '{"Aufbewahrungsort": {"wert": null}, "Beschreibstoff": {"wert": "Papier"}, '
    '"Schrift": {"wert": "Kurrent"}, "Datierung": {"wert": "16. Jh."}}'
    "\n\nEine Urkunde in Kurrentschrift, 16. Jahrhundert, auf Papier."
)


@pytest.fixture(autouse=True)
def _offline(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DESCRIPTIONS_DIR", tmp_path / "descriptions")
    (tmp_path / "descriptions").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def img(tmp_path):
    p = tmp_path / "page.jpg"
    p.write_bytes(b"\xff\xd8\xff")
    return str(p)


# ── the loop is broken: criteria come from the image ─────────────────────────

def test_degenerate_text_with_an_image_still_yields_criteria(monkeypatch, img):
    monkeypatch.setattr(agent_b.gs, "chat_vision", lambda *a, **k: VLM_JSON)

    result = agent_b.describe(doc_id="d-301", transcription=DEGENERATE, image_path=img)

    assert result["source"] == "image-only"
    assert result["low_confidence"] is True          # honest: not text-backed
    assert result["source_json"].get("Schrift", {}).get("wert") == "Kurrent"


def test_the_degenerate_text_never_reaches_the_model(monkeypatch, img):
    """Describing from "uuuu" is the hallucination #276 exists to prevent. This
    must not reopen it — the model sees the page, never the broken text."""
    seen = {}

    def capture(prompt, image_source=None, system=None, **kw):
        seen["prompt"] = prompt
        seen["image"] = image_source
        return VLM_JSON

    monkeypatch.setattr(agent_b.gs, "chat_vision", capture)
    result = agent_b.describe(doc_id="d-301", transcription=DEGENERATE, image_path=img)

    assert "uuuu" not in seen["prompt"]
    assert seen["image"] == img                      # the page WAS sent
    assert "uuuu" not in result["source_description"]


def test_text_only_chat_is_not_used_for_the_degenerate_path(monkeypatch, img):
    """It must go through the vision call — a text-only call cannot see the page,
    which is the entire point."""
    monkeypatch.setattr(agent_b.gs, "chat_vision", lambda *a, **k: VLM_JSON)
    monkeypatch.setattr(agent_b.gs, "chat_text",
                        lambda *a, **k: pytest.fail("degenerate path used chat_text"))

    agent_b.describe(doc_id="d-301", transcription=DEGENERATE, image_path=img)


def test_the_result_says_the_description_came_from_the_image(monkeypatch, img):
    monkeypatch.setattr(agent_b.gs, "chat_vision", lambda *a, **k: VLM_JSON)
    result = agent_b.describe(doc_id="d-301", transcription=DEGENERATE, image_path=img)

    assert "AUSSCHLIESSLICH aus dem Bild" in result["source_description"]


# ── the criteria are what #299 needs ─────────────────────────────────────────

def test_image_criteria_beat_a_blind_model_pick(monkeypatch, img):
    """The acceptance: score 0.00 "no match" was the starved state. The image-only
    description must give the selector something real to match on.

    Uses the real SourceCriteria + selector (pure) — the claim is about selection.
    """
    from agent_a.model_selector import SourceCriteria
    from agent_a import ensemble

    monkeypatch.setattr(agent_b.gs, "chat_vision", lambda *a, **k: VLM_JSON)
    result = agent_b.describe(doc_id="d-301", transcription=DEGENERATE, image_path=img)

    criteria = SourceCriteria.from_agent_b_and_json(
        result["source_description"], result["source_json"])
    assert criteria.script or criteria.century        # something to match on

    picks = ensemble.plan_models(criteria)
    kraken = next(p for p in picks if p.engine == "kraken")
    blind = next(p for p in ensemble.plan_models(SourceCriteria())
                 if p.engine == "kraken")
    assert kraken.score > blind.score                 # no longer starved


# ── guards: #276 behaviour is preserved where it should be ──────────────────

def test_no_image_still_refuses(monkeypatch):
    """#276 unchanged: without a page there is nothing honest to describe."""
    monkeypatch.setattr(agent_b.gs, "chat_vision",
                        lambda *a, **k: pytest.fail("no image — must not call the VLM"))
    monkeypatch.setattr(agent_b.gs, "chat_text",
                        lambda *a, **k: pytest.fail("no image — must not call the LLM"))

    result = agent_b.describe(doc_id="d-301", transcription=DEGENERATE)

    assert result["low_confidence"] is True
    assert result.get("source") != "image-only"
    assert "unlesbar" in result["source_description"]


def test_a_failing_vision_call_falls_back_to_the_honest_refusal(monkeypatch, img):
    def boom(*a, **k):
        raise RuntimeError("GPUStack 503")
    monkeypatch.setattr(agent_b.gs, "chat_vision", boom)

    result = agent_b.describe(doc_id="d-301", transcription=DEGENERATE, image_path=img)

    assert result["low_confidence"] is True
    assert result.get("source") != "image-only"       # never claim what we don't have
    assert "unlesbar" in result["source_description"]


def test_an_empty_vision_response_falls_back(monkeypatch, img):
    monkeypatch.setattr(agent_b.gs, "chat_vision", lambda *a, **k: "")
    result = agent_b.describe(doc_id="d-301", transcription=DEGENERATE, image_path=img)
    assert result.get("source") != "image-only"


def test_good_text_is_unaffected(monkeypatch, img):
    """The normal path must not route through the image-only describer."""
    monkeypatch.setattr(agent_b.gs, "chat_text", lambda *a, **k: VLM_JSON)
    monkeypatch.setattr(agent_b.gs, "chat_vision",
                        lambda *a, **k: pytest.fail("good text used the image-only path"))
    monkeypatch.setattr(agent_b, "_care_flag", lambda t: {"is_care_related": False,
                                                          "care_context": "",
                                                          "care_types": [],
                                                          "beteiligte": []})

    result = agent_b.describe(doc_id="d-301", transcription=GOOD_TEXT, image_path=img)
    assert result.get("source") != "image-only"
    assert not result.get("low_confidence")


def test_pins_still_win_on_the_image_path(monkeypatch, img):
    """Historian-confirmed criteria are authoritative everywhere (#147)."""
    monkeypatch.setattr(agent_b.gs, "chat_vision", lambda *a, **k: VLM_JSON)
    result = agent_b.describe(doc_id="d-301", transcription=DEGENERATE, image_path=img,
                              pins={"script": "Bastarda"})

    schrift = result["source_json"].get("Schrift", {})
    assert schrift.get("wert") == "Bastarda"
    assert schrift.get("quelle") == "historiker"
