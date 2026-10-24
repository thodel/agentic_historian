"""#711 — hold out whole objects of an export tree, before it goes up.

Without `eval_projects` the trainer splits pages, and two pages of one letter
share hand, paper and ink. On 2026-10-24 a script moved a seeded tenth of the
objects of every project of `lassberg-blb-gt` into `<project>_eval`; this is
that script as a module, a CLI command and a tool — idempotent, undoable, and
refusing a tree that has been uploaded.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
import hf_export as hf                     # noqa: E402
import tree_split as ts                    # noqa: E402
from mcp_atr import jobs                   # noqa: E402


def _tree(root: Path, projects: dict[str, dict[str, int]], name="blb") -> Path:
    tree = root / name
    for project, objects in projects.items():
        (tree / project / "page").mkdir(parents=True, exist_ok=True)
        for obj, pages in objects.items():
            for i in range(1, pages + 1):
                stem = f"{obj}_{i:04d}"
                (tree / project / f"{stem}.jpg").write_bytes(b"\xff\xd8")
                (tree / project / "page" / f"{stem}.xml").write_text("<PcGts/>", encoding="utf-8")
    hf.write_manifest(tree, mode="raw_xml", kind="ground-truth")
    return tree


def _files(tree: Path) -> dict[str, set[str]]:
    return {p.name: {f.name for f in p.iterdir() if f.is_file()}
            for p in tree.iterdir() if p.is_dir()}


@pytest.fixture
def tree(tmp_path):
    return _tree(tmp_path, {
        "lassberg": {f"K2911-{n}": 1 + n % 3 for n in range(1, 31)},       # 30 objects
        "korrespondenten": {f"K2912-{n}": 2 for n in range(1, 11)},        # 10 objects
    })


# ── the draw ─────────────────────────────────────────────────────────────────

def test_a_tenth_of_the_objects_moves_image_and_xml_together(tree):
    out = ts.split(tree, dry_run=False)
    las = out["projects"]["lassberg"]
    assert las["objects"] == 30 and las["eval_objects"] == 3
    moved = {f for f in (tree / "lassberg_eval").iterdir() if f.is_file()}
    xml = {f.stem for f in (tree / "lassberg_eval" / "page").iterdir()}
    assert {f.stem for f in moved} == xml, "every image went with its PAGE XML"
    objects = {ts.object_of(f.stem) for f in moved}
    assert objects == set(las["eval"])
    left = {ts.object_of(f.stem) for f in (tree / "lassberg").iterdir() if f.is_file()}
    assert not objects & left, "no object has pages on both sides"


def test_forced_objects_go_to_eval_and_count_towards_the_share(tree):
    out = ts.split(tree, force_eval=["K2911-17", "K2911-7"], dry_run=False)
    las = out["projects"]["lassberg"]
    assert {"K2911-17", "K2911-7"} <= set(las["eval"])
    assert las["eval_objects"] == 3 and las["forced"] == ["K2911-17", "K2911-7"]


def test_the_draw_is_seeded_per_project(tmp_path):
    a = ts.plan(_tree(tmp_path / "a", {"x": {f"O-{n}": 1 for n in range(40)},
                                       "y": {f"P-{n}": 1 for n in range(40)}}))
    b = ts.plan(_tree(tmp_path / "b", {"x": {f"O-{n}": 1 for n in range(40)}}))
    assert a[0].moving == b[0].moving, "adding project y left x's draw alone"
    c = ts.plan(tmp_path / "a" / "blb", seed=7)
    assert c[0].moving != a[0].moving


def test_a_second_run_moves_nothing(tree):
    ts.split(tree, force_eval=["K2911-17"], dry_run=False)
    before = _files(tree)
    again = ts.split(tree, force_eval=["K2911-17"], dry_run=False)
    assert again["pages_moved"] == 0 and _files(tree) == before


def test_the_dry_run_moves_nothing_and_says_so(tree):
    before = _files(tree)
    out = ts.split(tree)
    assert out["dry_run"] and out["projects"]["lassberg"]["pages_moving"] > 0
    assert _files(tree) == before
    assert "(dry run — nothing moved)" in ts.format_result(out)


def test_the_manifest_records_the_split_and_the_projects(tree):
    ts.split(tree, dry_run=False)
    m = hf.read_manifest(tree)
    assert m["split"]["by"].startswith("object") and m["split"]["seed"] == ts.DEFAULT_SEED
    assert m["projects"] == ["korrespondenten", "korrespondenten_eval", "lassberg", "lassberg_eval"]
    assert m["mode"] == "raw_xml", "the rest of the manifest is kept"


# ── refusals ─────────────────────────────────────────────────────────────────

def test_a_project_without_objects_in_its_names_is_refused(tmp_path):
    tree = _tree(tmp_path, {"p": {"K1-1": 1}})
    (tree / "p" / "loose.jpg").write_bytes(b"x")
    (tree / "p" / "page" / "loose.xml").write_text("<x/>")
    with pytest.raises(ts.SplitError) as err:
        ts.plan(tree)
    assert "carry no object" in str(err.value) and "loose.jpg" in str(err.value)


def test_after_an_upload_the_split_is_refused_unless_asked(tree):
    hf.record_upload(tree, "dh-unibe/x", private=True)
    with pytest.raises(ts.SplitError) as err:
        ts.split(tree, dry_run=False)
    assert "uploaded already" in str(err.value) and "dh-unibe/x" in str(err.value)
    assert ts.split(tree)["uploaded_before"] is True, "the dry run still shows the plan"
    assert ts.split(tree, dry_run=False, allow_after_upload=True)["pages_moved"] > 0


# ── undo ─────────────────────────────────────────────────────────────────────

def test_undo_puts_every_page_back(tree):
    before = _files(tree)
    ts.split(tree, dry_run=False)
    out = ts.undo(tree, dry_run=False)
    assert out["pages_back"]["lassberg"] > 0
    assert _files(tree) == before
    assert "split" not in hf.read_manifest(tree)


# ── the CLI and the tool ─────────────────────────────────────────────────────

def test_the_cli_is_a_dry_run_unless_apply(tree):
    base = [sys.executable, "-m", "agentic_historian", "split-tree", "--tree", str(tree)]
    dry = subprocess.run(base, cwd=PKG.parent, capture_output=True, text=True)
    assert dry.returncode == 0, dry.stderr
    assert "dry run" in dry.stdout and not (tree / "lassberg_eval").exists()
    real = subprocess.run(base + ["--apply", "--force-eval", "K2911-17"], cwd=PKG.parent,
                          capture_output=True, text=True)
    assert real.returncode == 0, real.stderr
    assert (tree / "lassberg_eval" / "K2911-17_0001.jpg").is_file()


def test_the_tool_takes_a_name_under_a_root(tree, monkeypatch):
    monkeypatch.setattr(config, "ATR_DATASETS_DIR", tree.parent)
    out = jobs.split_tree_job("datasets:blb", force_eval=["K2911-17"])
    assert out["dry_run"] and "K2911-17" in out["projects"]["lassberg"]["moving"]
    with pytest.raises(jobs.JobError):
        jobs.split_tree_job("datasets:blb", force_eval=["../etc"])
    with pytest.raises(jobs.JobError):
        jobs.split_tree_job(str(tree))          # a path, not a name


def test_the_upload_records_itself_in_the_manifest(tree):
    hf.record_upload(tree, "dh-unibe/image-text_lassberg-blb-gt", private=True)
    up = hf.read_manifest(tree)["uploads"]
    assert up[-1]["repo_id"] == "dh-unibe/image-text_lassberg-blb-gt" and up[-1]["private"]
    body = (PKG / "__main__.py").read_text(encoding="utf-8")
    assert "hf.record_upload(plan.tree, plan.repo_id" in body
