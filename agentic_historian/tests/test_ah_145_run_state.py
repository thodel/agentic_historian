
"""
test_ah_145_run_state.py - issue #145.
RunState stage-invalidation state machine + PipelineContext gate tracking.
Tests offline with temp dirs; no external services needed.
"""

from __future__ import annotations
import json, shutil, tempfile
from pathlib import Path
import sys
sys.path.insert(0, "/home/dh/.openclaw/workspace/agentic_historian")

import pytest
from run_state import (
    RunState,
    StageStatus,
    STAGE_ORDER,
    INVALIDATION_MATRIX,
    HumanOverride,
    GateDecision,
    PipelineArtifacts,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_state(doc_id: str = "test-doc-1") -> RunState:
    return RunState.create(doc_id=doc_id)


class TestStageStatusEnum:
    def test_stage_order_has_six_entries(self):
        assert len(STAGE_ORDER) == 6

    def test_stage_order(self):
        assert STAGE_ORDER == [
            "model_select", "kraken", "reconcile",
            "agent_b", "agent_c", "agent_d",
        ]


class TestInvalidationMatrix:
    def test_datierung_dirties_correct_stages(self):
        """Core acceptance: invalidate('datierung') dirties exactly
        {model_select, kraken, reconcile, agent_b, agent_c}, NOT agent_d."""
        rs = make_state()
        for stage in STAGE_ORDER:
            rs.mark_done(stage)

        dirtied = rs.invalidate("datierung")
        dirty = set(rs.dirty_stages())

        assert dirty == {"model_select", "kraken", "reconcile", "agent_b", "agent_c"}, \
            f"Unexpected dirty: {dirty}"
        assert "agent_d" not in dirty
        assert dirtied == dirty

    def test_sprache_dirties_correct_stages(self):
        rs = make_state()
        for stage in STAGE_ORDER:
            rs.mark_done(stage)
        rs.invalidate("sprache")
        dirty = set(rs.dirty_stages())
        assert dirty == {"model_select", "kraken", "reconcile", "agent_b", "agent_c"}

    def test_pfad_präferenz_dirties_only_b_and_c(self):
        rs = make_state()
        for stage in STAGE_ORDER:
            rs.mark_done(stage)
        rs.invalidate("pfad_präferenz")
        dirty = set(rs.dirty_stages())
        assert dirty == {"agent_b", "agent_c"}
        assert "model_select" not in dirty
        assert "kraken" not in dirty
        assert "reconcile" not in dirty
        assert "agent_d" not in dirty

    def test_entity_link_dirties_nothing(self):
        rs = make_state()
        for stage in STAGE_ORDER:
            rs.mark_done(stage)
        rs.invalidate("entity_link")
        assert rs.dirty_stages() == []

    def test_invalidate_idempotent(self):
        rs = make_state()
        for stage in STAGE_ORDER:
            rs.mark_done(stage)
        rs.invalidate("datierung")
        first = set(rs.dirty_stages())
        rs.invalidate("datierung")
        second = set(rs.dirty_stages())
        assert first == second

    def test_invalidate_unknown_field_noops(self):
        rs = make_state()
        for stage in STAGE_ORDER:
            rs.mark_done(stage)
        dirtied = rs.invalidate("unknown_field_xyz")
        assert dirtied == set()
        assert rs.dirty_stages() == []

    def test_pending_stages_not_marked_dirty(self):
        rs = make_state()
        rs.invalidate("datierung")
        assert rs.dirty_stages() == []


class TestMarkDoneRunningFailed:
    def test_mark_done_sets_done_status(self):
        rs = make_state()
        rs.mark_done("kraken")
        assert rs.stage_status("kraken") == StageStatus.DONE
        assert not rs.is_dirty("kraken")

    def test_mark_running_sets_running_status(self):
        rs = make_state()
        rs.mark_running("model_select")
        assert rs.stage_status("model_select") == StageStatus.RUNNING

    def test_mark_failed_sets_failed_status(self):
        rs = make_state()
        rs.mark_failed("agent_c")
        assert rs.stage_status("agent_c") == StageStatus.FAILED

    def test_mark_running_then_done(self):
        rs = make_state()
        rs.mark_running("kraken")
        rs.mark_done("kraken")
        assert rs.stage_status("kraken") == StageStatus.DONE


class TestPinField:
    def test_pin_field_records_override(self):
        rs = make_state()
        rs.pin_field("datierung", "15. Jh.", "14. Jh.", user="test-user")
        assert rs.is_field_pinned("datierung")
        assert rs.pinned_value("datierung") == "15. Jh."

    def test_pin_field_returns_previous_inferred(self):
        rs = make_state()
        rs.pin_field("datierung", "15. Jh.", "14. Jh.", user="test-user")
        o = rs.get_override("datierung")
        assert o.inferred == "14. Jh."
        assert o.value == "15. Jh."
        assert o.user == "test-user"

    def test_pin_field_overwrites_previous(self):
        rs = make_state()
        rs.pin_field("datierung", "15. Jh.", "14. Jh.", user="u1")
        rs.pin_field("datierung", "16. Jh.", "14. Jh.", user="u2")
        assert rs.pinned_value("datierung") == "16. Jh."
        overrides = [o for o in rs.human_overrides if o.field == "datierung"]
        assert len(overrides) == 1   # old one replaced

    def test_pin_field_invalidates_stages(self):
        rs = make_state()
        for stage in STAGE_ORDER:
            rs.mark_done(stage)
        rs.pin_field("datierung", "15. Jh.", "14. Jh.", user="test")
        dirty = set(rs.dirty_stages())
        assert "model_select" in dirty
        assert "kraken" in dirty
        assert "reconcile" in dirty


class TestGates:
    def test_add_gate_decision_blocked(self):
        rs = make_state()
        rs.add_gate_decision("gate_1", "blocked")
        assert "gate_1" in rs.unresolved_gates()

    def test_add_gate_decision_approved_replaces_blocked(self):
        """Adding approved for a gate removes the blocked entry (replace semantics)."""
        rs = make_state()
        rs.add_gate_decision("gate_2", "blocked")
        rs.add_gate_decision("gate_2", "approved")
        # After replace semantics: only 1 entry with decision=approved
        gate2_entries = [gd for gd in rs.gate_decisions if gd.gate == "gate_2"]
        assert len(gate2_entries) == 1
        assert gate2_entries[0].decision == "approved"
        assert "gate_2" not in rs.unresolved_gates()

    def test_gate_decision_idempotent(self):
        """Calling add_gate_decision twice with same args replaces (not duplicates)."""
        rs = make_state()
        rs.add_gate_decision("gate_1", "blocked")
        rs.add_gate_decision("gate_1", "blocked")
        gate1_entries = [gd for gd in rs.gate_decisions
                         if gd.gate == "gate_1" and gd.decision == "blocked"]
        assert len(gate1_entries) == 1   # replaced, not duplicated


class TestSaveLoad:
    def test_save_load_roundtrip(self, runs_dir):
        rs = make_state("roundtrip-test")
        rs.mark_done("model_select")
        rs.mark_done("kraken")
        rs.pin_field("datierung", "15. Jh.", "14. Jh.", user="test")
        # pin_field invalidates kraken + model_select; reset them to DONE
        rs.mark_done("model_select")
        rs.mark_done("kraken")
        rs.add_gate_decision("gate_1", "blocked")
        rs.save()

        loaded = RunState.load(doc_id="roundtrip-test")
        assert loaded is not None
        assert loaded.stage_status("model_select") == StageStatus.DONE
        assert loaded.pinned_value("datierung") == "15. Jh."
        assert "gate_1" in loaded.unresolved_gates()

    def test_load_nonexistent_returns_none(self, runs_dir):
        result = RunState.load(doc_id="nonexistent-doc-xyz-abc")
        assert result is None

    def test_save_load_preserves_dirty_stages(self, runs_dir):
        rs = make_state("dirty-test")
        for stage in STAGE_ORDER:
            rs.mark_done(stage)
        rs.invalidate("datierung")
        dirty_before = set(rs.dirty_stages())
        rs.save()

        loaded = RunState.load(doc_id="dirty-test")
        assert set(loaded.dirty_stages()) == dirty_before


class TestToDictFromDict:
    def test_to_dict_contains_expected_keys(self):
        rs = make_state()
        d = rs.to_dict()
        for stage in STAGE_ORDER:
            assert stage in d, f"Missing stage key: {stage}"
        assert "human_overrides" in d
        assert "gate_decisions" in d

    def test_from_dict_roundtrip(self):
        """Serialize + deserialize preserves all key fields."""
        rs = make_state()
        rs.mark_done("model_select")
        rs.pin_field("sprache", "la", "de", user="u1")
        # pin_field invalidates model_select (sprache dirties it) → reset
        rs.mark_done("model_select")
        d = rs.to_dict()
        restored = RunState.from_dict(d)
        assert restored.stage_status("model_select") == StageStatus.DONE
        assert restored.pinned_value("sprache") == "la"


class TestHumanOverride:
    def test_human_override_fields(self):
        ho = HumanOverride(
            field="datierung",
            value="15. Jh.",
            inferred="14. Jh.",
            user="test-user",
            ts="2026-07-05T00:00:00",
        )
        assert ho.value == "15. Jh."
        assert ho.inferred == "14. Jh."
        assert ho.user == "test-user"


class TestPipelineArtifacts:
    def test_default_constructor(self):
        pa = PipelineArtifacts()
        assert pa.transcription == ""
        assert pa.kraken_transcription == ""
        assert pa.reconciled_transcription == ""

    def test_constructor_kwargs(self):
        pa = PipelineArtifacts(
            transcription="ocr text",
            kraken_transcription="kraken result",
            reconciled_transcription="merged result",
        )
        assert pa.transcription == "ocr text"
        assert pa.kraken_transcription == "kraken result"
        assert pa.reconciled_transcription == "merged result"


class TestIsDirty:
    def test_is_dirty_after_invalidate(self):
        rs = make_state()
        for stage in STAGE_ORDER:
            rs.mark_done(stage)
        rs.invalidate("datierung")
        assert rs.is_dirty("model_select")
        assert rs.is_dirty("kraken")
        assert not rs.is_dirty("agent_d")

    def test_is_dirty_after_mark_done(self):
        rs = make_state()
        rs.mark_done("model_select")
        assert not rs.is_dirty("model_select")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
