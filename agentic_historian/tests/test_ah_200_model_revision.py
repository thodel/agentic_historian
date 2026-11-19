"""The version of the weights, now that the gateway can say it (serving#200).

``/models`` carries two new facts since serving-atr-inference#229 and #275:

* ``weights_digest`` — sha256 over the weight files, measured at the promotion
  gate. The version every model we trained ourselves can have.
* ``hf_revision`` — the commit of an ``hf_repo`` from the gateway's hub cache,
  with ``loaded`` saying whether the running instance loaded it or the cache
  merely resolves the ref to it now.

Until now ``provenance.model_identity`` read neither, so every self-trained
model was ``unversioned_path`` and every Hugging Face model
``unversioned_repo``. Pinned here: each becomes a revision of its own kind, the
loaded commit is told from the cached one, a value of the wrong shape never
reaches the field, and the order puts a Zenodo record first and a measurement
of the bytes before a commit name.

The payloads are written in the shape the gateway serves (serving's
``ModelInfo``, ``WeightsDigest`` and ``HfRevision``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import provenance   # noqa: E402

DIGEST = "d" * 64
COMMIT = "c0ffee" + "0" * 34

WEIGHTS_DIGEST = {
    "sha256": DIGEST,
    "files": [{"name": "model.mlmodel", "sha256": "e" * 64, "size_bytes": 12}],
    "host": "idhefix", "measured_at": "2026-11-18T09:00:00+00:00",
    "source": "/share/trained/german-medieval-v2/model.mlmodel",
}


def _rev(loaded):
    return {"sha": COMMIT, "ref": "main", "loaded": loaded,
            "read_at": "2026-11-19T07:00:00+00:00"}


def _payload(**spec):
    return {"models": [{"id": "m", "engine": "vllm", "level": "page", **spec}]}


def test_a_measured_digest_is_the_version_of_a_self_trained_model():
    got = provenance.model_identity("m", _payload(
        engine="kraken", level="line", local_path="/share/trained/german-medieval-v2",
        weights_digest=WEIGHTS_DIGEST))
    assert (got.revision, got.revision_kind) == (DIGEST, "weights_digest")
    assert got.revision_at == "2026-11-18T09:00:00+00:00"
    assert got.weights == "/share/trained/german-medieval-v2"


def test_the_loaded_commit_is_told_from_the_cached_one():
    loaded = provenance.model_identity("m", _payload(hf_repo="org/m", hf_revision=_rev(True)))
    cached = provenance.model_identity("m", _payload(hf_repo="org/m", hf_revision=_rev(False)))
    assert (loaded.revision, loaded.revision_kind) == (COMMIT, "hf_commit")
    assert (cached.revision, cached.revision_kind) == (COMMIT, "hf_commit_cached")
    assert loaded.weights == cached.weights == "org/m"
    assert loaded.revision_at == "2026-11-19T07:00:00+00:00"


@pytest.mark.parametrize("spec, kind", [
    (dict(hf_repo="org/m", hf_revision=None), "unversioned_repo"),
    (dict(hf_repo="org/m", hf_revision={"sha": "main", "loaded": True}), "unversioned_repo"),
    (dict(hf_repo="org/m", hf_revision={"sha": COMMIT[:39], "loaded": True}), "unversioned_repo"),
    (dict(hf_repo="org/m", hf_revision={"sha": COMMIT.upper(), "loaded": True}), "unversioned_repo"),
    (dict(hf_repo="org/m", hf_revision="c0ffee"), "unversioned_repo"),
    (dict(local_path="/w", hf_revision=_rev(True)), "unversioned_path"),   # no repo, no commit
    (dict(local_path="/w", weights_digest={"sha256": "d" * 63}), "unversioned_path"),
    (dict(local_path="/w", weights_digest={"sha256": None}), "unversioned_path"),
    (dict(local_path="/w", weights_digest=DIGEST), "unversioned_path"),    # a bare string
])
def test_a_value_of_the_wrong_shape_never_becomes_a_revision(spec, kind):
    got = provenance.model_identity("m", _payload(**spec))
    assert (got.revision, got.revision_kind, got.revision_at) == (None, kind, None)


def test_the_order_record_then_bytes_then_commit():
    everything = dict(zenodo_id="10.5281/zenodo.1", hf_repo="org/m",
                      weights_digest=WEIGHTS_DIGEST, hf_revision=_rev(True))
    assert provenance.model_identity("m", _payload(**everything)).revision_kind == "zenodo_record"
    del everything["zenodo_id"]
    measured = provenance.model_identity("m", _payload(**everything, local_path="/share/w"))
    assert measured.revision_kind == "weights_digest"
    assert measured.weights == "/share/w", "the digest measures the local file, not the repo"
    del everything["weights_digest"]
    assert provenance.model_identity("m", _payload(**everything)).revision_kind == "hf_commit"


def test_every_kind_is_declared_and_the_record_carries_revision_at():
    got = provenance.model_identity("m", _payload(hf_repo="org/m", hf_revision=_rev(False)))
    assert got.revision_kind in provenance.REVISION_KINDS
    assert {"weights_digest", "hf_commit", "hf_commit_cached"} <= set(provenance.REVISION_KINDS)
    assert got.to_dict()["revision_at"] == "2026-11-19T07:00:00+00:00"
    assert provenance.ModelIdentity(model="x").to_dict()["revision_at"] is None


def test_a_page_and_a_header_with_the_same_commit_do_not_contradict():
    """The header and the page carry one identity; the new fields do not make
    them disagree, and a different commit does."""
    ident = provenance.model_identity("m", _payload(hf_repo="org/m", hf_revision=_rev(True)))
    header = {"run": "r", "models": [ident.to_dict()]}
    page = {"run": "r", "model": ident.to_dict()}
    assert provenance.contradictions(header, page) == []
    other = dict(ident.to_dict(), revision="f" * 40)
    assert provenance.contradictions(header, {"run": "r", "model": other}) == [
        f"revision: header {COMMIT!r} vs page {'f' * 40!r}"]
