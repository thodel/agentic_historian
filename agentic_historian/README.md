# Agentic Historian

Autonomous pipeline for transcribing, describing, and analysing historical handwritten documents (14th–16th century, Swiss/German administrative sources).

## POC

This Discord server (#allgemein, channel `1519707390956798034`) is the live proof-of-concept environment. Progress reports run hourly here.

## Architecture

```
agents/
  text_recognition.py   — Agent A: HTR (kraken-first when available, VLM fallback)
  source_description.py — Agent B: source description (Ad Fontes 16-element; JSON+MD; human pins)
  entity_agent.py       — Agent C: entity extraction (NER) + MCP-federation / hub linking
  corpus_analysis.py    — Agent D: corpus stats, topics, taxonomy, care, Voyant
  meta_agent.py         — Agent E: resource tracking / report
  search_agent.py       — federated person search (parallel MCP query + resolve + rank)
  source_heuristic.py   — Ad Fontes (UZH) codicological prompt framework (16 elements)
agent_a/          — two-pronged HTR (VLM + kraken/TrOCR via the serving-atr-inference gateway)
knowledge_hub/
  mcp_registry.py       — declarative registry of the KH MCP sources (see docs/knowledge_hub.md)
  hub.py                — controlled vocabulary + thin cache (authority data via MCP federation)
  rdf_export.py         — CIDOC-CRM RDF/Turtle export (toward the QLEVER triple-store, WP4)
utils/
  gpustack_client.py    — single GPUStack (OpenAI-compatible) client
  mcp_client.py         — async client over the KH MCP federation (PersonResult contract)
  entity_resolver.py    — cross-source entity resolver/merger
  switchdrive.py        — WebDAV ingestion from SwitchDrive
  metrics.py            — per-run telemetry (Agent E)
orchestrator.py   — A→B→(kraken re-run)→C(→D) pipeline wiring (single doc + grouped "order")
runstate.py       — per-document run state + stage-invalidation state machine (HITL)
routing_card.py   — HITL Gate-1 routing card (metadata selects → re-route HTR)
path_compare.py   — HITL Gate-2 path-comparison card (measured CER)
bot.py            — Discord bot (py-cord slash commands)
config.py         — central config + role-based GPUStack routing
docs/knowledge_hub.md — MCP-federation methodology + how to add a source
```

All models run on the **unibe GPUStack** (`gpustack.unibe.ch`, OpenAI-compatible): vision `qwen3-vl-30b-a3b-instruct` (A/B), text `gpt-oss-120b` (C/D/E), `minimax-m2.7` reserved for orchestration. HTR's kraken/TrOCR path is served by the companion **serving-atr-inference** gateway (`ATR_GATEWAY_URL`, `X-API-Key`). Knowledge-hub authority data (persons/places — HLS, HBLS, KF, EOS, plus GND/Wikidata) is federated over **MCP**; the registry lives in `knowledge_hub/mcp_registry.py` and the methodology in `docs/knowledge_hub.md`. See `IMPLEMENTATION_PLAN.md` and `AGENTIC_HITL_PLAN.md`.

## Prompt Framework

`agents/source_heuristic.py` contains the Ad Fontes (UZH) codicological prompt framework — 16 description elements with archival context and observation questions derived from the [Ad Fontes tutorial](https://www.adfontes.uzh.ch/tutorium/handschriften-beschreiben).

## Quick Start

Requires Python 3.11+, on the unibe VPN (GPUStack is IP-gated).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../workspace/gpustack.env.example ../.env.gpustack   # then fill in the rotated key + Discord token
python bot.py            # or: python -m agentic_historian  (entry point, see pyproject.toml)
```
`config.py` loads `.env.gpustack` from the **repo root** (real process env always wins; dotenv never overrides it). In production the bot runs under systemd (`agentic-historian.service`).

## Discord Commands

| Command | Description |
|---|---|
| `/run <file>` | Full A→B→C pipeline on a file in the hot folder |
| `/run_agent_a <file>` | HTR only |
| `/hotfolder` | Process all files in the hot folder |
| `/pull [folder] [recursive]` | Pull images from a SwitchDrive folder and process each |
| `/pull_folder [folder] [reprocess]` | Process each SwitchDrive subfolder as one multi-page document |
| `/agent_d [corpus]` | Corpus analysis |
| `/agent_e` | Meta report |
| `/search <name>` | Federated person search across the KH MCP sources (HLS/HBLS/KF/EOS) |
| `/route <doc_id>` | HITL Gate-1 routing card — correct inferred metadata, re-route HTR |
| `/status`, `/progress` | Status |

Sensitive commands (`/run`, `/run_agent_a`, `/pull`, `/pull_folder`) are role-gated when `REQUIRED_DISCORD_ROLE_ID` is set. All commands are serialised through a single worker queue (responsive, no per-user races).

## Environment Variables (`.env.gpustack`, repo root)

| Variable | Description |
|---|---|
| `DISCORD_BOT_TOKEN` | Discord bot token |
| `REQUIRED_DISCORD_ROLE_ID` | Numeric role id allowed to run sensitive commands (empty = open) |
| `GPUSTACK_API_KEY` | GPUStack API key (rotate if leaked; gitignored) |
| `GPUSTACK_BASE_URL` | GPUStack endpoint (default `https://gpustack.unibe.ch/v1`) |
| `GPUSTACK_MODEL_VISION` / `_TEXT` / `_ORCHESTRATOR` | Role-based model routing |
| `GPUSTACK_MODEL_EMBEDDING` / `_RERANKER` | Retrieval models for Agent C linking |
| `GPUSTACK_TEXT_MAX_TOKENS` | Token budget floor for the gpt-oss reasoning model |
| `ATR_GATEWAY_URL` | serving-atr-inference gateway (kraken/TrOCR); falls back to legacy `KRAKEN_SERVICE_URL` |
| `ATR_API_KEY` | `X-API-Key` for the ATR gateway |
| `MCP_BASE_URL` / `MCP_TIMEOUT` | Knowledge-hub MCP federation base + per-request timeout |
| `ENABLE_MCP_LINKING` | Agent C links persons via the MCP federation (falls back to the local hub) |
| `SWITCHDRIVE_URL` / `_USER` / `_PASS` / `_REMOTE_DIR` | SwitchDrive WebDAV ingestion (app password) |
| `VOYANT_API_URL` | Self-hosted Voyant instance (Agent D) |
| `ENABLE_HLS_LOOKUP` / `HLS_DATA_PATH` | Offline HLS fallback (primary path is the HLS MCP) |

See `workspace/gpustack.env.example` for the full template.

## Contributing — PR & issue rules

This repo has multiple contributors (human and agents) working in parallel. These
rules exist because we have hit each of these failure modes — follow them.

### Pull requests
- **One focused change per PR.** Small and additive; don't refactor unrelated code.
- **Branch from the latest `main`** (`git fetch && git rebase origin/main`), and rebase again before opening if `main` moved.
- **Don't modify another epic's files** without coordinating — `agent_a/` (HTR) and the orchestrator are actively worked on.
- **Verify before opening:** the code imports/compiles, the relevant test passes, and every import is declared in `requirements.txt`. Exercise runtime/bot changes (LLM calls need the VPN).
- **Title:** imperative summary. **Body:** what changed, why, and how you verified it.
- **Link the issue with `Closes #N`** so it closes automatically on merge.
- **Never commit secrets.** `.env.gpustack` is gitignored; only `gpustack.env.example` (placeholders) is tracked.

### Solving / closing issues
- **An issue is "done" only when its fix is merged to `origin/main`** — not when a commit exists in a local branch, worktree, or sandbox.
- **Close issues through the PR** (`Closes #N`). Do **not** hand-close as "completed" before merge.
- **If you cite a commit, it must be reachable on `origin`.** Verify with `git cat-file -t <sha>` and `git branch -a --contains <sha>`; a SHA that doesn't resolve on origin does not count as a fix. _(This is exactly how #18 was wrongly closed — a cited commit that was never pushed.)_
- **Search before opening** to avoid duplicates; reference the backlog task ID (`AH-NN`) and link related issues.
- **Don't close another contributor's/agent's issue** as done without confirming the artifact is on `main`.

### Models & infrastructure
- **GPUStack only** (`gpustack.unibe.ch`) — no Claude/Gemini. Routing is role-based in `config.py`: vision `qwen3-vl-30b-a3b-instruct`, text `gpt-oss-120b`, orchestration `minimax-m2.7`.
- **`gpt-oss-120b` is a reasoning model** — it spends tokens on reasoning before emitting `content`; give text calls a generous `max_tokens` (the client enforces a floor + retry).
- **The endpoint is VPN-gated** — live LLM calls need the unibe VPN (off-VPN returns `403`).

### Tests
- Add an **offline test** (mock `gpustack_client`, the kraken client, or the MCP transport) for new logic so the suite runs without the VPN. Run **from the repo root**: `pytest agentic_historian/tests/`. GitHub Actions runs the import smoke + full suite on every PR — **CI must be green before merge**.

## Voyant Tools — Integration

Voyant Tools is available at **https://tei.dh.unibe.ch/voyant/**.

### Infrastructure

- **Server:** Voyant runs on `tei.dh.unibe.ch` via Jetty (`jetty-runner.jar`)
  - Port 8888: Jetty 9.4 (VoyantServer 2.6.21) — **production**
  - Port 8080: Jetty (VoyantServer 2.4-M45) — legacy/backup
- **nginx proxy chain:** `/:8889` → `localhost:8888` (Jetty); `/voyant/` → `localhost:8889` (nginx → Jetty)
- **Systemd:** managed by `/etc/systemd/system/voyant.service`
- **Restart:** `sudo systemctl restart voyant`
- **Log:** `/home/dh/voyant/voyant-2.6.log`
- **Voyant 2.6 app root:** `/opt/voyant/voyant-2.6/VoyantServer2_6_21/`
- **Legacy (2.4):** `/home/dh/voyant/VoyantServer2_4-M45/`

### How Voyant Receives Text

Voyant accepts plain-text documents and produces interactive analysis views (word frequency, KWIC, trends, etc.).

#### 1. Direct text via URL parameter

```
https://tei.dh.unibe.ch/voyant/?text=your+plain+text+here
```

For longer texts, POST the content as form data:

```bash
curl -X POST 'https://tei.dh.unibe.ch/voyant/?text=' \
  -d 'text=Erstes Beispiel. Zweites Beispiel. Drittes Beispiel.'
```

#### 2. Upload a plain-text file via multipart form

```bash
curl -X POST 'https://tei.dh.unibe.ch/voyant/?upload=1' \
  -F 'file=@/path/to/document.txt'
```

Or via the web UI at **https://tei.dh.unibe.ch/voyant/?upload=1**.

#### 3. Load text from a URL

```
https://tei.dh.unibe.ch/voyant/?input=https://example.com/document.txt
```

#### 4. Programmatic access via Spyral.js API

For embedding Voyant tools in notebooks or automated pipelines:

```javascript
// Load a text or corpus
const corpus = new Spyral.Load('https://tei.dh.unibe.ch/voyant/?text=your text here');

// Get word frequencies
const counts = corpus.getTermCounts({ limit: 100 });

// Create a KWIC view
new Spyral.KWIC().corpus(corpus).window(5).show();
```

Full API: **https://tei.dh.unibe.ch/voyant/docs/** (navigate to *Spyral* module docs).

#### 5. Corpus by ID

If a corpus already exists on the server:

```
https://tei.dh.unibe.ch/voyant/?corpus=<corpusId>
```

### nginx reverse proxy

The proxy chain (`/voyant/` → nginx:8889 → Jetty:8888) requires several nginx settings to work correctly:

**Key nginx settings** (in `/etc/nginx/sites-available/tei.dh.unibe.ch`, location `/voyant/`):

```nginx
proxy_http_version 1.1;
proxy_buffering off;
proxy_set_header Accept-Encoding "";        # Disable gzip so sub_filter can rewrite
proxy_redirect / /voyant/;                  # Rewrite Location: / → /voyant/ in 301 responses
proxy_pass http://127.0.0.1:8889/;

sub_filter_once off;
sub_filter_types application/javascript;     # text/html is default; just add js here
sub_filter 'href="/' 'href="/voyant/';
sub_filter 'src="/' 'src="/voyant/';
sub_filter 'url('/" 'url('/voyant/';
sub_filter 'url("/' 'url("/voyant/';
sub_filter 'window.location.replace("/' 'window.location.replace("/voyant/';
```

**Key insight:** When `sub_filter_types` is explicitly set, nginx overrides the default (`text/html` only) with the listed types — must include both `text/html` and `application/javascript` explicitly, or just omit `text/html` since it's the default.

**`/resources/` must be proxied separately** — requests for `/resources/...` don't go through `/voyant/` so they need their own location block:

```nginx
location /resources/ {
    proxy_pass http://127.0.0.1:8889/resources/;
    proxy_set_header Accept-Encoding "";
}
```

This means all navigation links, script tags, and stylesheet references work under the sub-directory — no changes to Voyant's internal `uri_path` setting are needed.

### Adding Voyant analysis to agent_d

agent_d can integrate Voyant by passing transcriptions to the server. Example flow:

```python
import requests

def analyse_with_voyant(text: str, endpoint: str = "https://tei.dh.unibe.ch/voyant/"):
    """Upload text to Voyant and return the session URL."""
    resp = requests.post(
        endpoint,
        params={"text": text},
        headers={"Accept": "application/json"},
        timeout=30
    )
    # Voyant redirects to /?corpus=<id> on success
    return resp.url
```

**Important:** The Voyant endpoint is internal-only (`localhost:8888` proxy) — always route through `https://tei.dh.unibe.ch/voyant/`.

## Models

Model registry lives in the sibling repo **serving-atr-inference**:

**`serving-atr-inference/config/models.yaml`** — authoritative ATR model registry, exposed by the gateway at `GET /models`.

`agent_a/models.py` holds a static fallback table; at startup `refresh_kraken_registry()`
fetches the live registry from the gateway (`GET /models`) so `model_selector.py`
routes language/script/century → model against what's actually served (no drift).

### TrOCR line-level models (currently deployed)

| Model ID | HF repo | Languages | Centuries |
|---|---|---|---|
| `trocr-medieval-escriptmask` | `dh-unibe/trocr-medieval-escriptmask` | de, fr, la, nl | 13–16 |
| `trocr-kurrent-xvi-xvii` | `dh-unibe/trozco-kurrent-XVI-XVII` | de | 16–17 |
| `trocr-essoins-middle-latin` | `dh-unibe/trozco-essoins-middle-latin` | la | 13–15 |

All three are **vision-encoder-decoder seq2seq** models served by the trocr engine
(GPU1, port :8202). They require pre-segmented **line images** — page-level input must
be segmented first (e.g. via kraken `blla`).

### Other engines

- **VLM (vLLM)** — GPU0/1, e.g. InternVL3-8B, Qwen3-VL — page-level, no prior seg needed
- **Kraken** — GPU1, various Zenodo models — page-level (segments internally)
- **Party/PARY** — GPU1, Zenodo `10.5281/zenodo.20642057` — page-level HTR

## Status

The core pipeline is operational (VLM-first) with CI on every PR. See
`IMPLEMENTATION_PLAN.md` for the detailed roadmap and `AGENTIC_HITL_PLAN.md`
for the interactive-feedback work.

- Scaffold + Discord bot, Agents A–E, hot-folder/SwitchDrive ingestion ✅
- **Knowledge Hub — MCP federation ✅** (registry + methodology; client, resolver, federated `/search`, Agent C linking)
- Code-review remediation ✅ (external review 2026-07; last item: reconciliation tuning #17)
- **serving-atr-inference gateway** 🔄 (auth + live registry done; TrOCR-via-gateway + reconciliation remaining)
- **Agentic HITL — clickable routing cards + metadata-driven re-runs** 🔄 (Gate 1/2 + RunState done; Gate 3, persistence, gating remaining)
- kraken activation, Voyant, NL/Scholar-in-the-Loop orchestrator ⬜

VLM-first is the current operational baseline; kraken/TrOCR is the enhancement track.