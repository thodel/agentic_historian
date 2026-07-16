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
    monkeypatch.setattr(orchestrator, "_publish_outputs",
                        lambda *a, **k: (True, "published to the outputs repo"))
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

    assert _phases(events) == ["vlm", "agent_b", "agent_c", "agent_d", "publish"]
    assert all(e.doc_id == "d-288" for e in events)
    assert all(e.status == "done" for e in events)
    assert [e.agent for e in events] == ["A", "B", "C", "D", "publish_github"]


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
    assert _phases(events)[-3:] == ["agent_b", "agent_c", "publish"]


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


# ── publish: the event must tell the truth about what happened ───────────────

def test_publish_event_reports_that_publishing_is_disabled(monkeypatch):
    """Publishing is opt-in. A bare "done" when nothing was published would be the
    false-green signal V-2 exists to remove — the event must say so.

    Deliberately does NOT use the `events` fixture: that fixture stubs
    _publish_outputs out, so a test using it would assert against the stub.
    """
    import utils.publish_github as pg
    monkeypatch.setattr(pg, "is_enabled", lambda: False)
    monkeypatch.setattr(pg, "publish_doc",
                        lambda *a, **k: pytest.fail("must not publish when disabled"))

    published, detail = orchestrator._publish_outputs("d-288", None)
    assert published is False and "disabled" in detail


def test_publish_failure_surfaces_in_the_event(events, tmp_path, monkeypatch):
    def boom(doc_id, source_url=None):
        return False, "GitHub 403: bad credentials"
    monkeypatch.setattr(orchestrator, "_publish_outputs", boom)

    orchestrator.run_full_pipeline(str(_img(tmp_path)), on_phase=events.append)
    pub = next(e for e in events if e.phase == "publish")

    assert "403" in pub.decision                  # the historian sees WHY it didn't land


def test_publish_never_breaks_the_run(monkeypatch):
    """_publish_outputs swallows its own errors (#200) — assert that still holds.
    Uses the real function, not the `events` fixture's stub."""
    import utils.publish_github as pg
    monkeypatch.setattr(pg, "is_enabled", lambda: True)
    monkeypatch.setattr(pg, "publish_doc",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network down")))

    published, detail = orchestrator._publish_outputs("d-288", None)
    assert published is False and "network down" in detail


# ── model_select: the choice Agent B drove is its own step ───────────────────

def test_model_select_event_names_the_chosen_model(events, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from agent_a.model_selector import RecognitionResult

    monkeypatch.setattr(orchestrator, "DUAL_AVAILABLE", True)
    monkeypatch.setattr(orchestrator, "refresh_kraken_registry", None)
    monkeypatch.setattr(orchestrator.config, "ENABLE_MULTI_ENGINE_FUSION", False)
    monkeypatch.setattr(orchestrator, "transcribe_dual", lambda *a, **k: SimpleNamespace(
        recognitions=[RecognitionResult(engine="kraken",
                                        model_id="catmus-medieval",
                                        text="unser fruntlich gruos",
                                        confidence=0.87)],
        kraken_transcription="unser fruntlich gruos",
        party_transcription="", error_kraken="", error_party=""))
    monkeypatch.setattr(orchestrator, "reconcile", lambda a, b: SimpleNamespace(
        reconciled=a, method="llm", agreement_score=0.9))

    orchestrator.run_full_pipeline(str(_img(tmp_path)), on_phase=events.append)

    ms = next(e for e in events if e.phase == "model_select")
    assert "catmus-medieval" in ms.decision and "0.87" in ms.decision
    assert _phases(events).index("model_select") < _phases(events).index("kraken")
