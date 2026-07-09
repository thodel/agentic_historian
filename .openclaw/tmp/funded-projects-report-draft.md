# Cross-Funder DH Project Tracking: Pipeline & Methodology Report

**Prepared by:** dh-bot (automated report)
**Date:** 2026-07-01
**Status:** Draft — for review and implementation

---

## Scope

This document describes a systematic, reproducible workflow for tracking funded Digital Humanities (DH) research projects across multiple European funders. It covers five national/regional agencies plus the European Research Council:

| Funder | Country/Region | Status |
|--------|---------------|--------|
| SNSF | Switzerland | ✅ Existing pipeline in production |
| ERC | European Union | 🔲 Planned |
| DFG | Germany | 🔲 Planned |
| FWF | Austria | 🔲 Planned |
| ANR | France | 🔲 Planned |

An additional candidate — **HAVI (Humanities and AI Virtual Institute / Schmidt Sciences)** — was flagged in the 2026-06-30 session as requiring manual tracking (no public data API; awardees listed on website only).

---

## Part 1 — What We Already Have: SNSF Pipeline

### 1.1 Data Source

**URL:** `https://data.snf.ch/datasets/grants_with_abstracts.csv`
**Format:** CSV (semicolon-delimited)
**Fields include:** GrantNumber, GrantNumberString, Title, TitleEnglish, ResponsibleApplicantName, ResearchInstitution, InstituteCountry, FundingInstrumentPublished, MainDiscipline, AllDisciplines, MainFieldOfResearch, EffectiveGrantStartDate, EffectiveGrantEndDate, AmountGrantedAllSets, Keywords, Abstract, State, CallFullTitle, CallDecisionYear

### 1.2 Existing Scripts

Two scripts in `.openclaw/tmp/`:

- **`snsf_analyse.py`** — Core analysis script. Downloads `grants_with_abstracts.csv`, applies DH classification filter, runs frequency analysis, keyword extraction, PI co-occurrence network, outputs CSV data files.
- **`snsf_export_active_dh.py`** — Companion export script. Produces filtered CSV of active/approved DH grants and per-PI grant counts.

### 1.3 Current Outputs (as of 2026-06-30)

Written to `internal-reporting/reports/v0.1_dh-field-overview-2026-06/data/`:

| File | Contents |
|------|---------|
| `active-dh-grants.csv` | Filtered DH grants (ongoing + approved) |
| `fine_granular_clusters.csv` | AI/ML Methods vs Other DH split |
| `institutional-breakdown.csv` | Institution ranking by DH grant count |
| `network_nodes.csv` | Institutions with country + funding totals |
| `network_inst_topic_edges.csv` | Institution ↔ discipline edges |
| `network_pi_pi_edges.csv` | co-PI co-authorship edges (keyword-based) |
| `dh-pi-index.csv` | PIs with ≥2 active DH grants |
| `dh-keyword-frequency.csv` | Keyword frequency among DH grants |
| `upcoming_topics_pi.csv` | Emerging topics / new PIs |

### 1.4 DH Filter (Current Definition)

A grant is classified as DH if **either**:

1. `MainDiscipline` ∈ {Information Technology, Applied Linguistics, Communication Sciences, German/English/Romance/Other Languages & Literature, General/Swiss/Ancient History, Classical Studies, Archaeology, Prehistory, Ecclesiastical History, Visual Arts & Art History, Philosophy, Arts, Musicology, Theatre & Cinema, Library & Documentation Science, Archive Science}

2. **Any** of `Title`, `Abstract`, `Keywords`, `MainFieldOfResearch` contains a DH keyword: `digital humanities`, `dh`, `digitisation`, `text mining`, `corpus linguistics`, `nlp`, `llm`, `machine learning`, `deep learning`, `semantic web`, `linked data`, `knowledge graph`, `digital archives`, `computational history`, `lexicography`, `philology`, `tei xml`, `ocr`, `handwriting recognition`, `paleographic`, `digital edition`, `virtual reconstruction`, `network analysis`, etc.

**Scope:** Active = `State` in {Ongoing, Approved} only.

### 1.5 Known Gaps & TODOs

- The DH keyword list should be reviewed by Tobias — should anything be added/removed?
- Discipline list excludes some bordering fields (e.g., Media Studies, Geography, Anthropology) that increasingly contain DH work
- No cross-funder deduplication or PI identity resolution across funders
- co-PI network currently keyword-based, not actual co-PI relationships (SNSF data lacks explicit co-PI field in the CSV export)

---

## Part 2 — Data Access: Funder-by-Funder Comparison

### 2.1 ERC (European Research Council)

**Primary source:** CORDIS (Community Research and Development Information Service)
**URL:** https://cordis.europa.eu/

| Aspect | Detail |
|--------|--------|
| **Data access** | REST API ("Data Extraction Tool") + CSV/JSON/XLSX downloads via web UI |
| **API docs** | https://cordis.europa.eu/dataextractions/api-docs-ui |
| **API key** | Required — obtained from https://cordis.europa.eu/user/api (free registration) |
| **Bulk export** | Available as registered user: "Download search results" → CSV, XSLX, JSON, XML |
| **Scope** | All Horizon Europe (2021–2027), FP7, Horizon 2020 projects; ERC grants are a subset |
| **Project fields** | Acronym, title, status, funding amount, start/end dates, PI name, institution, country, topics/keywords, abstract, call identifier |
| **ERC-specific filter** | Need to filter by `fundingScheme == "ERC Consolidator Grant"`, `"ERC Advanced Grant"`, `"ERC Starting Grant"`, `"ERC Synergy Grant"` |
| **Relevant Python libs** | `cordis` (R package), `cordis` Python client (benhoehne/CORDIS on GitHub) |

**Access pattern:** Filter CORDIS export by `fundingScheme` containing "ERC" and/or by specific ERC programme calls. CORDIS is the European Commission's dataset; ERC grants are a programme within Horizon Europe.

**Steps to implement:**
1. Register at cordis.europa.eu and obtain API key
2. Query for projects where `fundingScheme` in ["ERC Consolidator Grant", "ERC Advanced Grant", "ERC Starting Grant", "ERC Synergy Grant"]
3. Or: download all Horizon Europe project CSV and filter locally
4. Apply DH classification (see Part 3)

**Known challenges:**
- CORDIS contains ALL Horizon Europe projects, not just ERC — must filter by funding scheme
- Abstracts available but may be missing for some completed projects
- PI name and institution normalization needed (spelling variants, name changes)

### 2.2 DFG (Deutsche Forschungsgemeinschaft, Germany)

**Primary source:** GEPRIS (GEförderte PRojekte Informations System)
**URL:** https://gepris.dfg.de/gepris/OCTOPUS

| Aspect | Detail |
|--------|--------|
| **Data access** | Web UI (search) + **no public API** (as of July 2026) |
| **Bulk export** | Not available as a download; web scraping required |
| **Third-party efforts** | The Lone Scientist blog documents a full scraping approach: https://thelonescientist.com/posts/how-i-built-the-dfg-database |
| **Scope** | All DFG-funded projects since the 1990s; includes researcher names, institutions, titles, funding amounts (indirectly), status |
| **Key fields** | Project ID, title, PI name, institution, funding instrument, subject areas, start/end dates |
| **Auth** | None required for read-only web access |

**Steps to implement (scraping approach):**
1. GEPRIS allows search by keyword, person, institution
2. Pagination via URL parameters — can iterate programmatically
3. Alternatively: use pre-scraped DFG datasets available on Figshare or academic data repositories (search: "DFG funded projects dataset")
4. Store scraped results as JSON/CSV for reproducible pipeline

**Note on funding amounts:** DFG does not publicly display exact funding amounts in GEPRIS — only funding instrument type. Some third-party datasets have reconstructed this.

**Known challenges:**
- No official API or bulk download — scraping is the only automated option
- Polite crawling required (rate limiting, user-agent)
- Project pages may have inconsistent structure
- Some older projects may have incomplete metadata

### 2.3 FWF (Fonds zur Förderung der wissenschaftlichen Forschung, Austria)

**Primary source:** FWF Open API
**URL:** https://www.fwf.ac.at/en/discover/open-api

| Aspect | Detail |
|--------|--------|
| **Data access** | REST API — fully open, no authentication required |
| **Base URL** | `https://api.fwf.at/` (exact endpoint path TBC — see API docs) |
| **License** | CC0 (public domain) — completely free to use and redistribute |
| **Fields** | Grant DOI, ORCID, ROR ID, project data, outputs |
| **Status** | Relatively new (announced 2024/2025) — API is actively maintained |

**Steps to implement:**
1. Consult OpenAPI docs at FWF (reachable via their developer page)
2. Query by keywords relevant to DH, or iterate over all projects and filter
3. FWF offers a Dashboard + Research Radar page with pre-aggregated data as an alternative

**Known challenges:**
- API relatively new; endpoint structure may evolve
- Need to confirm exact API base URL and available query parameters

### 2.4 ANR (Agence Nationale de la Recherche, France)

**Primary sources:**
1. **data.gouv.fr** (primary government open data portal): https://www.data.gouv.fr/datasets/appels-a-projets-anr-projets-retenus-et-participants-identifies
2. **data. enseignementsup-recherche.gouv.fr** — institutional dataset with ANR projects
3. **dataanr.opendatasoft.com** — user-friendly API explorer

| Aspect | Detail |
|--------|--------|
| **Data access** | CSV download from data.gouv.fr + REST API via OpenDataSoft |
| **API** | `https://dataanr.opendatasoft.com/api/explore/v2.0/console` |
| **Bulk export** | Yes — CSV download on data.gouv.fr |
| **License** | Open Government License (etalab) |
| **Fields** | Project title, participants (with SIREN for French orgs, RNSR IDs), call name, year, amount, status |
| **Scope** | All ANR-funded projects since 2005 |

**Steps to implement:**
1. Download CSV from data.gouv.fr (covers retained projects + participants)
2. Or query via OpenDataSoft API with filters for DH-relevant calls
3. ANR uses `SIREN` (company registry numbers) for French institutions — useful for institutional deduplication

**Known challenges:**
- Abstracts not always included in the public dataset
- ANR has many instrument types — need to understand call structure to filter meaningfully
- SIREN/RNSR identifiers require mapping to institutional names

---

## Part 3 — Topic Classification Approaches

### 3.1 Reuse the SNSF Approach (Recommended Starting Point)

The existing SNSF pipeline uses a **dual-criterion keyword + discipline filter** (see 1.4). This approach is portable to other funders with minimal adaptation:

- **For ERC/DFG/FWF/ANR:** Replace `MainDiscipline` lookup with the funder's own subject classification field (ERC uses NABS chapters, DFG uses subject areas, ANR uses call-type labels)
- **Keep the keyword filter:** Apply the same DH keyword list to `Title`, `Abstract`, and `Keywords` fields across all funders

### 3.2 Enhanced Classification: Sub-Discipline Clustering

Once a project is flagged as DH, classify into a sub-cluster:

| Cluster | Keywords |
|---------|---------|
| Text / Language / Philology | corpus linguistics, nlp, language model, lexicography, computational linguistics, tei, digital edition |
| Historical Data & Archives | digital archives, archive science, paleographic, handwriting recognition, historical database |
| Cultural Heritage / Art | digital cultural heritage, virtual reconstruction, 3D model, art history, museum |
| AI/ML Methods | machine learning, deep learning, neural network, text mining, computer vision |
| Network / Knowledge Graph | semantic web, linked data, knowledge graph, network analysis |
| Multimedia / Corpora | audio corpus, video analysis, film, oral history recording |

This mirrors the existing `fine_granular_clusters.csv` output from the SNSF pipeline.

### 3.3 Scalable Classification: Embedding + Clustering

For a more robust approach at scale (hundreds to thousands of projects), consider:

1. **Embed abstracts** using a sentence-transformer model (e.g., `sentence-transformers/all-MiniLM-L6-v2`)
2. **Cluster** the resulting vectors using HDBSCAN or k-means
3. **Label clusters** manually or via keyword extraction from cluster members
4. This avoids hard-coded keyword lists and adapts to emerging DH topics

**Implementation sketch:**
```python
from sentence_transformers import SentenceTransformer
import hdbscan

model = SentenceTransformer('all-MiniLM-L6-v2')
abstracts = df['Abstract'].dropna().tolist()
embeddings = model.encode(abstracts, show_progress_bar=True)
clusterer = hdbscan.HDBSCAN(min_cluster_size=20, min_samples=5)
labels = clusterer.fit_predict(embeddings)
```

### 3.4 Cross-Funder Topic Normalization

Different funders use different classification taxonomies. To produce a consistent cross-funder topic view:

- Map each funder's subject categories to a shared DH taxonomy (e.g., a simplified version of the DH discipline list in 1.4)
- For ERC: NABS (Nomenclature for the Analysis and Comparison of Scientific Programmes) chapters are the standard classification — map to DH fields
- For DFG: subject area codes can be mapped to DH categories
- Create a `topic_mapping.csv` crosswalk table: `funder, original_topic, dh_topic_normalized`

---

## Part 4 — Institutional Metadata

### 4.1 Fields to Capture Per Funder

| Field | SNSF | ERC | DFG | FWF | ANR |
|-------|------|-----|-----|-----|-----|
| PI name | ✅ | ✅ | ✅ | ✅ | ✅ |
| PI institution | ✅ | ✅ | ✅ | ✅ | ✅ |
| Institution country | ✅ | ✅ | ✅ | ✅ | ✅ |
| Co-PI / collaborators | ⚠️ (implicit via keywords) | ⚠️ (in project participants) | ⚠️ (in project details) | ⚠️ | ⚠️ |
| Funding amount | ✅ | ✅ | ✅ | ✅ | ✅ |
| Funding instrument | ✅ | ✅ | ✅ | ✅ | ✅ |
| Start/end dates | ✅ | ✅ | ✅ | ✅ | ✅ |

### 4.2 Co-PI Network Construction

True co-PI data requires the "project participants" table, not just the lead PI. Available sources:

- **ERC (CORDIS):** Has explicit `participant` table with roles — can extract co-PI relationships directly
- **DFG (GEPRIS):** "Participating researchers" section on each project page — requires scraping
- **FWF (API):** Should include participant data if in their schema
- **ANR (data.gouv.fr):** Participant list with SIREN identifiers — explicitly includes all partners

**Recommendation:** Build participant edge extraction for each funder as a separate pipeline step.

### 4.3 Institutional Identity Resolution

Name variants for institutions are a significant problem. Recommended approach:

1. Use **ROR IDs** (Research Organization Registry) as canonical org identifiers — available for ERC and increasingly for DFG/FWF/ANR
2. Map funder-provided institution names to ROR using the ROR API: `https://ror.org/api/search`
3. Fallback: use normalized name strings (lowercase, strip punctuation, common abbreviations)

### 4.4 Cross-Funder PI Matching

Matching PIs across funders is hard (name variants, initials vs full names). Possible approaches:

- **ORCID** — if available across all fields (ERC includes ORCID; FWF API explicitly mentions ORCID; ANR may include it)
- **Name + institution fuzzy matching** — for PIs without ORCID, match by normalized name + same institution
- **Accept some duplication** — better to have duplicates than false merges; flag for manual review

---

## Part 5 — Integration into DH Field Overview Reports

### 5.1 Unified Data Schema

Define a **common export format** that all funder pipelines output, enabling a single report generator to consume data from all sources:

```
funder_project_export.csv
├── funder           # SNSF | ERC | DFG | FWF | ANR
├── project_id       # Funder's grant number
├── title            # English title (original if unavailable)
├── abstract         # English abstract
├── pi_name          # Lead PI full name
├── pi_orcid         # ORCID if available
├── institution_name # Raw institution name
├── institution_ror  # ROR ID (resolved)
├── institution_country
├── funding_amount   # Numeric (CHF for SNSF, EUR for others)
├── funding_instrument
├── grant_start_date
├── grant_end_date
├── project_state    # Ongoing | Approved | Completed
├── dh_classified    # TRUE | FALSE
├── dh_cluster       # Text/Language | CulturalHeritage | AI/ML | Network | Multimedia | Other
├── topics_raw       # Funder's original topic/keyword tags
└── topics_normalized # Mapped to shared DH taxonomy
```

### 5.2 Report Structure

The existing SNSF report `dh-field-overview-2026-06.md` can be extended with funder-specific sections:

```
# Digital Humanities Research Funding — European Overview

## Executive Summary
[Combined statistics across all funders]

## 1. SNSF (Switzerland)
[Existing content — auto-refreshed]

## 2. ERC (European Research Council)
[DH grants summary, top institutions, topic breakdown]

## 3. DFG (Germany)
[DH grants summary, top institutions, topic breakdown]

## 4. FWF (Austria)
[DH grants summary, top institutions, topic breakdown]

## 5. ANR (France)
[DH grants summary, top institutions, topic breakdown]

## 6. Cross-Funder Analysis
[Combined PI networks, institutional co-funding, topic overlap]
```

### 5.3 Automated Report Generation

Extend `generate_report.py` to:

1. Accept multiple input CSVs (one per funder) in the unified schema
2. Run funder-specific DH classification filters (some funders need different keyword sets)
3. Merge into a combined DataFrame for cross-funder stats
4. Produce funder-specific sections + combined summary

```python
# Sketch
def load_funder_csv(path, funder_name):
    df = pd.read_csv(path, dtype=str)
    df['funder'] = funder_name
    return df

def main(funders_config, output_dir):
    all_dfs = [load_funder_csv(cfg['path'], cfg['name']) for cfg in funders_config]
    combined = pd.concat(all_dfs, ignore_index=True)
    # ... rest unchanged for combined stats
    # Then loop over funder subsets for funder-specific sections
```

### 5.4 Refresh Cadence

| Funder | Data format | Refresh cadence |
|--------|------------|----------------|
| SNSF | CSV download | Every 6 months (cron job exists) |
| ERC | CORDIS API / CSV | Every 6–12 months |
| DFG | Scraping | Every 6–12 months |
| FWF | API (CC0) | Every 3–6 months |
| ANR | CSV download + API | Every 6–12 months |

Consider a **6-monthly combined report cycle** covering all funders simultaneously, with the SNSF cron kept at 6 months.

---

## Part 6 — Recommended Implementation Sequence

Given complexity and data quality, recommend a phased approach:

### Phase 1 — Quick Wins (1–2 days)
- [ ] **FWF:** Implement API scraper (API exists, CC0 license, well-structured). Small corpus — low risk.
- [ ] **ANR:** Download CSV from data.gouv.fr, apply same DH filter as SNSF.

### Phase 2 — Medium Effort (3–5 days)
- [ ] **ERC:** Register for CORDIS API key. Write CORDIS-specific pipeline: filter by `fundingScheme` = ERC grants. Apply DH classification.
- [ ] Build unified export schema and verify all 5 funders map correctly.

### Phase 3 — Harder (5–10 days)
- [ ] **DFG:** Implement GEPRIS scraper. Need to handle rate limiting, pagination, and inconsistent page structures. Consider pre-built datasets if available.
- [ ] **co-PI extraction:** Build participant extraction for ERC (easiest — structured data), then DFG/FWF/ANR.

### Phase 4 — Integration & Polish (3–5 days)
- [ ] Extend `generate_report.py` to handle multi-funder input.
- [ ] Build cross-funder PI network (ROR-based institutional resolution, ORCID-based identity).
- [ ] Produce first combined European DH funding landscape report.

---

## Appendix A — Quick Reference: Data Access Summary

| Funder | Access method | Auth | License | Bulk export | DH-relevant fields |
|--------|--------------|------|---------|-------------|-------------------|
| **SNSF** | CSV download (`data.snf.ch`) | None | CC-BY | Yes | Discipline, Keywords, Abstract, Title, Amount |
| **ERC/CORDIS** | REST API + Web UI | API key (free) | CC-BY | Yes (CSV/JSON/XLSX) | Abstract, participants, fundingScheme, topics |
| **DFG/GEPRIS** | Web scraping | None | Implicit fair use | No (scrape required) | Title, PI, institution, subject area |
| **FWF** | Open API | None | CC0 | Via API | Grant DOI, ORCID, ROR, project data |
| **ANR** | data.gouv.fr CSV + OpenDataSoft API | None | Open Government | Yes | Participants (SIREN), title, call type, amount |

---

## Appendix B — HAVI (Schmidt Sciences) — Manual Tracking

For completeness (flagged 2026-06-30):

- **No public data API** — awardees listed on https://www.schmidtsciences.org/humanities-and-ai-virtual-institute/
- 2025 inaugural cohort: 23 teams, $11M total
- Level I: $100K–$299K, Level II: $300K–$800K
- Requires dual humanities + AI co-PIs
- 2026 RFP closed March 13, 2026 (up to $800K)

**Recommended:** Manual scrape of awardee list annually, stored as `data/havi_awardees.csv`.

---

*This document is intended as a working reference. Update as implementation progresses.*