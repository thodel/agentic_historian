# Agentic Historian — Improvement Plan

**Created:** 2026-07-05 · **Status:** draft · **Owner:** thodel/agentic_historian

---

## Purpose

This document identifies concrete, actionable improvements across the agentic_historian
project — ordered by priority and grouped by theme/epic. Each item has a short
description, acceptance target, and issue number (if already filed).

---

## 1. 🔴 High Priority — HITL Epic Completion (#142)

The HITL epic is the biggest remaining user-facing feature. Gates 1 and 2 are done
(#145–#149). Gate 3 partial (write-back done as #152). Remaining items must land
before the system is usable in production.

### 1.1 HITL-2c: Persistent Views (#150) — P1
**What:** Gate 1/2/3 Discord views must survive bot restarts. `RoutingCardView`,
`PathComparisonView`, and `Gate3View` are already registered with `timeout=None`,
but the views are not persisted across restarts.

**Acceptance:** After `/restart` of the bot, all pending routing cards still accept
clicks with the same `custom_id` format (`ah:<doc_id>:<gate>:<field>`).

**How:** Ensure `bot.add_view()` is called on startup for each view type, or migrate
to `discord.Object` with `message_id` for persistent view registration.

---

### 1.2 HITL-3a: Gate 3 UI Integration (#151) — P1
**What:** Wire `gate3_entity_review.py` into `bot.py` — render the Gate 3 card after
Agent C completes, register the view, handle the apply callback. Depends on #150
(persistent views) to survive restarts.

**Acceptance:** After a `/run`, if any unverified/low entity exists, a Gate 3 card
appears in Discord. Clicking "Verlinkung bestätigen" updates the hub and re-runs
nothing (non-blocking, per design).

**Note:** #92 (MCP entity linking) is done, which unblocks this.

---

### 1.3 HITL-4a: Uncertainty Gating + Timeouts (#153) — P2
**What:** Implement the gating rules from the HITL plan:
- Gate blocks only when router is genuinely uncertain (model score < 0.6, or top-2
  scores within 0.15; Agent B marked field `unsicher: true`; CER > threshold).
- All other cases → card posted as ℹ️ auto-routed with "Ändern…" button.
- Timeout (default 30 min) fires and records `decided_by: "auto"`.

**Acceptance:** Confident documents produce zero blocking interruptions. Uncertain
documents block. Timeout fires and logs the auto-decision.

---

### 1.4 HITL-4b: Feedback Log + Agent E Reporting (#154) — P2
**What:** `data/feedback/routing.jsonl` — one JSON line per human input (inferred vs.
chosen value, model in/out, path preference). `/agent_e` reports:
- Override rate per field (Datierung, Sprache, etc.)
- Model win-rate per script/century combination
- Path preference distribution (VLM vs kraken vs reconciled)

**Acceptance:** After ≥50 runs, `/agent_e` shows meaningful per-field override rates
and model win-rate tables.

---

### 1.5 HITL-4c: Routing Prior in score_model() (#155) — P2
**What:** When `data/feedback/routing.jsonl` has ≥10 entries for a
(script, century, lang) combination, add a small additive prior to `score_model()`
so that the model historically preferred by humans scores slightly higher.

**Constraint:** Prior is capped below a full criteria match — it nudges, not overrides.

**Acceptance:** Override rate for the prior-augmented model selection drops
meaningfully vs. the pre-prior baseline (measurable after Phase 9 GT testing).

---

### 1.6 HITL-4d: Optional LLM Router (#156) — P2
**What:** Route the pipeline via `GPUSTACK_MODEL_ORCHESTRATOR` (`minimax-m2.7`) for
Phase 4+ decisions — input = stage outputs + pinned fields, output = routing
decision JSON validated against the invalidation matrix. Rule-based remains the
fallback when the LLM decision is invalid.

**Acceptance:** LLM router produces valid routing decisions. Invalid decisions fall
back to rule-based. Flag to enable/disable (`ORCHESTRATOR_LLM_ENABLED=false`).

---

## 2. 🔴 High Priority — Phase 9 GT / Eval (AH-10)

### 2.1 Phase 9: Real-Document Testing — P1
**What:** Run the full pipeline on a representative set of ≥20 real documents with
known ground-truth transcriptions. Measure:
- Single-path CER (VLM, kraken individually)
- Reconciled CER vs. best single path (AH-17 acceptance criterion)
- Entity linking F1 (hub_exact precision/recall)

**Acceptance:** Reconciled output ≥ best single-path CER on the GT set (from #17).
GT set lives in `data/gt/` with `document_id, transcription, entities.json`.

---

### 2.2 Ground Truth Pipeline (AH-10) — P1
**What:** Formalise the GT track: document ingestion, GT transcription storage,
automated CER measurement against GT on every run. `/agent_eval` command that
reports per-document CER, per-entity F1, and aggregate scores.

**Acceptance:** `/agent_eval` on the GT set returns per-document and aggregate
CER + entity F1 within 5 minutes of a full run.

---

## 3. 🟡 Medium Priority — Serving ATR Inference Gateway (#143)

### 3.1 TrOCR via Gateway — P1
**What:** Add TrOCR as a third HTR pathway via the serving-atr-inference gateway.
Depends on the gateway auth being in place (epic #143 — partial, auth done).

**Acceptance:** `--htr trocr` flag works and returns a transcription.

---

### 3.2 LLM Reconciliation via Gateway — P1
**What:** The reconciliation step (dual_pipeline → reconcile.py) currently calls
`gs.chat_text()` directly. If the gateway supports a dedicated reconciliation
model, use it there with explicit `GPUSTACK_MODEL_TEXT` budget (already done in
#17).

**Acceptance:** Reconciliation uses the configured `GPUSTACK_MODEL_TEXT` model with
adequate token budget (≥ 4096 tokens).

---

## 4. 🟡 Medium Priority — Entity Quality

### 4.1 Entity Disambiguation — P2
**What:** When Agent C returns multiple candidates for one mention, the current
behavior picks the top candidate by score. A disambiguation step (leveraging the
document context from Agent B's description) could improve precision.

**Acceptance:** Entity linking precision on the GT set improves ≥ 5% with
disambiguation vs. top-1 selection.

---

### 4.2 Hub Variant Quality — P2
**What:** Gate 3 write-back (#152) writes observed spellings as variants, but
doesn't normalise them (e.g. expand common abbreviations, strip punctuation).
Add a normalisation step so `Hermann` / `Hainricus` / `H.` variants are merged
correctly.

**Acceptance:** Hub deduplicates name variants for the same person/place correctly.

---

### 4.3 Wikidata/GND Fallback — P2
**What:** When HLS has no candidate for an entity but Wikidata or GND does, those
records are currently ignored. Add a fallback chain: HLS → GND → Wikidata for
authority linking.

**Acceptance:** Entity linking recall increases measurably on the GT set.

---

## 5. 🟡 Medium Priority — Reliability & Operations

### 5.1 RunState Recovery — P1
**What:** After a bot crash/restart, the worker queue is empty and any in-progress
run is orphaned. Implement RunState-based recovery: on startup, scan
`data/runs/*.json` for any run with `status: in_progress` and offer a `/resume <doc>`
to complete it.

**Acceptance:** After `systemctl restart agentic_historian`, all pending runs are
visible via `/status` and can be resumed with `/resume <doc_id>`.

---

### 5.2 Error Log Persistence (#112) — P1
**What:** Already filed as #112 (DONE per PROGRESS.md). Verify the fix is in the
main branch and the error log survives bot restarts.

---

### 5.3 Pipeline Retry Logic — P2
**What:** When a stage fails (network timeout, model unavailable), retry up to 2×
with exponential backoff before surfacing the error to the human. Log all retries
in RunState for transparency.

**Acceptance:** Transient network failures don't produce visible errors to the
historian; only persistent failures after retries surface.

---

## 6. 🟢 Lower Priority — Developer Experience

### 6.1 CI on Every PR — P1
**What:** Already noted in PROGRESS.md. Ensure `pytest` runs on every PR for:
`test_ah_17_`, `test_ah_145_`, `test_ah_146_`, `test_ah_147_`, `test_ah_148_`,
`test_ah_149_`, `test_ah_152_`, plus lint/format checks.

---

### 6.2 Docker/Deployment — P2
**What:** `Dockerfile` + `docker-compose.yml` so Tobias can deploy without manual
environment setup. Include `.env` template, healthcheck endpoint, and restart
policy.

**Acceptance:** `docker compose up` results in a fully operational bot within 2
minutes on a fresh machine.

---

### 6.3 Inline Documentation — P2
**What:** Many modules lack docstrings (e.g. `agent_a/dual_pipeline.py`,
`agents/entity_agent.py`, `agents/source_description.py`). Add module-level and
function-level docstrings following the existing `path_compare.py` and
`routing_card.py` style.

**Acceptance:** `pydoc` or `mkdocs` generates navigable HTML docs for all public
modules.

---

### 6.4 Model Selector Logging — P2
**What:** `agent_a/model_selector.py` logs why a model was/wasn't selected
(`matched_on` field). Expand this to log all scored candidates and the winner's
full score breakdown — useful for Phase 9 analysis and future model evaluation.

---

## 7. 🟢 Future / Exploratory

### 7.1 LLM Orchestrator (SitL, #32) — P3
**What:** Full conversational control of the pipeline via natural language. #156
(HITL-4d) is the narrow first application. Broader SitL requires careful design
to keep click-first interaction as the primary mode.

---

### 7.2 Web UI — P3
**What:** The HITL plan (§design principles) keeps the door open for a thin web
UI that reuses the same `PendingInput` records in RunState. Low priority until
the Discord UI is stable and the compounding loop (Gate 3 variant write-back)
is validated.

---

### 7.3 Cross-Corpus Entity Matching — P3
**What:** When two documents reference the same person/place with very different
spelling, the hub variants help. A cross-corpus fuzzy match (using the hub's
variant index) could merge records that should share an HLS ID.

---

## Issue Summary

| # | Priority | Epic | Title |
|---|---|---|---|
| #150 | P1 | HITL-2c | Persistent views |
| #151 | P1 | HITL-3a | Gate 3 UI integration |
| #153 | P2 | HITL-4a | Uncertainty gating + timeouts |
| #154 | P2 | HITL-4b | Feedback log + Agent E reporting |
| #155 | P2 | HITL-4c | Routing prior in score_model() |
| #156 | P2 | HITL-4d | Optional LLM router |
| AH-10 | P1 | Phase 9 | Ground truth eval pipeline |
| (new) | P1 | Phase 9 | Real-document CER measurement |
| (new) | P1 | #143 | TrOCR via gateway |
| (new) | P1 | Ops | RunState recovery after restart |
| (new) | P2 | Entity | Entity disambiguation step |
| (new) | P2 | Entity | Wikidata/GND fallback chain |
| (new) | P2 | Entity | Hub variant normalisation |
| (new) | P2 | Ops | Pipeline retry logic |
| (new) | P2 | DX | Docker + docker-compose |
| (new) | P2 | DX | Inline documentation |
| (new) | P2 | DX | Model selector logging |
| (new) | P3 | Future | Cross-corpus entity matching |