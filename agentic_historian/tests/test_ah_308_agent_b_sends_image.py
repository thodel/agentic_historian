"""#308: when Agent B says a description is based on the image, send the image.

describe() took an image_path and never sent it. It selected the codicological
prompt (which asks for Beschreibstoff, Wasserzeichen, Schriftraum, Haende — things
only visible on the page), told the model an image was available, then called
text-only gs.chat_text with the transcription. The model was asked to describe a
page it was never shown, and the result recorded `image_path: /path/page.jpg` as
though that were provenance.

It matters beyond tidiness: Schrift and Datierung drive model selection (#299), and
they are exactly the elements you read off the page rather than out of the text.

Offline — the VLM is mocked. Run from the repo root:
    pytest agentic_historian/tests/test_ah_308_agent_b_sends_image.py
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                     # noqa: E402
from agents import source_description as agent_b  # noqa: E402

GOOD_TEXT = ("Wir Hans von Wiler tuend kund allen die disen brief ansehent "
             "oder hoerent lesen dass wir mit guotem willen")
RESPONSE = ('{"Schrift": {"wert": "Kurrent"}, "Datierung": {"wert": "16. Jh."}}'
            "\n\n## Beschreibung\nEine Urkunde in Kurrentschrift.")


@pytest.fixture(autouse=True)
def _offline(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DESCRIPTIONS_DIR", tmp_path / "descriptions")
    (tmp_path / "descriptions").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(agent_b, "_care_flag",
                        lambda t: {"is_care_related": False, "care_context": "",
                                   "care_types": [], "beteiligte": []})
    return tmp_path


@pytest.fixture
def img(tmp_path):
    p = tmp_path / "page.jpg"
    p.write_bytes(b"\xff\xd8\xff")
    return str(p)


# ── the regression ───────────────────────────────────────────────────────────

def test_the_image_is_actually_sent(monkeypatch, img):
    seen = {}

    def vision(prompt, image_source=None, system=None, **kw):
        seen["image"] = image_source
        return RESPONSE

    monkeypatch.setattr(agent_b.gs, "chat_vision", vision)
    monkeypatch.setattr(agent_b.gs, "chat_text",
                        lambda *a, **k: pytest.fail("image given but chat_text used"))

    agent_b.describe(doc_id="d-308", transcription=GOOD_TEXT, image_path=img)
    assert seen["image"] == img


def test_the_transcription_still_goes_with_it(monkeypatch, img):
    """The image is additional evidence, not a replacement — the codicological
    prompt reasons over both."""
    seen = {}
    monkeypatch.setattr(agent_b.gs, "chat_vision",
                        lambda p, image_source=None, **k: seen.setdefault("prompt", p) or RESPONSE)

    agent_b.describe(doc_id="d-308", transcription=GOOD_TEXT, image_path=img)
    assert "Wir Hans von Wiler" in seen["prompt"]


def test_image_path_in_the_result_means_it_was_sent(monkeypatch, img):
    monkeypatch.setattr(agent_b.gs, "chat_vision", lambda *a, **k: RESPONSE)
    result = agent_b.describe(doc_id="d-308", transcription=GOOD_TEXT, image_path=img)
    assert result["image_path"] == img


# ── the image-less case is unchanged ─────────────────────────────────────────

def test_no_image_still_uses_text_only(monkeypatch):
    monkeypatch.setattr(agent_b.gs, "chat_text", lambda *a, **k: RESPONSE)
    monkeypatch.setattr(agent_b.gs, "chat_vision",
                        lambda *a, **k: pytest.fail("no image — must not call vision"))

    result = agent_b.describe(doc_id="d-308", transcription=GOOD_TEXT)
    assert result["image_path"] == "none"


# ── robustness ───────────────────────────────────────────────────────────────

def test_a_failing_vision_call_falls_back_to_text(monkeypatch, img):
    """A VLM hiccup must not cost the whole description."""
    def boom(*a, **k):
        raise RuntimeError("GPUStack 503")
    monkeypatch.setattr(agent_b.gs, "chat_vision", boom)
    monkeypatch.setattr(agent_b.gs, "chat_text", lambda *a, **k: RESPONSE)

    result = agent_b.describe(doc_id="d-308", transcription=GOOD_TEXT, image_path=img)

    assert result["source_json"].get("Schrift", {}).get("wert") == "Kurrent"
    # …but it must NOT claim the image: the model never saw it.
    assert result["image_path"] == "none"


def test_both_calls_failing_is_not_fatal(monkeypatch, img):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(agent_b.gs, "chat_vision", boom)
    monkeypatch.setattr(agent_b.gs, "chat_text", boom)

    result = agent_b.describe(doc_id="d-308", transcription=GOOD_TEXT, image_path=img)
    assert result["image_path"] == "none"
    assert result["source_json"] == {}


# ── the criteria that started all this ───────────────────────────────────────

def test_schrift_and_datierung_survive_to_the_criteria(monkeypatch, img):
    """The two elements that drive model selection (#299) are exactly the ones a
    VLM reads off the page."""
    from agent_a.model_selector import SourceCriteria

    monkeypatch.setattr(agent_b.gs, "chat_vision", lambda *a, **k: RESPONSE)
    result = agent_b.describe(doc_id="d-308", transcription=GOOD_TEXT, image_path=img)

    criteria = SourceCriteria.from_agent_b_and_json(
        result["source_description"], result["source_json"])
    assert criteria.script or criteria.century
