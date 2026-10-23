"""#685 — `export_run_hf` from a session.

The run → page-wise tree export (#682) existed only as a CLI, so the 2026-10-22
export of `gemini-300-final` was dictated to a terminal twice over — once as a
plan, once for real — while `upload_hf` next to it was a tool call. Same job
shape as `export_hf_job`, same two things missing from the surface: no source
or archive path from the caller, no repository.
"""
from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]


def _jobs():
    if str(PKG) not in sys.path:
        sys.path.insert(0, str(PKG))
    return importlib.import_module("mcp_atr.jobs")


def _run(tmp_path, name="gemini-300-final", model="gemini-3.8-flash"):
    d = tmp_path / "runs" / name / model
    d.mkdir(parents=True)
    (d / "k.txt").write_text("x", encoding="utf-8")
    return d.parent


def _peek_into(seen):
    def _peek(kind, argv, **kw):
        seen["kind"], seen["argv"], seen["kw"] = kind, list(argv), kw
        return {"done": False, "job_id": "j1", "state": "running"}
    return _peek


# ── the argv is the documented CLI ───────────────────────────────────────────

def test_the_argv_is_the_documented_cli():
    jobs = _jobs()
    argv = jobs.export_run_hf_argv(
        Path("/runs/g"), model="gemini-3.8-flash", out=Path("/out/tree"),
        skip_verdicts=["empty", "short"], keep_truncated=True,
        rejected_out=Path("/runs/run-rejected.tsv"), dry_run=False)

    assert argv[1:4] == ["-m", "agentic_historian", "export-run-hf"]
    assert argv[argv.index("--run-dir") + 1] == "/runs/g"
    assert argv[argv.index("--model") + 1] == "gemini-3.8-flash"
    assert argv[argv.index("--out") + 1] == "/out/tree"
    assert argv[argv.index("--skip-verdicts") + 1] == "empty,short"
    assert "--keep-truncated" in argv
    assert argv[argv.index("--rejected-out") + 1] == "/runs/run-rejected.tsv"
    assert "--dry-run" not in argv


def test_none_leaves_the_exporters_default_and_an_empty_list_refuses_nothing():
    """Two different requests, and the flag has to tell them apart."""
    jobs = _jobs()
    assert "--skip-verdicts" not in jobs.export_run_hf_argv(Path("/r"))
    argv = jobs.export_run_hf_argv(Path("/r"), skip_verdicts=[])
    assert argv[argv.index("--skip-verdicts") + 1] == ""


def test_no_source_archive_or_repository_comes_from_the_caller():
    """The source and the cold tier default to the configuration; the
    repository is `upload_hf`'s to name. None of the three is on the surface."""
    jobs = _jobs()
    argv = jobs.export_run_hf_argv(Path("/r"), dry_run=False)
    assert "--source" not in argv and "--archive" not in argv
    assert "--repo-id" not in argv
    params = inspect.signature(jobs.export_run_hf_job).parameters
    assert not {"source", "archive", "repo_id"} & set(params)


# ── the job ──────────────────────────────────────────────────────────────────

def test_a_dry_run_is_the_default_and_writes_nowhere(monkeypatch, tmp_path):
    jobs = _jobs()
    monkeypatch.setattr(jobs.config, "VLM_TEST_ROOT", tmp_path / "runs")
    _run(tmp_path)
    seen = {}
    monkeypatch.setattr(jobs, "start_and_peek", _peek_into(seen))

    result = jobs.export_run_hf_job("gemini-300-final")

    assert seen["kind"] == "export-run-hf"
    assert "--dry-run" in seen["argv"] and "--out" not in seen["argv"]
    assert result["out_dir"] is None and result["tree"] is None
    assert result["mode"] == "text"
    assert seen["kw"].get("run") == "gemini-300-final"


def test_writing_names_the_tree_under_the_root_for_upload_hf(monkeypatch, tmp_path):
    """`<run>-pagewise` by default — the name `upload_hf` takes as `tree`."""
    jobs = _jobs()
    monkeypatch.setattr(jobs.config, "VLM_TEST_ROOT", tmp_path / "runs")
    _run(tmp_path)
    seen = {}
    monkeypatch.setattr(jobs, "start_and_peek", _peek_into(seen))

    result = jobs.export_run_hf_job("gemini-300-final", dry_run=False)

    assert result["tree"] == "gemini-300-final-pagewise"
    assert result["out_dir"] == str(tmp_path / "runs" / "gemini-300-final-pagewise")
    assert seen["argv"][seen["argv"].index("--out") + 1] == result["out_dir"]
    assert "--dry-run" not in seen["argv"]


def test_an_explicit_tree_name_is_a_name_not_a_path(monkeypatch, tmp_path):
    jobs = _jobs()
    monkeypatch.setattr(jobs.config, "VLM_TEST_ROOT", tmp_path / "runs")
    _run(tmp_path)
    monkeypatch.setattr(jobs, "start_and_peek", _peek_into({}))

    with pytest.raises(jobs.JobError):
        jobs.export_run_hf_job("gemini-300-final", out="../../etc", dry_run=False)
    with pytest.raises(jobs.JobError):
        jobs.export_run_hf_job("gemini-300-final", out="../../etc")   # dry run too

    result = jobs.export_run_hf_job("gemini-300-final", out="pagewise-v2",
                                    dry_run=False)
    assert result["out_dir"] == str(tmp_path / "runs" / "pagewise-v2")


def test_a_tree_that_holds_files_is_refused_before_any_job_starts(monkeypatch, tmp_path):
    """The CLI refuses it too — but a job that fails on its first line is a
    worse answer than no job."""
    jobs = _jobs()
    monkeypatch.setattr(jobs.config, "VLM_TEST_ROOT", tmp_path / "runs")
    _run(tmp_path)
    tree = tmp_path / "runs" / "gemini-300-final-pagewise"
    tree.mkdir()
    (tree / "export.json").write_text("{}", encoding="utf-8")
    started = []
    monkeypatch.setattr(jobs, "start_and_peek",
                        lambda *a, **k: started.append(a) or {})

    with pytest.raises(jobs.JobError) as err:
        jobs.export_run_hf_job("gemini-300-final", dry_run=False)
    assert "already holds files" in str(err.value)
    assert started == []


def test_the_refusals_are_always_written_under_the_root(monkeypatch, tmp_path):
    """Dry run included, and not /tmp: the plan prints a summary, and the list is
    what the summary is a claim about (#535: /tmp did not survive a reboot)."""
    jobs = _jobs()
    monkeypatch.setattr(jobs.config, "VLM_TEST_ROOT", tmp_path / "runs")
    _run(tmp_path)
    seen = {}
    monkeypatch.setattr(jobs, "start_and_peek", _peek_into(seen))

    result = jobs.export_run_hf_job("gemini-300-final")

    rejected = seen["argv"][seen["argv"].index("--rejected-out") + 1]
    assert rejected == result["rejected"]
    assert Path(rejected).parent == tmp_path / "runs"
    assert Path(rejected).name.startswith("run-rejected-")


def test_a_missing_run_or_model_is_an_error_not_a_job(monkeypatch, tmp_path):
    jobs = _jobs()
    monkeypatch.setattr(jobs.config, "VLM_TEST_ROOT", tmp_path / "runs")
    _run(tmp_path)
    started = []
    monkeypatch.setattr(jobs, "start_and_peek",
                        lambda *a, **k: started.append(a) or {})

    with pytest.raises(jobs.JobError):
        jobs.export_run_hf_job("no-such-run")
    with pytest.raises(jobs.JobError):
        jobs.export_run_hf_job("gemini-300-final", model="qwen-nobody")
    with pytest.raises(jobs.JobError):
        jobs.export_run_hf_job("../../etc")
    assert started == []


def test_the_model_narrows_the_run(monkeypatch, tmp_path):
    jobs = _jobs()
    monkeypatch.setattr(jobs.config, "VLM_TEST_ROOT", tmp_path / "runs")
    _run(tmp_path)
    seen = {}
    monkeypatch.setattr(jobs, "start_and_peek", _peek_into(seen))

    result = jobs.export_run_hf_job("gemini-300-final", model="gemini-3.8-flash")

    assert seen["argv"][seen["argv"].index("--model") + 1] == "gemini-3.8-flash"
    assert result["model"] == "gemini-3.8-flash"


# ── which verdicts may be refused ────────────────────────────────────────────

def test_ok_cannot_be_refused_and_a_typo_is_not_silently_nothing(monkeypatch, tmp_path):
    """A verdict the exporter does not know would match no page — the plan would
    look like `short` was kept on purpose."""
    jobs = _jobs()
    monkeypatch.setattr(jobs.config, "VLM_TEST_ROOT", tmp_path / "runs")
    _run(tmp_path)
    monkeypatch.setattr(jobs, "start_and_peek", _peek_into({}))

    with pytest.raises(jobs.JobError) as err:
        jobs.export_run_hf_job("gemini-300-final", skip_verdicts=["ok"])
    assert "ok" in str(err.value)
    with pytest.raises(jobs.JobError) as err:
        jobs.export_run_hf_job("gemini-300-final", skip_verdicts=["empty", "shrot"])
    assert "shrot" in str(err.value)


def test_the_refusable_verdicts_are_the_page_quality_ones_minus_ok():
    jobs = _jobs()
    from page_quality import VERDICTS
    assert set(jobs.RUN_EXPORT_REFUSABLE) == set(VERDICTS) - {"ok"}


def test_short_can_be_refused_and_blanks_are_dropped(monkeypatch, tmp_path):
    jobs = _jobs()
    monkeypatch.setattr(jobs.config, "VLM_TEST_ROOT", tmp_path / "runs")
    _run(tmp_path)
    seen = {}
    monkeypatch.setattr(jobs, "start_and_peek", _peek_into(seen))

    jobs.export_run_hf_job("gemini-300-final",
                           skip_verdicts=["empty", " short ", ""],
                           keep_truncated=True)

    assert seen["argv"][seen["argv"].index("--skip-verdicts") + 1] == "empty,short"
    assert "--keep-truncated" in seen["argv"]


# ── the tool is on the server ────────────────────────────────────────────────

def test_the_server_registers_the_tool_next_to_export_hf():
    src = (PKG / "mcp_atr" / "server.py").read_text(encoding="utf-8")
    assert "def export_run_hf(" in src
    assert src.index("def export_hf(") < src.index("def export_run_hf(") \
        < src.index("def upload_hf(")
