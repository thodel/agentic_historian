"""Provenance at every reading — without it none of the three routes is citable (#482).

A published reading says today: here is text. It does not say which image it
came from, which model produced it, in which version, when, or in which run.
Somebody citing it in three years cannot check whether it still holds; somebody
comparing it with a newer reading cannot tell whether the model changed or the
page did.

The link to the image **already existed and was thrown away**: the page cache
writes a sidecar describing the archival bytes — name, size, sha256, formed from
what came over the wire, in the one moment those bytes are present as a whole.
None of it reached the output.

The four tests #482 names are here under their own headings, and the one that
carries the most weight is the second: a page whose sidecar is missing must get
``sha256: null`` and must **not** be quietly described with the derivative's
hash. The JPEG's digest is 64 hex characters that look exactly as trustworthy
and identify bytes no archive holds.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import atr_batch as batch  # noqa: E402
import provenance  # noqa: E402
from utils import images  # noqa: E402


# ── fixtures ─────────────────────────────────────────────────────────────────

def a_png(path: Path, colour=(10, 20, 30)) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 30), colour).save(path, format="PNG")
    return path


@pytest.fixture
def share(tmp_path: Path) -> Path:
    """Two holdings, so `bestand` has something to be wrong about."""
    root = tmp_path / "mount" / "digitalisate"
    a_png(root / "Marbach" / "letter-0001" / "001.png", (10, 20, 30))
    a_png(root / "Inzigkofen" / "Ms-321" / "001.png", (90, 80, 70))
    return root


def reading(text="Euer Hochwohlgeboren", **kw):
    base = dict(text=text, lines=[], confidence=0.9, engine="kraken",
                timing_ms=12, truncated=False, service_version="0.9.1",
                segmented_by="kraken:blla", second_opinion=None)
    base.update(kw)
    return SimpleNamespace(**base)


#: One gateway `/models` body, in the shape `ModelInfo(**spec.model_dump())`
#: produces — every registry field, which is how the revision kinds below are
#: distinguishable at all.
MODELS = {"models": [
    {"id": "kraken-dh", "engine": "kraken", "level": "line",
     "zenodo_id": "10.5281/zenodo.7516057", "vram_mb": 1200},
    {"id": "trocr-kurrent", "engine": "trocr", "level": "line",
     "hf_repo": "org/trocr-kurrent", "vram_mb": 2400},
    {"id": "german-medieval-v1", "engine": "kraken", "level": "line",
     "local_path": "/mnt/models/german-medieval-v1.mlmodel",
     "base_model": "kraken-dh"},
    {"id": "party-page", "engine": "party", "level": "page"},
]}


# ── 1. the digest names the original, never the derivative ───────────────────

def test_the_provenance_names_the_sidecars_digest_not_the_jpegs(share, tmp_path):
    """#482's first test. The working copy is a JPEG re-encoded from the
    original, so its digest is a different number that looks just as good."""
    src = share / "Marbach" / "letter-0001" / "001.png"
    cache = images.PageCache(share, tmp_path / "cache")
    pages = batch.discover_pages(share)
    page = next(p for p in pages if p.path == src)
    read_path, record = cache.fetch(src)

    payload = batch._result_payload(page, "kraken-dh", "run", reading(),
                                    source=record, read_path=read_path)

    original = hashlib.sha256(src.read_bytes()).hexdigest()
    derivative = hashlib.sha256(read_path.read_bytes()).hexdigest()
    assert original != derivative          # or this test proves nothing
    assert payload["source"]["sha256"] == original
    assert payload["source"]["sha256_of"] == "original"
    assert derivative not in json.dumps(payload)


def test_the_working_copy_is_named_but_never_hashed(share, tmp_path):
    """Both facts have to survive: which image the model saw, and which bytes
    the reading cites. Collapsing them is the whole failure mode."""
    src = share / "Marbach" / "letter-0001" / "001.png"
    cache = images.PageCache(share, tmp_path / "cache")
    page = next(p for p in batch.discover_pages(share) if p.path == src)
    read_path, record = cache.fetch(src)

    ref = provenance.source_ref(page, record, read_path)

    assert ref["working_copy"] == read_path.name
    assert ref["working_copy"].endswith(".jpg")
    assert ref["sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()


# ── 2. a missing sidecar is null, not the derivative's hash ──────────────────

def test_a_page_without_a_sidecar_gets_a_null_digest(share, tmp_path):
    """#482's second test, and the one worth the most. There is a working copy
    and no record of the original, so there is no digest — and a plausible one
    is worse than none."""
    src = share / "Marbach" / "letter-0001" / "001.png"
    page = next(p for p in batch.discover_pages(share) if p.path == src)
    working = tmp_path / "cache" / "001.jpg"
    working.parent.mkdir(parents=True)
    working.write_bytes(b"a jpeg that is not the original")

    ref = provenance.source_ref(page, None, working)

    assert ref["sha256"] is None
    assert ref["sha256_of"] is None
    assert hashlib.sha256(working.read_bytes()).hexdigest() not in json.dumps(ref)


def test_a_cache_record_without_a_digest_does_not_borrow_one(share, tmp_path):
    """A half-written sidecar is what an interrupted eviction leaves. It names
    the file and not its digest, and the gap must stay a gap."""
    src = share / "Marbach" / "letter-0001" / "001.png"
    page = next(p for p in batch.discover_pages(share) if p.path == src)
    working = tmp_path / "001.jpg"
    working.write_bytes(b"derivative")

    ref = provenance.source_ref(page, {"name": "001.png", "bytes": 103}, working)

    assert (ref["sha256"], ref["sha256_of"]) == (None, None)
    assert ref["name"] == "001.png"        # what is known is still said


def test_the_mirror_path_still_hashes_the_original(share):
    """The other half, so the test above cannot be satisfied by refusing to
    hash anything: with no working copy the file read *is* the original, which
    is what the runner always did and must keep doing."""
    src = share / "Marbach" / "letter-0001" / "001.png"
    page = next(p for p in batch.discover_pages(share) if p.path == src)

    ref = provenance.source_ref(page, None, None)

    assert ref["sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()
    assert ref["sha256_of"] == "original"
    assert "working_copy" not in ref


def test_an_unreadable_original_is_null_rather_than_an_exception(share, tmp_path):
    """Provenance is a record of a run, not a gate on it. A page the share gave
    up after the reading was made must not cost the reading."""
    src = share / "Marbach" / "letter-0001" / "001.png"
    page = next(p for p in batch.discover_pages(share) if p.path == src)
    src.unlink()

    ref = provenance.source_ref(page, None, None)

    assert (ref["sha256"], ref["sha256_of"]) == (None, None)


# ── 3. the revision comes from the gateway, and says what kind it is ─────────

def test_a_zenodo_record_is_the_one_real_revision():
    """Zenodo mints a record per version, so the id fixes the deposited files."""
    identity = provenance.model_identity("kraken-dh", MODELS)

    assert identity.revision == "10.5281/zenodo.7516057"
    assert identity.revision_kind == "zenodo_record"
    assert identity.level == "line"
    assert identity.engine == "kraken"


def test_a_repo_without_a_commit_is_not_a_revision():
    """#482's reason, in one assertion: "an id without a version says only
    which line stood in a configuration file". `main` moves."""
    identity = provenance.model_identity("trocr-kurrent", MODELS)

    assert identity.revision is None
    assert identity.revision_kind == "unversioned_repo"
    assert identity.weights == "org/trocr-kurrent"   # still worth citing


def test_local_weights_are_a_path_and_a_path_is_not_a_version():
    """Every model we trained ourselves looks like this today."""
    identity = provenance.model_identity("german-medieval-v1", MODELS)

    assert identity.revision is None
    assert identity.revision_kind == "unversioned_path"
    assert identity.base_model == "kraken-dh"


def test_a_model_the_gateway_does_not_describe_is_unreported():
    """Not the same as "it has no version", and the two must not collapse."""
    identity = provenance.model_identity("mystery", MODELS)

    assert identity.revision_kind == "unreported"
    assert (identity.revision, identity.weights, identity.level) == (None, None, None)


def test_a_probe_that_failed_reads_as_unreported_rather_than_raising():
    """`gateway_probe` files a transport failure as `{"error": ...}` under the
    same key. Provenance never raises on the batch path."""
    for payload in ({"error": "connection refused"}, {"status": 502, "body": "x"},
                    None, {}, {"models": None}):
        identity = provenance.model_identity("kraken-dh", payload)
        assert identity.revision_kind == "unreported"


def test_the_level_is_read_from_the_registry_not_guessed_from_the_lines():
    """A page-level engine with no lines and a line model that segmented
    nothing look identical in the output and are not the same fact."""
    page_level = provenance.model_identity("party-page", MODELS)
    line_level = provenance.model_identity("kraken-dh", MODELS)

    assert page_level.level == "page"
    assert line_level.level == "line"


def test_our_own_registry_is_never_consulted(monkeypatch):
    """The field would otherwise be a restatement of our configuration file
    dressed as a fact about the weights."""
    import config

    monkeypatch.setattr(config, "ATR_GATEWAY_URL", "http://nowhere", raising=False)
    identity = provenance.model_identity("kraken-dh", None)

    assert identity.revision_kind == "unreported"


# ── 4. header and page never contradict each other ──────────────────────────

def test_the_header_and_the_page_agree_on_the_model(share, tmp_path):
    """#482's third test. A header and a page record saying different things
    about the model are worse than neither."""
    src = share / "Marbach" / "letter-0001" / "001.png"
    page = next(p for p in batch.discover_pages(share) if p.path == src)
    identities = provenance.model_identities(["kraken-dh"], MODELS)

    header = provenance.run_header("run", source=str(share), models=["kraken-dh"],
                                   identities=identities, started_at="t0",
                                   ended_at="t1", resolve_commit=False)
    payload = batch._result_payload(page, "kraken-dh", "run", reading(),
                                    identity=identities["kraken-dh"])

    assert provenance.contradictions(header, payload["provenance"]) == []


def test_a_disagreement_is_reported_rather_than_hidden():
    """The guard on the test above: `contradictions` has to be able to fail."""
    identities = provenance.model_identities(["kraken-dh"], MODELS)
    header = provenance.run_header("run", models=["kraken-dh"],
                                   identities=identities, resolve_commit=False)
    page = {"run": "run", "model": {"model": "kraken-dh",
                                    "revision": "10.5281/zenodo.9999999",
                                    "revision_kind": "zenodo_record",
                                    "level": "line", "engine": "kraken",
                                    "weights": "10.5281/zenodo.9999999"}}

    found = provenance.contradictions(header, page)

    assert any("revision" in f for f in found)
    assert any("9999999" in f for f in found)


def test_a_page_from_another_run_is_a_contradiction():
    """What a resumed run over a reused output directory would look like."""
    identities = provenance.model_identities(["kraken-dh"], MODELS)
    header = provenance.run_header("run-b", models=["kraken-dh"],
                                   identities=identities, resolve_commit=False)
    page = {"run": "run-a", "model": identities["kraken-dh"].to_dict()}

    assert any(f.startswith("run:") for f in provenance.contradictions(header, page))


def test_a_model_missing_from_the_header_is_a_contradiction():
    header = provenance.run_header("run", models=["kraken-dh"],
                                   identities=provenance.model_identities(
                                       ["kraken-dh"], MODELS),
                                   resolve_commit=False)
    page = {"run": "run", "model": {"model": "trocr-kurrent"}}

    assert provenance.contradictions(header, page)


def test_a_header_that_knows_less_does_not_contradict_a_page_that_knows_more():
    """A summary that could not establish a revision has not made a conflicting
    claim about one. Otherwise every run without a probe would read as broken."""
    header = provenance.run_header("run", models=["kraken-dh"], resolve_commit=False)
    page = {"run": "run", "model": provenance.model_identity(
        "kraken-dh", MODELS).to_dict()}

    assert provenance.contradictions(header, page) == []


def test_unreported_is_an_absence_and_not_a_claim():
    """`revision_kind` is the one field that is never None — "unreported" is
    how it says "I could not establish this". Compared as a value it would make
    every run taken without a probe read as self-contradictory, which is the
    opposite of what this function is for."""
    known = provenance.model_identity("kraken-dh", MODELS).to_dict()
    blind = provenance.ModelIdentity(model="kraken-dh").to_dict()

    header_blind = {"run": "r", "models": [blind]}
    header_known = {"run": "r", "models": [known]}

    assert provenance.contradictions(header_blind, {"run": "r", "model": known}) == []
    assert provenance.contradictions(header_known, {"run": "r", "model": blind}) == []
    # And it is still not a licence to differ on a kind both sides did report.
    other = dict(known, revision_kind="unversioned_repo", revision=None)
    assert provenance.contradictions(header_known, {"run": "r", "model": other})


def test_the_report_carries_the_header(share, tmp_path):
    """It belongs in `report.json`, beside the counts — not in `report.md`,
    which is prose for a person."""
    pages = batch.discover_pages(share)
    out = tmp_path / "out"

    report = batch.run_batch(pages, ["kraken-dh"], "run", out,
                             lambda p, m: reading(), retries=0, concurrency=1,
                             probe={"models": MODELS}, source=str(share),
                             gateway="http://gw:8000")

    header = report.to_dict()["provenance"]
    assert header["source"] == str(share)
    assert header["gateway"] == "http://gw:8000"
    assert header["started_at"] and header["ended_at"]
    assert header["models"][0]["revision"] == "10.5281/zenodo.7516057"
    assert header["runner"]["name"] == provenance.RUNNER_NAME


def test_every_written_page_agrees_with_the_header(share, tmp_path):
    """The two halves held against each other over a whole run, which is the
    form the contradiction would actually take."""
    pages = batch.discover_pages(share)
    out = tmp_path / "out"

    report = batch.run_batch(pages, ["kraken-dh", "trocr-kurrent"], "run", out,
                             lambda p, m: reading(), retries=0, concurrency=1,
                             probe={"models": MODELS}, source=str(share))

    header = report.to_dict()["provenance"]
    seen = 0
    for written in out.rglob("*.json"):
        data = json.loads(written.read_text(encoding="utf-8"))
        if "provenance" not in data:
            continue
        seen += 1
        assert provenance.contradictions(header, data["provenance"]) == []
    assert seen == len(pages) * 2


# ── 5. a resumed run does not touch what is already written ─────────────────

def test_a_resumed_run_leaves_written_provenance_alone(share, tmp_path):
    """#482's fourth test. The provenance of a page is the record of the run
    that read it, and a later run must not restate it."""
    pages = batch.discover_pages(share)
    out = tmp_path / "out"
    batch.run_batch(pages, ["kraken-dh"], "run", out, lambda p, m: reading(),
                    retries=0, concurrency=1, probe={"models": MODELS})
    written = out / "kraken-dh" / f"{pages[0].key}.json"
    before = written.read_text(encoding="utf-8")

    # A second run with a *different* answer from the gateway for the same
    # model: were anything rewritten, this is what would overwrite it.
    moved = {"models": [dict(MODELS["models"][0],
                             zenodo_id="10.5281/zenodo.9999999")]}
    outcome = batch.run_batch(pages, ["kraken-dh"], "run", out,
                              lambda p, m: reading("different"), retries=0,
                              concurrency=1, probe=moved)

    assert outcome.models[0].skipped == len(pages)
    assert written.read_text(encoding="utf-8") == before


def test_a_page_written_before_provenance_existed_stays_complete(share, tmp_path):
    """The deliberate consequence of not bumping `SCHEMA`: a corpus read across
    the change carries pages with a provenance block and pages without, and the
    absence is visible rather than filled in with a guess about a run nobody
    observed. Bumping it instead would mean re-reading 6742 pages."""
    pages = batch.discover_pages(share)
    out = tmp_path / "out"
    old = out / "kraken-dh" / f"{pages[0].key}.json"
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps({"schema": batch.SCHEMA, "text": "read last week",
                               "lines": [], "source": {"key": pages[0].key}}),
                   encoding="utf-8")
    (out / "kraken-dh" / f"{pages[0].key}.txt").write_text("read last week",
                                                           encoding="utf-8")

    outcome = batch.run_model(pages, "kraken-dh", "run", out,
                              lambda p, m: reading(), retries=0, concurrency=1)

    assert outcome.skipped == 1
    data = json.loads(old.read_text(encoding="utf-8"))
    assert "provenance" not in data          # untouched, and legibly so


# ── 6. the record is enough to find the page again ──────────────────────────

def test_the_holding_is_the_top_folder_and_a_flat_corpus_has_none():
    """"The holding is the empty string" and "this corpus has no holdings" are
    different statements, and only one of them is true."""
    assert provenance.bestand_of("Marbach/letter-0001") == "Marbach"
    assert provenance.bestand_of("Marbach") == "Marbach"
    assert provenance.bestand_of("") is None
    assert provenance.bestand_of("/") is None


def test_a_page_can_be_found_from_its_record_alone(share, tmp_path):
    """#482's live check, offline: holding plus folder plus original filename
    locate the file under the root the header names, and its digest matches."""
    pages = batch.discover_pages(share)
    out = tmp_path / "out"
    report = batch.run_batch(pages, ["kraken-dh"], "run", out,
                             lambda p, m: reading(), retries=0, concurrency=1,
                             probe={"models": MODELS}, source=str(share))

    page = json.loads(
        (out / "kraken-dh" / f"{pages[0].key}.json").read_text(encoding="utf-8"))
    ref = page["provenance"]["source"]
    root = Path(report.to_dict()["provenance"]["source"])

    found = root / ref["doc_id"] / ref["name"]
    assert found.is_file()
    assert ref["bestand"] == ref["doc_id"].split("/")[0]
    assert hashlib.sha256(found.read_bytes()).hexdigest() == ref["sha256"]


def test_the_page_record_names_the_run_the_gateway_and_the_moment(share, tmp_path):
    pages = batch.discover_pages(share)
    page = pages[0]

    payload = batch._result_payload(page, "kraken-dh", "lassberg-01", reading(),
                                    identity=provenance.model_identity(
                                        "kraken-dh", MODELS))

    prov = payload["provenance"]
    assert prov["schema"] == provenance.PROVENANCE_SCHEMA
    assert prov["run"] == "lassberg-01"
    assert prov["gateway_version"] == "0.9.1"
    assert prov["segmented_by"] == "kraken:blla"
    assert prov["recognised_at"].endswith("+00:00")
    # One timestamp, not two: the payload and its provenance block naming
    # different seconds would be a contradiction nobody could resolve.
    assert prov["recognised_at"] == payload["recognised_at"]


# ── 7. the runner says which runner it was ──────────────────────────────────

def test_the_runner_version_is_the_packages_version():
    """Kept in step by this test rather than read from pyproject at run time:
    the deployed runner may be an installed package with no pyproject beside
    it, and a field that silently becomes "unknown" on the one host that
    matters is worse than a constant somebody has to bump."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = next(line.split("=", 1)[1].strip().strip('"')
                    for line in pyproject.read_text(encoding="utf-8").splitlines()
                    if line.strip().startswith("version"))

    assert provenance.RUNNER_VERSION == declared


def test_a_missing_commit_says_why_rather_than_reading_as_a_finding():
    """A null with no reason beside it has been wrong four times in this
    codebase (#119, #115, #487, serving#100)."""
    without = provenance.runner_identity(None, resolve=False)
    with_sha = provenance.runner_identity("a" * 40)

    assert without["commit"] is None
    assert without["commit_source"] == "unavailable"
    assert with_sha["commit_source"] == "git"


def test_the_commit_lookup_never_raises(tmp_path):
    """No .git, no git binary, a repository with no commit: all three mean the
    same thing for a citation, and none of them may cost a run."""
    assert provenance.git_commit(tmp_path) is None


# ── 8. nothing that already read these files breaks ─────────────────────────

def test_the_existing_source_keys_are_all_still_there(share, tmp_path):
    """`report_from_outputs`, `line_stubs`, `mcp_atr.jobs` and `publish_tree`
    all read this payload. The provenance block is an addition, not a reshape."""
    src = share / "Marbach" / "letter-0001" / "001.png"
    cache = images.PageCache(share, tmp_path / "cache")
    page = next(p for p in batch.discover_pages(share) if p.path == src)
    read_path, record = cache.fetch(src)

    payload = batch._result_payload(page, "kraken-dh", "run", reading(),
                                    source=record, read_path=read_path)

    for key in ("schema", "run", "model", "engine", "doc_id", "source", "text",
                "lines", "confidence", "segmented_by", "timing_ms", "truncated",
                "second_opinion", "gateway_version", "recognised_at"):
        assert key in payload, key
    for key in ("name", "bytes", "sha256", "key", "working_copy"):
        assert key in payload["source"], key
    assert len(payload["source"]["sha256"]) == 64
