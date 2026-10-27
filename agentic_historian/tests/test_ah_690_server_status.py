"""#690 — `server_status`: which code answers, which roots apply, what lies there.

The 2026-10-22 diagnosis of why `export-run-hf` was "invalid choice" on tei took
five terminal commands: `git status`, `git log`, `git reflog`, a config printout
of the data roots, `ls $GT_ROOT`. The finding — the production checkout stood on
a feature branch another workstream had switched to, and every MCP job ran that
branch — was the kind a session needs on every visit. Read-only, no secrets.
"""

import json
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
from mcp_atr import jobs                   # noqa: E402


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _repo(tmp_path):
    repo = tmp_path / "checkout"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.org")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "one")
    return repo


def _world(tmp_path, monkeypatch, repo=None):
    """Data roots the way tei has them, in a temp directory."""
    runs = tmp_path / "vlm_test"
    (runs / "gemini-eval-random" / "gemini-3.8-flash").mkdir(parents=True)
    for i in range(3):
        (runs / "gemini-eval-random" / "gemini-3.8-flash" / f"k{i}.txt").write_text("x")
        (runs / "gemini-eval-random" / "gemini-3.8-flash" / f"k{i}.json").write_text("{}")
    (runs / "older-run" / "m").mkdir(parents=True)
    (runs / "older-run" / "m" / "k.txt").write_text("x")
    gt = tmp_path / "gt"
    (gt / "eval-random").mkdir(parents=True)
    for i in range(4):
        (gt / "eval-random" / f"d__{i}.xml").write_text("<x/>")
    (gt / "eval-random.txt").write_text("# header\n# more\nK1\nK2\nK3\n\n", encoding="utf-8")
    cache = tmp_path / "page_cache"
    (cache / "Aarau").mkdir(parents=True)
    (cache / "Aarau" / "p1.jpg").write_bytes(b"1")
    (cache / "Aarau" / "p1.jpg.json").write_text("{}")
    archive = tmp_path / "archive"
    (archive / "Basel").mkdir(parents=True)
    for i in range(5):
        (archive / "Basel" / f"p{i}.jpg").write_bytes(b"1")
    monkeypatch.setattr(config, "VLM_TEST_ROOT", runs)
    monkeypatch.setattr(config, "GT_ROOT", gt)
    monkeypatch.setattr(config, "ATR_PAGE_CACHE", cache)
    monkeypatch.setattr(config, "ATR_PAGE_CACHE_ARCHIVE", archive)
    monkeypatch.setattr(config, "ATR_MOUNT_DIR", tmp_path / "gwdg")       # absent
    monkeypatch.setattr(config, "NEXTCLOUD_STAGING_DIR", tmp_path / "staging")
    monkeypatch.setattr(config, "ATR_RESEARCH_DIR", None)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "sk-this-must-not-appear")
    monkeypatch.setattr(config, "HF_TOKEN", "")
    monkeypatch.setattr(config, "ENV_FILES", (tmp_path / ".env.gpustack", tmp_path / ".env"))
    (tmp_path / ".env").write_text("X=1\n")
    if repo is not None:
        monkeypatch.setattr(jobs, "_checkout_dir", lambda: repo)
    return tmp_path


# ── the code ─────────────────────────────────────────────────────────────────

def test_the_checkout_is_described_branch_commit_dirt_and_worktrees(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _world(tmp_path, monkeypatch, repo)
    _git(repo, "checkout", "-q", "-b", "ruemlang-test")
    (repo / "untracked.txt").write_text("x")
    wt = tmp_path / "ah-main"
    _git(repo, "worktree", "add", "-q", str(wt), "main")

    code = jobs.server_status()["code"]

    assert code["checkout"] == str(repo)
    assert code["branch"] == "ruemlang-test"
    assert code["head"] == _git(repo, "rev-parse", "--short=12", "HEAD")
    assert code["dirty_files"] == 1
    assert [w["branch"] for w in code["worktrees"]] == ["ruemlang-test", "main"]
    assert str(wt) in [w["path"] for w in code["worktrees"]]
    assert "loaded" in code and "stale" in code, "batch_report's code block is here too"


def test_no_upstream_is_none_not_an_error(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _world(tmp_path, monkeypatch, repo)
    assert jobs.server_status()["code"]["upstream"] is None


def test_a_checkout_that_is_not_a_repository_still_answers(tmp_path, monkeypatch):
    _world(tmp_path, monkeypatch, repo=tmp_path / "plain")
    (tmp_path / "plain").mkdir()
    code = jobs.server_status()["code"]
    assert code["branch"] is None and code["head"] is None
    assert code["worktrees"] == [] and code["dirty_files"] is None


# ── the roots ────────────────────────────────────────────────────────────────

def test_gt_root_lists_the_names_the_other_tools_take(tmp_path, monkeypatch):
    _world(tmp_path, monkeypatch, _repo(tmp_path))
    gt = jobs.server_status()["roots"]["gt_root"]
    assert gt["exists"]
    assert gt["entries"] == [
        {"name": "eval-random", "type": "dir", "files": 4},
        {"name": "eval-random.txt", "type": "file", "lines": 3},
    ], "a key list's lines are keys, not comments or blanks"


def test_the_runs_are_listed_newest_first_with_pages_per_model(tmp_path, monkeypatch):
    _world(tmp_path, monkeypatch, _repo(tmp_path))
    import os
    import time
    old = time.time() - 3600
    os.utime(tmp_path / "vlm_test" / "older-run", (old, old))
    runs = jobs.server_status()["runs"]
    assert [r["name"] for r in runs] == ["gemini-eval-random", "older-run"]
    assert runs[0]["models"] == {"gemini-3.8-flash": 3}, "txt files, not sidecars"


def test_the_roots_say_whether_they_are_there(tmp_path, monkeypatch):
    _world(tmp_path, monkeypatch, _repo(tmp_path))
    roots = jobs.server_status()["roots"]
    assert roots["mount"]["exists"] is False
    assert roots["page_cache"]["exists"] and roots["page_cache"]["configured"]
    assert roots["page_cache_archive"]["exists"]
    assert roots["research_dir"] == {"path": None, "configured": False}


def test_counts_are_off_by_default_and_count_images_only_when_asked(tmp_path, monkeypatch):
    _world(tmp_path, monkeypatch, _repo(tmp_path))
    quick = jobs.server_status()["roots"]
    assert "pages" not in quick["page_cache"] and quick["page_cache_archive"].get("pages") is None
    counted = jobs.server_status(counts=True)["roots"]
    assert counted["page_cache"]["pages"] == 1, "the sidecar is not a page"
    assert counted["page_cache_archive"]["pages"] == 5
    assert counted["page_cache_archive"]["complete"] is True


# ── no secrets ───────────────────────────────────────────────────────────────

def test_no_secret_value_is_in_the_reply(tmp_path, monkeypatch):
    _world(tmp_path, monkeypatch, _repo(tmp_path))
    result = jobs.server_status()
    text = json.dumps(result)
    assert "sk-this-must-not-appear" not in text
    assert result["env"]["gemini_api_key"] == "set"
    assert result["env"]["hf_token"] == "unset"


def test_the_env_files_are_named_in_reading_order_with_existence(tmp_path, monkeypatch):
    _world(tmp_path, monkeypatch, _repo(tmp_path))
    monkeypatch.delenv("AH_ENV_FILE", raising=False)
    env = jobs.server_status()["env"]
    assert [f["exists"] for f in env["files"]] == [False, True]
    assert env["ah_env_file"] is None
    monkeypatch.setenv("AH_ENV_FILE", "/etc/dh-bot.env")
    assert jobs.server_status()["env"]["ah_env_file"] == "/etc/dh-bot.env"


def test_the_tool_is_on_the_server_and_only_sees():
    src = (PKG / "mcp_atr" / "server.py").read_text(encoding="utf-8")
    assert "def server_status(" in src
    body = src[src.index("def server_status("):src.index("def share_list(")]
    assert "never switches" in body


# ── what a job would die on (27.10.: huggingface_hub missing in the MCP venv) ─

def test_dependencies_name_what_is_missing(tmp_path, monkeypatch):
    req = tmp_path / "requirements.txt"
    req.write_text("# comment\nloguru>=0.7.0\nno-such-package-xyz>=1  # gone\n"
                   "atr-client @ git+https://example.org/x#subdirectory=c\n")
    monkeypatch.setattr(jobs, "REQUIREMENTS", req)
    monkeypatch.setattr(jobs.shutil, "which", lambda prog: None if prog == "ssh" else f"/bin/{prog}")
    deps = jobs._dependencies()
    assert "loguru" in deps["packages"]
    assert "no-such-package-xyz" in deps["missing"]
    assert deps["missing_programs"] == ["ssh (ubelix_*)"]
    assert deps["python"] == sys.executable


def test_the_shipped_requirements_parse():
    deps = jobs._dependencies()
    assert set(deps["packages"]) | set(deps["missing"]) >= {"loguru", "openai", "rapidfuzz",
                                                           "huggingface_hub", "pyarrow", "httpx"}
