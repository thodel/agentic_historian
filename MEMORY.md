# MEMORY.md — Long-Term Memory

Updated: 2026-07-06

## Königsfelden (kf) / SSRQ / HLS Cross-Reference

### Key Finding
- **kf `hls_id` is entirely null** for all 5,260 persons — not populated anywhere
- **162 kf persons** have HLS IDs derivable via the SSRQ TTL (`ssrq__fuseki_042810.ttl`)
- Source: `pers:refs [ pers:id "XXXXX" ; pers:type "HLS" ]` in the TTL
- Crosswalk: `kf perXXXXXX` → strip `per` → zero-pad → SSRQ TTL `perXXXXXX`
- ID mapping is unreliable alone (same numeric ID in both corpora often = different person)

### 162 HLS IDs — Confidence Distribution
| Confidence | Count | Notes |
|---|---|---|
| High (exact name match) | 155 | `name_match_quality: exact` |
| Medium (name variant) | 7 | confirmed same person by expert |
| Low/verify | 0 | — |

### 7 Name-Variant Entries (Medium Confidence)
All confirmed as same person despite spelling/title differences:

| kf ID | kf name | SSRQ name | HLS ID |
|---|---|---|---|
| per000519 | Heinrich Chur | Heinrich von Hewen | 12692 |
| per001096 | Friedrich von Chur | Friedrich von Erdingen | 12592 |
| per005100 | Otto von Hachberg | Otto von Hochberg | 12699 |
| per005757 | Arnold Fruonz | Arnold Frunz | 21159 |
| per008302 | Burkart von Mansberg | Burkhart von Mansberg | 19475 |
| per011284 | Urban von Muleren | Urban Muleren, von | 17113 |
| per012269 | Martin Papst | Martin V. (Papst) | 012788 |

Confirmed by Tobias 2026-06-30: all 7 are the same historical person, different title variants.

### KF → HBLS Cross-Reference (2026-06-30)
- **Method:** Surname-first index + ASCII name normalization + year overlap (±25 yr)
- **High quality (exact name + year):** 453 KF persons
- **Medium quality (forename+surname in variants):** 1,126 KF persons
- **Low quality (surname only):** 1,133 KF persons (many false positives)
- **Unmatched:** 2,548 KF persons
- **KF→HLS→HBLS triple links:** 62 persons (have HLS ID and match to HBLS)
- **HBLS persons with HLS links:** 809 / 137,038 (0.6%), covering 515 unique HLS IDs
- **HBLS persons with Wikidata/GND links:** 768 / 137,038
- **HBLS record schema:** `n` (canonical name), `v` (variants), `y` (year range), `c` (mention count), `d` (dossier count), `occ`, `loc`, `dos` (dossier mentions), `hls` (HLS link), `wd` (Wikidata link)

### Files
- `.openclaw/tmp/kf_hls_id_map.json` — curated 162-entry map with confidence levels
- `.openclaw/tmp/kf_ssrq_hls_crossref.json` — full 5,260-person crossref
- `.openclaw/tmp/kf_ssrq_exclusions.json` — persons excluded from safe crosswalk

### Nextcloud (hodelweb.ch) — local only, never commit
- URL: `https://cloud.hodelweb.ch`
- Username: `dh-bot`
- Password: `mLasUXllDLAGX2Rkgmc6CA==`
- Access: Full admin — read/write all files + user management
- WebDAV: `https://cloud.hodelweb.ch/remote.php/dav/files/dh-bot/`
- Tobias's files: `/Documents (2)/committees/` → `hist-inst/`, `unige/`, `wit-ö/`
- Projects in `projects/` subfolder

## Project Workflow Rules

### Individual PRs for All Projects
**Rule:** For all projects/repos (e.g. `agentic_historian`), each distinct piece of work must get its own individual PR to be merged, whether manually or at a later stage. Only explicitly indicated otherwise can supersede this rule.

- Never commit multiple unrelated changes to a single PR
- Each issue/feature gets its own branch and PR
- Always create PRs proactively; don't wait to be asked
- If in doubt, separate into individual PRs rather than combining

## Databases
- kf: `/home/dh/kf_data/kf.db` (5,260 persons, IDs per000089–per030180)
- SSRQ: `/home/dh/.openclaw/tmp/ssrq_v6.db` (23,674 persons, 7,047 orgs, 138,298 name variants)
- SSRQ TTL: `/home/dh/resources/ssrq__fuseki_042810.ttl` (Fuseki RDF dump, 72MB)
- HBLS: `/home/dh/eos_persons/persons_resolved.json` (137,038 merged person records from HGB Basel, ca. 1400–1700)
- HGB: `/home/dh/eos_data/hgb.db` (raw spans: 75,447 documents, 893,303 spans)

## SSRQ Shell Script (`ssrq`)
- Location: `/home/dh/.local/bin/ssrq` (in PATH)
- Commands: `ping`, `search <name>`, `person <id>`, `org <id>`, `names <id>`, `related <id>`
- MCP server: `https://tei.dh.unibe.ch/mcp/ssrq/`
- Skill doc: `.openclaw/tmp/ssrq_skill/SKILL.md`