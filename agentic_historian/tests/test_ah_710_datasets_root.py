"""#710 — a datasets root: `upload_hf` reaches trees too large for the checkout.

On 2026-10-24 `dh-unibe/image-text_lassberg-blb-gt` (2,704 pages, 7.75 GB) went
up through the CLI, because `upload_hf` took only names under VLM_TEST_ROOT and
the tree lived on the research mount, where a tree that size belongs. And the
object counts per project — what the held-out split is drawn over — needed a
`ls | sed | sort -u` loop of their own.

`datasets:<name>` names a tree under ATR_DATASETS_DIR. The prefix picks a root;
the name after it passes the same check as every run name, so it never widens
one. The upload plan counts objects where the file names carry them.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
import hf_export as hf                     # noqa: E402
from mcp_atr import jobs                   # noqa: E402


def _tree(root: Path, name: str, projects: dict[str, list[str]]) -> Path:
    tree = root / name
    for project, stems in projects.items():
        (tree / project / "page").mkdir(parents=True, exist_ok=True)
        for stem in stems:
            (tree / project / f"{stem}.jpg").write_bytes(b"\xff\xd8")
            (tree / project / "page" / f"{stem}.xml").write_text("<PcGts/>", encoding="utf-8")
    (tree / "export.json").write_text('{"mode": "raw_xml", "kind": "ground-truth"}', encoding="utf-8")
    return tree


@pytest.fixture
def roots(tmp_path, monkeypatch):
    runs, datasets = tmp_path / "vlm_test", tmp_path / "datasets"
    runs.mkdir()
    _tree(datasets, "lassberg-blb-gt", {
        "lassberg": ["K2911-47_0001", "K2911-47_0002", "K2911-48_0001"],
        "lassberg_eval": ["K2911-17_0001"],
    })
    _tree(runs, "gemini-eval-random-hf", {"lassberg": ["a", "b"]})
    (datasets / "_oai").mkdir()                       # a cache, not a tree
    (datasets / "notes").mkdir()                      # no export.json, no page/
    monkeypatch.setattr(config, "VLM_TEST_ROOT", runs)
    monkeypatch.setattr(config, "ATR_DATASETS_DIR", datasets)
    return runs, datasets


# ── the name resolves, the path does not ─────────────────────────────────────

def test_a_datasets_name_resolves_under_the_datasets_root(roots):
    _, datasets = roots
    assert jobs.resolve_tree("datasets:lassberg-blb-gt") == datasets / "lassberg-blb-gt"


def test_a_bare_name_is_still_a_tree_under_vlm_test_root(roots):
    runs, _ = roots
    assert jobs.resolve_tree("gemini-eval-random-hf") == runs / "gemini-eval-random-hf"


@pytest.mark.parametrize("bad", ["datasets:../vlm_test", "datasets:/etc", "datasets:a/b",
                                 "datasets:", "../datasets/lassberg-blb-gt"])
def test_the_prefix_chooses_a_root_and_never_widens_it(roots, bad):
    with pytest.raises(jobs.JobError):
        jobs.resolve_tree(bad)


def test_an_unknown_tree_names_the_ones_there_are(roots):
    with pytest.raises(jobs.JobError) as err:
        jobs.resolve_tree("datasets:lassberg-blb")
    assert "lassberg-blb-gt" in str(err.value)
    assert "_oai" not in str(err.value) and "notes" not in str(err.value)


def test_without_the_setting_the_prefix_is_refused_by_name(roots, monkeypatch):
    monkeypatch.setattr(config, "ATR_DATASETS_DIR", None)
    with pytest.raises(jobs.JobError) as err:
        jobs.resolve_tree("datasets:lassberg-blb-gt")
    assert "ATR_DATASETS_DIR is not configured" in str(err.value)


def test_the_upload_job_hands_the_cli_the_resolved_tree_dry_by_default(roots, monkeypatch):
    _, datasets = roots
    seen = {}

    def _peek(kind, argv, **kw):
        seen["kind"], seen["argv"] = kind, list(argv)
        return {"done": False, "job_id": "j1", "state": "running"}

    monkeypatch.setattr(jobs, "start_and_peek", _peek)
    out = jobs.upload_hf_job("datasets:lassberg-blb-gt",
                             repo_id="dh-unibe/image-text_lassberg-blb-gt")
    assert out["ok"] and out["private"] is True
    assert out["tree"] == str(datasets / "lassberg-blb-gt")
    argv = seen["argv"]
    assert argv[argv.index("--tree") + 1] == str(datasets / "lassberg-blb-gt")
    assert "--dry-run" in argv and "--public" not in argv


# ── server_status shows the root and its trees ───────────────────────────────

def test_server_status_lists_the_trees_under_the_datasets_root(roots, monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "_checkout_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "ENV_FILES", ())
    status = jobs.server_status()
    entry = status["roots"]["datasets_dir"]
    assert entry["configured"] and entry["exists"]
    assert entry["trees"] == ["lassberg-blb-gt"]


# ── objects per project ──────────────────────────────────────────────────────

def test_objects_are_the_stem_before_the_page_suffix():
    assert hf.count_objects(["K2911-47_0001", "K2911-47_0002", "K2911-48_0001"]) == 2
    assert hf.count_objects(["A_V_1443_Hertwig_003", "A_V_1443_Hertwig_004"]) == 1


def test_a_project_whose_names_carry_no_object_has_no_count():
    assert hf.count_objects(["K2911-47_0001", "loose-page"]) is None
    assert hf.count_objects([]) is None


def test_the_upload_plan_prints_objects_beside_pages(roots):
    runs, datasets = roots
    plan = hf.inspect_upload(datasets / "lassberg-blb-gt", "dh-unibe/image-text_lassberg-blb-gt")
    assert plan.projects == {"lassberg": 3, "lassberg_eval": 1}
    assert plan.objects == {"lassberg": 2, "lassberg_eval": 1}
    text = hf.format_upload(plan)
    assert "objects   : lassberg 2, lassberg_eval 1" in text
    plain = hf.inspect_upload(runs / "gemini-eval-random-hf", "dh-unibe/x")
    assert plain.objects == {} and "objects   :" not in hf.format_upload(plain)
