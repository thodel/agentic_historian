"""Offline coverage for #275: HTR-only anti-repetition decoding."""

import sys
from pathlib import Path
from types import SimpleNamespace

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))


def test_run_vlm_sources_penalties_from_config(monkeypatch, tmp_path):
    """The transcription call carries the tunable values and no live call runs."""
    from agent_a import dual_pipeline as dp

    request = {}

    def fake_chat_vision(**kwargs):
        request.update(kwargs)
        return "Wir Hans von Wiler tuend kund allen den die disen brief ansehent"

    monkeypatch.setattr(dp.config, "VLM_FREQUENCY_PENALTY", 0.37)
    monkeypatch.setattr(dp.config, "VLM_PRESENCE_PENALTY", 0.11)
    monkeypatch.setattr(dp.gs, "chat_vision", fake_chat_vision)

    image = tmp_path / "hard-page.jpg"
    image.write_bytes(b"not-used-by-mock")
    text, _ = dp._run_vlm(image)

    assert text.startswith("Wir Hans")
    assert request["frequency_penalty"] == 0.37
    assert request["presence_penalty"] == 0.11


def test_gpustack_transport_forwards_optional_penalties(monkeypatch):
    """The OpenAI-compatible request receives both optional parameters."""
    from utils import gpustack_client as gs

    sent = {}

    def fake_create(**kwargs):
        sent.update(kwargs)
        choice = SimpleNamespace(
            message=SimpleNamespace(content="transcription"),
            finish_reason="stop",
        )
        return SimpleNamespace(choices=[choice])

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    monkeypatch.setattr(gs, "get_client", lambda: client)

    content, finish = gs._create(
        "vision-model",
        [{"role": "user", "content": "transcribe"}],
        temperature=0.0,
        max_tokens=1024,
        frequency_penalty=0.25,
        presence_penalty=0.05,
    )

    assert (content, finish) == ("transcription", "stop")
    assert sent["frequency_penalty"] == 0.25
    assert sent["presence_penalty"] == 0.05


def test_gpustack_transport_omits_penalties_for_other_calls(monkeypatch):
    """Existing orchestrator/reconcile calls do not gain HTR decoding options."""
    from utils import gpustack_client as gs

    sent = {}

    def fake_create(**kwargs):
        sent.update(kwargs)
        choice = SimpleNamespace(
            message=SimpleNamespace(content="answer"),
            finish_reason="stop",
        )
        return SimpleNamespace(choices=[choice])

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    monkeypatch.setattr(gs, "get_client", lambda: client)

    gs._create("text-model", [], temperature=1.0, max_tokens=1024)

    assert "frequency_penalty" not in sent
    assert "presence_penalty" not in sent
