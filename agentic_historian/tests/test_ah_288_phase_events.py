"""#288 (V-2): every orchestrator step emits a PhaseEvent with a real excerpt.

The u-17__/BAT failures were invisible while running: the pipeline logged "fertig"
and only the merged text ever surfaced. V-2 makes each step announce itself —
which agent ran, whether it worked, and the first lines of what it produced — so
#289 can put that on a Discord board.

Offline — every agent and the publish/persist side-effects are stubbed. Run from
the repo root:
    pytest agentic_historian/tests/test_ah_288_phase_events.py
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import orchestrator  # noqa: E402


TRANSCRIPTION = ("unser fruntlich gruos vor liebe getruwe\n"
                 "von der stoesse wegen zwuschen\n"
                 "den von underwalden und uns\n"
                 "vierte zeile die nicht erscheinen darf")
DESCRIPTION = {"source_description": "Urkunde, Kurrent, 15. Jh.",
               "source_json": {"schrift": "Kurrent", "jahrhundert": "15"}}
ENTITIES = {"entities": [{"name": "Underwalden", "type": "place"},
                         {"name": "Bern", "type": "place"}]}


@pytest.fixture
def events(monkeypatch, tmp_path):
    """Run the pipeline with all agents stubbed; collect the emitted events."""
    monkeypatch.setattr(orchestrator, "DUAL_AVAILABLE", False)
    monkeypatch.setattr(orchestrator.agent_a, "process_file",
                        lambda img, **k: {"transcription": TRANSCRIPTION, "qa_score": 0.82})
    monkeypatch.setattr(orchestrator.agent_a, "transcribe_image",
                        lambda img, **k: {"transcription": TRANSCRIPTION, "qa_score": 0.82})
    monkeypatch.setattr(orchestrator.agent_a, "save_transcription",
                        lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.agent_b, "describe", lambda **k: DESCRIPTION)
    monkeypatch.setattr(orchestrator.agent_c, "extract_entities",
                        lambda *a, **k: ENTITIES)
    monkeypatch.setattr(orchestrator.agent_d, "analyse_corpus", lambda **k: {})
    monkeypatch.setattr(orchestrator, "_save_pipeline_result", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_publish_outputs", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.config, "DATA_DIR", tmp_path)
    return []


def _img(tmp_path, name="d-288.jpg"):
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff")          # not a real JPEG; agents are stubbed
    return p


def _phases(evs):
    return [e.phase for e in evs]


# ── run_full_pipeline ────────────────────────────────────────────────────────

def test_each_step_emits_an_event_in_order(events, tmp_path, monkeypatch):
    orchestrator.run_full_pipeline(str(_img(tmp_path)), run_agent_d=True,
                                   on_phase=events.append)

    assert _phases(events) == ["vlm", "agent_b", "agent_c", "agent_d"]
    assert all(e.doc_id == "d-288" for e in events)
    assert all(e.status == "done" for e in events)
    assert [e.agent for e in events] == ["A", "B", "C", "D"]


def test_excerpt_carries_the_first_lines_of_the_output(events, tmp_path):
    orchestrator.run_full_pipeline(str(_img(tmp_path)), on_phase=events.append)
    vlm = next(e for e in events if e.phase == "vlm")

    assert "unser fruntlich gruos" in vlm.excerpt          # the actual reading
    assert "vierte zeile" not in vlm.excerpt               # only the first 3 lines
    assert "qa=0.82" in vlm.decision


def test_entity_excerpt_names_the_entities(events, tmp_path):
    orchestrator.run_full_pipeline(str(_img(tmp_path)), on_phase=events.append)
    c = next(e for e in events if e.phase == "agent_c")

    assert "Underwalden" in c.excerpt                      # first entries, not "{...}"
    assert "2 entities" in c.decision


def test_a_failing_step_emits_an_error_event_and_the_run_continues(events, tmp_path,
                                                                   monkeypatch):
    def boom(**k):
        raise RuntimeError("GPUStack 503")
    monkeypatch.setattr(orchestrator.agent_b, "describe", boom)

    result = orchestrator.run_full_pipeline(str(_img(tmp_path)),
                                            on_phase=events.append)

    b = next(e for e in events if e.phase == "agent_b")
    assert b.status == "error" and "503" in b.error
    assert "agent_c" in _phases(events)                    # C still ran
    assert result["transcription"]                          # pipeline completed


def test_a_raising_callback_never_breaks_the_pipeline(events, tmp_path):
    """Observability must not become a new failure mode."""
    def bad_sink(ev):
        raise RuntimeError("discord down")

    result = orchestrator.run_full_pipeline(str(_img(tmp_path)), on_phase=bad_sink)
    assert result["transcription"] == TRANSCRIPTION


def test_default_is_unchanged_no_callback_no_crash(events, tmp_path):
    """Without on_phase the events go to the log — callers that never opted in
    behave exactly as before."""
    result = orchestrator.run_full_pipeline(str(_img(tmp_path)))
    assert result["transcription"] == TRANSCRIPTION


# ── run_full_pipeline_group ──────────────────────────────────────────────────

def test_grouped_run_emits_one_event_per_page(events, tmp_path):
    pages = [_img(tmp_path, "p1.jpg"), _img(tmp_path, "p2.jpg")]
    orchestrator.run_full_pipeline_group("order-288", [str(p) for p in pages],
                                         on_phase=events.append)

    vlm = [e for e in events if e.phase == "vlm"]
    assert len(vlm) == 2
    assert {"p1.jpg", "p2.jpg"} == {e.decision.split(" ·")[0] for e in vlm}
    assert _phases(events)[-2:] == ["agent_b", "agent_c"]


def test_grouped_page_failure_is_reported_per_page(events, tmp_path, monkeypatch):
    def flaky(img, **k):
        if img.name == "p2.jpg":
            raise RuntimeError("VLM timeout")
        return {"transcription": TRANSCRIPTION, "qa_score": 0.82}
    monkeypatch.setattr(orchestrator.agent_a, "transcribe_image", flaky)

    orchestrator.run_full_pipeline_group("order-288",
                                         [str(_img(tmp_path, "p1.jpg")),
                                          str(_img(tmp_path, "p2.jpg"))],
                                         on_phase=events.append)

    failed = [e for e in events if e.status == "error"]
    assert len(failed) == 1
    assert failed[0].decision == "p2.jpg" and "timeout" in failed[0].error
