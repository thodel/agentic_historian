# Agentic HITL Plan — Clickable Checkpoints, Metadata-Driven Re-Runs

**Status:** in progress — HITL-1a…2b merged (#145–#149); #150→ remaining. #139 (verbatim feedback) subsumed ·
**Updated:** 2026-07-04 · **Companion docs:** `IMPLEMENTATION_PLAN.md` (MCP hub), `README.md`

## Why

Today the pipeline is a fixed A→B→(kraken re-run)→C(→D) sequence: the historian
drops a file and receives final artifacts. All routing decisions (which kraken
model, which language/century assumptions, which transcription wins) are made
by heuristics on **inferred** metadata — precisely the fields a historian can
correct in **two seconds and one click**. This plan makes the system agentic in
the useful sense: it acts autonomously, but exposes its routing decisions as
compact, clickable checkpoints whose inputs trigger targeted re-runs.

## Design principles

1. **Humans correct metadata, never transcriptions.** Dating, language,
   script/style, document type — small structured inputs. Text correction is
   explicitly out of scope (that is the eval/GT track, AH-10).
2. **Click, don't chat.** All interaction is Discord UI components
   (`discord.ui.Select`, `Button`) on ONE self-updating "routing card" message
   per document. The bot never asks open questions and never spams follow-up
   messages; the card edits itself in place.
3. **Ask only when uncertain.** A checkpoint blocks only if the router is
   genuinely unsure (see gating rules). Otherwise the pipeline auto-proceeds
   and the card shows what was decided, with a non-blocking "Ändern…" button.
4. **Human input is authoritative and persistent.** A clicked value pins the
   field (the LLM may not override it in re-runs), is stored in the RunState,
   and is fed back as a routing prior for future documents.
5. **Re-run only what the input invalidates.** Changing the century must not
   redo the VLM transcription; it re-selects the kraken model, re-runs kraken,
   re-reconciles, and refreshes B/C outputs that depend on the result.

## The routing card (Gate 1 — after Phase 1+2)

After the VLM draft (Phase 1) and Agent B's description (Phase 2), the bot
posts one embed per document:

```
📜 saa-0428  ·  Routing
Datierung   : 15. Jh.        (inferred, unsicher)
Sprache     : Frühnhd. (de)  (inferred)
Schrift     : Kursive        (inferred, unsicher)
Typ         : Zinsregister   (inferred)
HTR-Modell  : trocr-kurrent-xvi-xvii  (score 0.45 ⚠️)
[Select: Datierung ▾] [Select: Sprache ▾] [Select: Schrift ▾] [Select: Typ ▾]
[✅ Weiter]  [🔁 Neu mit meiner Auswahl]  [⏭️ Überspringen]
```

- The four selects map **1:1 onto `SourceCriteria`**
  (`agent_a/model_selector.py`) — the plumbing already exists:
  `script`, `lang`, `century`, `document_type`.
- Select options come from existing registries: centuries 13.–17. Jh.,
  `LANG_ALIASES` codes, `SCRIPT_ALIASES` keys, `hub.get_document_types()`.
- **Any select change → `🔁` becomes primary.** Clicking it builds a
  `SourceCriteria` with the human values, calls `select_kraken_model()`,
  re-runs the kraken path with the new model, re-reconciles, and updates the
  card in place (model name + score change is immediately visible).
- Pinned fields are injected into downstream prompts as constraints:
  `"Gesichert (Historiker:in): Datierung = 15. Jh."` — Agent B must adopt,
  not re-infer, and its JSON element is overwritten with the pinned value.

### Invalidation matrix (what a click re-runs)

| Human input      | model sel. | kraken | reconcile | Agent B | Agent C | Agent D |
|------------------|:---:|:---:|:---:|:---:|:---:|:---:|
| Datierung        | ✅ | ✅ | ✅ | pin field | ✅ | stale-flag |
| Sprache          | ✅ | ✅ | ✅ | pin field | ✅ | stale-flag |
| Schrift/Stil     | ✅ | ✅ | ✅ | pin field | — | — |
| Dokumenttyp      | ✅ | ✅ | ✅ | pin field | — | — |
| Pfad-Präferenz (Gate 2) | — | — | choose | ✅ | ✅ | stale-flag |
| Entity-Link (Gate 3)    | — | — | — | — | write hub | — |

("stale-flag": corpus outputs get a `stale: true` marker; Agent D re-runs
lazily on next `/agent_d`, not eagerly per click.)

## Gate 2 — path comparison (after Phase 3)

When ≥2 transcription paths produced output, the card gains a compare section:
first ~300 chars of VLM / kraken / reconciled side by side, plus their mutual
CER (from `eval/metrics.py` — measured disagreement, not a fake confidence).

```
[Nutze VLM] [Nutze Kraken] [Nutze Reconciled ✓default]
```

One click sets `ctx.transcription`, reruns B/C on it, and records the
preference (see feedback loop). No free-text discussion.

## Gate 3 — entity-link review (after Agent C)

Only entities with `link_method in {none, hls_dhs}` (i.e. `unverified`/`low`)
surface for review — max 5 per document, one select per entity listing the
top-3 candidates from the MCP federation (HLS / SSRQ / GND / Wikidata, per
`IMPLEMENTATION_PLAN.md`) plus "kein Link". A click:

1. sets the link on the entity output,
2. **writes the observed spelling into the hub as a variant**
   (`hub.add_person/add_place`), so the next document gets a `hub_exact` hit.

This is the compounding loop: every click permanently improves linking.

## Gating rules (when a gate actually blocks)

A gate interrupts only if any of:
- `select_kraken_model()` top score < 0.6, or top-2 scores within 0.15;
- Agent B marked `Datierung`/`Sprache`/`Schrift` as `unsicher: true`;
- reconciliation disagreement (CER between paths) > threshold (Gate 2);
- ≥1 unverified PERSON/PLACE entity (Gate 3, batched).

Otherwise the card is posted already-resolved (ℹ️ auto-routed) with an
"Ändern…" button — the human can still intervene, but nothing waits. Every
blocking gate has a **timeout** (default 30 min, configurable) after which the
default action fires and is recorded as `decided_by: "auto"`.

## Mechanics

- **RunState** (`data/runs/<doc_id>.json`): stage artifacts, criteria,
  `human_overrides: [{field, value, user, ts}]`, gate decisions, Discord
  message ids. Makes every run resumable and re-runnable; the orchestrator
  becomes a small state machine over stages instead of one linear function.
- **Persistent views:** components use stable `custom_id`s
  (`ah:<doc_id>:<gate>:<field>`), views registered with `timeout=None` +
  `bot.add_view()` on startup — clicks survive bot restarts.
- **Single worker queue:** interaction callbacks only enqueue events
  (`asyncio.Queue`); one worker consumes them and runs blocking stages via
  `asyncio.to_thread`. Replaces the per-user `_active_runs` guard and keeps
  the event loop responsive.
- **Gate logic stays UI-agnostic:** gates emit/consume `PendingInput` records
  in RunState; the Discord layer is just one renderer. A later thin web UI
  (or Argilla-style review app) reuses the same records.
- **Feedback log** (`data/feedback/routing.jsonl`): one line per human input —
  inferred value vs. chosen value, model in/out, path preference. Agent E
  reports override rates per field and model win-rates; once enough data
  exists, `score_model()` gets a small additive prior from it ("historians
  picked this model for kurrent/16. Jh. 9 of 10 times").
- **Orchestrator model:** phases 1–3 stay rule-based. Phase 4 may hand routing
  to `GPUSTACK_MODEL_ORCHESTRATOR` (`minimax-m2.7` — reserved and currently
  unused): input = stage outputs + pinned fields, output = a routing decision
  JSON validated against the same invalidation matrix. Rule-based remains the
  fallback.

## Issue breakdown (Epic #142)

Filed 2026-07-04. Suggested build order: top to bottom; #146 is the first
user-visible milestone. #151 is blocked on #92 (KH-6, MCP entity linking).

| ✓ | ID | Issue | Prio | Scope | Acceptance (short) |
|---|---|---|---|---|---|
| ✅ | HITL-1a | #145 | P0 | RunState + stage-invalidation state machine | `invalidate("century")` dirties exactly {model_select, kraken, reconcile, B-pin, C}; `resume()` re-runs only dirty stages |
| ✅ | HITL-1b | #146 | P0 | Gate 1 routing card (selects → kraken re-run) | "15. Jh." + 🔁 switches the model on the card and refreshes B/C; card edits in place |
| ✅ | HITL-1c | #147 | P1 | Pinned fields authoritative downstream | pinned Datierung survives into `descriptions/<id>.json` with `quelle: historiker` |
| ✅ | HITL-2a | #148 | P1 | Worker queue replaces `_active_runs` | two parallel `/run`s stay responsive; clicks queued, not raced |
| ✅ | HITL-2b | #149 | P1 | Gate 2 path comparison (measured CER) | choosing kraken re-runs B/C; no interrupt when paths agree |
|  | HITL-2c | #150 | P1 | Persistent views | clicks survive bot restart (custom_id `ah:<doc>:<gate>:<field>`) |
|  | HITL-3a | #151 | P1 | Gate 3 entity-link review (MCP candidates) | only unverified/low entities gate; #92 (MCP linking) now done |
|  | HITL-3b | #152 | P1 | Hub variant write-back on click | reprocessing same spelling links `hub_exact` with zero interaction |
|  | HITL-4a | #153 | P2 | Uncertainty gating + timeouts | confident docs: zero blocking interrupts; timeout → `decided_by: auto` |
|  | HITL-4b | #154 | P2 | Feedback log + Agent E reporting | one JSONL line per decision; override rates in `/agent_e` |
|  | HITL-4c | #155 | P2 | Routing prior in `score_model()` (flag) | flag off = byte-identical scores; prior capped below a full criteria match |
|  | HITL-4d | #156 | P2 | Optional LLM router on `minimax-m2.7` (flag) | invalid decisions fall back rule-based; first concrete scope for #32 |

## Relation to other epics

- **#139 (verbatim agent feedback) — SUBSUMED into this epic (reconciled 2026-07-04).**
  #139 and this plan are two layers of one feature: #139 is the *observe/verbatim*
  layer (a structured per-phase event stream), this epic is the *interact/correct*
  layer (a self-updating card that renders those events and adds click-to-correct +
  targeted re-runs). Rather than run two plans, #139 is folded in — one card, one
  event stream. Mapping of the #139 VF-items to their home here:

  | #139 item | Home | Note |
  |---|---|---|
  | VF-1 `PhaseEvent` emitter | **#145** (RunState + state machine) | RunState *is* the per-stage event/decision store; the orchestrator emits stage events as it fills it — build one, not two |
  | VF-2 incremental `/run` rendering | **#146** (Gate-1 routing card) | the card is the verbatim renderer *and* the interactive control — one self-updating message |
  | VF-3 verbatim errors (real messages, not counts) | **#146** acceptance | the card shows the actual `{agent, phase}: message`, never `Errors: <n>` |
  | VF-4 `/inspect <doc>` full-content dump | **kept as a small standalone read-only command** | card = summary + controls; `/inspect` dumps the full RunState/pipeline.json (transcription, description, entities) on demand |
  | VF-5 excerpt/verbosity config | **#146/#153** config | `MAX_FEEDBACK_EXCERPT`; storage stays fully verbatim (#98/#100/#121), only the *display* excerpt is bounded |

  Net: **build VF-1 as the shared foundation of #145**, so gates and verbatim
  feedback share one event stream; #139 is closed as subsumed.
- **#33 (thin bot shell)** — gates emit UI-agnostic `PendingInput` records;
  `bot.py` only renders them.
- **#129 / #92 (KH consumer track)** — Gate 3 candidate lists come from the
  MCP federation; #151 depends on #92.
- **#32 (SitL/NL orchestrator)** — #156 is its first narrow, schema-validated,
  fallback-protected application; conversational control stays out of scope.
- **#24 (GT eval / AH-10)** — Gate 2 shows *measured* inter-path CER from
  `eval/metrics.py`; no pseudo-confidence anywhere in the UI.

## Out of scope

- Transcription editing UIs (Transkribus exists; our GT track is AH-10).
- Conversational/NL control of the pipeline ("talkative" mode) — the SitL
  orchestrator (WP1) may add it later, but checkpoints stay click-first.
- Web frontend (the gate/RunState design keeps the door open).
