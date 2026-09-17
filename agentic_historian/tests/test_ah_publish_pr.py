"""Proposing a run to the edition repository as a pull request.

The readings go into somebody else's repository and its maintainer decides what
enters it, so ``publish-batch`` opens a pull request instead of pushing: the
commits land in a fork we own, and nothing needs write access to the target.

Offline — every GitHub call is stubbed.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                   # noqa: E402
from utils import publish_github as pub         # noqa: E402

UPSTREAM_SHA = "a" * 40


class FakeAPI:
    """Just enough GitHub to tell the interesting cases apart."""

    def __init__(self, *, branches=None, pr_exists=False, base_missing=False):
        self.branches = dict(branches or {})
        self.pr_exists = pr_exists
        self.base_missing = base_missing
        self.created_refs: list[tuple[str, str, str]] = []
        self.pr_calls: list[dict] = []

    # -- requests.Session surface ------------------------------------------
    def get(self, url, timeout=None, params=None):
        if "/git/ref/heads/" in url:
            repo = url.split("/repos/")[1].split("/git/")[0]
            branch = url.split("/git/ref/heads/")[1]
            if repo == "michaelscho/lassberg" and branch == "main":
                if self.base_missing:
                    return _Resp(404, {})
                return _Resp(200, {"object": {"sha": UPSTREAM_SHA}})
            sha = self.branches.get((repo, branch))
            return _Resp(200, {"object": {"sha": sha}}) if sha else _Resp(404, {})
        if url.endswith("/pulls"):
            return _Resp(200, [{"html_url": "https://github.test/pr/7"}]
                         if self.pr_exists else [])
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, json=None, timeout=None):
        if url.endswith("/git/refs"):
            repo = url.split("/repos/")[1].split("/git/")[0]
            self.created_refs.append((repo, json["ref"], json["sha"]))
            return _Resp(201, {})
        if url.endswith("/pulls"):
            self.pr_calls.append(json)
            if self.pr_exists:
                return _Resp(422, {"message": "A pull request already exists"})
            return _Resp(201, {"html_url": "https://github.test/pr/1"})
        raise AssertionError(f"unexpected POST {url}")


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def api(monkeypatch):
    fake = FakeAPI()
    commits: list[dict] = []

    def _fake_commit(files, message, session=None, *, repo=None, branch=None,
                     base_branch=None):
        commits.append({"files": dict(files), "message": message,
                        "repo": repo, "branch": branch})
        return f"https://github.test/commit/{len(commits)}"

    monkeypatch.setattr(pub, "_commit_files", _fake_commit)
    monkeypatch.setattr(pub, "_session", lambda: fake)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "ghp_test")
    fake.commits = commits
    return fake


def make_run(root: Path, pages=3) -> Path:
    model = root / "trocr-kurrent"
    model.mkdir(parents=True)
    for i in range(pages):
        (model / f"page-{i:03d}.txt").write_text("Euer Hochwohlgeboren", encoding="utf-8")
        (model / f"page-{i:03d}.json").write_text('{"text": "x"}', encoding="utf-8")
    (root / "report.md").write_text("# ATR batch\n\n- pages: **3**\n", encoding="utf-8")
    (root / "scan.tif").write_bytes(b"\x00" * 16)      # never publishable
    return root


# ── the shape of a proposal ──────────────────────────────────────────────────

def test_commits_go_to_the_fork_and_the_pr_to_the_edition(api, tmp_path):
    run = make_run(tmp_path / "atr_trocr_corpus")

    result = pub.publish_pr(run, repo="michaelscho/lassberg",
                            path_prefix="data/textrecognition",
                            head_repo="thodel/lassberg")

    assert all(c["repo"] == "thodel/lassberg" for c in api.commits)
    assert api.pr_calls[0]["head"] == "thodel:textrecognition/atr_trocr_corpus"
    assert api.pr_calls[0]["base"] == "main"
    assert result["pull_request"] == "https://github.test/pr/1"
    assert result["files"] == 7          # 3 pages x2 + report.md; the .tif is not published


def test_the_branch_starts_at_the_upstream_head_not_the_forks(api, tmp_path):
    """A stale fork's main would make a two-file publish look like a mass revert."""
    run = make_run(tmp_path / "run")

    pub.publish_pr(run, repo="michaelscho/lassberg", path_prefix="data/textrecognition",
                   head_repo="thodel/lassberg")

    assert api.created_refs == [
        ("thodel/lassberg", "refs/heads/textrecognition/run", UPSTREAM_SHA)
    ]


def test_scans_are_never_proposed(api, tmp_path):
    run = make_run(tmp_path / "run")
    (run / "trocr-kurrent" / "page-000.jpg").write_bytes(b"\xff\xd8")

    pub.publish_pr(run, repo="michaelscho/lassberg", path_prefix="data/textrecognition",
                   head_repo="thodel/lassberg")

    published = {p for c in api.commits for p in c["files"]}
    assert not any(p.endswith((".jpg", ".tif")) for p in published)
    assert all(p.startswith("data/textrecognition/") for p in published)


def test_the_pr_body_carries_the_run_report_and_the_caveat(api, tmp_path):
    run = make_run(tmp_path / "run")

    pub.publish_pr(run, repo="michaelscho/lassberg", path_prefix="data/textrecognition",
                   head_repo="thodel/lassberg")

    body = api.pr_calls[0]["body"]
    assert "uncorrected machine output" in body
    assert "# ATR batch" in body                     # the run's own report, not a copy


def test_a_run_without_a_report_still_proposes(api, tmp_path):
    run = make_run(tmp_path / "run")
    (run / "report.md").unlink()

    result = pub.publish_pr(run, repo="michaelscho/lassberg",
                            path_prefix="data/textrecognition",
                            head_repo="thodel/lassberg")

    assert result["pull_request"]


# ── running it twice ─────────────────────────────────────────────────────────

def test_a_second_publish_adds_to_the_same_pull_request(tmp_path, monkeypatch):
    """"Publish once everything is collected" has to survive being run twice."""
    fake = FakeAPI(branches={("thodel/lassberg", "textrecognition/run"): "b" * 40},
                   pr_exists=True)
    commits: list[dict] = []
    monkeypatch.setattr(pub, "_commit_files",
                        lambda files, msg, session=None, *, repo=None, branch=None,
                        base_branch=None: commits.append(repo) or "u")
    monkeypatch.setattr(pub, "_session", lambda: fake)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "ghp_test")
    run = make_run(tmp_path / "run")

    result = pub.publish_pr(run, repo="michaelscho/lassberg",
                            path_prefix="data/textrecognition",
                            head_repo="thodel/lassberg")

    assert fake.created_refs == []                   # the branch was already there
    assert result["pull_request"] == "https://github.test/pr/7"   # found, not failed


def test_a_missing_base_branch_stops_rather_than_guesses(api, tmp_path):
    api.base_missing = True
    run = make_run(tmp_path / "run")

    with pytest.raises(RuntimeError, match="no branch 'main'"):
        pub.publish_pr(run, repo="michaelscho/lassberg",
                       path_prefix="data/textrecognition",
                       head_repo="thodel/lassberg")


def test_an_empty_run_proposes_nothing(api, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()

    result = pub.publish_pr(empty, repo="michaelscho/lassberg",
                            path_prefix="data/textrecognition",
                            head_repo="thodel/lassberg")

    assert result == {"branch": None, "head": None, "files": 0, "commits": [],
                      "pull_request": None}
    assert api.pr_calls == []


def test_branching_inside_the_repo_needs_no_owner_prefix(api, tmp_path):
    """When we do own the target, head is a plain branch name."""
    run = make_run(tmp_path / "run")

    pub.publish_pr(run, repo="michaelscho/lassberg",
                   path_prefix="data/textrecognition",
                   head_repo="michaelscho/lassberg")

    assert api.pr_calls[0]["head"] == "textrecognition/run"


# ── the CLI ──────────────────────────────────────────────────────────────────

def _cli():
    import importlib.util

    spec = importlib.util.spec_from_file_location("ah_cli", PKG / "__main__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_publish_batch_proposes_by_default(api, tmp_path, capsys):
    mod = _cli()
    run = make_run(tmp_path / "atr_trocr_corpus")

    args = mod.build_parser().parse_args(["publish-batch", "--run-dir", str(run)])
    assert mod.publish_batch(args) == 0

    assert api.pr_calls[0]["base"] == "main"
    assert all(c["repo"] == config.GITHUB_TEXT_FORK == "thodel/lassberg"
               for c in api.commits)
    assert "https://github.test/pr/1" in capsys.readouterr().out


def test_publish_batch_push_still_commits_directly(monkeypatch, tmp_path, capsys):
    mod = _cli()
    seen: list[dict] = []
    monkeypatch.setattr(
        pub, "_commit_files",
        lambda files, msg, session=None, *, repo=None, branch=None, base_branch=None:
            seen.append({"repo": repo, "branch": branch}) or "https://github.test/c/1")
    monkeypatch.setattr(pub, "_session", lambda: object())
    monkeypatch.setattr(config, "GITHUB_TOKEN", "ghp_test")
    run = make_run(tmp_path / "run")

    args = mod.build_parser().parse_args(
        ["publish-batch", "--run-dir", str(run), "--push"])
    assert mod.publish_batch(args) == 0

    assert seen[0] == {"repo": "michaelscho/lassberg", "branch": "main"}
