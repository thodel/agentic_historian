## Issue
Closes #221 — P1-A1: emit search-index.json in the output-repo build Action.

## What was built

### `build_index.py` — now emits two files
| File | What it contains |
|---|---|
| `docs/index.md` | Catalogue table (unchanged) |
| `docs/search-index.json` | One record per doc: `{doc_id, date, lang, script, entities[], snippet, url}` |

### search-index.json schema
```json
{
  "doc_id": "doc_1234",
  "date": "1485",
  "lang": "de",
  "script": "kurrent",
  "entities": ["Hans Müller", "Thun"],
  "snippet": "Vorder\n...\n",
  "url": "doc_1234/"
}
```

### Rules (per issue spec)
- `_val()`: unwraps `{"wert": …}` / `{"value": …}` — shared helper
- **Entities**: from `pipeline.json.entities.entities[].normalised | text`, deduplicated
- **Snippet**: whitespace-collapsed, truncated to ~300 chars
- **Malformed pipeline.json**: doc appears with empty fields, run succeeds
- **Deterministic**: sorted by `doc_id`, byte-identical on every run (sorted JSON output)
- Stdlib only — no new dependencies (CI-safe)

## Live verification (documented in the PR)
1. Copy the updated `output_site/scripts/build_index.py` into the output repo
2. Push a test doc or re-process an existing one
3. Confirm the Action commits both `docs/index.md` and `docs/search-index.json`
4. Check `search-index.json` has the expected schema for each doc

```bash
# Quick local smoke-test (no CI needed):
cd output_repo
python ../agentic_historian/output_site/scripts/build_index.py
jq '.[] | .doc_id, .entities, .snippet' docs/search-index.json | head -20
```

## Tests
- **18 new tests** — all offline, no network
- **Full suite: 600 passed**, 1 skipped (+18 vs #241/#240)
- Run: `ah/.venv/bin/python -m pytest ah/tests/ -q`