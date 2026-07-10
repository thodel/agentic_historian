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

    # ── Recognition results (#238) ─────────────────────────────────────────
    # Show every engine's output + provenance on the fused transcription.
    # Parsed from pipeline.json (written by nl_orchestrator after Phase 3).
    _recs = pipe.get("recognitions", []) or []
    if _recs:
        L += ["## Recognition results", ""]
        L += [f"__{len(_recs)} engine(s) — collapsible view_", ""]
        L += ["<details>", "<summary>Show all engine outputs</summary>", ""]
        # Table: engine, model, provenance, chars
        L += ["| Engine | Model | Provenance | Chars |",
              "|---|---|---|---|"]
        for r in _recs:
            _engine = r.get("engine", "?")
            _model = r.get("model_id", "—")
            _txt = r.get("text", "") or ""
            _len = len(_txt)
            _src = "single" if len(_recs) == 1 else "fused"
            # provenance badge on fused transcription
            _a_meta_fusion = a_meta.get("fusion_strategy", "")
            _llm_skipped = a_meta.get("fusion_llm_skipped", False)
            if _a_meta_fusion:
                _prov = "⚖️ vote" if _llm_skipped else "🤖 LLM-arbitrated"
            else:
                _prov = "single engine"
            L.append(f"| `{_engine}` | `{_model}` | {_prov} | {_len} |")
            L.append(f"<details>")
            L.append(f"<summary>{_engine} output ({_len} chars)</summary>")
            L.append("")
            L.append("```")
            L.append(_txt[:2000] + ("..." if len(_txt) > 2000 else ""))
            L.append("```")
            L.append("</details>")
        # Provenance legend for the fused transcription
        if a_meta.get("fusion_strategy"):
            _n_arbitrated = a_meta.get("fusion_arbitrated", 0)
            _skipped = a_meta.get("fusion_llm_skipped", False)
            _cer = a_meta.get("fusion_agreement_cer", 0.0)
            _prov_note = (
                f"Fused transcription: {_n_arbitrated} spans LLM-arbitrated, "
                f"agreement CER={_cer:.1%}. "
                f"{'LLM skipped (high agreement)' if _skipped else 'LLM used for disagreements.'}"
            )
            L += ["", _prov_note]
        L += ["</details>", ""]

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

    if parent is not None and not create_ref:
        ru = s.patch(f"{git}/refs/heads/{branch}", json={"sha": new_sha}, timeout=_TIMEOUT)
    else:
        ru = s.post(f"{git}/refs", json={"ref": f"refs/heads/{branch}", "sha": new_sha},
                    timeout=_TIMEOUT)
    ru.raise_for_status()
    return rc.json().get("html_url")


def open_pr(head_branch: str, title: str, body: str, *,
            repo: Optional[str] = None, base: str = "main",
            session: Optional[requests.Session] = None) -> Optional[str]:
    """Open a pull request ``head_branch`` → ``base`` on ``repo``. Returns the
    PR html_url. Used by /mcp_propose (#229) after committing the source patch."""
    repo = repo or config.GITHUB_CODE_REPO
    s = session or _session()
    r = s.post(f"{_API}/repos/{repo}/pulls", timeout=_TIMEOUT,
               json={"title": title, "head": head_branch, "base": base, "body": body})
    r.raise_for_status()
    return r.json().get("html_url")


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
        # Write one file per engine (skip if text is missing/error)
        for _r in _recs:
            _engine = _r.get("engine", "engine")
            _model = _r.get("model_id", "")
            _txt = _r.get("text", "") or ""
            _err = _r.get("error", "") or ""
            if not _txt or _err:
                continue
            _fname = f"recognitions/{_engine}"
            if _model:
                _fname += f"-{_model}"
            _fname += ".txt"
            contents[_fname] = _txt.encode("utf-8")
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
