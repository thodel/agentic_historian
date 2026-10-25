"""An upload stages on the dataset mount, and says beforehand whether it fits.

25.10.: the BLB tree (2704 pages, 7.75 GB) went to `pagexml-hf` twice and
twice ended at page 1280 with "No space left on device". The library builds
the dataset as Arrow files in the `datasets` cache and the parquet shards in
TMPDIR — about three times the tree — and both defaults are on tei's small
local disk, not on the mount the tree lives on.
"""

import os
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
import hf_export as hf                     # noqa: E402


def _tree(root: Path, pages: int = 2, size: int = 1000) -> Path:
    tree = root / "lassberg-blb-gt"
    (tree / "lassberg" / "page").mkdir(parents=True)
    for i in range(1, pages + 1):
        (tree / "lassberg" / f"K2911-47_{i:04d}.jpg").write_bytes(b"x" * size)
        (tree / "lassberg" / "page" / f"K2911-47_{i:04d}.xml").write_text("<PcGts/>")
    hf.write_manifest(tree, mode="raw_xml", kind="ground-truth")
    return tree


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ATR_DATASETS_DIR", tmp_path / "datasets")
    monkeypatch.setattr(config, "ATR_HF_SCRATCH", None)
    return tmp_path


def test_a_tree_under_the_datasets_root_stages_beside_it(roots):
    tree = _tree(roots / "datasets")
    assert hf.scratch_for(tree) == roots / "datasets" / "_hf_scratch"


def test_a_tree_elsewhere_keeps_the_library_defaults(roots):
    tree = _tree(roots / "vlm_test")
    assert hf.scratch_for(tree) is None


def test_the_setting_wins(roots, monkeypatch):
    monkeypatch.setattr(config, "ATR_HF_SCRATCH", roots / "big")
    assert hf.scratch_for(_tree(roots / "vlm_test")) == roots / "big"


def test_the_environment_moves_the_cache_and_tmp_and_keeps_the_rest(roots, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "t")
    env, work = hf.upload_env(roots / "s", "20261025T1")
    assert work == roots / "s" / "20261025T1"
    assert env["HF_DATASETS_CACHE"] == str(work / "datasets") and Path(env["HF_DATASETS_CACHE"]).is_dir()
    assert env["TMPDIR"] == str(work / "tmp") and env["HF_TOKEN"] == "t"
    env2, none = hf.upload_env(None, "x")
    assert none is None and env2.get("HF_DATASETS_CACHE") == os.environ.get("HF_DATASETS_CACHE")


def test_too_little_room_is_a_problem_before_the_upload(roots, monkeypatch):
    tree = _tree(roots / "datasets", size=1_000_000)
    monkeypatch.setattr(hf, "free_bytes", lambda p: 1_000_000)
    plan = hf.inspect_upload(tree, "dh-unibe/x")
    assert any("No space left on device" in p for p in plan.problems)
    assert plan.tree_bytes >= 2_000_000 and not plan.ok
    monkeypatch.setattr(hf, "free_bytes", lambda p: 10**12)
    plan = hf.inspect_upload(tree, "dh-unibe/x")
    assert not any("space" in p for p in plan.problems)
    assert "staging   : " in hf.format_upload(plan) and "_hf_scratch" in hf.format_upload(plan)


def test_the_cli_runs_pagexml_hf_in_that_environment_and_removes_the_staging():
    src = (PKG / "__main__.py").read_text(encoding="utf-8")
    body = src[src.index("def upload_hf(args"):src.index("def split_tree(args")]
    assert "hf.upload_env(plan.scratch" in body
    assert "subprocess.run(argv, env=env)" in body
    assert "shutil.rmtree(work, ignore_errors=True)" in body and "finally:" in body
