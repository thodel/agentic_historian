## Issue
Closes #222 — P1-A2: entity aggregation core + per-entity pages on the catalogue.

## What was built

### `entity_index.py` — the core module
| Component | Description |
|---|---|
| `build_index(entities_dir)` | Walks `*_entities.json` files; merges by GND then normalised name+type; resolves slug collisions |
| `EntityIndex` dataclass | Inverted index: `slug → EntityEntry` with `by_gnd()`, `by_name_type()`, `search()` helpers |
| `EntityEntry` / `EntityMention` | Merged entity with de-duplicated mentions (doc_id + context dedup) |
| `_slugify()` | Umlaut → ae/oe/ue/ss (HBLS convention), filesystem-safe |
| `_norm_name()` | Particle-dropping (`von`, `van`, `de`…) — mirrors `entity_resolver._norm_name` |
| `write_entity_pages(index, output_dir)` | Idempotent: per-entity `.md` + `docs/entities/index.md` A–Z register |

### Merging rules
1. Same GND → one `EntityEntry`, slug = `gnd-<id>`
2. Else same normalised name + type → one `EntityEntry`, slug = `_slugify(name)`
3. Same name, different type → two separate entries (type is part of the key)
4. Collision (Müller/Mueller) → `mueller`, `mueller-1` suffixes

### Authority links rendered
- GND → `https://d-nb.info/gnd/<id>`
- HLS → `https://hls-dhs-dss.ch/de/articles/<id>`
- Wikidata → `https://www.wikidata.org/wiki/<id>`

## Tests
- **23 new tests** — all offline, no VPN/GPUStack/GitHub API
- Full suite: **582 passed**, 1 skipped (+23 vs before)
- Run: `ah/.venv/bin/python -m pytest ah/tests/ -q`