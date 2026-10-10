"""#394 (P1): publish an order as one commit, rebuild the index once.

`publish_github` committed each document separately and every commit triggers
the output repo's index-rebuild Action, so a 500-page batch meant 500 commits
and 500 Action runs for one holding. The Git Data mechanics already supported
one commit for many files — `_commit_files` builds blobs, tree and commit from
a dict — and only the *building* of that dict was welded to one document.

Four contracts, each with its own section:

**N documents, one commit.** Whose tree carries every document's files.

**`publish_doc` is the N=1 case**, so the two cannot drift.

**Retryable without duplicating content.** `_commit_files` makes no commit when
the new tree matches the base, so a retry after a 5xx that actually landed is
`unchanged` — not a second commit and not a reported failure. Saying "failed"
there would invite a third attempt.

**`url is None` has three causes** and they may not be conflated: nothing to
publish, already up to date, and the call failed. The project has paid for that
conflation often enough.

Offline — the GitHub API is a stub. Run from the repo root:
    pytest agentic_historian/tests/test_ah_394_batch_publish.py
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
from utils import publish_github as pg     # noqa: E402


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(config, "ENABLE_GITHUB_PUBLISH", True)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "t0ken")
    monkeypatch.setattr(config, "GITHUB_OUTPUT_REPO", "owner/outputs")
    monkeypatch.setattr(config, "GITHUB_OUTPUT_BRANCH", "main")
    # `_write` waits out a retryable status, and the real wait grows per
    # attempt. The retry *logic* is what these tests exercise; the sleeping is
    # not, and leaving it in cost this file 31 seconds.
    monkeypatch.setattr(pg, "_retry_after_s", lambda response, attempt: 0.0)
    return tmp_path


def _artifacts(monkeypatch, docs: dict):
    """`doc_id -> {filename: text}` as `collect_artifacts` would find on disk."""
    written: dict = {}
    for doc_id, files in docs.items():
        folder = config.DATA_DIR / "docs" / doc_id
        folder.mkdir(parents=True, exist_ok=True)
        written[doc_id] = {}
        for name, text in files.items():
            (folder / name).write_text(text, encoding="utf-8")
            written[doc_id][name] = folder / name
    monkeypatch.setattr(pg, "collect_artifacts",
                        lambda doc_id: written.get(doc_id, {}))
    return written


class _Api:
    """The Git Data endpoints, enough of them. Records every write."""

    def __init__(self, *, head="base-sha", base_tree="base-tree",
                 tree_sha="new-tree", fail_trees=0):
        self.writes: list = []
        self.trees: list = []
        self._head, self._base_tree = head, base_tree
        self._tree_sha, self._fail_trees = tree_sha, fail_trees

    # read
    def get(self, url, **kw):
        if "/git/ref/heads/" in url:
            return _Resp(200, {"object": {"sha": self._head}})
        if "/git/commits/" in url:
            return _Resp(200, {"tree": {"sha": self._base_tree}})
        return _Resp(404, {})

    # write — `_write` dispatches through `session.post` / `.patch`, which is
    # "the surface the rest of this module already uses, and the one every test
    # double here implements". A stub with only `request` is not that surface,
    # and my first version was one.
    def post(self, url, **kw):
        return self._write("POST", url, **kw)

    def patch(self, url, **kw):
        return self._write("PATCH", url, **kw)

    def _write(self, method, url, **kw):
        self.writes.append((method, url, kw.get("json")))
        if url.endswith("/git/trees"):
            self.trees.append(kw.get("json"))
            if self._fail_trees > 0:
                self._fail_trees -= 1
                return _Resp(502, {"message": "Bad gateway"})
            return _Resp(201, {"sha": self._tree_sha})
        if url.endswith("/git/commits"):
            return _Resp(201, {"sha": "commit-sha",
                               "html_url": "https://github.com/x/commit/abc"})
        return _Resp(200, {})

    @property
    def commits(self):
        return [w for w in self.writes if w[1].endswith("/git/commits")]


class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload
        self.headers = {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}", response=self)


def _session(api):
    class S:
        get = staticmethod(api.get)
        post = staticmethod(api.post)
        patch = staticmethod(api.patch)
    return S()


# ── N documents, one commit ──────────────────────────────────────────────────

def test_three_documents_become_one_commit(monkeypatch):
    _artifacts(monkeypatch, {
        "saa-0428": {"transcription.txt": "a", "pipeline.json": "{}"},
        "saa-0429": {"transcription.txt": "b", "pipeline.json": "{}"},
        "saa-0430": {"transcription.txt": "c", "pipeline.json": "{}"},
    })
    api = _Api()

    got = pg.publish_docs(["saa-0428", "saa-0429", "saa-0430"],
                          label="lassberg", session=_session(api))

    assert got.outcome == "published" and got.ok
    assert len(api.commits) == 1, f"{len(api.commits)} commits for three docs"
    assert sorted(got.published) == ["saa-0428", "saa-0429", "saa-0430"]


def test_the_single_tree_carries_every_documents_files(monkeypatch):
    _artifacts(monkeypatch, {
        "doc-a": {"transcription.txt": "a"},
        "doc-b": {"transcription.txt": "b"},
    })
    api = _Api()

    pg.publish_docs(["doc-a", "doc-b"], session=_session(api))
    paths = {e["path"] for e in api.trees[0]["tree"]}

    assert "docs/doc-a/transcription.txt" in paths
    assert "docs/doc-b/transcription.txt" in paths
    assert "docs/doc-a/index.md" in paths and "docs/doc-b/index.md" in paths


def test_the_message_names_the_order_and_the_count(monkeypatch):
    _artifacts(monkeypatch, {f"d{i}": {"transcription.txt": "x"}
                             for i in range(4)})
    api = _Api()

    pg.publish_docs([f"d{i}" for i in range(4)], label="lassberg",
                    session=_session(api))
    message = api.commits[0][2]["message"]

    assert message == "Publish lassberg (4 docs)"


def test_one_document_keeps_the_old_message(monkeypatch):
    """The catalogue's commit history should not change shape for a /run."""
    _artifacts(monkeypatch, {"only": {"transcription.txt": "x"}})
    api = _Api()

    pg.publish_docs(["only"], label="only", session=_session(api))

    assert api.commits[0][2]["message"] == "Publish only"


def test_a_source_url_can_be_given_per_document(monkeypatch):
    _artifacts(monkeypatch, {"a": {"transcription.txt": "x"},
                             "b": {"transcription.txt": "y"}})
    api = _Api()

    got = pg.publish_docs([("a", "https://s/a"), ("b", None)],
                          session=_session(api))

    assert got.published == ["a", "b"]


# ── publish_doc is the N=1 case ──────────────────────────────────────────────

def test_publish_doc_delegates_to_the_batch_api(monkeypatch):
    """Two implementations of "commit a document" would drift."""
    seen: list = []
    monkeypatch.setattr(pg, "publish_docs",
                        lambda docs, **kw: seen.append((list(docs), kw))
                        or pg.BatchPublish(outcome="published", url="u"))

    assert pg.publish_doc("d", "https://s/d") == "u"
    assert seen == [([("d", "https://s/d")], {"label": "d", "session": None})]


def test_publish_doc_still_raises_for_a_refused_id(monkeypatch):
    """For one document a refusal is the whole answer, and
    `orchestrator._publish_outputs` reports it in the run's publish event."""
    _artifacts(monkeypatch, {"kf-": {"transcription.txt": "x"}})

    with pytest.raises(pg.DocumentIdRefused):
        pg.publish_doc("kf-")


def test_publish_doc_returns_none_when_there_is_nothing(monkeypatch):
    _artifacts(monkeypatch, {})

    assert pg.publish_doc("missing") is None


# ── a refused id costs itself, not the batch ─────────────────────────────────

def test_one_bad_id_does_not_lose_the_other_forty_nine(monkeypatch):
    """A batch must not lose its good documents to one folder somebody named
    `kf-`. Collected and reported, not raised."""
    _artifacts(monkeypatch, {"good-1": {"transcription.txt": "a"},
                             "kf-": {"transcription.txt": "b"},
                             "good-2": {"transcription.txt": "c"}})
    api = _Api()

    got = pg.publish_docs(["good-1", "kf-", "good-2"], session=_session(api))

    assert sorted(got.published) == ["good-1", "good-2"]
    assert "kf-" in got.refused and "refused" in got.refused["kf-"].lower()
    assert len(api.commits) == 1


def test_a_document_with_no_artifacts_is_empty_not_refused(monkeypatch):
    """"Nothing to publish" and "may not be published" are different answers."""
    _artifacts(monkeypatch, {"has": {"transcription.txt": "a"}})
    api = _Api()

    got = pg.publish_docs(["has", "nothing-here"], session=_session(api))

    assert got.published == ["has"]
    assert got.empty == ["nothing-here"] and got.refused == {}


def test_a_batch_of_only_refusals_makes_no_commit(monkeypatch):
    _artifacts(monkeypatch, {"kf-": {"transcription.txt": "b"}})
    api = _Api()

    got = pg.publish_docs(["kf-"], session=_session(api))

    assert got.outcome == "nothing" and api.commits == []
    assert got.refused and not got.ok


# ── url is None has three causes ─────────────────────────────────────────────

def test_an_identical_tree_is_unchanged_and_not_a_failure(monkeypatch):
    """The successful retry. `_commit_files` makes no commit when the new tree
    matches the base, and reporting that as a failure would invite a third
    attempt at work that is already done."""
    _artifacts(monkeypatch, {"a": {"transcription.txt": "x"}})
    api = _Api(base_tree="same-tree", tree_sha="same-tree")

    got = pg.publish_docs(["a"], session=_session(api))

    assert got.outcome == "unchanged" and got.url is None
    assert got.ok, "a retry that found everything already there is not a loss"
    assert api.commits == [], "an empty commit was made"


def test_a_failing_api_call_is_failed_with_the_reason(monkeypatch):
    _artifacts(monkeypatch, {"a": {"transcription.txt": "x"}})
    api = _Api(fail_trees=99)

    got = pg.publish_docs(["a"], session=_session(api))

    assert got.outcome == "failed" and not got.ok
    assert got.error and "502" in got.error


def test_nothing_to_publish_is_its_own_outcome(monkeypatch):
    _artifacts(monkeypatch, {})

    got = pg.publish_docs(["a", "b"])

    assert got.outcome == "nothing" and not got.ok and got.files == 0


def test_publishing_disabled_says_so_rather_than_failing(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_GITHUB_PUBLISH", False)

    got = pg.publish_docs(["a"])

    assert got.outcome == "nothing" and "ENABLE_GITHUB_PUBLISH" in got.error


def test_an_empty_list_is_not_an_error():
    got = pg.publish_docs([])

    assert got.outcome == "nothing" and got.files == 0


def test_a_retry_after_a_transient_5xx_still_makes_one_commit(monkeypatch):
    """`_write` retries, so one 502 on the tree call must not become two
    commits."""
    _artifacts(monkeypatch, {"a": {"transcription.txt": "x"}})
    api = _Api(fail_trees=1)

    got = pg.publish_docs(["a"], session=_session(api))

    assert got.outcome == "published"
    assert len(api.commits) == 1, "the retry produced a second commit"
    assert len(api.trees) == 2, "the tree call was not retried"


# ── the batch runner uses it ─────────────────────────────────────────────────

def test_the_runner_publishes_once_at_the_end_by_default(tmp_path, monkeypatch):
    """`BATCH_PUBLISH_EVERY = 0` is the per-order case and the fewest index
    rebuilds."""
    import batch_runner as br
    import corpus_manifest as cm

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "d")
    cm.close()
    root = tmp_path / "f"
    root.mkdir()
    for i in range(5):
        (root / f"p{i}.jpg").write_bytes(b"\xff\xd8\xff")
    source = br.inspect_source(root)
    batches: list = []

    br.run_batch("run", source, workers=1, pipeline=lambda *a: None,
                 publish=lambda ids, label: batches.append(list(ids)),
                 publish_every=0, sleep=lambda _s: None, heartbeat_every=0.01)
    cm.close()

    assert len(batches) == 1, f"{len(batches)} commits for one run"
    assert sorted(batches[0]) == sorted(source.doc_ids)


def test_the_runner_publishes_every_n_when_asked(tmp_path, monkeypatch):
    import batch_runner as br
    import corpus_manifest as cm

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "d")
    cm.close()
    root = tmp_path / "f"
    root.mkdir()
    for i in range(7):
        (root / f"p{i}.jpg").write_bytes(b"\xff\xd8\xff")
    source = br.inspect_source(root)
    batches: list = []

    br.run_batch("run", source, workers=1, pipeline=lambda *a: None,
                 publish=lambda ids, label: batches.append(list(ids)),
                 publish_every=3, sleep=lambda _s: None, heartbeat_every=0.01)
    cm.close()

    assert [len(b) for b in batches] == [3, 3, 1], \
        "seven documents at three a time is 3+3+1"
    assert sorted(d for b in batches for d in b) == sorted(source.doc_ids)


def test_a_failed_document_is_not_published(tmp_path, monkeypatch):
    import batch_runner as br
    import corpus_manifest as cm

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "d")
    cm.close()
    root = tmp_path / "f"
    root.mkdir()
    for name in ("ok.jpg", "bad.jpg"):
        (root / name).write_bytes(b"\xff\xd8\xff")
    source = br.inspect_source(root)
    batches: list = []

    def pipeline(doc_id, paths, mode):
        if doc_id == "bad":
            raise RuntimeError("x")

    br.run_batch("run", source, workers=1, pipeline=pipeline, max_attempts=1,
                 publish=lambda ids, label: batches.append(list(ids)),
                 sleep=lambda _s: None, heartbeat_every=0.01)
    cm.close()

    assert batches == [["ok"]], "a document that failed was published"


def test_an_interrupted_run_still_publishes_what_it_finished(tmp_path,
                                                             monkeypatch):
    """Work that is done and paid for must not be thrown away."""
    import batch_runner as br
    import corpus_manifest as cm

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "d")
    cm.close()
    root = tmp_path / "f"
    root.mkdir()
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        (root / name).write_bytes(b"\xff\xd8\xff")
    source = br.inspect_source(root)
    batches: list = []
    seen: list = []

    def pipeline(doc_id, paths, mode):
        seen.append(doc_id)
        if len(seen) == 2:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        br.run_batch("run", source, workers=1, pipeline=pipeline,
                     publish=lambda ids, label: batches.append(list(ids)),
                     sleep=lambda _s: None, heartbeat_every=0.01)
    cm.close()

    assert batches and len(batches[0]) == 1, \
        "the document that finished before the interrupt was not published"


def test_per_doc_publishing_suppresses_the_batch_publish(tmp_path, monkeypatch):
    """Otherwise the batch would claim a publish the pipeline already made."""
    import batch_runner as br
    import corpus_manifest as cm

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "d")
    cm.close()
    root = tmp_path / "f"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"\xff\xd8\xff")
    batches: list = []

    br.run_batch("run", br.inspect_source(root), workers=1,
                 pipeline=lambda *a: None, per_doc_publish=True,
                 publish=lambda ids, label: batches.append(list(ids)),
                 sleep=lambda _s: None, heartbeat_every=0.01)
    cm.close()

    assert batches == []


def test_the_runner_hands_its_flag_to_the_default_pipeline(tmp_path,
                                                            monkeypatch):
    """The gap my first test left: it called `_default_pipeline` directly, so
    nothing checked that `run_batch` passes `per_doc_publish` into it. Hardcoding
    `publish=True` in the closure passed every other test here."""
    import batch_runner as br
    import corpus_manifest as cm

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "d")
    cm.close()
    root = tmp_path / "f"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"\xff\xd8\xff")
    seen: list = []
    monkeypatch.setattr(br, "_default_pipeline",
                        lambda d, p, m, **kw: seen.append(kw.get("publish")))

    # A run id per iteration: `requeue` deliberately leaves `done` alone (R1),
    # so re-running the same id has nothing to claim and the second pass would
    # see nothing — which is what my first version of this test did.
    for i, flag in enumerate((False, True)):
        seen.clear()
        br.run_batch(f"run-{i}", br.inspect_source(root), workers=1,
                     per_doc_publish=flag, publish=lambda ids, label: None,
                     sleep=lambda _s: None, heartbeat_every=0.01)
        assert seen == [flag], f"per_doc_publish={flag} was not passed through"
    cm.close()


def test_the_orchestrator_honours_its_publish_seam():
    """Source-level on purpose, and labelled as such.

    `run_full_pipeline` cannot be called in this suite — it runs the VLM, the
    ATR gateway and the MCP federation — so the one thing testable offline is
    that both publish sites are guarded and that the unguarded case says what it
    deferred. Deliberately as blunt as the #105 decorator-order guard: a grep
    cannot be gamed, and `if True:` in place of `if publish:` passed every
    behavioural test in this file.
    """
    src = (PKG / "orchestrator.py").read_text(encoding="utf-8")

    assert src.count("if publish:\n        _published, _detail = "
                     "_publish_outputs(") == 2, \
        "both publish sites must be behind the seam"
    assert src.count('decision="deferred to the batch publish (#394)"') == 2, \
        "a skipped publish must say it was deferred, not stay silent"
    assert "publish: bool = True," in src, "the seam defaults to publishing"


def test_the_default_pipeline_defers_publishing_to_the_batch(monkeypatch):
    """The 500-commits-per-holding this replaced."""
    import batch_runner as br
    import orchestrator
    seen: list = []
    monkeypatch.setattr(orchestrator, "run_full_pipeline",
                        lambda p, **kw: seen.append(("single", kw.get("publish"))))
    monkeypatch.setattr(orchestrator, "run_full_pipeline_group",
                        lambda d, p, **kw: seen.append(("group", kw.get("publish"))))

    br._default_pipeline("d", [Path("/x/a.jpg")], br.PAGES)
    br._default_pipeline("o", [Path("/x/a.jpg")], br.ORDERS)
    br._default_pipeline("d", [Path("/x/a.jpg")], br.PAGES, publish=True)

    assert seen == [("single", False), ("group", False), ("single", True)]
