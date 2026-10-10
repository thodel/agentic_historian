"""Tests for SEC-12 (#583): /agent_d must not upload corpus text to Voyant by
default.

`analyse_corpus` uploaded up to 50k chars of transcription to Voyant (a
third-party service) and `/agent_d` posted the publicly shareable `?corpus=`
link — so unpublished or licensed transcriptions could leak. The upload is now
behind `config.ENABLE_VOYANT_UPLOAD`, off by default, and when a link is posted
the channel is told it is public.

Offline: the LLM/analysis helpers and the Voyant call are stubbed; no network.
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
from agents import corpus_analysis as ca   # noqa: E402


def _stub_analysis(monkeypatch, tmp_path):
    """Make analyse_corpus run offline: one transcription, no LLM, no disk write,
    and a Voyant call that records whether it was invoked."""
    (tmp_path / "doc1.txt").write_text("Ein Beispieltext.", encoding="utf-8")
    monkeypatch.setattr(config, "TRANSCRIPTIONS_DIR", tmp_path)
    monkeypatch.setattr(ca, "_topics_large", lambda text: {})
    monkeypatch.setattr(ca, "_taxonomy_large", lambda text: {"taxonomies": []})
    monkeypatch.setattr(ca, "_care_analysis_large", lambda text: {})
    monkeypatch.setattr(ca, "_save", lambda name, result: None)
    calls = []
    monkeypatch.setattr(ca, "_voyant_url",
                        lambda text, name: calls.append(name) or "https://voyant/?corpus=X")
    return calls


def test_default_config_does_not_enable_the_upload():
    src = (PKG / "config.py").read_text(encoding="utf-8")
    assert 'ENABLE_VOYANT_UPLOAD = _get("ENABLE_VOYANT_UPLOAD", "false")' in src
    # The live default (unless the environment turned it on) must be off.
    import os
    if not os.environ.get("ENABLE_VOYANT_UPLOAD"):
        assert config.ENABLE_VOYANT_UPLOAD is False


def test_upload_is_skipped_when_disabled(monkeypatch, tmp_path):
    calls = _stub_analysis(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "ENABLE_VOYANT_UPLOAD", False)
    result = ca.analyse_corpus("default")
    assert calls == [], "corpus text must not be uploaded to Voyant when disabled"
    assert result["voyant_url"] == ""


def test_upload_happens_only_when_enabled(monkeypatch, tmp_path):
    calls = _stub_analysis(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "ENABLE_VOYANT_UPLOAD", True)
    result = ca.analyse_corpus("default")
    assert calls == ["default"], "with the flag on, the corpus is uploaded"
    assert result["voyant_url"] == "https://voyant/?corpus=X"


def test_agent_d_warns_that_the_voyant_link_is_public():
    """When a link is posted, the channel message must flag it as publicly
    shareable (the reading of the corpus text leaves the server)."""
    src = (PKG / "bot.py").read_text(encoding="utf-8")
    i = src.index('result.get("voyant_url")')
    window = src[i:i + 400]
    assert "öffentlich teilbar" in window
