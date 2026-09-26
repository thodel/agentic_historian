"""
utils/publish_github.py — publish processed outputs to a public GitHub repo (#200).

After a document is processed, its **text** artifacts (transcription, source
description, entities, pipeline JSON) plus a rendered ``index.md`` are committed
to a dedicated output repo under ``docs/<doc_id>/`` in ONE atomic commit via the
GitHub Git Data API (blobs → tree → commit → update ref). The repo is served as
a GitHub Pages catalogue (#201). Because every run is a commit, each document
gets a full history/diff for free.

Source IMAGES are never committed — the doc page links back to the source. The
publisher is opt-in (``ENABLE_GITHUB_PUBLISH``) and non-fatal: any failure is
logged and returns None, never breaking the pipeline.
"""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Optional

import requests
from loguru import logger

import config

_API = "https://api.github.com"
_TIMEOUT = 30


def is_enabled() -> bool:
    """True when publishing is switched on and the repo + token are configured."""
    return bool(
        config.ENABLE_GITHUB_PUBLISH
        and config.GITHUB_OUTPUT_REPO
        and config.GITHUB_TOKEN
    )


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    return s


#: GitHub answers a *secondary* rate limit with 403, not 429, and the body says
#: so in prose. A publish that makes thousands of write calls will meet it, and
#: `raise_for_status` turned that into a dead run 24 of 68 commits in
#: (2026-09-26). These are the statuses worth waiting out rather than failing on.
_RETRY_STATUS = frozenset({403, 429, 500, 502, 503, 504})
_RETRY_ATTEMPTS = 5


def _retry_after_s(response: requests.Response, attempt: int) -> float:
    """How long to wait before retrying, taking the server's word for it.

    GitHub says when to come back — ``Retry-After`` in seconds, or
    ``x-ratelimit-reset`` as an epoch. Guessing instead of reading those is how a
    backoff loop turns one rate limit into a longer one.
    """
    after = response.headers.get("Retry-After")
    if after:
        try:
            return max(1.0, float(after))
        except ValueError:
            pass
    reset = response.headers.get("x-ratelimit-reset")
    remaining = response.headers.get("x-ratelimit-remaining")
    if reset and remaining == "0":
        try:
            return max(1.0, float(reset) - time.time())
        except ValueError:
            pass
    return min(2.0 ** attempt, 60.0)


def _write(session: requests.Session, method: str, url: str, **kw):
    """One write call to the API, retried when the answer is "not now".

    Only the statuses in :data:`_RETRY_STATUS` are retried, and a 403 that is a
    genuine permission failure is among them — it costs a few waits before the
    error surfaces, which is the cheaper mistake: the other way round, an hour of
    publishing dies on a limit that would have lifted in sixty seconds.
    """
    # Dispatched through ``session.post`` / ``.patch`` rather than
    # ``session.request``: that is the surface the rest of this module already
    # uses, and the one every test double here implements.
    call = getattr(session, method.lower())
    last = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        last = call(url, timeout=_TIMEOUT, **kw)
        if last.status_code not in _RETRY_STATUS:
            last.raise_for_status()
            return last
        if attempt == _RETRY_ATTEMPTS:
            break
        wait = _retry_after_s(last, attempt)
        logger.warning(
            f"[Publish] {method} {url.rsplit('/', 1)[-1]}: {last.status_code} "
            f"(attempt {attempt}/{_RETRY_ATTEMPTS}) — waiting {wait:.0f}s"
        )
        time.sleep(wait)
    last.raise_for_status()
    return last


def collect_artifacts(doc_id: str) -> dict[str, Path]:
    """Map the published filename → local path for whichever outputs exist.

    Text artifacts only; source images are intentionally excluded.
    """
    candidates = {
        "transcription.txt": config.TRANSCRIPTIONS_DIR / f"{doc_id}.txt",
        "description.md": config.DESCRIPTIONS_DIR / f"{doc_id}.md",
        "description.json": config.DESCRIPTIONS_DIR / f"{doc_id}.json",
        "entities.md": config.OUTPUTS_DIR / f"{doc_id}_entities.md",
        "entities.json": config.OUTPUTS_DIR / f"{doc_id}_entities.json",
        "pipeline.json": config.OUTPUTS_DIR / f"{doc_id}_pipeline.json",
    }
    return {name: p for name, p in candidates.items() if p.exists()}


def _val(x) -> str:
    """Unwrap Agent B's ``{"wert": …}`` element shape (or return the value)."""
    if isinstance(x, dict):
        return str(x.get("wert") or x.get("value") or "")
    return "" if x is None else str(x)


_AUTHORITIES = (
    ("gnd", "GND", "https://d-nb.info/gnd/{}"),
    ("hls", "HLS", "https://hls-dhs-dss.ch/de/{}"),
    ("wikidata", "WD", "https://www.wikidata.org/entity/{}"),
)


def _entity_links(ent: dict) -> str:
    out = []
    for key, label, tmpl in _AUTHORITIES:
        v = ent.get(key) or ent.get(f"{key}_id")
        if v:
            out.append(f"[{label}]({tmpl.format(v)})")
    return " · ".join(out)


def _recognition_filename(r: dict) -> str:
    """Export path for ONE candidate transcription (#284).

    ``recognitions/<page>/<engine>-<model>.txt`` — page-scoped so a multi-page
    order's candidates are attributable, and slashes in a model id (raw Zenodo
    DOIs) are flattened so they don't create stray directories. Shared by the
    index renderer and the publisher so the links always match the files written.
    """
    page = (r.get("page") or "").strip()
    engine = (r.get("engine") or "engine").strip() or "engine"
    model = (r.get("model_id") or "").strip().replace("/", "_")
    stem = f"{engine}-{model}" if model else engine
    page_dir = ""
    if page:
        page_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", page.rsplit(".", 1)[0])
        page_dir = f"{page_slug}/"
    return f"recognitions/{page_dir}{stem}.txt"


def _index_md(doc_id: str, artifacts: dict[str, bytes], source_url: Optional[str]) -> str:
    """Render the per-document page (#201): metadata + entities (with authority
    links) + transcription + file links. Parses ``pipeline.json`` (the single
    source that carries description/entities/a_meta); degrades gracefully.
    """
    pipe: dict = {}
    if "pipeline.json" in artifacts:
        try:
            pipe = json.loads(artifacts["pipeline.json"].decode("utf-8", "replace"))
        except (ValueError, TypeError):
            pipe = {}
    sj = (pipe.get("description") or {}).get("source_json") or {}
    a_meta = pipe.get("a_meta") or {}
    entities = (pipe.get("entities") or {}).get("entities") or []
    closest = pipe.get("closest_reading") or {}
    transcription = closest.get("text") or pipe.get("transcription") or \
        artifacts.get("transcription.txt", b"").decode("utf-8", "replace")

    L = ["---", "layout: default", f"title: {doc_id}", "---", "", f"# {doc_id}", ""]
    if source_url:
        L += [f"**Quelle:** [{source_url}]({source_url})", ""]

    meta = []
    for element, label in (("Datierung", "Datierung"), ("Sprache", "Sprache"),
                           ("Schrift", "Schrift")):
        v = _val(sj.get(element))
        if v:
            meta.append(f"| {label} | {v} |")
    if a_meta.get("qa_score") is not None:
        meta.append(f"| HTR | {a_meta.get('source', '?')} (QA {a_meta.get('qa_score')}) |")
    if meta:
        L += ["## Metadaten", "", "| Feld | Wert |", "|---|---|", *meta, ""]

    if entities:
        L += ["## Entitäten", ""]
        by_type: dict[str, list] = {}
        for e in entities:
            by_type.setdefault(e.get("type", "?"), []).append(e)
        for t in sorted(by_type):
            L.append(f"### {t}")
            for e in by_type[t]:
                name = e.get("normalised") or e.get("text") or ""
                links = _entity_links(e)
                L.append(f"- {name}" + (f" — {links}" if links else ""))
            L.append("")

    if transcription.strip():
        if closest:
            chosen = ", ".join(closest.get("chosen") or [])
            action = "combined" if closest.get("combined") else "selected"
            L += ["## Closest available reading", "",
                  ("_Revisable editorial choice: the closest option currently "
                   "available, not an independently established reference text._"), "",
                  (f"_Provenance: {action} from `{chosen}`; "
                   f"editor `{closest.get('editor_pseudonym', 'unknown')}`; "
                   f"confirmed {closest.get('confirmed_at', 'unknown')}._"), ""]
        else:
            L += ["## Transkription", ""]
        L += ["```", transcription.strip(), "```", ""]

    # ── Recognition results (#238, per-page exports #284) ──────────────────
    # Every candidate transcription is exported as its own file and represented
    # individually here — page-attributed, so a multi-page order's N engines × M
    # pages stay tellable apart.
    _recs = pipe.get("recognitions", []) or []
    if _recs:
        L += ["## Recognition results", ""]
        if a_meta.get("fusion_strategy"):
            _cer = a_meta.get("fusion_agreement_cer", 0.0)
            _n_arb = a_meta.get("fusion_arbitrated", 0)
            _skipped = a_meta.get("fusion_llm_skipped", False)
            L += [f"_Fused from {len(_recs)} candidate transcription(s): {_n_arb} span(s) "
                  f"LLM-arbitrated, agreement CER {_cer:.1%}"
                  f"{' — LLM skipped (high agreement)' if _skipped else ''}._", ""]
        else:
            L += [f"_{len(_recs)} independent candidate transcription(s), "
                  f"each exported separately._", ""]

        # One row per candidate, linking its own export (table kept unbroken).
        L += ["| Page | Engine | Model | Chars | Export |", "|---|---|---|---|---|"]
        for r in _recs:
            _txt = r.get("text", "") or ""
            _err = r.get("error", "") or ""
            _fname = _recognition_filename(r)
            if _txt and not _err:
                _link = f"[{_fname.rsplit('/', 1)[-1]}]({_fname})"
            elif _err:
                _link = f"⚠️ {_err[:40]}"
            else:
                _link = "—"
            L.append(f"| `{r.get('page') or '—'}` | `{r.get('engine', '?')}` | "
                     f"`{r.get('model_id', '—')}` | {len(_txt)} | {_link} |")
        L += [""]

        # Full text per candidate, collapsed (after the table, so it stays a table).
        for r in _recs:
            _txt = r.get("text", "") or ""
            if not _txt:
                continue
            _label = " · ".join(x for x in (r.get("page"), r.get("engine"),
                                            r.get("model_id")) if x)
            L += ["<details>", f"<summary>{_label} ({len(_txt)} chars)</summary>", "",
                  "```", _txt[:2000] + ("…" if len(_txt) > 2000 else ""), "```",
                  "</details>", ""]

    L += ["## Dateien", ""]
    # Include recognition files in the file list
    _file_keys = sorted(k for k in artifacts if k not in ("index.md", "pipeline.json"))
    L += [f"- [{name}]({name})" for name in _file_keys]
    return "\n".join(L) + "\n"


def _commit_files(files: dict[str, bytes], message: str,
                  session: Optional[requests.Session] = None,
                  *, repo: Optional[str] = None, branch: Optional[str] = None,
                  base_branch: Optional[str] = None) -> Optional[str]:
    """Commit ``files`` (remote path → bytes) to ``repo``/``branch`` in one commit.

    Defaults to the output repo/branch (the publishing path). Pass ``repo`` /
    ``branch`` to target another repo (e.g. the code repo for /mcp_propose, #229).
    When ``branch`` does not exist and ``base_branch`` is given, a NEW branch is
    forked off ``base_branch`` (its tree is the base, its HEAD the parent) — this
    is how a feature branch for a PR is created. Without ``base_branch``, a 404
    branch means an empty repo (first commit). Returns the commit html_url.
    """
    if not files:
        return None
    repo = repo or config.GITHUB_OUTPUT_REPO
    branch = branch or config.GITHUB_OUTPUT_BRANCH
    s = session or _session()
    git = f"{_API}/repos/{repo}/git"

    # base commit + tree; decide whether we update an existing ref or create one.
    create_ref = False
    r = s.get(f"{git}/ref/heads/{branch}", timeout=_TIMEOUT)
    if r.status_code == 200:
        parent = r.json()["object"]["sha"]
        rc = s.get(f"{git}/commits/{parent}", timeout=_TIMEOUT)
        rc.raise_for_status()
        base_tree = rc.json()["tree"]["sha"]
    elif r.status_code == 404 and base_branch:
        rb = s.get(f"{git}/ref/heads/{base_branch}", timeout=_TIMEOUT)
        rb.raise_for_status()
        parent = rb.json()["object"]["sha"]
        rc = s.get(f"{git}/commits/{parent}", timeout=_TIMEOUT)
        rc.raise_for_status()
        base_tree = rc.json()["tree"]["sha"]
        create_ref = True                   # new feature branch off base_branch
    elif r.status_code == 404:
        parent, base_tree = None, None      # empty repo → first commit
    else:
        r.raise_for_status()
        return None

    # Tree entries carry the **content**, not a blob sha. The create-tree API
    # writes the blob itself, which is the difference between three write calls
    # per commit and two hundred and three. Publishing 13 441 files as 68 chunks
    # of 200 cost ~13 800 write calls the old way and tripped GitHub's secondary
    # rate limit at chunk 24 (2026-09-26); the same publish is ~204 calls now.
    #
    # Only text can be inlined — the field is a JSON string — so anything that is
    # not valid UTF-8 still goes through a blob, which is also the only way to
    # commit bytes that are not text at all.
    tree = []
    for path, content in files.items():
        try:
            tree.append({"path": path, "mode": "100644", "type": "blob",
                         "content": content.decode("utf-8")})
        except UnicodeDecodeError:
            rb = _write(s, "POST", f"{git}/blobs", json={
                "content": base64.b64encode(content).decode("ascii"),
                "encoding": "base64",
            })
            tree.append({"path": path, "mode": "100644", "type": "blob",
                         "sha": rb.json()["sha"]})

    tree_payload: dict = {"tree": tree}
    if base_tree:
        tree_payload["base_tree"] = base_tree
    rt = _write(s, "POST", f"{git}/trees", json=tree_payload)
    tree_sha = rt.json()["sha"]

    # A chunk that rewrites identical bytes produces the identical tree. Committing
    # it anyway is an empty commit, and a resumed publish would leave a row of them
    # in the pull request before reaching the work that is actually left.
    if base_tree and tree_sha == base_tree and not create_ref:
        logger.debug(f"[Publish] {repo}@{branch}: unchanged, no commit")
        return None

    commit_payload: dict = {"message": message, "tree": tree_sha}
    if parent:
        commit_payload["parents"] = [parent]
    rc = _write(s, "POST", f"{git}/commits", json=commit_payload)
    new_sha = rc.json()["sha"]

    if parent is not None and not create_ref:
        _write(s, "PATCH", f"{git}/refs/heads/{branch}", json={"sha": new_sha})
    else:
        _write(s, "POST", f"{git}/refs",
               json={"ref": f"refs/heads/{branch}", "sha": new_sha})
    return rc.json().get("html_url")


def open_pr(head_branch: str, title: str, body: str, *,
            repo: Optional[str] = None, base: str = "main",
            session: Optional[requests.Session] = None) -> Optional[str]:
    """Open a pull request ``head_branch`` → ``base`` on ``repo``. Returns the
    PR html_url. Used by /mcp_propose (#229) after committing the source patch.

    ``head_branch`` may be ``owner:branch`` to propose from a fork.

    An open PR for the same head is **returned rather than an error**. A batch
    that grows — more pages recognised, then published again — should land in the
    one pull request its earlier commits are already in; GitHub answers the second
    attempt with a 422, and treating that as a failure would report a successful
    publish as a broken one.
    """
    repo = repo or config.GITHUB_CODE_REPO
    s = session or _session()
    r = s.post(f"{_API}/repos/{repo}/pulls", timeout=_TIMEOUT,
               json={"title": title, "head": head_branch, "base": base, "body": body})
    if r.status_code == 422:
        existing = _find_pr(repo, head_branch, s)
        if existing:
            logger.info(f"[Publish] pull request for {head_branch} already open: {existing}")
            return existing
    r.raise_for_status()
    return r.json().get("html_url")


def _find_pr(repo: str, head_branch: str, session: requests.Session) -> Optional[str]:
    """The open PR whose head is ``head_branch`` (``owner:branch``), if any."""
    head = head_branch if ":" in head_branch else f"{repo.split('/')[0]}:{head_branch}"
    r = session.get(f"{_API}/repos/{repo}/pulls", timeout=_TIMEOUT,
                    params={"head": head, "state": "open"})
    if r.status_code != 200:
        return None
    items = r.json()
    return items[0].get("html_url") if items else None


def publish_doc(doc_id: str, source_url: Optional[str] = None,
                session: Optional[requests.Session] = None) -> Optional[str]:
    """Publish a processed document's outputs to ``docs/<doc_id>/``.

    Non-fatal: returns the commit URL on success, or None (logging a warning) if
    publishing is disabled, there is nothing to publish, or the API call fails.
    """
    if not is_enabled():
        return None
    try:
        local = collect_artifacts(doc_id)
        if not local:
            logger.info(f"[Publish] {doc_id}: no artifacts to publish")
            return None
        contents = {name: p.read_bytes() for name, p in local.items()}

        # ── Publish all per-engine recognitions (#238) ─────────────────────
        # Generate one txt file per engine + fused.txt from pipeline.json.
        # This is additive: recognitions that are already on disk (future
        # RECOGNITIONS_DIR) are also picked up automatically.
        _recs: list = []
        _fused_text = ""
        if "pipeline.json" in contents:
            try:
                _pipe = json.loads(contents["pipeline.json"].decode("utf-8", "replace"))
                _recs = _pipe.get("recognitions", []) or []
                _fused_text = _pipe.get("transcription", "") or ""
            except (ValueError, TypeError):
                _recs = []
        # One export per candidate transcription, page-attributed (#284).
        for _r in _recs:
            _txt = _r.get("text", "") or ""
            if not _txt or (_r.get("error") or ""):
                continue
            contents[_recognition_filename(_r)] = _txt.encode("utf-8")
        # Write fused transcription
        if _fused_text:
            contents["recognitions/fused.txt"] = _fused_text.encode("utf-8")

        contents["index.md"] = _index_md(doc_id, contents, source_url).encode("utf-8")
        files = {f"docs/{doc_id}/{name}": data for name, data in contents.items()}
        url = _commit_files(files, f"Publish {doc_id}", session=session)
        if url:
            logger.info(f"[Publish] {doc_id} → {url}")
        return url
    except Exception as e:  # network, HTTP, encoding — never fatal to the pipeline
        logger.warning(f"[Publish] {doc_id} failed: {e}")
        return None


# ── publishing a directory tree (#batch) ─────────────────────────────────────

#: What may be published from a local directory. An allowlist, not a denylist:
#: these trees sit next to the source images they were produced from, and the one
#: mistake that cannot be taken back is committing somebody's digitised holdings
#: to a public repository. A format nobody anticipated is skipped and named in the
#: log, which is the safe direction to be wrong in.
PUBLISHABLE_SUFFIXES = frozenset({".txt", ".json", ".md", ".jsonl", ".csv", ".xml"})


def collect_tree(local_dir: Path, path_prefix: str,
                 suffixes: frozenset[str] = PUBLISHABLE_SUFFIXES) -> dict[str, bytes]:
    """``{remote path: bytes}`` for everything publishable under ``local_dir``.

    Shared by the two ways a run reaches a repository — a direct commit and a
    pull request — so they cannot disagree about what a publish contains.
    """
    local_dir = Path(local_dir)
    if not local_dir.is_dir():
        raise FileNotFoundError(f"not a directory: {local_dir}")
    if not config.GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is not set — cannot publish")

    prefix = path_prefix.strip("/")
    files: dict[str, bytes] = {}
    skipped: list[str] = []
    for p in sorted(local_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in suffixes:
            skipped.append(p.name)
            continue
        rel = p.relative_to(local_dir).as_posix()
        files[f"{prefix}/{rel}" if prefix else rel] = p.read_bytes()

    if skipped:
        logger.info(
            f"[Publish] {len(skipped)} file(s) not published (unpublishable type): "
            + ", ".join(sorted({Path(s).suffix or s for s in skipped}))
        )
    if not files:
        logger.warning(f"[Publish] nothing publishable under {local_dir}")
    return files


def publish_tree(local_dir: Path, *, repo: str, path_prefix: str,
                 branch: str = "main", message: str = "Publish outputs",
                 suffixes: frozenset[str] = PUBLISHABLE_SUFFIXES,
                 chunk: int = 200,
                 session: Optional[requests.Session] = None) -> list[str]:
    """Commit every publishable file under ``local_dir`` to ``repo`` at
    ``path_prefix/``, preserving the tree. Returns the commit URLs.

    Unlike :func:`publish_doc` this is **not** gated on ``ENABLE_GITHUB_PUBLISH``:
    that flag exists so the pipeline does not publish as a side effect of
    processing. Calling this is not a side effect of anything — it is someone
    asking for these files to be pushed — so the only precondition is a token.

    Large trees are split into several commits of at most ``chunk`` files. One
    commit would be tidier, and it is not worth it: each file costs a blob API
    call, so a thousand-file tree is a thousand requests, and a failure at request
    900 of a single commit publishes nothing at all. Chunked, the same failure
    leaves the completed chunks published and the rest to retry — and re-running
    is safe, because a chunk that rewrites identical bytes produces an empty tree
    delta rather than a duplicate.

    Raises on a failed API call. This is an explicit, outward-facing action; a
    publish that quietly published half a run and returned would be worse than one
    that stops and says where it stopped.
    """
    files = collect_tree(local_dir, path_prefix, suffixes)
    if not files:
        return []

    paths = sorted(files)
    urls: list[str] = []
    s = session or _session()
    batches = [paths[i:i + chunk] for i in range(0, len(paths), chunk)]
    for n, batch in enumerate(batches, 1):
        part = f" ({n}/{len(batches)})" if len(batches) > 1 else ""
        url = _commit_files({p: files[p] for p in batch}, f"{message}{part}",
                            session=s, repo=repo, branch=branch)
        logger.info(f"[Publish] {repo}@{branch}: {len(batch)} file(s){part} → {url}")
        if url:
            urls.append(url)
    return urls


# ── publishing as a pull request (#lassberg) ─────────────────────────────────

def _branch_ref(repo: str, branch: str, session: requests.Session) -> Optional[str]:
    """The sha ``branch`` points at in ``repo``, or None when it does not exist."""
    r = session.get(f"{_API}/repos/{repo}/git/ref/heads/{branch}", timeout=_TIMEOUT)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()["object"]["sha"]


def _fork_branch_at_upstream(head_repo: str, branch: str, upstream_sha: str,
                             session: requests.Session) -> str:
    """Make ``branch`` exist in ``head_repo``, starting at ``upstream_sha``.

    Branching off the **upstream's** head rather than the fork's own default
    branch is the whole trick. A fork that has not been synced for a while has a
    stale ``main``, and a pull request from a branch off that stale main shows
    every upstream commit since as a deletion — a two-file publish arrives
    looking like it reverts a month of somebody else's work. A fork shares its
    parent's object store, so a ref in the fork can point straight at an upstream
    commit, and the pull request then contains exactly the files this publish
    wrote.

    An existing branch is left alone and returned: a run that grows should add
    commits to the pull request it already has, not open a second one.
    """
    existing = _branch_ref(head_repo, branch, session)
    if existing:
        return existing
    r = session.post(f"{_API}/repos/{head_repo}/git/refs", timeout=_TIMEOUT,
                     json={"ref": f"refs/heads/{branch}", "sha": upstream_sha})
    r.raise_for_status()
    return upstream_sha


def _pr_body(local_dir: Path, files: dict[str, bytes], path_prefix: str) -> str:
    """What the pull request says about the run that produced it.

    The run's own ``report.md`` is the description — it already states what each
    model produced, what it cost, how many pages were cut off and how many came
    back empty, along with the standing warning that none of those columns is
    quality. Restating it in the pull request would be a second version of the
    same numbers, free to drift from the first.
    """
    report = local_dir / "report.md"
    lines = [
        f"Machine transcription of {len(files)} file(s) under `{path_prefix}/`, "
        f"from run `{local_dir.name}`.",
        "",
        "Produced by [agentic_historian](https://github.com/thodel/agentic_historian) "
        "`atr-batch`. Two files per page: `<page>.txt` is the transcription alone, "
        "`<page>.json` adds timings, the engine that answered, the per-line readings "
        "with their geometry, and the **sha256 of the source scan** the reading came "
        "from. The scans themselves are not in this pull request and are not in this "
        "repository — they stay in the Nextcloud share.",
        "",
        "**This is uncorrected machine output.** It has not been proofread against "
        "the originals and it is not an edition.",
    ]
    if report.exists():
        try:
            lines += ["", "---", "", report.read_text(encoding="utf-8").strip()]
        except OSError:
            pass
    return "\n".join(lines) + "\n"


def publish_pr(local_dir: Path, *, repo: str, path_prefix: str,
               head_repo: Optional[str] = None, base: str = "main",
               branch: Optional[str] = None, title: Optional[str] = None,
               body: Optional[str] = None, message: Optional[str] = None,
               suffixes: frozenset[str] = PUBLISHABLE_SUFFIXES,
               chunk: int = 200,
               session: Optional[requests.Session] = None) -> dict:
    """Publish a run directory to ``repo`` as a **pull request**, not a push.

    The readings go to somebody else's repository, and the edition's maintainer
    decides what enters it — a token with write access would work and would make
    that decision for them. Proposing instead needs no rights on ``repo`` at all:
    the commits land in ``head_repo`` (a fork we do own) and the pull request asks.

    Returns ``{"branch", "head", "files", "commits", "pull_request"}``. Re-running
    with the same ``branch`` adds to the same pull request rather than opening a
    second one, which is what makes "publish once everything is collected" safe to
    run twice — a corpus that gains pages is published again, not restarted.
    """
    local_dir = Path(local_dir)
    files = collect_tree(local_dir, path_prefix, suffixes)
    if not files:
        return {"branch": None, "head": None, "files": 0, "commits": [],
                "pull_request": None}

    head_repo = head_repo or repo
    branch = branch or f"textrecognition/{local_dir.name}"
    s = session or _session()

    upstream = _branch_ref(repo, base, s)
    if not upstream:
        raise RuntimeError(f"{repo} has no branch {base!r} to propose against")
    _fork_branch_at_upstream(head_repo, branch, upstream, s)

    paths = sorted(files)
    batches = [paths[i:i + chunk] for i in range(0, len(paths), chunk)]
    urls: list[str] = []
    msg = message or f"Add machine transcription: {local_dir.name}"
    for n, batch in enumerate(batches, 1):
        part = f" ({n}/{len(batches)})" if len(batches) > 1 else ""
        url = _commit_files({p: files[p] for p in batch}, f"{msg}{part}",
                            session=s, repo=head_repo, branch=branch)
        logger.info(f"[Publish] {head_repo}@{branch}: {len(batch)} file(s){part} → "
                    f"{url or 'unchanged, already published'}")
        if url:
            urls.append(url)

    head = branch if head_repo == repo else f"{head_repo.split('/')[0]}:{branch}"
    pr = open_pr(head, title or f"Machine transcription: {local_dir.name}",
                 body or _pr_body(local_dir, files, path_prefix.strip("/")),
                 repo=repo, base=base, session=s)
    logger.info(f"[Publish] {len(files)} file(s) proposed to {repo}: {pr}")
    return {"branch": branch, "head": head, "files": len(files),
            "commits": urls, "pull_request": pr}
