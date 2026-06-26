# Agentic Historian — Consolidation & Critical Feedback

_Audit of `agentic_historian` (GPUStack WIP) and `agentic-historian` (Gemini, complete), measured against the project proposal (PDF 1) and the technical implementation description (PDF 2). Target: one consolidated codebase in `agentic_historian` running on the unibe GPUStack._

> **Status note (2026-06-26).** Several items below have since been addressed on `main` and are kept here for the record:
> - 🔴 Leaked API key — **key rotated**; `.env.gpustack` untracked. (History rewrite is now optional cleanup, not urgent.)
> - Duplicate `ocr_pipeline.py` / `agent_c/` modules — largely removed on `main` (this PR removes the last dead root `ocr_pipeline.py`).
> - HTR via a single general VLM (§2.1) — `main` now ships a **two-pronged `agent_a/`** (VLM + kraken + HF + LLM reconciliation), the right direction.
>
> **This PR** delivers the GPUStack hardening still missing: correct env loading, role-based model routing, and a reasoning-model-safe client (required for `gpt-oss-120b`). See §6 for the confirmed routing.

---

## 0. TL;DR verdict

- **`agentic-historian` (hyphen) is the better-engineered codebase**: fully async, consistent LLM client with retry/backoff, real two-call QA loop, persistent JSON hub with rich domain defaults, Wikidata linking, clean dataclass results. It just happens to be wired to **Gemini 3.1-pro**, not Claude (so PDF 2's "Claude everywhere" is already abandoned — fine, you want GPUStack anyway).
- **`agentic_historian` (underscore, the target) is currently a half-migrated tangle**: two parallel and mutually incompatible implementations of every agent, three different ways of calling GPUStack with three different env-var schemes, a fully synchronous pipeline driven by an async Discord bot, a broken in-memory knowledge hub, a fake QA heuristic, and a **live API key committed to git**.
- **Recommendation: do not keep growing the underscore repo's structure. Port the hyphen repo's clean architecture into `agentic_historian`, swap the LLM layer Gemini→GPUStack, and graft on the two things the underscore repo genuinely does better** (the Ad Fontes `source_heuristic` framework and the `reporter`). Delete the rest.

---

## 1. Critical / blocking issues (fix before anything else)

### 1.1 🔴 Live GPUStack API key committed to git
`.env.gpustack` is tracked (`git ls-files` lists it) and contains a real key:
```
GPUSTACK_API_KEY=gpustack_8927f7921abf8020_...
```
`.gitignore` only started ignoring it in commit `2a088e3` — **it is already in history** (`8be87ff`/`718d74f` era). The endpoint is IP-gated (returns 403 off-network), which limits but does not remove the exposure.
**Action: rotate the key now, then scrub history (`git filter-repo`/BFG). Never commit `.env.gpustack`; ship only `gpustack.env.example` with placeholders.**

### 1.2 🔴 Two parallel, incompatible agent implementations
The target repo contains **both**:
- `agents/text_recognition.py`, `agents/entity_agent.py`, `agents/source_description.py`, `agents/corpus_analysis.py`, `agents/meta_agent.py` (used by `orchestrator.py`, use `utils/gpustack_client`, loguru), **and**
- `agent_a/ocr_pipeline.py`, `agent_c/entity_extraction.py` (standalone, raw `requests`, `VLM_*` env vars, `get_hub()`), plus a third copy in the repo **root** `ocr_pipeline.py`.

They have different function signatures, different model selection, and different hub APIs. This is pure confusion and dead code. **Pick `agents/`, delete `agent_a/`, `agent_c/`, and root `ocr_pipeline.py`.**

### 1.3 🔴 Broken knowledge-hub API contract
- `agents/entity_agent.py` calls `hub.search_person(text)` / `hub.search_place(text)` — **these do not exist** on `knowledge_hub/hub.py` (which only offers `find_person`/`find_place` on an in-memory `KnowledgeHub`). Agent C will throw `AttributeError` at runtime.
- `agent_c/entity_extraction.py` instead calls `hub.find_person(...).wikidata_id` (object attributes) — a different, incompatible contract again.
- The target hub is **in-memory only**: no persistence, no defaults, no controlled vocabulary, no document types. Every restart loses all curated data. This directly contradicts the proposal's premise that historians *populate and steer* the hub.
The hyphen repo's `knowledge_hub/hub.py` is the correct one: JSON-persisted, seeded with real `document_types`, `controlled_vocabulary` (arme lüt, erbar lüt, Vogt, care terms…), `persons`/`places`/`organisations`, with `find_*` dict lookups.

### 1.4 🔴 Synchronous pipeline inside an async Discord bot
`orchestrator.py` and all `agents/` functions are synchronous and call `requests.post(..., timeout=120)`. The Discord command handlers `await ctx.defer()` then call `run_full_pipeline(fp)` **synchronously**, blocking the event loop for the entire multi-minute, multi-call pipeline → heartbeat timeouts, the bot stops responding to everything else. The hyphen repo solved this already (everything is `async`/`await`, blocking work in `asyncio.to_thread`). Keep async.

### 1.5 🔴 Discord library mismatch
`requirements.txt` pins `discord.py>=2.3.2`, but `bot.py` uses `from discord import Option` and `@bot.slash_command(...)` — that is the **py-cord** API, not discord.py. As written the bot will not import. Decide on one library (py-cord *or* discord.py app-commands) and pin it.

### 1.6 🟠 Config / env-var schema is internally inconsistent
- `config.py` reads `GPUSTACK_MODEL_TEXT` (default `minimax-m2.7`) and `GPUSTACK_MODEL_VISION` (default `internvl3-8b-instruct`).
- `.env.gpustack` only sets `GPUSTACK_MODEL` (single var) → **the text-model var is never set**, so every text agent silently falls back to `minimax-m2.7`, which may not even be served by the unibe stack.
- `agent_a/`, `agent_c/` read a *third* scheme: `VLM_ENDPOINT` / `VLM_API_KEY` / `VLM_MODEL`.

Unify on **one** scheme (`GPUSTACK_BASE_URL`, `GPUSTACK_API_KEY`, `GPUSTACK_MODEL_TEXT`, `GPUSTACK_MODEL_VISION`) and **verify the actual served model IDs on-network** (`GET /v1/models`) — the endpoint is 403 from outside, so this must be checked from inside the VPN before the defaults can be trusted.

---

## 2. Substantive critique vs. the proposal (the parts that matter scientifically)

### 2.1 HTR with a general VLM is the weakest link — and the QA is circular
Agent A uses **InternVL3-8B-Instruct** to transcribe 14th–16th c. gothic cursive. A general-purpose VLM has effectively no exposure to medieval German/Latin Kurrent; expect high character-error rates, silent hallucination of plausible-but-wrong words, and instability run-to-run. The proposal (PDF 1, §2.2 "Digitale Vorarbeiten von Hodel", WP2) is explicitly built on **Transkribus-grade, generalised HTR models** and Hodel's own ATR research — that expertise is the whole point and is currently bypassed.

Worse, the "quality assurance" is **the same model scoring its own output** (`_qa()` shows the model its transcription + image and asks for a number 0.0–1.0). That is not QA; it is a confidence theater that correlates with verbosity, not accuracy. The retry loop then re-prompts the *same* model and can loop to `MAX_RETRIES` without improving anything. The underscore `agent_a/ocr_pipeline.py` is even worse: `score = len(text)/(len(prompt)*10)` — a pure length heuristic.

**What to do:**
- Keep Agent A as a **pluggable backend** (it's already conceptually a wrapper — PDF 2 §8 says so). Backend 1 = InternVL3 on GPUStack as a *baseline*. Backend 2 = **Transkribus API** (or a fine-tuned TrOCR/Kraken model you can host on GPUStack) for real medieval hands.
- Replace self-QA with a **small human-corrected ground-truth set** and report **CER/WER** as the actual quality metric. Use the LLM-QA only as a cheap triage flag, never as the acceptance gate.

### 2.2 Knowledge Hub ≠ the proposal's Knowledge Graph
PDF 1 WP4 is unambiguous: the hub should be an **RDF triple-store** (QLEVER), structured by the **SDHSS ontology** (Beretta), integrated with **LOD4HSS (SDHSS + WissKI + Logre)** and linked to **Wikidata + HLS (Historisches Lexikon der Schweiz)**. Both repos implement flat JSON lists (or in-memory objects). That's an acceptable *interim*, but:
- Design the hub behind an **interface** (`get_person`, `find_place`, `link_entity`…) so the JSON store can be swapped for a SPARQL/QLEVER backend without touching the agents.
- Implement the **CIDOC-CRM / SDHss RDF export** that PDF 2 §8 already lists as planned (an `rdflib` serializer) — it's the bridge to WP4 and cheap to add now.
- **HLS linking is entirely missing** in the GPUStack version (the Gemini Agent C at least has an `hls_id` slot). The proposal names HLS explicitly; add an HLS resolver alongside Wikidata.

### 2.3 Entity model is being silently downgraded
The proposal and PDF 2 define **8 entity types** (PERSON, PLACE, ORG, SOCIAL_GROUP, CARE_ACTOR, CARE_ACTION, ROLE, DATE) — the two "social taxonomy" and "care" types are the entire research payload (Teilprojekt 1 & 2). The underscore `agent_c/entity_extraction.py` drops to **6 types and omits SOCIAL_GROUP and CARE_ACTION** — i.e. it throws away exactly the categories the historians care about. The `agents/entity_agent.py` version keeps all 8 (good) but is the one with the broken hub call. Keep all 8 and wire it to the real hub.

### 2.4 Wrong model for the job in places
`agent_c/entity_extraction.py` runs **text NER through the vision model** (`internvl3-8b`). NER over a transcription is a pure-text task and should use the **text** model (`chat_text`). The `agents/` version does this correctly. (This is another reason to keep `agents/` and delete `agent_c/`.)

### 2.5 Corpus analysis (Agent D) doesn't actually scale
`_topics`, `_taxonomy`, `_care_analysis` each truncate the whole corpus to **5,000 characters** and ask the LLM to "count" occurrences. On a real corpus this both overflows nothing and counts nothing meaningful. Token-frequency `_stats` is real Python (good). For taxonomy/care frequency, **count deterministically against the controlled vocabulary** (the hub already holds the term list), and use the LLM only for *labelling/interpretation* over representative samples — not as a fake aggregator. Voyant export is a reasonable touch; keep it.

### 2.6 Orchestration is hardwired A→B→C, but the proposal's WP1 is natural-language SitL orchestration (OpenClaw)
PDF 1 §2.3/WP1 describes a **Scholar-in-the-Loop** orchestrator driven in **natural language** (OpenClaw, `docs.openclaw.ai`), where the researcher negotiates which steps run at which granularity. Both repos implement a fixed sequential pipeline behind slash commands. That's fine as the v1 substrate, **but flag it explicitly as a known gap**: the orchestrator should expose each agent as a discrete, individually-callable *tool* (it half-does via `/agent x`) so an NL orchestration layer can be added later without rewrites. Don't build OpenClaw now; do keep the seams.

### 2.7 Discord is correctly understood as throwaway — keep it thin
PDF 1 footnote 3 states Discord is the **experimental, non-sustainable** front end, to be replaced by an **Ad fontes (adfontes.uzh.ch) web interface** in year 3. Good news: nothing should be invested in Discord beyond a thin command shell. Keep all real logic in `orchestrator` + `agents` (callable without Discord), so the future Ad fontes web layer is a second front end over the same core. Avoid putting business logic in `bot.py`.

---

## 3. What to keep from each repo (consolidation map)

| Component | Source of truth | Why |
|---|---|---|
| Async architecture, orchestrator, `PipelineResult` dataclasses | **hyphen** (`agentic-historian`) | Non-blocking, clean, already handles page grouping, PDF→images, hot folder |
| LLM client | **new** — adapt hyphen's `utils/llm_client.py` shape onto the underscore repo's `utils/gpustack_client.py` (OpenAI-compatible) | Keep async `ask`/`ask_structured` interface, retry/backoff (tenacity), but point at GPUStack |
| Agent A (HTR) | **hyphen** structure + **GPUStack** vision call; keep backend-pluggable | Real 2-call QA *shape*, retry, page combine; swap Gemini→InternVL3, add Transkribus backend slot |
| Agent B (Source description) | **merge**: hyphen's async/dataclass wiring + **underscore's `source_heuristic.py`** (Ad Fontes 16-element framework) | `source_heuristic.py` is a genuine value-add and matches WP3; the underscore `source_description.py` wiring around it is fine to port |
| Agent C (Entities) | **hyphen** (keeps all 8 types, correct text model, Wikidata + GND + HLS slots, hub-first linking) | Underscore version drops SOCIAL_GROUP/CARE_ACTION and uses the wrong model |
| Agent D (Corpus) | **hyphen** wiring; fix the "5000-char LLM counting" per §2.5; keep Voyant | — |
| Agent E (Meta) | **hyphen**; recompute cost/token tracking for GPUStack pricing (or mark as local/free) | PDF 2 cost-in-USD assumes a paid API; on local GPUStack track GPU-time/tokens instead |
| Knowledge Hub | **hyphen** (JSON-persisted, seeded defaults) behind an interface for future QLEVER | Underscore's in-memory hub loses data and breaks Agent C |
| `reporter.py` + `PROGRESS.md` | **underscore** | Genuine value-add (phase tracking, hourly status); not in hyphen repo |
| Discord bot | **one** library; keep thin; prefer hyphen's structure | Underscore bot has the discord.py/py-cord mismatch |
| `docs/` web mockup | **hyphen** (`docs/index.html`…) | Early seed for the eventual Ad fontes front end |

---

## 4. Implementation plan (phased, concrete)

### Phase 0 — Stop the bleeding (½ day)
1. **Rotate** the GPUStack key; scrub it from git history; verify `.env.gpustack` is untracked.
2. From inside the unibe VPN, hit `GET /v1/models` and **record the exact served model IDs** (text + vision). These become the only valid defaults.
3. Freeze the decision: **base = the hyphen architecture, ported into `agentic_historian`.**

### Phase 1 — Unify the LLM layer (1 day)
4. Rewrite `utils/gpustack_client.py` to expose the hyphen client's **async** interface (`ask(system, user_text, image_path=…)`, `ask_structured(...)`) backed by the OpenAI-compatible GPUStack endpoint, with tenacity retry/backoff and a single env scheme (`GPUSTACK_BASE_URL`, `GPUSTACK_API_KEY`, `GPUSTACK_MODEL_TEXT`, `GPUSTACK_MODEL_VISION`).
5. Delete `agent_a/`, `agent_c/`, root `ocr_pipeline.py`, and the `VLM_*` env scheme. One client, one config.
6. Rewrite `config.py` cleanly (drop the stray non-ASCII/typos: `默认值`, `Prüft.Required`, the Chinese comment in the client). Add `config.check_config()` for required keys.

### Phase 2 — Port agents onto GPUStack (2–3 days)
7. Bring the hyphen `agents/` (A–E), `orchestrator.py`, `knowledge_hub/hub.py` into `agentic_historian`, swapping `llm_client` calls for `gpustack_client`.
8. **Agent A:** keep the async QA *shape* but (a) make the backend pluggable (`HTR_BACKEND=internvl|transkribus`), (b) demote LLM-self-QA to a triage flag, (c) add a CER/WER eval harness against a tiny ground-truth set.
9. **Agent B:** wire in `source_heuristic.py` (Ad Fontes 16-element) as the prompt source; keep the Care-Flag.
10. **Agent C:** all 8 entity types; text model for NER; hub-first → Wikidata → **add HLS resolver** → LLM disambiguation; emit JSON + Markdown.
11. **Agent D:** deterministic vocab/care counting from the hub; LLM only for topic *labels*; keep Voyant export.
12. **Agent E:** track GPU-time/tokens (not USD) for the local stack.

### Phase 3 — Front end + glue (1 day)
13. One Discord library, pinned; thin command shell over `orchestrator`; hot-folder watcher via `watchdog` running blocking work in `asyncio.to_thread`.
14. Keep `reporter.py`/`PROGRESS.md`; expose `/status`, `/progress`.

### Phase 4 — Bridge toward the proposal (ongoing, post-consolidation)
15. Put the hub behind an interface; add an **RDF/SDHss (CIDOC-CRM) export** (`rdflib`) as the first step toward the QLEVER triple-store of WP4.
16. Expose each agent as an individually-callable tool to leave room for the **NL/OpenClaw SitL orchestrator** (WP1) without a rewrite.
17. Keep `docs/` as the seed of the **Ad fontes web** front end (year-3 target).

### Definition of done for the consolidation (Phases 0–3)
- One agent implementation, one LLM client, one env scheme, one Discord lib.
- No secrets in the repo or its history.
- `python bot.py` boots; `/run <file>` executes A→B→C→(D) **without blocking** the event loop and produces transcription + description + entities + (corpus) outputs against the real GPUStack models.
- Knowledge hub persists across restarts and seeds the domain defaults.
- Agent C emits all 8 entity types; Agent A reports a real CER on the ground-truth set.

---

## 5. Open questions for the team
- ~~**Which models does the unibe GPUStack actually serve?**~~ **Resolved 2026-06-26** — see §6.
- **HTR strategy:** is Transkribus API access available within the project, or should Agent A's "real" backend be a self-hosted fine-tuned model on GPUStack? `olmocr-2-7b` (a purpose-built OCR model) exists on the stack but is currently **Not Ready** — getting it served would be the strongest local HTR option. Until then qwen3-vl-30b is a baseline, not a solution.
- **Knowledge graph timeline:** is QLEVER/SDHss/WissKI in scope for this consolidation, or strictly an interim JSON hub + RDF export now, triple-store later?
- **Discord library:** py-cord or discord.py app-commands? (Pin one and delete the other's idioms.)

---

## 6. Model routing — confirmed on-network (2026-06-26)

`GET /v1/models` returns **HTTP 200** inside the VPN; chat path smoke-tested OK. Ten models are **Ready** (two — `olmocr-2-7b`, `qwen2.5-coder-7b` — are "Not Ready" and not served).

| Role | Model | Used by | Notes |
|---|---|---|---|
| **Vision** | `qwen3-vl-30b-a3b-instruct` (64K) | Agent A (HTR), Agent B (description) | Best served VLM; alternatives `qwen3-vl-8b-instruct` (31K), `internvl3-8b-instruct` (64K) |
| **General LLM** | `gpt-oss-120b` (78K) | Agent C (NER), D, E, care-flag | ⚠️ **reasoning model** — see below |
| **Orchestration** | `minimax-m2.7` (98K) | future NL/SitL orchestrator (WP1) | reserved; not used by the fixed pipeline yet |
| **Embedding** | `qwen3-embedding-0.6b` / `granite-embedding-107m-multilingual` | hub linking, corpus semantic search | newly available — see below |
| **Reranker** | `jina-reranker-v2-base-multilingual` | entity linking / retrieval rerank | newly available |
| (Speech) | `faster-whisper-large-v3` | — | not relevant to this project |

Wired into `config.py` as role-based vars (`GPUSTACK_MODEL_VISION` / `_TEXT` / `_ORCHESTRATOR` / `_EMBEDDING` / `_RERANKER`) and `gpustack.env.example`.

### ⚠️ `gpt-oss-120b` is a reasoning model
It emits `reasoning_content` separately and consumes the token budget on reasoning **before** writing `content`. With `max_tokens:10`, `content` came back **null** (`finish_reason: length`); with `max_tokens:500` it returned clean `{"ok": true}` after 228 chars of reasoning. Therefore:
- Give every gpt-oss text call a **generous `max_tokens`** (≥1–2k for JSON tasks, more for D/E). The current agents cap NER at 2000 and care-flag at 600 — **too low**, will truncate before any answer.
- The client must read `message.content` (not `reasoning_content`) and handle the null-while-reasoning case.
- Do **not** rely on `temperature` alone to suppress reasoning.

### New opportunity: embeddings + reranker
The stack now serves embedding and reranker models. This directly fixes two weaknesses above:
- **Agent C linking** (§2.2): embed entity mentions + hub/Wikidata/HLS candidate labels, retrieve by cosine, rerank with `jina-reranker`, and only fall back to the LLM for genuine ambiguity — far cheaper and more reliable than LLM-only disambiguation.
- **Agent D** (§2.5): replace the "5000-char LLM counting" with embedding-based semantic retrieval over the full corpus (and deterministic vocab counts from the hub). The LLM labels clusters; it doesn't aggregate.

### Two integration bugs found while wiring this
- 🟠 **`config.py` never loads your env file.** It calls `load_dotenv(BASE_DIR / ".env")` where `BASE_DIR` is the inner package dir, but the secrets live in `.env.gpustack` at the **outer** repo root → only the in-code defaults ever apply, and `GPUSTACK_API_KEY` resolves empty. Fix the load path (and load `.env.gpustack`) as part of Phase 0.
- 🟠 `.env.gpustack` is still **git-tracked** and uses the dead single-var `GPUSTACK_MODEL`. Untrack + rotate (see §1.1) and migrate to the role-based vars.
