"""#223 (P1-A3): client-side catalogue search.

Two checks:
  1. contract — every field `search.js` matches against (SEARCHED_FIELDS) is
     actually produced by build_index.py's search-index records, so the UI can
     never silently search a field the index doesn't emit.
  2. logic — run the pure `searchIndex()` under node (skipped if node is absent)
     covering diacritic-insensitivity, entity hits, empty and no-hit queries,
     and multi-term AND.

Run from the repo root:
    pytest agentic_historian/tests/test_ah_223_search.py
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
SEARCH_JS = PKG / "output_site" / "docs" / "assets" / "search.js"
SCRIPTS = PKG / "output_site" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_index  # noqa: E402


def _searched_fields_from_js() -> list[str]:
    """Extract the SEARCHED_FIELDS array literal declared in search.js."""
    src = SEARCH_JS.read_text(encoding="utf-8")
    m = re.search(r"SEARCHED_FIELDS\s*=\s*\[([^\]]*)\]", src)
    assert m, "SEARCHED_FIELDS array not found in search.js"
    return re.findall(r'"([^"]+)"', m.group(1))


def _write_pipeline(docs_dir: Path, doc_id: str, doc: dict) -> None:
    d = docs_dir / doc_id
    d.mkdir(parents=True)
    (d / "pipeline.json").write_text(json.dumps(doc), encoding="utf-8")


# ── contract: search.js only reads fields the index emits ────────────────────

def test_searched_fields_are_all_produced_by_the_index(tmp_path):
    _write_pipeline(tmp_path, "doc-1", {
        "description": {"source_json": {"Datierung": "1432", "Sprache": "de",
                                        "Schrift": "Bastarda"}},
        "transcription": "Wir Hans von Wiler tuend kund",
        "entities": {"entities": [{"normalised": "Müller"}]},
    })
    records = build_index.build_search_index(tmp_path)
    assert len(records) == 1
    rec = records[0]

    fields = _searched_fields_from_js()
    assert fields, "search.js declares no searched fields"
    missing = [f for f in fields if f not in rec]
    assert not missing, f"search.js searches fields the index never emits: {missing}"
    # url is used for the result link, so it must exist even though it isn't searched
    assert "url" in rec


# ── logic: the pure searchIndex() under node ─────────────────────────────────

_NODE = shutil.which("node")

_DRIVER = r"""
const api = require(process.argv[2]);
const job = JSON.parse(process.argv[3]);
const out = {};
for (const [name, q] of Object.entries(job.queries)) {
  out[name] = api.searchIndex(job.records, q).map(r => r.doc_id);
}
process.stdout.write(JSON.stringify(out));
"""

_RECORDS = [
    {"doc_id": "d-mueller", "date": "1432", "lang": "de", "script": "Bastarda",
     "entities": ["Müller", "Bern"], "snippet": "ein brief aus bern", "url": "d-mueller/"},
    {"doc_id": "d-zurich", "date": "1501", "lang": "la", "script": "Kurrent",
     "entities": ["Zürich"], "snippet": "littera de turego", "url": "d-zurich/"},
]


@pytest.mark.skipif(_NODE is None, reason="node not available")
def test_search_logic_under_node():
    job = {"records": _RECORDS, "queries": {
        "diacritic":  "muller",        # folds to match "Müller"
        "entity":     "zürich",        # entity-name hit
        "empty":      "   ",           # whitespace → all
        "nohit":      "xyzzy",         # no match
        "and_terms":  "de bern",       # both terms must appear (d-mueller only)
        "case":       "BASTARDA",      # case-insensitive metadata hit
    }}
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(_DRIVER)
        driver = f.name
    proc = subprocess.run([_NODE, driver, str(SEARCH_JS), json.dumps(job)],
                          capture_output=True, text=True, timeout=30)
    Path(driver).unlink(missing_ok=True)
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout)

    assert res["diacritic"] == ["d-mueller"]
    assert res["entity"] == ["d-zurich"]
    assert sorted(res["empty"]) == ["d-mueller", "d-zurich"]
    assert res["nohit"] == []
    assert res["and_terms"] == ["d-mueller"]
    assert res["case"] == ["d-mueller"]
