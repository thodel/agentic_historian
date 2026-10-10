"""Tests for S3 (#568): the OpenClaw home is no longer in this repository.

`workspace/` was the OpenClaw agent's home — AGENTS.md, SOUL.md, IDENTITY.md,
USER.md, HEARTBEAT.md, TOOLS.md, skills/ — plus its own requirements.txt and a
scratch script, living next to the bot's code. That coupled the two systems at
the repository and put the agent's identity and personal notes into the bot's
tree. The bot never imported or read anything from it (the standalone CI job,
S5 #570, proves import and start without it). This pins that nothing of it is
tracked any more, and that what the bot did keep there — its env template and
two planning documents — is still where the docs say.

Offline: file-level checks against the tracked tree.
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: What made workspace/ the OpenClaw home. None of these is a bot file.
OPENCLAW_HOME = {
    "AGENTS.md", "SOUL.md", "IDENTITY.md", "USER.md", "HEARTBEAT.md", "TOOLS.md",
    "ocr_pipeline_README.md", "scramble.py",
}


def _tracked() -> set[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


# ── gone ─────────────────────────────────────────────────────────────────────

def test_no_workspace_directory_is_tracked():
    stray = sorted(f for f in _tracked() if f.split("/")[0] == "workspace")
    assert not stray, f"the OpenClaw workspace/ is still tracked: {stray}"


def test_no_openclaw_home_file_is_tracked_anywhere():
    stray = sorted(f for f in _tracked() if Path(f).name in OPENCLAW_HOME)
    assert not stray, f"OpenClaw home files tracked in the bot repo: {stray}"


def test_no_skills_directory_is_tracked():
    stray = sorted(f for f in _tracked()
                   if f.startswith("skills/") or "/skills/" in f)
    assert not stray, f"OpenClaw skills tracked in the bot repo: {stray}"


# ── kept, and findable ───────────────────────────────────────────────────────

def test_the_env_template_lives_in_deploy():
    template = REPO_ROOT / "deploy/gpustack.env.example"
    assert template.exists(), "the .env.gpustack template moved to deploy/"
    text = template.read_text(encoding="utf-8")
    assert "GPUSTACK_API_KEY=" in text
    assert ".env.gpustack" in text, "the template must say where its copy goes"


def test_the_planning_documents_live_in_docs():
    for name in ("CONSOLIDATION_AND_FEEDBACK.md", "KNOWLEDGE_HUB_DATA_INTEGRATION.md"):
        assert (REPO_ROOT / "docs" / name).exists(), f"docs/{name} missing"


def test_the_readmes_point_at_the_template_not_the_workspace():
    for readme in (REPO_ROOT / "README.md", REPO_ROOT / "agentic_historian/README.md"):
        text = readme.read_text(encoding="utf-8")
        assert "workspace/gpustack.env.example" not in text, readme
        assert "deploy/gpustack.env.example" in text, readme


def test_separation_doc_warns_about_the_deploy_and_shows_recovery():
    """update.sh resets the production checkout; if the agent's home IS that
    checkout's workspace/, deploying this change deletes it. The doc has to say
    so, and say how to get the files back out of history."""
    text = (REPO_ROOT / "deploy/systemd/SEPARATION.md").read_text(encoding="utf-8")
    assert "#568" in text
    assert "update.sh" in text, "the deploy hazard must be named"
    assert "git archive" in text, "the recovery recipe must be there"
