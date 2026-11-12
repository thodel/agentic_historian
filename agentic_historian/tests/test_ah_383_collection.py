"""#383: the holding travels from the batch runner to the catalogue partition.

The output catalogue (agentic-historian-outputs#239) partitions its index by a
``collection`` field in each ``pipeline.json`` and falls back to the publication
year when there is none. Nothing on this side ever wrote the field, so a corpus
of two thousand documents from one holding would have been filed under "2026".

What is asserted here:

* ``RunState.collection`` round-trips, and an older run state without it loads;
* ``pipeline.json`` carries ``collection`` on both write paths — derived from
  the RunState (single ``/run``, re-derive after ``/reprocess``) and from the
  context (the order pipeline) — and omits the key when there is no holding;
* the batch runner files every document under the folder it was pointed at,
  a named holding wins, and ``""`` means none;
* ``batch --collection`` reaches ``run_batch``.

A mailbox order (``ingest.run_switchdrive_orders``) carries none: the mailbox
lists one level of folders, so there is no holding between it and an order.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_383_collection.py
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import batch_runner as br
import config
import corpus_manifest as cm
import orchestrator as orch
from runstate import RunState


@pytest.fixture(autouse=True)
def _engines_are_up(monkeypatch):
    """The runner refuses to start when a planned engine is down (#599);
    these tests are about what it files, not about the gateway."""
    import atr_engines
    monkeypatch.setattr(atr_engines, "check_sync",
                        lambda needed=None, **kw: atr_engines.Preflight(
                            needed=tuple(needed or atr_engines.PLANNED_ENGINES)))


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(config, "META_LOG_PATH",
                        tmp_path / "outputs" / "meta_agent_log.json")
    monkeypatch.setattr(config, "ENABLE_GITHUB_PUBLISH", False)
    cm.close()
    yield tmp_path
    cm.close()


def _pages(root: Path, names=("a.jpg", "b.jpg")) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        (root / name).write_bytes(b"\xff\xd8\xff")
    return root


def _pipeline_json(doc_id: str) -> dict:
    return json.loads((config.OUTPUTS_DIR / f"{doc_id}_pipeline.json")
                      .read_text(encoding="utf-8"))


# ── RunState ──────────────────────────────────────────────────────────────────

def test_the_run_state_round_trips_the_holding():
    RunState(doc_id="d", collection="Lassberg").save()

    assert RunState.load("d").collection == "Lassberg"


def test_an_older_run_state_without_a_holding_still_loads():
    """Every run state written before this field exists; none may stop loading."""
    path = RunState._path("old")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"doc_id": "old"}), encoding="utf-8")

    assert RunState.load("old").collection is None


# ── pipeline.json ─────────────────────────────────────────────────────────────

def test_the_context_carries_the_holding_only_when_it_has_one():
    ctx = orch.PipelineContext("d")
    assert "collection" not in ctx.to_json()

    ctx.collection = "Lassberg"
    assert ctx.to_json()["collection"] == "Lassberg"


def test_pipeline_json_derived_from_the_run_state_carries_its_holding():
    """The re-derive path (`ingest._default_export` after a `/reprocess`) comes
    with an empty context, so the RunState's own value must be enough."""
    RunState(doc_id="d", collection="Lassberg").save()

    orch._save_pipeline_result("d", orch.PipelineContext("d"), from_runstate=True)

    assert _pipeline_json("d")["collection"] == "Lassberg"


def test_the_context_holding_wins_over_the_run_state():
    RunState(doc_id="d", collection="stale").save()
    ctx = orch.PipelineContext("d")
    ctx.collection = "Lassberg"

    orch._save_pipeline_result("d", ctx, from_runstate=True)

    assert _pipeline_json("d")["collection"] == "Lassberg"


def test_no_holding_means_no_key():
    """An absent key is what the catalogue reads as "derive it" — an empty
    string would be a holding called nothing."""
    RunState(doc_id="d").save()

    orch._save_pipeline_result("d", orch.PipelineContext("d"), from_runstate=True)
    assert "collection" not in _pipeline_json("d")

    orch._save_pipeline_result("d", orch.PipelineContext("d"))
    assert "collection" not in _pipeline_json("d")


def test_the_order_pipeline_json_carries_the_holding():
    """The group pipeline writes from the context, not from the RunState."""
    ctx = orch.PipelineContext("order-1")
    ctx.collection = "Marbach"

    orch._save_pipeline_result("order-1", ctx)

    assert _pipeline_json("order-1")["collection"] == "Marbach"


def test_both_entry_points_take_a_holding():
    for fn in (orch.run_full_pipeline, orch.run_full_pipeline_group):
        param = inspect.signature(fn).parameters.get("collection")
        assert param is not None and param.default == "", fn.__name__


# ── batch runner ──────────────────────────────────────────────────────────────

def test_the_default_pipeline_hands_the_holding_to_both_entry_points(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(orch, "run_full_pipeline",
                        lambda *a, **kw: seen.__setitem__("pages", kw))
    monkeypatch.setattr(orch, "run_full_pipeline_group",
                        lambda *a, **kw: seen.__setitem__("orders", kw))

    br._default_pipeline("d", [Path("x.jpg")], br.PAGES, collection="Lassberg")
    br._default_pipeline("o", [Path("x.jpg")], br.ORDERS, collection="Lassberg")

    assert seen["pages"]["collection"] == "Lassberg"
    assert seen["orders"]["collection"] == "Lassberg"


def test_a_batch_run_files_its_documents_under_the_folder_it_was_pointed_at(
        monkeypatch, tmp_path):
    root = _pages(tmp_path / "Lassberg")
    seen: list = []
    monkeypatch.setattr(br, "_default_pipeline",
                        lambda d, p, m, **kw: seen.append(kw.get("collection")))

    br.run_batch("run", br.inspect_source(root), workers=1,
                 publish=lambda ids, label: None,
                 sleep=lambda _s: None, heartbeat_every=0.01)

    assert seen == ["Lassberg", "Lassberg"]


def test_a_named_holding_wins_and_an_empty_one_means_none(monkeypatch, tmp_path):
    root = _pages(tmp_path / "scratch", ("a.jpg",))
    seen: list = []
    monkeypatch.setattr(br, "_default_pipeline",
                        lambda d, p, m, **kw: seen.append(kw.get("collection")))

    br.run_batch("run-named", br.inspect_source(root), workers=1,
                 collection="Marbach", publish=lambda ids, label: None,
                 sleep=lambda _s: None, heartbeat_every=0.01)
    br.run_batch("run-none", br.inspect_source(root), workers=1,
                 collection="", publish=lambda ids, label: None,
                 sleep=lambda _s: None, heartbeat_every=0.01)

    assert seen == ["Marbach", ""]


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli():
    spec = importlib.util.spec_from_file_location("ah_cli", PKG / "__main__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_cli_defaults_the_holding_to_the_folder_and_takes_a_name(
        monkeypatch, tmp_path, capsys):
    cli = _cli()
    root = _pages(tmp_path / "Lassberg", ("a.jpg",))
    seen: list = []

    def run_batch(run_id, source, **kw):
        seen.append(kw.get("collection"))
        return br.Summary(run_id=run_id)
    monkeypatch.setattr(br, "run_batch", run_batch)

    args = cli.build_parser().parse_args(["batch", str(root)])
    assert args.func(args) == 0
    args = cli.build_parser().parse_args(
        ["batch", str(root), "--collection", "Marbach"])
    assert args.func(args) == 0
    args = cli.build_parser().parse_args(["batch", str(root), "--collection", ""])
    assert args.func(args) == 0

    assert seen == ["Lassberg", "Marbach", ""]
    out = capsys.readouterr().out
    assert "collection: Lassberg" in out and "collection: Marbach" in out
    assert "collection: (none)" in out
