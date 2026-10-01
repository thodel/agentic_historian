"""Where a reading came from, as data beside the reading itself (#482).

A published reading says today: here is text. It does not say which image it
came from, which model produced it, in which version, when, or in which run.
Somebody citing it in three years cannot check whether it still holds; somebody
comparing it with a newer reading cannot tell whether the model changed or the
page did.

That is the same gap in each of the three publication routes and worth something
different in each: the edition needs the shelfmark, the dataset needs the model
version, the catalogue needs the source reference. All three need the link to
the image.

**The link already exists and was being thrown away.** The page cache writes a
sidecar for every file it fetches, describing the *archival* bytes — name, size,
sha256, formed from what came over the wire, in the one moment those bytes are
present as a whole (afterwards only the JPEG is there). None of it appeared in
the output.

Three rules this module exists to enforce
-----------------------------------------

**A digest names the original or it is absent.** Never the derivative. The
working copy is a JPEG re-encoded from a TIFF; its digest is a different number
that looks exactly as trustworthy. A page whose sidecar is missing therefore
gets ``sha256: None`` and ``sha256_of: None``, and nothing in this module can
put the working copy's digest in that field — :func:`source_ref` is never given
the working copy's bytes, only its name.

**A revision comes from the gateway, or it is marked as not having come from
there.** Our own ``models.yaml`` is deliberately not consulted: an id without a
version says only which line stood in a configuration file when the run began.
The gateway reports what identifies the weights, and that is not always a
version — see :data:`REVISION_KINDS`.

**Everything that holds for the whole run is said once.** The run header carries
source, model list, runner version and the window; the page records carry what
is theirs. :func:`contradictions` is the proof that the two never disagree,
because a header and a page record that say different things about the model are
worse than neither.

Not here, on purpose: a citation format (D0 settles the formulas), any judgement
of how good a reading is (that is #483, which writes its own block beside this
one), and copying the images anywhere.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

__all__ = [
    "PROVENANCE_SCHEMA",
    "RUNNER_NAME",
    "RUNNER_VERSION",
    "REVISION_KINDS",
    "ModelIdentity",
    "bestand_of",
    "contradictions",
    "model_identity",
    "page_provenance",
    "run_header",
    "runner_identity",
    "source_ref",
]

#: Versioned apart from ``atr_batch.SCHEMA`` on purpose. That constant gates
#: resume — ``is_complete`` rejects a page written under a different value, so
#: bumping it means re-reading the whole corpus. The provenance block is an
#: *addition* to that payload, so pages written before it exist stay complete
#: and keep what they have, which is exactly what #482 asks for ("a resumed run
#: does not change the provenance of pages already written"). The consequence is
#: real and deliberate: a corpus read across the change carries pages with a
#: provenance block and pages without, and the absence is visible rather than
#: filled in with a guess about a run nobody observed.
PROVENANCE_SCHEMA = "ah-provenance/1"

RUNNER_NAME = "agentic-historian"

#: Kept in step with ``pyproject.toml`` by a test rather than read from it at
#: run time: the deployed runner may be an installed package with no pyproject
#: beside it, and a provenance field that silently becomes "unknown" on the one
#: host that matters is worse than a constant somebody has to bump.
RUNNER_VERSION = "0.1.0"

#: What the gateway's answer lets us say about *which* weights ran, in the four
#: cases that actually occur. The distinction is the point of the field: three
#: of these four are not versions, and a record that presented them as one would
#: be citable-looking and wrong.
#:
#: * ``zenodo_record`` — a Zenodo record id. Genuinely version-bearing: Zenodo
#:   mints a new record per version, so the id fixes the deposited files. This
#:   is the only kind that yields a ``revision``.
#: * ``unversioned_repo`` — the gateway names a Hugging Face repository and no
#:   commit. A repository name is not a version; its ``main`` moves. ``revision``
#:   is None and ``weights`` says which repo.
#: * ``unversioned_path`` — weights on the serving host, by path. A path is not a
#:   version either, and this is the case for every model we trained ourselves.
#: * ``unreported`` — the gateway said nothing: the model is not in ``/models``,
#:   or ``/models`` did not answer. Not the same as "no version exists", and the
#:   two must not collapse into one empty string.
REVISION_KINDS = ("zenodo_record", "unversioned_repo", "unversioned_path",
                  "unreported")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── the model ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ModelIdentity:
    """Which model ran, as the gateway describes it — never as we configured it.

    ``level`` is the recognition level #482 asks for, ``page`` or ``line``, and
    it is read from the registry entry the gateway serves rather than guessed
    from the engine name or from whether ``lines`` came back empty. A page-level
    engine returning no lines and a line-level engine that found none look the
    same in the output and are not the same fact.
    """

    model: str
    engine: Optional[str] = None
    level: Optional[str] = None
    revision: Optional[str] = None
    revision_kind: str = "unreported"
    #: What the gateway named as the weights — a record id, a repo, or a path.
    #: Carried even when it is not a revision, because "repo X at an unknown
    #: commit" is a far better citation than silence.
    weights: Optional[str] = None
    base_model: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "engine": self.engine,
            "level": self.level,
            "revision": self.revision,
            "revision_kind": self.revision_kind,
            "weights": self.weights,
            "base_model": self.base_model,
        }


def _spec_for(model: str, models_payload: Optional[dict]) -> Optional[dict]:
    """The gateway's registry entry for one model, or None.

    Takes the ``/models`` body as the gateway returns it — ``{"models": [...]}``
    — which is what ``mcp_atr.server.gateway_probe`` files under ``"models"``.
    A probe that failed files an ``{"error": ...}`` or ``{"status": ...}`` dict
    there instead, and that has no entries, so it falls out here as a miss
    rather than as an exception on the provenance path.
    """
    if not isinstance(models_payload, dict):
        return None
    for spec in models_payload.get("models") or []:
        if isinstance(spec, dict) and spec.get("id") == model:
            return spec
    return None


def model_identity(model: str,
                   models_payload: Optional[dict] = None) -> ModelIdentity:
    """What the gateway says about ``model``, with the revision honestly kinded.

    Never raises and never falls back to our own registry. A gateway that did
    not answer gives ``revision_kind="unreported"``, which is the truthful
    record of a run whose model version nobody can now establish — and is
    distinguishable from a model that has no version to report.
    """
    spec = _spec_for(model, models_payload)
    if spec is None:
        return ModelIdentity(model=model)

    level = spec.get("level")
    identity = {
        "model": model,
        "engine": spec.get("engine") or None,
        "level": level if level in ("page", "line") else None,
        "base_model": spec.get("base_model") or None,
    }

    # Order matters: a model can carry more than one of these, and the
    # version-bearing one wins. Only the first is a revision.
    if spec.get("zenodo_id"):
        return ModelIdentity(**identity, revision=str(spec["zenodo_id"]),
                             revision_kind="zenodo_record",
                             weights=str(spec["zenodo_id"]))
    if spec.get("hf_repo"):
        return ModelIdentity(**identity, revision_kind="unversioned_repo",
                             weights=str(spec["hf_repo"]))
    if spec.get("local_path"):
        return ModelIdentity(**identity, revision_kind="unversioned_path",
                             weights=str(spec["local_path"]))
    return ModelIdentity(**identity)


def model_identities(models: Sequence[str],
                     models_payload: Optional[dict] = None
                     ) -> dict[str, ModelIdentity]:
    """:func:`model_identity` for a run's whole model list, keyed by id."""
    return {m: model_identity(m, models_payload) for m in models}


# ── the page ─────────────────────────────────────────────────────────────────

def bestand_of(doc_id: str) -> Optional[str]:
    """The holding a page belongs to: the top folder under the corpus root.

    ``Marbach/letter-0001`` is Marbach. A flat corpus has no holding and gets
    None rather than ``""`` — "this corpus is not divided into holdings" and
    "the holding is the empty string" are different statements, and only one of
    them is true.
    """
    first = (doc_id or "").strip("/").split("/")[0]
    return first or None


def _size_of(path) -> Optional[int]:
    try:
        return Path(path).stat().st_size
    except OSError:
        return None


def _sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_ref(page, source: Optional[dict] = None,
               read_path: Optional[Path] = None,
               digest: Optional[Callable[[Path], str]] = None) -> dict:
    """Which bytes produced this reading, with the digest's subject named.

    Three cases, and the third is the one #482 is about:

    * **A sidecar.** The page cache hashed the original while it had it in hand,
      which over a mount is the only affordable moment. That digest is used.
    * **No sidecar, and the original is what was read.** The mirror path: there
      is no working copy, so hashing the file *is* hashing the original. This is
      what the runner always did and it stays.
    * **No sidecar, and a working copy is all there is.** ``sha256`` is None and
      ``sha256_of`` is None with it.

    The third case is the trap. Hashing ``read_path`` there would produce a
    perfectly plausible 64 hex characters identifying bytes no archive holds —
    a JPEG re-encoded from the archival TIFF — and a reader could not tell it
    from the real thing. So ``read_path`` is used for its *name* only, never as
    a source of bytes, and that is enforced here rather than trusted to callers.
    """
    record = dict(source) if source else {}
    digest_fn = digest or _sha256_of_file
    sha = record.get("sha256") or None
    if sha is None and (read_path is None
                        or Path(read_path) == Path(page.path)):
        try:
            sha = digest_fn(Path(page.path))
        except OSError:
            sha = None
    ref = {
        "key": page.key,
        "doc_id": page.doc_id,
        "bestand": bestand_of(page.doc_id),
        "name": record.get("name") or page.name,
        "bytes": record.get("bytes") if record.get("bytes") is not None
                 else _size_of(page.path),
        "sha256": sha,
        # "original" or None. Never "working_copy": see above.
        "sha256_of": "original" if sha else None,
    }
    if record.get("pdf_page") is not None:
        ref["pdf_page"] = record["pdf_page"]
    elif getattr(page, "pdf_page", None) is not None:
        ref["pdf_page"] = page.pdf_page
    if record.get("remote"):
        ref["remote"] = record["remote"]
    if read_path is not None and Path(read_path) != Path(page.path):
        ref["working_copy"] = Path(read_path).name
    return ref


def page_provenance(page, model: str, run: str, result,
                    *, source: Optional[dict] = None,
                    read_path: Optional[Path] = None,
                    identity: Optional[ModelIdentity] = None,
                    recognised_at: Optional[str] = None) -> dict:
    """One page's provenance record: enough to find the image and the model.

    ``identity`` is the gateway's word on the model (see :func:`model_identity`).
    Omitted, the record says ``revision_kind="unreported"`` rather than reaching
    for our configuration file.

    ``level`` falls back to nothing. The gateway's registry is the only thing
    that knows whether a model reads lines or pages; inferring it from an empty
    ``lines`` list would make a line model that segmented nothing look like a
    page model, which is the error this field exists to prevent.
    """
    identity = identity or ModelIdentity(model=model)
    return {
        "schema": PROVENANCE_SCHEMA,
        "run": run,
        "source": source_ref(page, source, read_path),
        "model": identity.to_dict(),
        "gateway_version": getattr(result, "service_version", None) or None,
        "segmented_by": getattr(result, "segmented_by", None),
        "recognised_at": recognised_at or _now(),
    }


# ── the run ──────────────────────────────────────────────────────────────────

def git_commit(repo: Optional[Path] = None) -> Optional[str]:
    """The checkout's commit, or None when there is no answer to be had.

    None on a deployed copy with no ``.git``, on a git that is not installed,
    and on a repository that has no commit yet. All three mean the same thing
    for a citation — the runner's exact source cannot be established from here —
    and none of them may cost a run.
    """
    root = Path(repo) if repo else Path(__file__).resolve().parent
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    sha = (out.stdout or "").strip()
    return sha if out.returncode == 0 and sha else None


def runner_identity(commit: Optional[str] = None,
                    *, resolve: bool = True) -> dict:
    """Which runner produced the readings.

    ``commit_source`` is the three-valued part: ``"git"`` when the checkout
    answered, ``"unavailable"`` when it could not. A null commit with no reason
    beside it reads like a finding, and in this codebase that has been wrong
    four times (#119, #115, #487, serving#100).
    """
    sha = commit if commit is not None else (git_commit() if resolve else None)
    return {
        "name": RUNNER_NAME,
        "version": RUNNER_VERSION,
        "commit": sha,
        "commit_source": "git" if sha else "unavailable",
    }


def run_header(run: str, *, source: Optional[str] = None,
               models: Sequence[str] = (),
               identities: Optional[dict] = None,
               started_at: str = "", ended_at: str = "",
               gateway: Optional[str] = None,
               commit: Optional[str] = None,
               resolve_commit: bool = True) -> dict:
    """What holds for every page of the run, said once.

    The page records would otherwise carry the model list and the runner version
    six thousand times over, and six thousand copies of a fact are six thousand
    chances for one of them to differ.

    ``identities`` is ``{model: ModelIdentity}`` as :func:`model_identities`
    returns it. The header states the same identity the page records state, and
    :func:`contradictions` is the test that they agree.
    """
    identities = identities or {}
    listed = list(models) or sorted(identities)
    return {
        "schema": PROVENANCE_SCHEMA,
        "run": run,
        "source": source,
        "gateway": gateway,
        "runner": runner_identity(commit, resolve=resolve_commit),
        "started_at": started_at or None,
        "ended_at": ended_at or None,
        "models": [
            (identities.get(m) or ModelIdentity(model=m)).to_dict()
            for m in listed
        ],
    }


def contradictions(header: dict, page: dict) -> list[str]:
    """Where a run header and a page record disagree. Empty is the good case.

    Only the model identity is compared, and only the fields that are *claims
    about the world* rather than observations of one page: the run, the model
    id, its revision and kind, its level and engine. A page recognised in an
    earlier run under a different model version is not a contradiction in that
    run's header — it is a page from another run, and the ``run`` mismatch is
    what this reports.

    A field the header leaves open is not a contradiction of a page that names
    it: the header is a summary, and a summary that could not establish a
    revision has not made a conflicting claim about one. "Open" is two values
    here, not one — ``None`` for the fields that can be absent, and
    ``"unreported"`` for ``revision_kind``, which never is. Treating
    ``"unreported"`` as a claim would make every run taken without a probe read
    as self-contradictory, which is the opposite of what this function is for.
    """
    found: list[str] = []
    if header.get("run") != page.get("run"):
        found.append(f"run: header {header.get('run')!r} "
                     f"vs page {page.get('run')!r}")

    page_model = page.get("model") or {}
    model_id = page_model.get("model")
    listed = {m.get("model"): m for m in header.get("models") or []}
    if model_id not in listed:
        found.append(f"model {model_id!r} is not in the run header")
        return found

    for field in ("engine", "level", "revision", "revision_kind", "weights"):
        theirs = listed[model_id].get(field)
        mine = page_model.get(field)
        if theirs is None or mine is None:
            continue
        if "unreported" in (theirs, mine):
            continue
        if theirs != mine:
            found.append(f"{field}: header {theirs!r} vs page {mine!r}")
    return found
