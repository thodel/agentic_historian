#!/usr/bin/env python3
"""
Rebuild docs/index.md and docs/search-index.json (#221 P1-A1).

Reads every ``docs/*/pipeline.json`` and writes:
  1. docs/index.md        — table of all documents (existing behaviour)
  2. docs/search-index.json — one record per document for live-search consumers

Run by the build-index GitHub Action on push. Stdlib-only (no dependencies).

search-index.json schema:
  {"doc_id", "date", "lang", "script", "entities", "snippet", "url"}

Rules (#221):
  - unwrap Agent B's {"wert": …} shape via _val()
  - entities from entities.entities[].normalised | text, deduplicated
  - snippet: whitespace-collapsed, truncated to ~300 chars
  - malformed/absent pipeline.json → doc appears with empty fields (never crashes)
  - deterministic: sorted by doc_id → byte-identical on every run
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SNIPPET_LIMIT = 300


def _val(x) -> str:
    """Unwrap {"wert": …} or {"value": …} wrapper; return empty string for None."""
    if isinstance(x, dict):
        return str(x.get("wert") or x.get("value") or "")
    return "" if x is None else str(x)


def _collapse(s: str, limit: int = SNIPPET_LIMIT) -> str:
    """Collapse whitespace and truncate to ``limit`` chars."""
    s = re.sub(r"\s+", " ", s).strip()
    return (s[:limit] + "…") if len(s) > limit else s


def _entities_from_pipeline(d: dict) -> list[str]:
    """Deduplicated normalised/text entity names from pipeline.json entities block."""
    seen: set[str] = set()
    out: list[str] = []
    for ent in (d.get("entities") or {}).get("entities") or []:
        name = _val(ent.get("normalised")) or _val(ent.get("text"))
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def build_search_index(docs_dir: Path | str) -> list[dict]:
    """
    Build search-index records from all ``<docs_dir>/*/pipeline.json`` files.

    Each record: {doc_id, date, lang, script, entities, snippet, url}
    Malformed pipeline.json → record with empty fields (not an error).
    Output is sorted by doc_id for deterministic output.
    """
    docs_dir = Path(docs_dir)
    records: list[dict] = []

    for pj in sorted(docs_dir.glob("*/pipeline.json")):
        doc_id = pj.parent.name
        try:
            d = json.loads(pj.read_text(encoding="utf-8"))
        except Exception:
            d = {}

        sj = (d.get("description") or {}).get("source_json") or {}
        transcript = _val(d.get("transcription") or "")
        snippet = _collapse(transcript) if transcript else ""

        records.append({
            "doc_id":   doc_id,
            "date":     _val(sj.get("Datierung")),
            "lang":     _val(sj.get("Sprache")),
            "script":   _val(sj.get("Schrift")),
            "entities": _entities_from_pipeline(d),
            "snippet":  snippet,
            "url":      f"{doc_id}/",
        })

    records.sort(key=lambda r: r["doc_id"])
    return records


def build(docs_dir: Path | str | None = None) -> int:
    """
    Rebuild docs/index.md and docs/search-index.json.

    ``docs_dir`` defaults to ``Path.cwd() / "docs"`` (GitHub Action runner root).
    Returns the number of document records processed.
    """
    docs_dir = Path(docs_dir) if docs_dir is not None else (Path.cwd() / "docs")
    search_records = build_search_index(docs_dir)

    rows: list[tuple] = []
    for rec in search_records:
        rows.append((
            rec["doc_id"],
            rec["date"],
            rec["lang"],
            rec["script"],
            len(rec["entities"]),
        ))

    # ── docs/index.md ──────────────────────────────────────────────────────
    out = [
        "---",
        "layout: default",
        "title: Agentic Historian — Outputs",
        "---",
        "",
        "# Verarbeitete Dokumente",
        "",
        f"{len(rows)} Dokument(e). · [🔍 Volltextsuche](search.html)",
        "",
        "| Dokument | Datierung | Sprache | Schrift | Entitäten |",
        "|---|---|---|---|---|",
    ]
    for doc_id, date, lang, script, n in rows:
        out.append(f"| [{doc_id}]({doc_id}/) | {date} | {lang} | {script} | {n} |")
    (docs_dir / "index.md").write_text("\n".join(out) + "\n", encoding="utf-8")

    # ── docs/search-index.json ──────────────────────────────────────────────
    (docs_dir / "search-index.json").write_text(
        json.dumps(search_records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote docs/index.md ({len(rows)} row(s)) and "
          f"docs/search-index.json ({len(search_records)} record(s))")
    return len(search_records)


if __name__ == "__main__":
    build()
