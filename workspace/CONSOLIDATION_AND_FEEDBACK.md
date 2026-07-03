# Agentic Historian — Consolidation Backlog

Contributor task list for consolidating `agentic_historian` onto the unibe GPUStack,
derived from an audit of both repos against the project proposal (PDF 1) and the
technical implementation description (PDF 2).

**How to use this file**
- Each task has a stable ID (`AH-NN`), a priority, a status, an _acceptance_ criterion, and _refs_ (proposal Work Package and/or audit note).
- Pick an unchecked task, open a GitHub issue with the same ID/title, assign yourself, and link the PR. Check the box here when merged.
- Keep PRs small and additive. The repo has multiple active contributors — rebase before opening, and don't touch another epic's files unless coordinated.

**Legend**
- Priority: **P0** = blocking/correctness · **P1** = important · **P2** = later/nice-to-have
- Status: `[ ]` todo · `[~]` in progress · `[x]` done · 🔒 blocked (note the blocker)

---

## ✅ Already done (on `main` / in-flight PRs)
- [x] Rotate the leaked GPUStack API key; untrack `.env.gpustack`.
- [x] Remove duplicate agent implementations (`agent_c/`, inner `ocr_pipeline.py`).
- [x] Two-pronged HTR `agent_a/` (VLM + kraken + HF + LLM reconciliation).
- [x] **GPUStack hardening** (PR #1): env loading, role-based model routing, reasoning-model-safe client. _Routing: vision=`qwen3-vl-30b-a3b-instruct` (A/B), text=`gpt-oss-120b` (C/corpus/meta/reconcile), `minimax-m2.7` reserved for orchestration._

---

## Epic 1 — Infrastructure & repo hygiene (WP1)

- [ ] **AH-01** · P0 · Resolve the Discord library mismatch. `bot.py` uses the **py-cord** API (`Option`, `@bot.slash_command`) but `requirements.txt` pins `discord.py` → the bot won't import. _Acceptance:_ one library pinned, `python bot.py` imports and registers commands. _Refs: audit §1.5._
- [ ] **AH-02** · P0 · Make the pipeline non-blocking. `orchestrator.py` + agents are synchronous (`requests`, 120 s timeouts) but the Discord bot is async → blocking handlers freeze the bot. Migrate agents to `async` (or wrap blocking calls in `asyncio.to_thread`); the client already exposes `ask()`/`ask_structured()`. _Acceptance:_ a full `/run` does not block the event loop (bot stays responsive). _Refs: audit §1.4._
- [ ] **AH-03** · P1 · Add minimal CI (lint + import smoke test + `pip install -r requirements.txt`). _Acceptance:_ GitHub Action green on PRs; catches undeclared imports like the earlier missing `openai`.
- [ ] **AH-04** · P1 · Add a tiny test suite: client reasoning-model handling (mock), hub round-trip, orchestrator wiring. _Acceptance:_ `pytest` runs in CI.
- [ ] **AH-05** · P2 · Optional: scrub the old key blob from git history (`git filter-repo`). Non-urgent since the key is rotated; coordinate a force-push with all contributors first. _Refs: audit §1.1._

## Epic 2 — Agent A: HTR (WP2)

- [ ] **AH-10** · P0 · Build a ground-truth eval set (a handful of human-corrected transcriptions) and report **CER/WER** per pathway. Replace model self-scoring as the *acceptance gate*; keep LLM-QA only as a triage flag. _Acceptance:_ `eval` script prints CER for VLM vs kraken vs reconciled on the GT set. _Refs: audit §2.1._
- [ ] **AH-11** · P1 · Validate VLM choice for medieval hands: compare `qwen3-vl-30b-a3b-instruct` vs `qwen3-vl-8b` vs `internvl3-8b` on the GT set; set the winner as `GPUSTACK_MODEL_VISION` and align `agent_a/models.py`'s VLM registry. _Acceptance:_ documented CER comparison + chosen default. _Refs: audit §2.1._
- [ ] **AH-12** · P1 · Populate the kraken model registry (`agent_a/models.py KRAKEN_MODELS`) with real Zenodo model IDs for German/Latin scripts of the 14th–16th c. _Acceptance:_ `kraken` path produces output on a sample page. _Blocker:_ needs the curated model list.
- [ ] **AH-13** · P2 · Wire kraken line-segmentation before the HF OCR path (currently returns "requires pre-segmentation"). _Acceptance:_ HF path runs end-to-end on a line-image model. _Refs: `agent_a/dual_pipeline.py:_run_hf_ocr`._
- [ ] **AH-14** · P1 · Tune the LLM reconciliation prompt + agreement scoring; ensure it uses `GPUSTACK_MODEL_TEXT` with an adequate budget (reasoning model). _Acceptance:_ reconciled output ≥ best single-path CER on the GT set.

## Epic 3 — Agent B: Source description (WP3)

- [ ] **AH-20** · P1 · Emit **structured** output (JSON keyed by the 16 Ad Fontes elements) alongside the Markdown, so downstream agents and the hub can consume it. _Acceptance:_ `descriptions/<id>.json` validates against a documented schema. _Refs: `agents/source_heuristic.py`._
- [ ] **AH-21** · P1 · Raise the care-flag token budget (currently `max_tokens=600`, truncates the reasoning model) and parse defensively. _Acceptance:_ care-flag returns valid JSON on gpt-oss without truncation. _(Partly mitigated by PR #1's token floor.)_ _Refs: audit §2.1, `agents/source_description.py:76`._

## Epic 4 — Agent C: Entities & linking (WP4)

- [ ] **AH-30** · P0 · Restore all **8 entity types** (add back `SOCIAL_GROUP` and `CARE_ACTION` — the two categories that *are* the research payload). _Acceptance:_ extractor returns all 8 types; sample doc shows social/care entities. _Refs: audit §2.3._
- [ ] **AH-31** · P0 · Fix the hub linking contract. Agent C must call methods that exist on the persistent hub (see AH-40). _Acceptance:_ Agent C runs end-to-end with hub-first linking, no `AttributeError`. _Refs: audit §1.3._
- [ ] **AH-32** · P1 · Add **HLS** (Historisches Lexikon der Schweiz) linking alongside Wikidata + GND. _Acceptance:_ persons/places resolve to HLS IDs where available. _Refs: WP4, audit §2.2._
- [ ] **AH-33** · P1 · Embedding + reranker linking: embed mentions and hub/Wikidata/HLS candidates (`qwen3-embedding-0.6b`), rerank (`jina-reranker-v2`), fall back to the LLM only for ambiguity. _Acceptance:_ linking precision measured on a sample; fewer LLM calls. _Refs: audit §6._

## Epic 5 — Knowledge Hub → Knowledge Graph (WP4)

- [ ] **AH-40** · P0 · Replace the in-memory hub with a **persistent** store (JSON files, seeded with the domain defaults: document types, controlled vocabulary, persons/places/orgs) behind a stable interface (`get/find/add/link`). _Acceptance:_ hub survives restart; defaults present; Agent B/C consume it. _Refs: audit §1.3, §2.2._
- [ ] **AH-41** · P1 · Add an **RDF / SDHss (CIDOC-CRM) export** (`rdflib`) of entities + links. _Acceptance:_ `outputs/<id>.ttl` validates; LOD-ready. _Refs: WP4, PDF 2 §8._
- [ ] **AH-42** · P2 · Design the interface so the JSON store can be swapped for a **QLEVER** triple-store backend (WP4 target) without touching agents. _Acceptance:_ documented hub interface + a stub SPARQL backend. _Refs: WP4._

## Epic 6 — Agent D: Corpus analysis (WP5)

- [ ] **AH-50** · P1 · Stop "LLM counting" over a 5 000-char truncation. Count taxonomy/care terms **deterministically** against the hub vocabulary across the full corpus; use the LLM only to label clusters. _Acceptance:_ counts are exact and corpus-wide. _Refs: audit §2.5._
- [ ] **AH-51** · P2 · Embedding-based semantic retrieval over the corpus for topic/care clustering. _Acceptance:_ clusters reproducible; labels via LLM. _Refs: audit §6._
- [ ] **AH-52** · P2 · Verify the Voyant export endpoint + URL generation against a real corpus. _Acceptance:_ `/corpus` returns a working Voyant link.

## Epic 7 — Agent E: Meta / evaluation (WP6)

- [ ] **AH-60** · P2 · Track GPU-time / token usage for the **local** stack (drop the USD cost model from PDF 2, which assumes a paid API). _Acceptance:_ meta report shows per-agent token + wall-clock, no fake USD. _Refs: audit §2 (Agent E)._

## Epic 8 — Orchestration & front end (WP1)

- [ ] **AH-70** · P1 · Expose each agent as a discrete, individually-callable tool with a stable signature, so an NL orchestration layer can be added without rewrites. _Acceptance:_ documented tool interface; `/agent x` maps 1:1 to it. _Refs: WP1, audit §2.6._
- [ ] **AH-71** · P2 · Prototype the **natural-language / Scholar-in-the-Loop** orchestrator (`minimax-m2.7`, OpenClaw-style) that plans which agents to run from a chat instruction. _Acceptance:_ NL request → correct agent sequence on a sample. _Refs: WP1, PDF 1 §2.3._
- [ ] **AH-72** · P2 · Keep `bot.py` a thin shell; keep all logic in `orchestrator`/`agents` so the year-3 **Ad fontes web** front end is a second UI over the same core. _Refs: WP1, PDF 1 fn. 3._

---

## Reference: why these tasks exist (audit summary)

The two repos diverged: `agentic-historian` (Gemini) was the cleaner, complete async implementation; `agentic_historian` (this repo, GPUStack) was the migration target but had duplicate agents, a broken in-memory hub, a synchronous pipeline behind an async bot, a fake QA heuristic, and a leaked key. `main` has since fixed several of these and added a strong two-pronged HTR design. The proposal's larger vision — Scholar-in-the-Loop NL orchestration (OpenClaw), an SDHss/QLEVER knowledge graph, HLS + Wikidata linking, Transkribus-grade HTR, and a future Ad fontes web UI — frames the remaining epics above. The backlog turns that gap into pickable units; the model substrate is the unibe GPUStack throughout (Claude/Gemini are not used).

### Confirmed GPUStack model routing (2026-06-26, on-network)
| Role | Model | Used by |
|---|---|---|
| Vision | `qwen3-vl-30b-a3b-instruct` | Agent A (HTR), Agent B (description) |
| Text/LLM | `gpt-oss-120b` (reasoning model) | Agent C (NER), D, E, reconciliation, care-flag |
| Orchestration | `minimax-m2.7` | reserved for NL/SitL orchestrator |
| Embedding | `qwen3-embedding-0.6b` / `granite-embedding-107m-multilingual` | linking, corpus search |
| Reranker | `jina-reranker-v2-base-multilingual` | entity-linking rerank |

⚠️ `gpt-oss-120b` is a **reasoning model**: it spends tokens on `reasoning_content` before writing `content`, so text calls need a generous `max_tokens` (the client enforces a floor + retries). Other served models: `qwen3-coder-30b-a3b-instruct`, `faster-whisper-large-v3`; `olmocr-2-7b` (purpose-built OCR) exists but is currently **Not Ready** — bringing it up would be the strongest local HTR backend (see AH-11/AH-12).
