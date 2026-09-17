"""Publishing a finished run directory to a GitHub repo (utils/publish_github.publish_tree).

Offline: ``_commit_files`` is replaced, so nothing here reaches the GitHub API.
Run from the repo root::

    pytest agentic_historian/tests/test_ah_publish_tree.py
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                   # noqa: E402
from utils import publish_github as pub         # noqa: E402


@pytest.fixture
def commits(monkeypatch):
    """Capture what would have been committed, instead of committing it."""
    seen: list[dict] = []

    def _fake(files, message, session=None, *, repo=None, branch=None, base_branch=None):
        seen.append({"files": dict(files), "message": message,
                     "repo": repo, "branch": branch})
        return f"https://github.test/commit/{len(seen)}"

    monkeypatch.setattr(pub, "_commit_files", _fake)
    monkeypatch.setattr(pub, "_session", lambda: object())
    monkeypatch.setattr(config, "GITHUB_TOKEN", "ghp_test")
    return seen


def make_run(root: Path) -> Path:
    """A run directory shaped like one atr_batch produces."""
    for model in ("model-a", "model-b"):
        d = root / model
        d.mkdir(parents=True)
        (d / "letter-01__001.txt").write_text("transcription")
        (d / "letter-01__001.json").write_text('{"schema": "atr-batch/1"}')
    (root / "report.md").write_text("# report")
    (root / "manifest.jsonl").write_text('{"key": "letter-01__001"}\n')
    return root


def test_the_tree_is_preserved_under_the_path_prefix(commits, tmp_path):
    run = make_run(tmp_path / "atr_test_lassberg")
    urls = pub.publish_tree(run, repo="owner/name", path_prefix="data/vlm-outputs/run1")

    assert urls == ["https://github.test/commit/1"]
    assert sorted(commits[0]["files"]) == [
        "data/vlm-outputs/run1/manifest.jsonl",
        "data/vlm-outputs/run1/model-a/letter-01__001.json",
        "data/vlm-outputs/run1/model-a/letter-01__001.txt",
        "data/vlm-outputs/run1/model-b/letter-01__001.json",
        "data/vlm-outputs/run1/model-b/letter-01__001.txt",
        "data/vlm-outputs/run1/report.md",
    ]
    assert commits[0]["repo"] == "owner/name"


def test_source_images_are_never_published(commits, tmp_path):
    """The one mistake that cannot be taken back. These trees sit next to the
    digitised holdings they were produced from, and an allowlist is the only
    direction it is safe to be wrong in — an unanticipated format is skipped, not
    committed."""
    run = make_run(tmp_path / "run")
    (run / "model-a" / "page.jpg").write_bytes(b"\xff\xd8scan")
    (run / "model-a" / "page.tif").write_bytes(b"II*\x00scan")
    (run / "model-a" / "notes.docx").write_bytes(b"PK\x03\x04")

    pub.publish_tree(run, repo="owner/name", path_prefix="p")
    published = " ".join(commits[0]["files"])
    for leaked in (".jpg", ".tif", ".docx"):
        assert leaked not in published


def test_content_is_the_file_on_disk(commits, tmp_path):
    run = make_run(tmp_path / "run")
    pub.publish_tree(run, repo="owner/name", path_prefix="p")
    assert commits[0]["files"]["p/model-a/letter-01__001.txt"] == b"transcription"


def test_a_large_tree_is_split_into_several_commits(commits, tmp_path):
    """A file costs an API call, so a thousand-file tree is a thousand requests.
    In one commit, a failure at request 900 publishes nothing at all; chunked, the
    finished chunks stay published and only the rest has to be retried."""
    run = tmp_path / "run"
    (run / "m").mkdir(parents=True)
    for i in range(25):
        (run / "m" / f"p{i:03d}.txt").write_text(str(i))

    urls = pub.publish_tree(run, repo="owner/name", path_prefix="p", chunk=10)
    assert len(urls) == 3
    assert [len(c["files"]) for c in commits] == [10, 10, 5]
    assert "(1/3)" in commits[0]["message"] and "(3/3)" in commits[2]["message"]
    # every file lands exactly once
    assert len({f for c in commits for f in c["files"]}) == 25


def test_publishing_without_a_token_is_an_error_not_a_silent_skip(commits, tmp_path,
                                                                  monkeypatch):
    monkeypatch.setattr(config, "GITHUB_TOKEN", "")
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        pub.publish_tree(make_run(tmp_path / "run"), repo="owner/name", path_prefix="p")


def test_publishing_is_not_gated_on_the_pipeline_publish_flag(commits, tmp_path,
                                                              monkeypatch):
    """ENABLE_GITHUB_PUBLISH exists so the pipeline does not publish as a side
    effect of processing. Calling this is not a side effect of anything."""
    monkeypatch.setattr(config, "ENABLE_GITHUB_PUBLISH", False)
    assert pub.publish_tree(make_run(tmp_path / "run"), repo="owner/name", path_prefix="p")


def test_a_directory_with_nothing_publishable_commits_nothing(commits, tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "page.jpg").write_bytes(b"scan")
    assert pub.publish_tree(run, repo="owner/name", path_prefix="p") == []
    assert commits == []


def test_a_missing_run_directory_says_so(commits, tmp_path):
    with pytest.raises(FileNotFoundError):
        pub.publish_tree(tmp_path / "nope", repo="owner/name", path_prefix="p")


# ── the destination is a decision, not a command-line argument (#lassberg) ────

def _cli():
    """The CLI module, loaded by path.

    ``__main__.py`` is not importable by name from inside the package — it *is*
    the entry point — so it is loaded from its file, the way ``python -m`` would.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("ah_cli", PKG / "__main__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_publish_batch_push_uses_the_configured_destination(commits, tmp_path):
    """Recognised text has exactly one home, and it is named in config.

    The scans stay in the Nextcloud share — mounted, never copied into git — and
    the readings go to the edition repository. Requiring ``--repo`` on every
    publish meant the destination lived in whoever's shell history ran it last.
    (``--push`` is the direct-commit path; the default is a pull request, covered
    in ``test_ah_publish_pr.py``.)
    """
    mod = _cli()

    run_dir = make_run(tmp_path / "atr_trocr_corpus")
    args = mod.build_parser().parse_args(
        ["publish-batch", "--run-dir", str(run_dir), "--push"])
    assert mod.publish_batch(args) == 0

    assert commits, "nothing was published"
    assert commits[0]["repo"] == config.GITHUB_TEXT_REPO == "michaelscho/lassberg"
    assert commits[0]["branch"] == "main"
    assert all(p.startswith("data/textrecognition/") for p in commits[0]["files"])


def test_publish_batch_still_takes_an_explicit_destination(commits, tmp_path):
    mod = _cli()

    run_dir = make_run(tmp_path / "run")
    args = mod.build_parser().parse_args([
        "publish-batch", "--run-dir", str(run_dir), "--push",
        "--repo", "someone/else", "--path", "elsewhere", "--branch", "wip",
    ])
    assert mod.publish_batch(args) == 0

    assert commits[0]["repo"] == "someone/else"
    assert commits[0]["branch"] == "wip"
    assert all(p.startswith("elsewhere/") for p in commits[0]["files"])
