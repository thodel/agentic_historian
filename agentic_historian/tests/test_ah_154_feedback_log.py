"""
Tests for #154 (HITL-4b): feedback logging + Agent E routing report.

Offline. Run:
    cd agentic_historian
    .venv/bin/python -m pytest tests/test_ah_154_feedback_log.py -v
"""

import json
import pathlib
import sys
import tempfile
from unittest.mock import patch, MagicMock

PKG = pathlib.Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from feedback_logger import (
    ROUTING_FEEDBACK_FIELDS,
    log_routing_feedback,
    _now_iso,
)
import routing_report


# ── helpers ───────────────────────────────────────────────────────────────────

class _MockRunState:
    """Minimal RunState stand-in for testing."""
    def __init__(self, doc_id="d1"):
        self.doc_id = doc_id
        self.criteria = {"century": 14, "lang": "de", "script": "caroline"}
        self.gate_decisions = {}
        self.artifacts = {}


# ── log_routing_feedback ─────────────────────────────────────────────────────

def test_log_criteria_feedback_creates_jsonl_entry():
    """A criteria change appends one correctly-structured JSON line."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        log_path = tmp / "routing.jsonl"

        with patch("feedback_logger.config.ROUTING_LOG_PATH", log_path):
            with patch("feedback_logger.config.FEEDBACK_DIR", tmp):
                log_routing_feedback(
                    state=_MockRunState("doc1"),
                    field="lang",
                    inferred_value="la",
                    chosen_value="de",
                    decided_by="human",
                )

        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["doc_id"] == "doc1"
        assert entry["field"] == "lang"
        assert entry["inferred_value"] == "la"
        assert entry["chosen_value"] == "de"
        assert entry["decided_by"] == "human"
        assert entry["model_id"] is None
        assert entry["path"] is None
        assert "ts" in entry


def test_log_model_select_feedback_includes_model_fields():
    """model_select feedback includes model_id, model_name, model_score."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        log_path = tmp / "routing.jsonl"

        with patch("feedback_logger.config.ROUTING_LOG_PATH", log_path):
            pass  # will be set below

        with patch("feedback_logger.config.ROUTING_LOG_PATH", log_path):
            with patch("feedback_logger.config.FEEDBACK_DIR", tmp):
                log_routing_feedback(
                    state=_MockRunState("doc2"),
                    field="model_select",
                    inferred_value="model_a",
                    chosen_value="model_b",
                    model_id="model_b",
                    model_name="Fraktur-Ensemble",
                    model_score=0.82,
                    decided_by="human",
                    score=0.82,
                )

        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["model_id"] == "model_b"
        assert entry["model_name"] == "Fraktur-Ensemble"
        assert entry["model_score"] == 0.82
        assert entry["score"] == 0.82


def test_log_path_preference_feedback_includes_path():
    """path_preference feedback includes the path field."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        log_path = tmp / "routing.jsonl"

        with patch("feedback_logger.config.ROUTING_LOG_PATH", log_path):
            with patch("feedback_logger.config.FEEDBACK_DIR", tmp):
                log_routing_feedback(
                    state=_MockRunState("doc3"),
                    field="path_preference",
                    inferred_value="vlm",
                    chosen_value="reconciled",
                    path="reconciled",
                    decided_by="human",
                )

        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["path"] == "reconciled"


def test_log_auto_decision_sets_decided_by_auto():
    """Auto-proceed logs decided_by='auto'."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        log_path = tmp / "routing.jsonl"

        with patch("feedback_logger.config.ROUTING_LOG_PATH", log_path):
            with patch("feedback_logger.config.FEEDBACK_DIR", tmp):
                log_routing_feedback(
                    state=_MockRunState("doc4"),
                    field="lang",
                    inferred_value="de",
                    chosen_value="de",
                    decided_by="auto",
                )

        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["decided_by"] == "auto"


def test_multiple_entries_append_not_overwrite():
    """Multiple log calls append, not overwrite."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        log_path = tmp / "routing.jsonl"

        with patch("feedback_logger.config.ROUTING_LOG_PATH", log_path):
            with patch("feedback_logger.config.FEEDBACK_DIR", tmp):
                for i in range(5):
                    log_routing_feedback(
                        state=_MockRunState(f"doc{i}"),
                        field="lang",
                        inferred_value="la",
                        chosen_value="de",
                        decided_by="human",
                    )

        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 5
        doc_ids = [json.loads(l)["doc_id"] for l in lines]
        assert doc_ids == ["doc0", "doc1", "doc2", "doc3", "doc4"]


# ── routing_report: compute_override_rates ───────────────────────────────────

def _write_routing_jsonl(tmp: pathlib.Path, entries: list[dict]) -> None:
    path = tmp / "routing.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def test_override_rate_calculation():
    """Override rate = (chosen != inferred) / total per field."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        path = tmp / "routing.jsonl"
        _write_routing_jsonl(tmp, [
            {"field": "lang", "inferred_value": "la", "chosen_value": "de"},
            {"field": "lang", "inferred_value": "la", "chosen_value": "de"},
            {"field": "lang", "inferred_value": "la", "chosen_value": "la"},  # no override
            {"field": "century", "inferred_value": 14, "chosen_value": 15},
            {"field": "century", "inferred_value": 14, "chosen_value": 14},  # no override
        ])

        with patch("routing_report.config.ROUTING_LOG_PATH", path):
            rates = routing_report.compute_override_rates()

        assert rates["lang"]["total"] == 3
        assert rates["lang"]["overrides"] == 2
        assert abs(rates["lang"]["override_rate"] - 2 / 3) < 0.001
        assert rates["century"]["total"] == 2
        assert rates["century"]["overrides"] == 1


def test_override_rate_zero_when_no_overrides():
    """If all chosen == inferred, override_rate is 0.0 (not division by zero)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        path = tmp / "routing.jsonl"
        _write_routing_jsonl(tmp, [
            {"field": "lang", "inferred_value": "de", "chosen_value": "de"},
            {"field": "lang", "inferred_value": "de", "chosen_value": "de"},
        ])

        with patch("routing_report.config.ROUTING_LOG_PATH", path):
            rates = routing_report.compute_override_rates()

        assert rates["lang"]["override_rate"] == 0.0


def test_override_rate_excludes_path_preference():
    """path_preference is tracked separately, not in override rates."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        path = tmp / "routing.jsonl"
        _write_routing_jsonl(tmp, [
            {"field": "path_preference", "inferred_value": "vlm", "chosen_value": "reconciled"},
        ])

        with patch("routing_report.config.ROUTING_LOG_PATH", path):
            rates = routing_report.compute_override_rates()

        assert "path_preference" not in rates


# ── routing_report: compute_model_winrates ───────────────────────────────────

def test_model_winrate_basic():
    """Model with highest win/total for a bucket is the winner."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        path = tmp / "routing.jsonl"
        _write_routing_jsonl(tmp, [
            {"field": "model_select", "script": "Caroline", "century": 14, "lang": "de",
             "model_id": "m1", "chosen_value": "m1", "decided_by": "human"},
            {"field": "model_select", "script": "Caroline", "century": 14, "lang": "de",
             "model_id": "m1", "chosen_value": "m1", "decided_by": "human"},
            {"field": "model_select", "script": "Caroline", "century": 14, "lang": "de",
             "model_id": "m2", "chosen_value": "m2", "decided_by": "human"},
            # auto: model won (chosen_value == model_id)
            {"field": "model_select", "script": "Caroline", "century": 14, "lang": "de",
             "model_id": "m1", "chosen_value": "m1", "decided_by": "auto"},
        ])

        with patch("routing_report.config.ROUTING_LOG_PATH", path):
            winrates = routing_report.compute_model_winrates()

        key = ("caroline", 14, "de")
        assert key in winrates
        assert winrates[key]["m1"]["wins"] == 3
        assert winrates[key]["m1"]["total"] == 3
        assert winrates[key]["m2"]["wins"] == 1
        assert winrates[key]["m2"]["total"] == 1


def test_model_winrate_ignores_non_model_select():
    """Non-model_select entries are skipped in model win-rate calculation."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        path = tmp / "routing.jsonl"
        _write_routing_jsonl(tmp, [
            {"field": "lang", "script": "Caroline", "century": 14, "lang": "de",
             "chosen_value": "fr"},
        ])

        with patch("routing_report.config.ROUTING_LOG_PATH", path):
            winrates = routing_report.compute_model_winrates()

        assert winrates == {}


# ── routing_report: compute_path_preferences ─────────────────────────────────

def test_path_preferences_counts():
    """Count each path choice."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        path = tmp / "routing.jsonl"
        _write_routing_jsonl(tmp, [
            {"field": "path_preference", "chosen_value": "reconciled"},
            {"field": "path_preference", "chosen_value": "reconciled"},
            {"field": "path_preference", "chosen_value": "vlm"},
        ])

        with patch("routing_report.config.ROUTING_LOG_PATH", path):
            prefs = routing_report.compute_path_preferences()

        assert prefs["reconciled"] == 2
        assert prefs["vlm"] == 1


def test_path_preferences_empty_log():
    """Empty log → empty dict."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        path = tmp / "routing.jsonl"
        path.touch()

        with patch("routing_report.config.ROUTING_LOG_PATH", path):
            prefs = routing_report.compute_path_preferences()

        assert prefs == {}


# ── routing_report: format_routing_stats ─────────────────────────────────────

def test_format_routing_stats_shows_override_rate():
    """Formatted stats include override rates."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        path = tmp / "routing.jsonl"
        _write_routing_jsonl(tmp, [
            {"field": "lang", "inferred_value": "la", "chosen_value": "de"},
            {"field": "lang", "inferred_value": "la", "chosen_value": "de"},
            {"field": "lang", "inferred_value": "la", "chosen_value": "la"},
        ])

        with patch("routing_report.config.ROUTING_LOG_PATH", path):
            stats = routing_report.format_routing_stats()

        assert "Override-Rate" in stats
        assert "lang" in stats
        assert "2/3" in stats


def test_format_routing_stats_empty_log_message():
    """Empty log produces the placeholder message."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        path = tmp / "routing.jsonl"
        path.touch()

        with patch("routing_report.config.ROUTING_LOG_PATH", path):
            stats = routing_report.format_routing_stats()

        assert "leer" in stats or "—" in stats


# ── routing_report: routing_stats_embed ─────────────────────────────────────

def test_routing_stats_embed_returns_embed_when_data_exists():
    """routing_stats_embed returns a Discord Embed when there is data."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        path = tmp / "routing.jsonl"
        _write_routing_jsonl(tmp, [
            {"field": "lang", "inferred_value": "la", "chosen_value": "de"},
        ])

        with patch("routing_report.config.ROUTING_LOG_PATH", path):
            embed = routing_report.routing_stats_embed()

        assert embed is not None
        assert embed.title == "📋 Routing-Statistik (HITL-4b)"
        assert "Override-Rate" in embed.description


def test_routing_stats_embed_returns_none_when_empty():
    """No data → None (bot.py skips sending)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        path = tmp / "routing.jsonl"
        path.touch()

        with patch("routing_report.config.ROUTING_LOG_PATH", path):
            embed = routing_report.routing_stats_embed()

        assert embed is None