"""#226 (P1-B2): selective reprocessing — invalidate + resume with runners.

Offline: the stage runners are injected (mocked), so no agent/HTR calls happen.
RunState is redirected to a tmp dir via config.DATA_DIR. Run from the repo root:
    pytest agentic_historian/tests/test_ah_226_reprocess.py
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                    # noqa: E402
import ingest                                    # noqa: E402
from runstate import RunState, StageResult, STAGES, DONE, PENDING  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    """Point RunState persistence at a throwaway dir."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def _save_all_done(doc_id="d1", **criteria) -> RunState:
    rs = RunState(doc_id=doc_id)
    for s in STAGES:
        rs.stage_status[s] = DONE
    rs.artifacts = {s: f"artifact-{s}" for s in STAGES}
    rs.artifacts["transcription"] = "current text"
    rs.criteria = criteria or {"script": "Bastarda"}
    rs.save()
    return rs


def _recording_runners(called, error_stage=None):
    """A runner per STAGE that records the call and returns success — or raises
    for ``error_stage``."""
    def make(stage):
        def _r(state):
            called.append(stage)
            if stage == error_stage:
                raise RuntimeError(f"{stage} boom")
            return StageResult(artifact=f"new-{stage}", agent=stage)
        return _r
    return {s: make(s) for s in STAGES}


# ── fields → only matrix-dirty stages run, others reused ─────────────────────

def test_fields_script_runs_only_matrix_dirty_reusing_vlm():
    _save_all_done("d1")
    called: list[str] = []
    res = ingest.reprocess("d1", fields=["script"],
                           runners=_recording_runners(called),
                           export=lambda d: None, publish=lambda d, u: None)

    # #145 matrix for "script": model_select, kraken, reconcile, agent_b (NOT agent_c/vlm)
    assert set(res["ran"]) == {"agent_b", "model_select", "kraken", "reconcile"}
    assert "vlm" not in called and "agent_c" not in called and "agent_d" not in called
    assert res["errors"] == []
    # the reused VLM artifact is untouched
    assert RunState.load("d1").artifacts["vlm"] == "artifact-vlm"


# ── the re-run sees the human-pinned criteria ────────────────────────────────

def test_runner_receives_pinned_criteria():
    _save_all_done("d2", script="Kurrent", lang="de")
    seen: dict = {}

    runners = _recording_runners([])
    def model_select(state):
        seen.update(state.criteria)
        return StageResult(artifact="ms", agent="model_select")
    runners["model_select"] = model_select

    ingest.reprocess("d2", fields=["script"], runners=runners,
                     export=lambda d: None, publish=lambda d, u: None)
    assert seen.get("script") == "Kurrent" and seen.get("lang") == "de"


# ── a runner error halts downstream, is recorded, state saved, no publish ────

def test_runner_error_halts_downstream_and_is_recorded():
    _save_all_done("d3")
    called: list[str] = []
    published: list = []
    res = ingest.reprocess("d3", fields=["script"],
                           runners=_recording_runners(called, error_stage="kraken"),
                           export=lambda d: None,
                           publish=lambda d, u: published.append(d))

    assert "kraken" in res["errors"]
    assert "reconcile" not in res["ran"]           # downstream of kraken not run
    assert published == []                          # no publish on failure
    # the error is persisted
    assert RunState.load("d3").stage_status["kraken"] == "error"


# ── resume_pending: only pending/dirty; fully-done → nothing ─────────────────

def test_resume_pending_runs_only_pending():
    rs = _save_all_done("d4")
    rs.stage_status["agent_c"] = PENDING            # one unfinished stage
    rs.save()
    called: list[str] = []
    res = ingest.resume_pending("d4", runners=_recording_runners(called),
                                export=lambda d: None, publish=lambda d, u: None)
    assert res["ran"] == ["agent_c"] and called == ["agent_c"]


def test_resume_pending_fully_done_returns_empty_and_no_publish():
    _save_all_done("d5")
    published: list = []
    res = ingest.resume_pending("d5", runners=_recording_runners([]),
                                export=lambda d: None,
                                publish=lambda d, u: published.append(d))
    assert res["ran"] == [] and res["published"] is False and published == []


# ── publish fires exactly once on success, never when all skipped ────────────

def test_publish_and_export_fire_once_on_success():
    _save_all_done("d6")
    calls = {"export": 0, "publish": 0}
    res = ingest.reprocess("d6", fields=["script"], runners=_recording_runners([]),
                           export=lambda d: calls.__setitem__("export", calls["export"] + 1),
                           publish=lambda d, u: calls.__setitem__("publish", calls["publish"] + 1))
    assert res["published"] is True
    assert calls == {"export": 1, "publish": 1}
