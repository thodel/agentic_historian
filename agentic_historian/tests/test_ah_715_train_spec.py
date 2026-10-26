"""#715 — a training request from a spec file, pinned, for any engine.

`training/specs/` holds three fine-tunes on `lassberg-blb-gt`, written before
the dataset was uploaded, so their BLB entries say `revision: null` — which
the trainer reads as "whatever main is when I fetch". The tool pins it, drops
the `_note` keys, refuses a second job for one model id, and leaves UBELIX
submission to a person.
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import hf_export as hf                     # noqa: E402
import train_spec as tsp                   # noqa: E402
from mcp_atr import jobs                   # noqa: E402

BLB = "dh-unibe/image-text_lassberg-blb-gt"
SHA = "0123456789abcdef0123456789abcdef01234567"


# ── the specs in the repository ──────────────────────────────────────────────

@pytest.mark.parametrize("name", ["blb-qwen35-page-v1", "blb-olmocr2-page-v1", "blb-kraken-v1"])
def test_the_three_blb_specs_prepare_cleanly(name, monkeypatch):
    monkeypatch.setattr(tsp, "resolve_base", lambda base: (base, None))
    prepared = tsp.prepare(tsp.load(name), resolve=lambda repo: SHA if repo == BLB else None)
    assert prepared["ok"], prepared["problems"]
    assert prepared["pinned"] == {BLB: SHA}
    blb = next(d for d in prepared["body"]["datasets"] if d["hf_repo"] == BLB)
    assert blb["revision"] == SHA
    assert all(p.endswith("_eval") for p in blb["eval_projects"])
    assert "drucke" not in blb["train_projects"], "the printed almanac stays out"
    assert "_note" not in json.dumps(prepared["body"])


def test_vlm_specs_train_on_pages_and_kraken_from_the_xix_model():
    q = tsp.load("blb-qwen35-page-v1")
    assert q["engine"] == "vllm" and q["params"]["granularity"] == "page"
    o = tsp.load("blb-olmocr2-page-v1")
    assert o["engine"] == "vllm" and o["params"]["granularity"] == "page"
    k = tsp.load("blb-kraken-v1")
    assert k["engine"] == "kraken" and k["base_model"] == "trained:kraken-german-xix-v1"


def test_a_trained_base_becomes_its_file_and_a_missing_file_stops_the_request(tmp_path, monkeypatch):
    from ruemlang import train_kraken
    monkeypatch.setattr(train_kraken, "TRAINED_ROOT", str(tmp_path))
    spec = {"engine": "kraken", "model_id": "m", "base_model": "trained:kraken-german-xix-v1",
            "datasets": [{"hf_repo": BLB, "revision": "abc"}]}
    missing = tsp.prepare(spec)
    assert not missing["ok"] and "no file at" in missing["problems"][0]
    f = tmp_path / "kraken-german-xix-v1" / "kraken-german-xix-v1.mlmodel"
    f.parent.mkdir()
    f.write_bytes(b"model")
    ok = tsp.prepare(spec)
    assert ok["ok"] and ok["body"]["base_model"] == str(f)
    bad = tsp.prepare(dict(spec, base_model="trained:../../etc"))
    assert not bad["ok"]
    plain = tsp.prepare(dict(spec, base_model="kraken-cursive_inzigkofen"))
    assert plain["body"]["base_model"] == "kraken-cursive_inzigkofen"


# ── preparing ────────────────────────────────────────────────────────────────

def test_an_unresolvable_null_revision_is_a_problem_not_a_silent_main():
    spec = {"engine": "vllm", "model_id": "m", "datasets": [{"hf_repo": BLB, "revision": None}]}
    out = tsp.prepare(spec, resolve=lambda repo: None)
    assert not out["ok"] and "revision is null" in out["problems"][0]


def test_a_pinned_revision_is_left_alone():
    spec = {"engine": "kraken", "model_id": "m", "datasets": [{"hf_repo": BLB, "revision": "abc"}]}
    out = tsp.prepare(spec, resolve=lambda repo: pytest.fail("resolved a pinned revision"))
    assert out["ok"] and out["pinned"] == {} and out["body"]["datasets"][0]["revision"] == "abc"


def test_train_and_eval_projects_must_not_overlap():
    spec = {"engine": "kraken", "model_id": "m",
            "datasets": [{"hf_repo": BLB, "revision": "abc", "train_projects": ["a", "b"],
                          "eval_projects": ["b"]}]}
    assert "train and eval" in tsp.prepare(spec)["problems"][0]


def test_unknown_engine_and_missing_model_id_are_named():
    out = tsp.prepare({"engine": "magic", "datasets": [{"hf_repo": BLB, "revision": "x"}]})
    assert any("engine" in p for p in out["problems"]) and any("model_id" in p for p in out["problems"])


@pytest.mark.parametrize("bad", ["../etc/passwd", "/tmp/x", "a b"])
def test_a_spec_is_a_name_under_the_directory(bad):
    with pytest.raises(tsp.SpecError):
        tsp.load(bad)


def test_ubelix_is_printed_never_submitted():
    body = {"engine": "kraken", "model_id": "kraken-german-xix-blb-v1", "datasets": []}
    state = {"blb-kraken-v1": {"file": "blb-kraken-v1@c25587d.json", "present": True, "same": True}}
    cmds = "\n".join(tsp.ubelix_commands([("blb-kraken-v1", body)], state))
    assert "prepare.sbatch" in cmds and "train.sbatch" in cmds and "curl -fsSL" in cmds


# ── the hub names its commit ─────────────────────────────────────────────────

def test_hub_state_carries_the_commit(monkeypatch):
    class _R:
        status_code = 200

        def json(self):
            return {"private": True, "sha": SHA, "cardData": {"dataset_info": {
                "splits": [{"num_examples": 2704}], "dataset_size": 7, "features": [{"name": "image"}]}}}

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _R())
    state = hf.hub_state(BLB, token="t")
    assert state["sha"] == SHA and state["rows"] == 2704
    assert f"revision  : {SHA}" in hf.format_hub_state(state)


# ── the job ──────────────────────────────────────────────────────────────────

def test_the_job_takes_a_spec_name_and_an_action():
    argv = jobs.train_spec_argv("blb-kraken-v1")
    assert argv[3:] == ["train-spec", "--spec", "blb-kraken-v1", "--action", "request"]
    assert jobs.train_spec_argv("blb-kraken-v1.json", "submit")[-1] == "submit"
    with pytest.raises(jobs.JobError):
        jobs.train_spec_argv("no-such-spec")
    with pytest.raises(jobs.JobError):
        jobs.train_spec_argv("blb-kraken-v1", "delete")
    with pytest.raises(jobs.JobError):
        jobs.train_spec_argv("blb-kraken-v1", "submit", job_id="20261022T134133Z-x")
    st = jobs.train_spec_argv("blb-kraken-v1", "status", job_id="20261022T134133Z-kraken-x")
    assert st[-2:] == ["--job-id", "20261022T134133Z-kraken-x"]
