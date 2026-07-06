# Output-repo scaffolding

Configures the **output repo** (`thodel/agentic-historian-outputs`) as a GitHub
Pages catalogue for the Agentic Historian publisher (#200/#201). These files live
here as the version-controlled source; install them **into the output repo** once.

## Install (one-time, in the OUTPUT repo)
Copy, preserving paths:

| from (this folder)                       | to (output repo)                       |
|------------------------------------------|----------------------------------------|
| `docs/_config.yml`                       | `docs/_config.yml`                     |
| `scripts/build_index.py`                 | `scripts/build_index.py`               |
| `.github/workflows/build-index.yml`      | `.github/workflows/build-index.yml`    |

Then enable Pages: **output repo → Settings → Pages → Source: Deploy from a branch → `main` / `/docs`.**

## How it works
- The bot's publisher (#200) commits `docs/<doc_id>/` — `transcription.txt`,
  `description.*`, `entities.*`, `pipeline.json`, and a rendered `index.md`
  (metadata, entities with GND/HLS/Wikidata links, transcription) — one commit
  per processed document, so every run is diffable.
- The **build-index** Action regenerates `docs/index.md` (the catalogue table)
  from every `docs/*/pipeline.json` on each push; Pages renders the site.

## Notes
- The publishing token on tei needs `contents:write` on this repo
  (`ENABLE_GITHUB_PUBLISH=true`, `GITHUB_OUTPUT_REPO=thodel/agentic-historian-outputs`).
- The Action uses the repo's built-in `GITHUB_TOKEN` — no setup.
- The actor guard (`github-actions[bot]`) stops the index commit from
  retriggering the Action.
