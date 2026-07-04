# Agentic HITL Plan — Clickable Checkpoints, Metadata-Driven Re-Runs

**Status:** proposal · **Updated:** 2026-07-04 · **Companion docs:** `IMPLEMENTATION_PLAN.md` (MCP hub), `README.md`

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

## Phasing & acceptance

- **HITL-1 — RunState + Gate 1 (routing card).**
  _Accept:_ `/run` posts the card; selecting "15. Jh." + 🔁 visibly switches
  the kraken model and refreshes B/C outputs; pinned Datierung survives into
  `descriptions/<id>.json`; suite offline-testable (gate logic mocked, no
  Discord needed).
- **HITL-2 — Gate 2 compare + persistent views + worker queue.**
  _Accept:_ path choice reruns B/C; buttons still work after bot restart;
  two parallel `/run`s don't block the event loop.
- **HITL-3 — Gate 3 entity review + hub write-back.**
  _Accept:_ clicking a candidate adds the variant to `hub.json`; reprocessing
  the same document links it as `hub_exact`.
- **HITL-4 — uncertainty gating + feedback priors (+ optional LLM router).**
  _Accept:_ confident documents pass with zero interrupts; Agent E reports
  override rates; `score_model()` consumes the prior behind a feature flag.

## Out of scope

- Transcription editing UIs (Transkribus exists; our GT track is AH-10).
- Conversational/NL control of the pipeline ("talkative" mode) — the SitL
  orchestrator (WP1) may add it later, but checkpoints stay click-first.
- Web frontend (the gate/RunState design keeps the door open).
