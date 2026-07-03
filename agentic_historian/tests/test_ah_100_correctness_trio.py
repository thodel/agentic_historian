"""Tests for #100: reporter path · /run QA key · header-strip maxsplit.

Three independent correctness bugs. Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_100_correctness_trio.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))


# ── Bug 1: reporter PROGRESS.md path ─────────────────────────────────────────

def test_reporter_progress_path_is_package_dir():
    import reporter
    # PROGRESS.md must resolve to the package dir (next to reporter.py),
    # not the repo root (the old `.parent.parent` overshoot).
    assert reporter.PROGRESS_FILE == PKG / "PROGRESS.md", (
        f"PROGRESS_FILE should be the package-dir PROGRESS.md, got {reporter.PROGRESS_FILE}"
    )
    assert reporter.PROGRESS_FILE.parent == PKG


# ── Bug 2: /run QA key ───────────────────────────────────────────────────────

def test_bot_reads_qa_from_a_meta():
    src = (PKG / "bot.py").read_text()
    assert "result.get('transcription_qa'" not in src, (
        "bot.py must not read the phantom 'transcription_qa' key"
    )
    assert "'a_meta', {}).get('qa_score'" in src, (
        "bot.py must read the QA score from a_meta.qa_score (the real key)"
    )


def test_a_meta_actually_carries_qa_score():
    """The pipeline exposes the Agent-A QA score under a_meta.qa_score — the key
    bot.py now reads. Build a ctx the way run_full_pipeline does and check to_json."""
    import orchestrator
    ctx = orchestrator.PipelineContext("doc1")
    ctx.a_meta = {"doc_id": "doc1", "transcription": "x", "qa_score": 0.83,
                  "source": "vlm", "success": True}
    out = ctx.to_json()
    assert out.get("a_meta", {}).get("qa_score") == 0.83


# ── Bug 3: header-strip must not drop the first body paragraph ────────────────

def test_header_strip_preserves_body_paragraphs(tmp_path, monkeypatch):
    """run_agent_b/run_agent_c strip the saved header; with a body that has a
    blank-line paragraph break, maxsplit=2 dropped paragraph 1. maxsplit=1 keeps it."""
    import config, orchestrator

    doc_id = "multi"
    header = (
        "# Transkription: multi\n"
        "# QA-Score: 0.80\n"
        "# HTR-Source: vlm\n"
        "# Modell: qwen3-vl\n\n"
    )
    body = "Erster Absatz der Urkunde.\n\nZweiter Absatz mit den Namen."
    monkeypatch.setattr(config, "TRANSCRIPTIONS_DIR", tmp_path)
    (tmp_path / f"{doc_id}.txt").write_text(header + body, encoding="utf-8")

    captured = {}

    def _fake_describe(d, transcription):
        captured["text"] = transcription
        return {"ok": True}

    monkeypatch.setattr(orchestrator.agent_b, "describe", _fake_describe)
    orchestrator.run_agent_b(doc_id)

    # The full body (both paragraphs) must survive; the header must be gone.
    assert captured["text"] == body, "header-strip dropped part of the body"
    assert "Erster Absatz" in captured["text"]
    assert "Zweiter Absatz" in captured["text"]
    assert "# QA-Score" not in captured["text"]
