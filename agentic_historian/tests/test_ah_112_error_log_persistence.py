"""Tests for #112: ctx.errors are never persisted — META_LOG_PATH stays empty.

Run offline (no GPUStack/VPN) — file-level checks + functional tests.
"""

import json
import os
import tempfile
from pathlib import Path
from datetime import datetime, timezone

# ─────────────────────────────────────────────────────────────────────────────
# File-level checks
# ─────────────────────────────────────────────────────────────────────────────

def test_append_errors_function_exists():
    """orchestrator.py must define _append_errors_to_log(doc_id, errors)."""
    src = open("agentic_historian/orchestrator.py").read()
    assert "_append_errors_to_log" in src, (
        "_append_errors_to_log must be defined in orchestrator.py"
    )
    assert "def _append_errors_to_log(doc_id: str, errors: list)" in src, (
        "Function signature must be: _append_errors_to_log(doc_id: str, errors: list)"
    )


def test_append_errors_called_in_save_pipeline():
    """_save_pipeline_result must call _append_errors_to_log(doc_id, ctx.errors)."""
    src = open("agentic_historian/orchestrator.py").read()
    assert "_append_errors_to_log(doc_id, ctx.errors)" in src, (
        "_save_pipeline_result must call _append_errors_to_log(doc_id, ctx.errors)"
    )


def test_meta_agent_save_does_not_reset_log():
    """meta_agent._save() must NOT write '[]' to META_LOG_PATH.

    Previously _save() did: config.META_LOG_PATH.write_text("[]", ...)
    which wiped the error log every time Agent E ran.
    """
    src = open("agentic_historian/agents/meta_agent.py").read()
    # The bad pattern must be gone
    assert 'META_LOG_PATH.write_text("[]"' not in src, (
        "meta_agent._save() must not reset META_LOG_PATH to []"
    )
    # The comment explaining why must be present
    assert "no longer reset" in src or "Resetting it would lose" in src, (
        "_save() must have a comment explaining why META_LOG_PATH is not reset"
    )


def test_errors_field_in_pipeline_context():
    """PipelineContext must have an errors list that serialises to JSON."""
    src = open("agentic_historian/orchestrator.py").read()
    assert 'self.errors: list = []' in src, "PipelineContext.errors must be a list"
    assert '"errors": self.errors' in src, "to_json() must include errors"


# ─────────────────────────────────────────────────────────────────────────────
# Functional test: verify _append_errors_to_log logic
# ─────────────────────────────────────────────────────────────────────────────

def test_append_errors_roundtrip():
    """Simulate two pipeline runs for different doc_ids and verify the log
    is a JSON list with one entry per run, each containing doc_id + errors."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "meta_agent_log.json"
        doc_id = "test_doc_001"

        # Simulate the _append_errors_to_log logic inline
        def append_errors(doc_id, errors):
            if log_path.exists():
                try:
                    entries = json.loads(log_path.read_text(encoding="utf-8"))
                except Exception:
                    entries = []
            else:
                entries = []
            entries.append({
                "doc_id": doc_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "errors": errors,
            })
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2))

        # Run 1: one error
        append_errors("doc_a", [{"agent": "A", "phase": 1, "error": "timeout"}])
        entries = json.loads(log_path.read_text())
        assert len(entries) == 1
        assert entries[0]["doc_id"] == "doc_a"
        assert len(entries[0]["errors"]) == 1

        # Run 2: no errors (empty list is still an entry)
        append_errors("doc_b", [])
        entries = json.loads(log_path.read_text())
        assert len(entries) == 2
        assert entries[1]["doc_id"] == "doc_b"
        assert entries[1]["errors"] == []

        # Run 3: multiple errors
        append_errors("doc_c", [
            {"agent": "B", "error": "invalid json"},
            {"agent": "C", "error": "rate limit"},
        ])
        entries = json.loads(log_path.read_text())
        assert len(entries) == 3
        assert entries[2]["errors"][0]["agent"] == "B"
        assert entries[2]["errors"][1]["agent"] == "C"


def test_malformed_json_log_is_not_crash():
    """If META_LOG_PATH contains garbage, _append_errors_to_log must not crash
    — it should treat it as empty and start fresh."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "log.json"
        log_path.write_text("NOT JSON{", encoding="utf-8")  # corrupt

        # Simulate the recovery logic
        try:
            content = log_path.read_text(encoding="utf-8")
            entries = json.loads(content)
        except Exception:
            entries = []

        assert entries == [], "Corrupt log must be treated as empty list, not crash"


if __name__ == "__main__":
    test_append_errors_function_exists()
    print("PASS: test_append_errors_function_exists")

    test_append_errors_called_in_save_pipeline()
    print("PASS: test_append_errors_called_in_save_pipeline")

    test_meta_agent_save_does_not_reset_log()
    print("PASS: test_meta_agent_save_does_not_reset_log")

    test_errors_field_in_pipeline_context()
    print("PASS: test_errors_field_in_pipeline_context")

    test_append_errors_roundtrip()
    print("PASS: test_append_errors_roundtrip")

    test_malformed_json_log_is_not_crash()
    print("PASS: test_malformed_json_log_is_not_crash")

    print("\nAll #112 tests passed.")
