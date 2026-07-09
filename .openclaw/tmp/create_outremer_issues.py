#!/usr/bin/env python3
"""Create all OUTREMER GPUStack issues on thodel/outremer."""
import subprocess

MILESTONE_MAP = {
    "Epic 1 — LLM Provider Abstraction": "Epic 1 — LLM Provider Abstraction",
    "Epic 2 — Pipeline Reliability": "Epic 2 — Pipeline Reliability",
    "Epic 3 — KG Enrichment & Authority Quality": "Epic 3 — KG Enrichment & Authority Quality",
}

ISSUES = [
    # Epic 1 — LLM Provider Abstraction
    {
        "title": "M1.1 — Config Layer (scripts/config.py + .env.gpustack)",
        "body": """**Epic:** Epic 1 — LLM Provider Abstraction

## Goal
GPUStack as a first-class config option. All LLM calls route through tei.dh.unibe.ch.

## Files
- `scripts/config.py` (new) — GPUSTACK_BASE_URL, GPUSTACK_API_KEY, GPUSTACK_TIMEOUT, EXTRACTION_MODEL, ORCHESTRATOR_MODEL, OCR_ENGINE
- `.env.gpustack` (new, git-ignored)
- `.gitignore` — confirm `.env.gpustack` is excluded

## Env vars
| Var | Default |
| GPUSTACK_BASE_URL | https://tei.dh.unibe.ch/v1 |
| GPUSTACK_API_KEY | (empty) |
| GPUSTACK_TIMEOUT | 120 |
| EXTRACTION_MODEL | qwen3-30b-a3b-instruct |
| ORCHESTRATOR_MODEL | minimax-m2.7 |
| OCR_ENGINE | easyocr |

## Acceptance
Pipeline reads GPUStack config from .env.gpustack — no hardcoded URLs or keys.""",
        "milestone": "Epic 1 — LLM Provider Abstraction",
        "labels": ["epic"],
    },
    {
        "title": "M1.2 — GPUStack Client (scripts/llm_client.py)",
        "body": """**Epic:** Epic 1 — LLM Provider Abstraction

## Goal
Create `scripts/llm_client.py` — thin `openai.OpenAI` wrapper pointed at GPUStack base URL.

## API
```python
from scripts.llm_client import generate, get_client
text = generate("Extract persons from: ...", system="You are an expert historian...")
text = generate(prompt, model="minimax-m2.7", max_tokens=2048, temperature=0.1)
```

## Implementation
- Singleton `openai.OpenAI` client (reused across calls)
- `generate(prompt, *, system=None, model=None, **kwargs)` returns `str`
- Uses `config.GPUSTACK_BASE_URL`, `config.GPUSTACK_API_KEY`, `config.GPUSTACK_TIMEOUT`
- Mirror pattern from `thodel/agentic_historian/utils/gpustack_client.py`

## Acceptance
`python -c "from scripts.llm_client import generate; print(generate('Say hello in one word'))"` returns a response from tei.dh.unibe.ch.""",
        "milestone": "Epic 1 — LLM Provider Abstraction",
        "labels": ["epic"],
    },
    {
        "title": "M1.3 — Port Person Extraction to GPUStack",
        "body": """**Epic:** Epic 1 — LLM Provider Abstraction

## Goal
Replace `google.genai` calls in `scripts/extract_persons_google.py` with `scripts.llm_client.generate()`.

## Changes
1. Remove `from google import genai` import and `google.genai.Client` instantiation
2. Replace `client.models.generate_content(model="gemini-2.0-flash", ...)` with `generate(prompt, system=SYSTEM_PROMPT, model=EXTRACTION_MODEL, ...)`
3. Remove `google-genai` from `requirements.txt`; add `openai` explicit
4. Remove `GOOGLE_API_KEY` checks in `run_pipeline.py`
5. Replace emoji in SYSTEM_PROMPT with ASCII: `[EXCLUDE]`, `[INCLUDE]`, `[FORMAT]`
6. Remove Gemini-specific framing; add "Output valid JSON only. No markdown fences."

## Acceptance
`python scripts/run_pipeline.py --genai-metadata` extracts persons using GPUStack (Qwen3). Quality delta <10% vs Gemini baseline on same benchmark documents.""",
        "milestone": "Epic 1 — LLM Provider Abstraction",
        "labels": ["epic"],
    },
    {
        "title": "M1.4 — Port OCR to EasyOCR + GPUStack Fallback",
        "body": """**Epic:** Epic 1 — LLM Provider Abstraction

## Goal
Replace `mistralai` OCR in `scripts/run_pipeline.py` with local EasyOCR + GPUStack MiniMax-M2.7 fallback.

## Changes
```python
def _ocr_image(path: Path) -> str:
    engine = os.environ.get("OCR_ENGINE", "easyocr")
    if engine == "easyocr":
        return _easyocr(path) or _gpustack_ocr(path) or _mistral_ocr(path)
    elif engine == "gpustack":
        return _gpustack_ocr(path) or _mistral_ocr(path)
    else:
        return _mistral_ocr(path)

def _easyocr(path: Path) -> str:       # new — local, no API
def _gpustack_ocr(path: Path) -> str:  # new — GPUStack MiniMax-M2.7
```

## Priority: EasyOCR (local) > GPUStack MiniMax-M2.7 > Mistral (legacy)

## Acceptance
`OCR_ENGINE=easyocr` produces readable text from medieval Latin/Old French charters with no external API call.""",
        "milestone": "Epic 1 — LLM Provider Abstraction",
        "labels": ["epic"],
    },
    {
        "title": "M1.5 — Smoke Test (scripts/test_llm_client.py)",
        "body": """**Epic:** Epic 1 — LLM Provider Abstraction

## Goal
Create `scripts/test_llm_client.py` smoke test — verifies both GPUStack models respond and parse correctly.

## Test cases
1. EXTRACTION_MODEL responds to a simple prompt
2. ORCHESTRATOR_MODEL responds to a simple prompt
3. JSON output from EXTRACTION_MODEL parses correctly
4. Credentials are valid (not 401/403)

## Run
```bash
python scripts/test_llm_client.py
```

## Acceptance
All 4 test cases pass when GPUStack is reachable at tei.dh.unibe.ch.""",
        "milestone": "Epic 1 — LLM Provider Abstraction",
        "labels": ["epic"],
    },
    # Epic 2 — Pipeline Reliability
    {
        "title": "M2.1 — JSON Recovery for Malformed LLM Output",
        "body": """**Epic:** Epic 2 — Pipeline Reliability

## Problem
Local models sometimes output markdown fences or extra text around JSON. Current parser dies.

## Fix
Add `_parse_llm_json()` in `scripts/extract_persons_google.py`:
1. Try direct `json.loads()`
2. Strip markdown fences (` ```json ... ``` `)
3. Extract first `{` to last `}` window
4. Return `None` if all fail (caller handles gracefully)

## Acceptance
95% of LLM outputs parse successfully after recovery attempts on benchmark set.""",
        "milestone": "Epic 2 — Pipeline Reliability",
        "labels": ["epic"],
    },
    {
        "title": "M2.2 — Chunk Boundary Respect",
        "body": """**Epic:** Epic 2 — Pipeline Reliability

## Problem
`_chunk_text()` splits mid-sentence, breaking person name context across chunks.

## Fix
Split on `\\n\\n` (paragraph boundaries) instead of arbitrary character offsets. Ensure no chunk is larger than the configured chunk size but never split mid-sentence.

## Acceptance
Chunk boundaries fall on paragraph breaks; no person mention is split across chunks.""",
        "milestone": "Epic 2 — Pipeline Reliability",
        "labels": ["epic"],
    },
    {
        "title": "M2.3 — Retry with Exponential Backoff",
        "body": """**Epic:** Epic 2 — Pipeline Reliability

## Goal
Add retry logic to all unreliable call sites with exponential backoff.

## Apply to
- `generate()` in `scripts/llm_client.py` (3 attempts, 2s base delay)
- `_gpustack_ocr()` in `scripts/run_pipeline.py`
- Wikidata SPARQL calls in `scripts/wikidata_reconcile.py`

## Pattern
```python
@functools.wraps(fn)
def with_retry(fn, max_attempts=3, base_delay=2.0, logger=None):
    for attempt in range(max_attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if attempt == max_attempts - 1: raise
            delay = base_delay * (2 ** attempt)
            if logger: logger.warning("Retry %d/%d after %.1fs: %s", ...)
            time.sleep(delay)
```

## Acceptance
Transient network/model errors are retried transparently; final error is logged with attempt count.""",
        "milestone": "Epic 2 — Pipeline Reliability",
        "labels": ["epic"],
    },
    {
        "title": "M2.4 — Pipeline Run Reports",
        "body": """**Epic:** Epic 2 — Pipeline Reliability

## Goal
Write `data/staging/run_report.json` after each pipeline run.

## Schema
```json
{
  "run_at": "2026-07-05T18:00:00Z",
  "docs_total": 12,
  "docs_ok": 10,
  "docs_failed": 2,
  "total_persons": 347,
  "llm_provider": "gpustack",
  "extraction_model": "qwen3-30b-a3b-instruct",
  "ocr_engine": "easyocr",
  "failures": [{"doc": "henri1_charter", "error": "timeout", "retry": 3}]
}
```

## Acceptance
Every `run_pipeline.py` invocation produces a run report in `data/staging/run_report.json`.""",
        "milestone": "Epic 2 — Pipeline Reliability",
        "labels": ["epic"],
    },
    # Epic 3 — KG Enrichment & Authority Quality
    {
        "title": "M3.1 — Fuzzy Wikidata Matching (0 to 10k+ QID links)",
        "body": """**Epic:** Epic 3 — KG Enrichment & Authority Quality

## Problem
`build_unified_kg.py` uses exact normalized name matching only. Result: 0 Wikidata QID links despite 19k+ Wikidata persons in the peerage export.

## Fix
Replace exact match with RapidFuzz fuzzy matching:
```python
from rapidfuzz import fuzz

def match_wikidata_to_authority(auth_persons, wikidata_persons, threshold=85):
    for auth_id, person in auth_persons.items():
        name = person["preferred_label"]
        best_score, best_qid = 0, None
        for qid, wd in wikidata_persons.items():
            score = fuzz.token_sort_ratio(name, wd["preferred_label"])
            if score > best_score:
                best_score, best_qid = score, qid
        if best_score >= threshold:
            person["identifiers"]["wikidata_qid"] = best_qid
```

## Acceptance
>=50% of the 126 authority persons get a `wikidata_qid` link.""",
        "milestone": "Epic 3 — KG Enrichment & Authority Quality",
        "labels": ["epic"],
    },
    {
        "title": "M3.2 — Gender Field Fix",
        "body": """**Epic:** Epic 3 — KG Enrichment & Authority Quality

## Problem
`load_wikidata_peerage()` parses P21 (sex or gender) but populates `bio.gender` instead of `bio.sex`. Most Wikidata persons have gender=unknown.

## Fix
Fix the field name in `load_wikidata_peerage()`:
```python
# Before (wrong):
person["bio"]["gender"] = gender_label
# After (correct):
person["bio"]["sex"] = gender_label
```

## Acceptance
Gender field populated for >=80% of Wikidata-sourced persons in `unified_kg.json`.""",
        "milestone": "Epic 3 — KG Enrichment & Authority Quality",
        "labels": ["epic"],
    },
]

REPO = "thodel/outremer"

for issue in ISSUES:
    title = issue["title"]
    body = issue["body"]
    milestone = issue["milestone"]
    labels = ",".join(issue["labels"])

    result = subprocess.run(
        ["gh", "issue", "create",
         "--repo", REPO,
         "--title", title,
         "--body", body,
         "--milestone", milestone,
         "--label", labels],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"Created: {title}")
    else:
        print(f"FAILED ({result.stderr.strip()[:100]}): {title}")