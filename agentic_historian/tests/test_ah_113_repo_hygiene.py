"""Tests for #113: Repo hygiene — flatten layout, isolate persona files, drop stray files.

Run offline — file-level checks against the repo root.
"""

import os
import subprocess

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def git_ls_files(root="."):
    """Return set of all tracked files under root (relative to root)."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root, capture_output=True, text=True
    )
    return {f.strip() for f in result.stdout.splitlines() if f.strip()}


def ls_root(root="."):
    """Non-directory entries at repo root (files + symlinks, no dirs)."""
    entries = set()
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if os.path.isfile(path) or os.path.islink(path):
            entries.add(name)
    return entries


def test_no_conflicting_requirements_at_repo_root():
    """Root requirements.txt conflicts with agentic_historian/requirements.txt.

    It must not exist at repo root (deleted or moved to workspace/.
    """
    tracked = git_ls_files(REPO_ROOT)
    assert "requirements.txt" not in tracked, (
        "requirements.txt still tracked at repo root — "
        "conflicts with agentic_historian/requirements.txt"
    )


def test_no_scramble_py_at_repo_root():
    """scramble.py is a stray file unrelated to the agentic_historian."""
    tracked = git_ls_files(REPO_ROOT)
    assert "scramble.py" not in tracked, "scramble.py must be deleted from repo root"


def test_no_zenodo_json_at_repo_root():
    """zenodo_htr_results.json is data, not code."""
    tracked = git_ls_files(REPO_ROOT)
    assert "zenodo_htr_results.json" not in tracked, (
        "zenodo_htr_results.json must be deleted from repo root"
    )


def test_persona_files_moved_to_workspace():
    """SOUL.md, IDENTITY.md, USER.md, etc. belong in workspace/, not the repo root."""
    tracked = git_ls_files(REPO_ROOT)
    persona_files = {
        "SOUL.md", "IDENTITY.md", "USER.md", "AGENTS.md",
        "HEARTBEAT.md", "TOOLS.md", "KNOWLEDGE_HUB_DATA_INTEGRATION.md",
        "CONSOLIDATION_AND_FEEDBACK.md", "ocr_pipeline_README.md",
        "gpustack.env.example",
    }
    stray = persona_files & tracked
    assert not stray, f"Persona files still in repo root: {stray}"


def test_skills_moved_to_workspace():
    """skills/ directory is workspace config, not part of the agentic_historian repo."""
    tracked = git_ls_files(REPO_ROOT)
    stray = {f for f in tracked if f.startswith("skills/")}
    assert not stray, f"skills/ still in repo root: {stray}"


def test_package_root_is_single_agentic_historian():
    """The package root must be agentic_historian/ — no nested double-indirection.

    The package root is the directory containing bot.py, config.py,
    orchestrator.py, etc.
    """
    tracked = git_ls_files(REPO_ROOT)
    # bot.py, config.py, orchestrator.py, reporter.py must live directly
    # under agentic_historian/ (not agentic_historian/agentic_historian/)
    assert "agentic_historian/bot.py" in tracked, "bot.py must be at agentic_historian/bot.py"
    assert "agentic_historian/orchestrator.py" in tracked, "orchestrator.py must be at agentic_historian/orchestrator.py"
    # Nested agentic_historian/agentic_historian/ is NOT allowed
    assert "agentic_historian/agentic_historian/bot.py" not in tracked, (
        "nested agentic_historian/agentic_historian/ detected — "
        "package must be flattened to single agentic_historian/ prefix"
    )


if __name__ == "__main__":
    for test_name in dir():
        if test_name.startswith("test_"):
            try:
                globals()[test_name]()
                print(f"PASS: {test_name}")
            except AssertionError as e:
                print(f"FAIL: {test_name}: {e}")
            except Exception as e:
                print(f"ERROR: {test_name}: {e}")
