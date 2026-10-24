"""Tests for mcp_atr/transkribus.py — the argv the Transkribus pipeline tools build.

Same stance as test_mcp_atr.py: what reaches ``execve`` is a list built here,
and every value in it has been checked. Nothing is started; the job tests stub
``start_and_peek``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                    # noqa: E402
from mcp_atr import jobs, transkribus as tk      # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    vlm, data = tmp_path / "vlm_test", tmp_path / "data"
    vlm.mkdir(); data.mkdir()
    monkeypatch.setattr(config, "VLM_TEST_ROOT", vlm)
    monkeypatch.setattr(config, "DATA_DIR", data)
    started = []
    monkeypatch.setattr(jobs, "start_and_peek",
                        lambda kind, argv, **kw: started.append((kind, argv)) or {"job_id": "x"})
    return started


def _after(argv, flag):
    return argv[argv.index(flag) + 1]


# ── ground-truth export ──────────────────────────────────────────────────────

def test_gt_export_is_a_dry_run_unless_asked(sandbox):
    argv = tk.gt_export_argv("13922499", "3-60", "hertwig-gt", test_pages="8,17,26")
    assert argv[1] == str(tk.GT_EXPORT)
    assert _after(argv, "--pages") == "3-60" and _after(argv, "--test-pages") == "8,17,26"
    assert argv[-1] == "--dry-run"
    assert "--dry-run" not in tk.gt_export_argv("13922499", "3-60", "t", dry_run=False)
    assert "--segmentation" in tk.gt_export_argv("13922499", "3-60", "t", segmentation=True)
    assert "--segmentation" not in argv


@pytest.mark.parametrize("kw", [
    {"doc": "13922499; rm -rf /"}, {"doc": "../1"}, {"pages": "3-60 --force"},
    {"pages": "a-b"}, {"tree": "../escape"}, {"tree": "-flag"}, {"pages": ""},
])
def test_gt_export_refuses_what_is_not_a_value(sandbox, kw):
    args = {"doc": "13922499", "pages": "3-60", "tree": "t", **kw}
    with pytest.raises(jobs.JobError):
        tk.gt_export_argv(args["doc"], args["pages"], args["tree"])


# ── kraken training ──────────────────────────────────────────────────────────

def test_kraken_request_is_the_default_and_sends_nothing(sandbox):
    out = tk.kraken_train_job()
    kind, argv = sandbox[0]
    assert kind == "kraken-train-request" and argv[1:] == [str(tk.TRAIN_KRAKEN), "request"]
    assert out["action"] == "request"


def test_kraken_submit_carries_checked_params(sandbox):
    argv = tk.kraken_train_argv("submit", model_id="kraken-bern-hertwig-v2", lrate=5e-4,
                                epochs=150, base_model="10.5281/zenodo.18207767")
    assert _after(argv, "--lrate") == "0.0005" and _after(argv, "--epochs") == "150"
    assert _after(argv, "--base-model") == "10.5281/zenodo.18207767"


@pytest.mark.parametrize("kw", [
    {"action": "delete"}, {"action": "submit", "lrate": 1.0}, {"action": "submit", "epochs": 0},
    {"action": "submit", "hf_repo": "no-slash"}, {"action": "submit", "base_model": "x y"},
    {"action": "status", "job_id": "../../etc"}, {"action": "log", "job_id": "20261022T134133Z-m",
                                                   "stage": "rm"},
])
def test_kraken_refusals(sandbox, kw):
    with pytest.raises(jobs.JobError):
        tk.kraken_train_argv(**kw)


def test_kraken_status_by_job_or_newest_of_model(sandbox):
    assert tk.kraken_train_argv("status", job_id="20261022T134133Z-kraken-bern-hertwig-v2")[-1] \
        == "20261022T134133Z-kraken-bern-hertwig-v2"
    assert tk.kraken_train_argv("status", model_id="kraken-bern-hertwig-v2")[-2:] \
        == ["--model-id", "kraken-bern-hertwig-v2"]
    assert tk.kraken_train_argv("list")[-1] == "all"


# ── pipeline ─────────────────────────────────────────────────────────────────

def test_run_posts_nothing_unless_upload(sandbox):
    argv = tk.pipeline_argv("run", "hertwig-run", doc="13922499", extra_model="kraken-bern-hertwig-v2",
                            primary="D", only_extra=True, no_llm=True)
    assert argv[-1] == "--dry-run"
    assert _after(argv, "--out") == str(config.VLM_TEST_ROOT / "hertwig-run")
    assert [_after(argv, f) for f in ("--line-model", "--trocr-model", "--page-model")] == ["none"] * 3
    assert "--no-llm" in argv and _after(argv, "--primary") == "D"
    assert "--dry-run" not in tk.pipeline_argv("run", "r", doc="1", upload=True)


def test_upload_step_is_a_dry_run_unless_upload(sandbox):
    assert tk.pipeline_argv("upload", "r")[-1] == "--dry-run"
    assert "--dry-run" not in tk.pipeline_argv("upload", "r", upload=True)


@pytest.mark.parametrize("kw", [
    {"step": "shell"}, {"step": "run"},                         # doc is required for run
    {"step": "eval", "doc": "1", "primary": "C"},
    {"step": "eval", "doc": "1", "primary": "D"},              # D without a model
    {"step": "run", "doc": "1", "only_extra": True},            # nothing left to read with
    {"step": "eval", "doc": "1", "variants": "A;B"},
    {"step": "run", "doc": "1", "extra_model": "x/../y"},
    {"step": "plan", "doc": "1", "run": "../x"},
])
def test_pipeline_refusals(sandbox, kw):
    args = {"run": "r", **kw}
    step, run = args.pop("step"), args.pop("run")
    with pytest.raises(jobs.JobError):
        tk.pipeline_argv(step, run, **args)


def test_eval_variants_and_reader_override(sandbox):
    argv = tk.pipeline_argv("eval", "hertwig", doc="13922499", pages="8,17,26,39,48,55",
                            extra_model="kraken-bern-hertwig-v2", variants="A,D", page_model="none")
    assert _after(argv, "--variants") == "A,D" and _after(argv, "--page-model") == "none"
    assert "--dry-run" not in argv


def test_status_reads_counts_errors_and_eval(sandbox):
    root = config.VLM_TEST_ROOT / "hertwig-run"
    (root / "pages" / "doc1_p0001").mkdir(parents=True)
    (root / "pages" / "doc1_p0002").mkdir(parents=True)
    (root / "plan.json").write_text(json.dumps({"pages": [{}, {}]}))
    for name in ("rec_kraken-x.json", "adjudicated.json", "page.xml"):
        (root / "pages" / "doc1_p0001" / name).write_text("{}")
    (root / "pages" / "doc1_p0002" / "rec_kraken-x.error.json").write_text("{}")
    (root / "eval").mkdir()
    (root / "eval" / "eval.json").write_text(json.dumps(
        [{"page": 8, "gt_chars": 9, "D": 0.3, "D~": 0.2, "D~seg": {}}, {"page": 9, "gt_chars": 9, "D": 0.5}]))
    st = tk.pipeline_status("hertwig-run")
    assert st["planned_pages"] == 2
    assert st["on_disk"] == {"kraken-x": 1, "adjudicated": 1, "page": 1, "errors": 1}
    assert st["errors"] == ["doc1_p0002/rec_kraken-x.error.json"]
    assert st["eval_mean_cer"] == {"D": 0.4, "D~": 0.2}
    with pytest.raises(jobs.JobError):
        tk.pipeline_status("nope")


def test_argv_targets_exist():
    for script in (tk.PIPELINE, tk.GT_EXPORT, tk.TRAIN_KRAKEN):
        assert script.is_file(), script
