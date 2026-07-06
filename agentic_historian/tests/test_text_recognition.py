"""Offline tests for Agent A / text_recognition (#72, AH-92).

Mocks kraken (`_run_kraken`) and the VLM (`gpustack_client.chat_vision`) so the
suite runs without the VPN/GPUStack. Asserts the kraken-first policy, VLM
fallback only on kraken failure/unavailability, the documented process_file dict,
and the _quality_score heuristic.

Run from the repo root:
    pytest agentic_historian/tests/test_text_recognition.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config  # noqa: E402
from agents import text_recognition as tr  # noqa: E402


def _kraken(monkeypatch, fn):
    monkeypatch.setattr(tr, "HAS_KRAKEN", True)
    monkeypatch.setattr(tr, "_run_kraken", fn, raising=False)


def _vlm(monkeypatch, fn):
    monkeypatch.setattr(tr.gs, "chat_vision", lambda **k: fn())


# ── kraken-first policy ──────────────────────────────────────────────────────

def test_kraken_used_when_available_vlm_not_called(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TRANSCRIPTIONS_DIR", tmp_path)
    _kraken(monkeypatch, lambda p: ("Wir Hans von Wiler tuend kund", "kraken-de"))
    calls = {"n": 0}
    _vlm(monkeypatch, lambda: calls.__setitem__("n", calls["n"] + 1) or "vlm")

    r = tr.transcribe_image(tmp_path / "BAT_1.jpg")
    assert r["source"] == "kraken"
    assert r["transcription"].startswith("Wir Hans")
    assert calls["n"] == 0                      # VLM never touched when kraken wins


def test_vlm_fallback_when_kraken_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TRANSCRIPTIONS_DIR", tmp_path)
    monkeypatch.setattr(tr, "HAS_KRAKEN", False)
    _vlm(monkeypatch, lambda: "Ein langer VLM-Transkriptionstext hier")

    r = tr.transcribe_image(tmp_path / "BAT_2.jpg")
    assert r["source"] == "vlm" and "VLM" in r["transcription"]


def test_vlm_fallback_when_kraken_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TRANSCRIPTIONS_DIR", tmp_path)
    def boom(p): raise RuntimeError("gateway down")
    _kraken(monkeypatch, boom)
    _vlm(monkeypatch, lambda: "VLM fallback text lang genug hier")

    assert tr.transcribe_image(tmp_path / "BAT_3.jpg")["source"] == "vlm"


def test_vlm_fallback_when_kraken_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TRANSCRIPTIONS_DIR", tmp_path)
    _kraken(monkeypatch, lambda p: ("", "no-lines"))
    _vlm(monkeypatch, lambda: "VLM weil kraken leer war und nichts lieferte")

    assert tr.transcribe_image(tmp_path / "BAT_4.jpg")["source"] == "vlm"


# ── documented return dict ───────────────────────────────────────────────────

def test_process_file_returns_documented_dict(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TRANSCRIPTIONS_DIR", tmp_path)
    _kraken(monkeypatch, lambda p: ("Kraken Transkription lang genug Text", "m"))

    out = tr.process_file(tmp_path / "BAT_5.jpg")
    assert set(out) >= {"doc_id", "transcription", "qa_score", "source", "path", "success"}
    assert out["doc_id"] == "BAT_5" and out["source"] == "kraken"
    assert out["success"] is True and Path(out["path"]).exists()


# ── QA heuristic ─────────────────────────────────────────────────────────────

def test_quality_score_heuristic_and_bounds():
    assert tr._quality_score("") == 0.0
    assert tr._quality_score("   ") == 0.0
    assert tr._quality_score("....!!!!----1234") == 0.2      # alpha_ratio < 0.1
    assert tr._quality_score("kurz") == 0.3                  # < 20 chars
    assert tr._quality_score("Ein normaler lesbarer Satz hier") == 0.8
    for t in ["", "abc", "....", "Wir Hans von Wiler tuend kund allen"]:
        assert 0.0 <= tr._quality_score(t) <= 1.0


def test_vlm_retry_on_low_qa(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TRANSCRIPTIONS_DIR", tmp_path)
    monkeypatch.setattr(config, "HTR_QUALITY_THRESHOLD", 0.75)
    monkeypatch.setattr(tr, "HAS_KRAKEN", False)
    outs = iter(["....", "Ein guter langer Transkriptionstext nach dem Retry"])
    monkeypatch.setattr(tr.gs, "chat_vision", lambda **k: next(outs))

    r = tr.transcribe_image(tmp_path / "BAT_6.jpg")
    assert r["source"] == "vlm_retry" and r["qa_score"] == 0.8
