"""Tests for #200: publish processed outputs to a GitHub repo (Git Data API).

Offline — the GitHub API is faked via an injected session. Run from the repo root:
    pytest agentic_historian/tests/test_ah_200_publish_github.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config  # noqa: E402
from utils import publish_github as pg  # noqa: E402


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeGitHub:
    """Simulates the Git Data API and records what it was asked to commit."""

    def __init__(self, empty_repo=False, fail_on=None):
        self.empty = empty_repo
        self.fail_on = fail_on
        self.calls = []
        self.tree_paths = []
        self.base_tree_sent = False
        self.commit_parents = "unset"
        self.ref_created = False
        self.ref_patched = False

    def _maybe_fail(self, url):
        if self.fail_on and self.fail_on in url:
            raise RuntimeError("boom")

    def get(self, url, **kw):
        self.calls.append(("GET", url)); self._maybe_fail(url)
        if "/ref/heads/" in url:
            return _Resp(404 if self.empty else 200, {"object": {"sha": "basecommit"}})
        if "/commits/" in url:
            return _Resp(200, {"tree": {"sha": "basetree"}})
        return _Resp(200, {})

    def post(self, url, json=None, **kw):
        self.calls.append(("POST", url)); self._maybe_fail(url)
        if url.endswith("/blobs"):
            return _Resp(201, {"sha": "blob"})
        if url.endswith("/trees"):
            self.tree_paths = [e["path"] for e in json["tree"]]
            self.base_tree_sent = "base_tree" in json
            return _Resp(201, {"sha": "newtree"})
        if url.endswith("/commits"):
            self.commit_parents = json.get("parents")
            return _Resp(201, {"sha": "newcommit", "html_url": "https://gh/commit/newcommit"})
        if url.endswith("/refs"):
            self.ref_created = True
            return _Resp(201, {})
        return _Resp(200, {})

    def patch(self, url, json=None, **kw):
        self.calls.append(("PATCH", url)); self._maybe_fail(url)
        self.ref_patched = True
        return _Resp(200, {})


def _enable(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ENABLE_GITHUB_PUBLISH", True)
    monkeypatch.setattr(config, "GITHUB_OUTPUT_REPO", "owner/out")
    monkeypatch.setattr(config, "GITHUB_TOKEN", "tok")
    monkeypatch.setattr(config, "GITHUB_OUTPUT_BRANCH", "main")
    for attr, sub in (("TRANSCRIPTIONS_DIR", "t"),
                      ("DESCRIPTIONS_DIR", "d"), ("OUTPUTS_DIR", "o")):
        (tmp_path / sub).mkdir(exist_ok=True)
        monkeypatch.setattr(config, attr, tmp_path / sub)


def _write_outputs(tmp_path, doc="doc1"):
    (tmp_path / "t" / f"{doc}.txt").write_text("Wir Hans von Wiler …", encoding="utf-8")
    (tmp_path / "d" / f"{doc}.json").write_text('{"x":1}', encoding="utf-8")
    (tmp_path / "o" / f"{doc}_entities.json").write_text("[]", encoding="utf-8")
    (tmp_path / "o" / f"{doc}_pipeline.json").write_text('{"doc_id":"doc1"}', encoding="utf-8")


def test_disabled_returns_none_without_http(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GITHUB_PUBLISH", False)
    fake = FakeGitHub()
    assert pg.publish_doc("doc1", session=fake) is None
    assert fake.calls == []


def test_collect_artifacts_only_existing(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    _write_outputs(tmp_path)
    assert set(pg.collect_artifacts("doc1")) == {
        "transcription.txt", "description.json", "entities.json", "pipeline.json"}


def test_publish_doc_atomic_commit(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    _write_outputs(tmp_path)
    fake = FakeGitHub()
    url = pg.publish_doc("doc1", source_url="https://src/doc1", session=fake)
    assert url == "https://gh/commit/newcommit"
    assert all(p.startswith("docs/doc1/") for p in fake.tree_paths)
    assert {"docs/doc1/index.md", "docs/doc1/transcription.txt"} <= set(fake.tree_paths)
    assert not any(p.endswith((".jpg", ".png", ".tif")) for p in fake.tree_paths)
    assert fake.commit_parents == ["basecommit"]      # non-empty repo → parent
    assert fake.ref_patched and not fake.ref_created
    assert fake.base_tree_sent


def test_publish_doc_empty_repo_bootstraps_ref(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    _write_outputs(tmp_path)
    fake = FakeGitHub(empty_repo=True)
    url = pg.publish_doc("doc1", session=fake)
    assert url == "https://gh/commit/newcommit"
    assert fake.commit_parents is None                # first commit, no parents
    assert fake.ref_created and not fake.ref_patched
    assert not fake.base_tree_sent


def test_publish_doc_failure_is_non_fatal(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    _write_outputs(tmp_path)
    assert pg.publish_doc("doc1", session=FakeGitHub(fail_on="/blobs")) is None


def test_publish_doc_no_artifacts_no_http(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)      # dirs exist but empty
    fake = FakeGitHub()
    assert pg.publish_doc("doc1", session=fake) is None
    assert fake.calls == []
