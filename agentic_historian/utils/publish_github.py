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
    transcription = pipe.get("transcription") or \
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
        L += ["## Transkription", "", "```", transcription.strip(), "```", ""]

    L += ["## Dateien", ""]
    L += [f"- [{name}]({name})" for name in sorted(artifacts) if name != "index.md"]
    return "\n".join(L) + "\n"


def _commit_files(files: dict[str, bytes], message: str,
                  session: Optional[requests.Session] = None) -> Optional[str]:
    """Commit ``files`` (remote path → bytes) to the output repo in one commit.

    Handles an empty (un-initialised) repo by creating the first commit and ref.
    Returns the commit's html_url, or None if there is nothing to commit.
    """
    if not files:
        return None
    repo = config.GITHUB_OUTPUT_REPO
    branch = config.GITHUB_OUTPUT_BRANCH
    s = session or _session()
    git = f"{_API}/repos/{repo}/git"

    # base commit + tree (or none, for an empty repo)
    r = s.get(f"{git}/ref/heads/{branch}", timeout=_TIMEOUT)
    if r.status_code == 200:
        parent = r.json()["object"]["sha"]
        rc = s.get(f"{git}/commits/{parent}", timeout=_TIMEOUT)
        rc.raise_for_status()
        base_tree = rc.json()["tree"]["sha"]
    elif r.status_code == 404:
        parent, base_tree = None, None      # empty repo → first commit
    else:
        r.raise_for_status()
        return None

    # blobs → tree entries
    tree = []
    for path, content in files.items():
        rb = s.post(f"{git}/blobs", timeout=_TIMEOUT, json={
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        })
        rb.raise_for_status()
        tree.append({"path": path, "mode": "100644", "type": "blob",
                     "sha": rb.json()["sha"]})

    tree_payload: dict = {"tree": tree}
    if base_tree:
        tree_payload["base_tree"] = base_tree
    rt = s.post(f"{git}/trees", json=tree_payload, timeout=_TIMEOUT)
    rt.raise_for_status()

    commit_payload: dict = {"message": message, "tree": rt.json()["sha"]}
    if parent:
        commit_payload["parents"] = [parent]
    rc = s.post(f"{git}/commits", json=commit_payload, timeout=_TIMEOUT)
    rc.raise_for_status()
    new_sha = rc.json()["sha"]

    if parent is not None:
        ru = s.patch(f"{git}/refs/heads/{branch}", json={"sha": new_sha}, timeout=_TIMEOUT)
    else:
        ru = s.post(f"{git}/refs", json={"ref": f"refs/heads/{branch}", "sha": new_sha},
                    timeout=_TIMEOUT)
    ru.raise_for_status()
    return rc.json().get("html_url")


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
        contents["index.md"] = _index_md(doc_id, contents, source_url).encode("utf-8")
        files = {f"docs/{doc_id}/{name}": data for name, data in contents.items()}
        url = _commit_files(files, f"Publish {doc_id}", session=session)
        if url:
            logger.info(f"[Publish] {doc_id} → {url}")
        return url
    except Exception as e:  # network, HTTP, encoding — never fatal to the pipeline
        logger.warning(f"[Publish] {doc_id} failed: {e}")
        return None
