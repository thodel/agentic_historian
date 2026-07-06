#!/usr/bin/env python3
"""Rebuild docs/index.md — the catalogue of all processed documents (#201).

Reads every ``docs/*/pipeline.json`` committed by the Agentic Historian publisher
(#200) and writes a table linking to each document page. Run by the build-index
GitHub Action on push. Stdlib-only (no dependencies).
"""

import json
from pathlib import Path

DOCS = Path("docs")


def _val(x) -> str:
    if isinstance(x, dict):
        return str(x.get("wert") or x.get("value") or "")
    return "" if x is None else str(x)


def build() -> int:
    rows = []
    for pj in sorted(DOCS.glob("*/pipeline.json")):
        doc_id = pj.parent.name
        try:
            d = json.loads(pj.read_text(encoding="utf-8"))
        except Exception:
            d = {}
        sj = (d.get("description") or {}).get("source_json") or {}
        ents = (d.get("entities") or {}).get("entities") or []
        rows.append((doc_id, _val(sj.get("Datierung")), _val(sj.get("Sprache")),
                     _val(sj.get("Schrift")), len(ents)))
    rows.sort(key=lambda r: r[0])

    out = ["---", "layout: default", "title: Agentic Historian — Outputs", "---", "",
           "# Verarbeitete Dokumente", "", f"{len(rows)} Dokument(e).", "",
           "| Dokument | Datierung | Sprache | Schrift | Entitäten |",
           "|---|---|---|---|---|"]
    for doc_id, date, lang, script, n in rows:
        out.append(f"| [{doc_id}]({doc_id}/) | {date} | {lang} | {script} | {n} |")
    (DOCS / "index.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Wrote docs/index.md with {len(rows)} row(s)")
    return len(rows)


if __name__ == "__main__":
    build()
