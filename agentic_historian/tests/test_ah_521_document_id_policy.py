"""A document id becomes a public URL, so it is checked before it becomes one.

``publish_doc`` interpolated the id straight into ``docs/<doc_id>/`` with no
check. Ids come from the material — ``ingest`` takes the ingested folder's
name, ``text_recognition`` takes an image's stem — so whatever a directory
happened to be called became a published address.

Measured on the unfixed publisher, every one of these committed:

    docs/u-17__/index.md
    docs/kf-/index.md
    docs/saa-0001-test/index.md
    docs/hello world/index.md
    docs/../etc/passwd/index.md

The first two are the ids that had to be retired by hand afterwards in the
outputs repository, through the lineage pointer whose own cleanup then went
wrong (agentic-historian-outputs#195, #255). The third is one of six
engineering fixtures that had to be withdrawn. The last builds a path leading
out of ``docs/`` altogether; whether the Git Data API would normalise or reject
it is untested and beside the point, because constructing it is the defect.

The outputs repository already refuses all of these — but at *build* time,
after the directory is committed, when the address exists and the site build is
broken until a person intervenes. These tests hold the same policy at the
moment the id would become a path.

Run from the repo root:
    pytest agentic_historian/tests/test_ah_521_document_id_policy.py
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config  # noqa: E402
from utils import document_id  # noqa: E402
from utils import publish_github as pg  # noqa: E402


# ── the policy itself ───────────────────────────────────────────────────

@pytest.mark.parametrize("doc_id", [
    "bat", "order-ens", "order-001-group", "u-17", "BAT_664_r_00027",
    "saa-0428", "koenige", "a", "a1", "doc.v2", "latest", "contest",
])
def test_a_publishable_id_is_not_refused(doc_id):
    """Including 'latest' and 'contest', which contain 'test' undelimited."""
    assert document_id.publication_refusal(doc_id) == ""


@pytest.mark.parametrize("doc_id,fragment", [
    ("u-17__", "start and end"),
    ("kf-", "start and end"),
    ("abc.", "start and end"),
    ("-kf", "start and end"),
    ("_u17", "start and end"),
    (".hidden", "start and end"),
    ("hello world", "may only contain"),
    ("café", "may only contain"),
    ("kf/../etc", "may only contain"),
    ("../etc/passwd", "start and end"),
    ("saa-0001-test", "engineering fixture"),
    ("epic2-test-doc", "engineering fixture"),
    ("test", "engineering fixture"),
    ("", "is empty"),
    ("   ", "is empty"),
    (" x ", "whitespace"),
])
def test_an_unpublishable_id_says_why(doc_id, fragment):
    reason = document_id.publication_refusal(doc_id)
    assert reason, f"{doc_id!r} was accepted"
    assert fragment in reason, f"{doc_id!r}: {reason!r} does not mention {fragment!r}"


def test_the_policy_matches_the_outputs_repository():
    """The two are copies; this is what keeps them from drifting apart.

    Neither repository can import the other, so the policy is duplicated. The
    answers may not be: an id that publishes here and is rejected there is the
    exact failure this change exists to prevent, just moved one step later.
    """
    outputs = Path("/home/user/agentic-historian-outputs/scripts/build_outputs.py")
    if not outputs.exists():
        pytest.skip("the outputs repository is not checked out beside this one")

    namespace: dict = {}
    source = outputs.read_text(encoding="utf-8")
    # Execute only the two definitions and what they need, not the module.
    import re as _re
    wanted = ["SLUG_PATTERN", "def slug_violation", "def is_test_id"]
    chunks = ["import re"]
    for name in wanted:
        match = _re.search(
            rf"^{_re.escape(name)}.*?(?=\n\n\n|\ndef |\Z)", source,
            _re.S | _re.M)
        assert match, f"{name} is gone from the outputs repository"
        chunks.append(match.group(0))
    exec("\n\n".join(chunks), namespace)  # noqa: S102 — our own source, read above

    for doc_id in ("bat", "u-17", "u-17__", "kf-", "hello world", "café",
                   "BAT_664_r_00027", "saa-0001-test", "latest", "a"):
        here = document_id.slug_violation(doc_id)
        there = namespace["slug_violation"](doc_id)
        assert bool(here) == bool(there), (
            f"{doc_id!r}: this repository says {here!r}, the outputs "
            f"repository says {there!r}")
        assert document_id.is_test_id(doc_id) == namespace["is_test_id"](doc_id), (
            f"{doc_id!r}: the two repositories disagree about test ids")


# ── the publisher ───────────────────────────────────────────────────────

class _NoHTTP:
    """Any request at all means the refusal came too late."""

    def request(self, *a, **kw):  # pragma: no cover - must never run
        raise AssertionError(f"the publisher called the API: {a} {kw}")


@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    """A document with something to publish, so nothing else stops the call."""
    for attribute in ("TRANSCRIPTIONS_DIR", "DESCRIPTIONS_DIR", "OUTPUTS_DIR"):
        monkeypatch.setattr(config, attribute, tmp_path)
    monkeypatch.setattr(pg, "is_enabled", lambda: True)

    def write(doc_id):
        (tmp_path / f"{doc_id}.txt").write_text("Wir Johans tuon kunt", encoding="utf-8")
        (tmp_path / f"{doc_id}_pipeline.json").write_text(
            json.dumps({"doc_id": doc_id}), encoding="utf-8")
    return write


@pytest.mark.parametrize("doc_id", [
    "u-17__", "kf-", "saa-0001-test", "hello world", "../etc/passwd",
])
def test_the_publisher_refuses_before_touching_the_api(doc_id, artifacts, monkeypatch):
    committed = []
    monkeypatch.setattr(
        pg, "_commit_files",
        lambda files, message, session=None: committed.append(files))
    try:
        artifacts(doc_id)
    except OSError:
        pass  # a name the local filesystem will not take is refused anyway
    with pytest.raises(pg.DocumentIdRefused) as caught:
        pg.publish_doc(doc_id, session=_NoHTTP())
    assert doc_id in str(caught.value)
    assert committed == [], f"{doc_id!r} was committed: {committed}"


def test_a_refusal_says_what_to_do(artifacts):
    artifacts("u-17__")
    with pytest.raises(pg.DocumentIdRefused) as caught:
        pg.publish_doc("u-17__", session=_NoHTTP())
    message = str(caught.value)
    assert "permanent public URL" in message
    assert "Rename the source material" in message


def test_a_valid_id_still_publishes(artifacts, monkeypatch):
    """The guard must not be in the way of the ordinary case."""
    committed = {}
    monkeypatch.setattr(
        pg, "_commit_files",
        lambda files, message, session=None: committed.update(files)
        or "https://github.test/commit/abc")
    artifacts("saa-0428")
    url = pg.publish_doc("saa-0428", session=None)
    assert url == "https://github.test/commit/abc"
    assert any(name.startswith("docs/saa-0428/") for name in committed)
    assert all(name.startswith("docs/saa-0428/") for name in committed), committed


def test_no_published_path_can_leave_the_docs_tree(artifacts, monkeypatch):
    """The property the slug policy buys, stated directly."""
    committed: dict = {}
    monkeypatch.setattr(
        pg, "_commit_files",
        lambda files, message, session=None: committed.update(files)
        or "https://github.test/commit/abc")
    artifacts("saa-0428")
    pg.publish_doc("saa-0428", session=None)
    for name in committed:
        assert ".." not in Path(name).parts, f"{name} climbs out of docs/"
        assert name.startswith("docs/"), f"{name} is outside docs/"
