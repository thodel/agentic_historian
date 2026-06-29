# Agentic Historian

Autonomous pipeline for transcribing, describing, and analysing historical handwritten documents (14th–16th century, Swiss/German administrative sources).

## POC

This Discord server (#allgemein, channel `1519707390956798034`) is the live proof-of-concept environment. Progress reports run hourly here.

## Architecture

```
agent_a/          — HTR pipeline (VLM OCR + QA scoring)
agent_b/          — Source description (Ad Fontes UZH 16-element schema)
agent_c/          — Entity extraction (LLM NER + Wikidata/GND linking)
agent_d/          — Corpus analysis (stats, topics, Voyant Tools)
agent_e/          — Meta agent (resource tracking, error log, suggestions)
knowledge_hub/    — In-memory store for persons, places, vocabulary
orchestrator.py   — A→B→C pipeline wiring
bot.py            — Discord bot (slash commands)
reporter.py       — Progress tracking + report generation
```

## Prompt Framework

`agents/source_heuristic.py` contains the Ad Fontes (UZH) codicological prompt framework — 16 description elements with archival context and observation questions derived from the [Ad Fontes tutorial](https://www.adfontes.uzh.ch/tutorium/handschriften-beschreiben).

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in your values
python bot.py
```

## Discord Commands

| Command | Description |
|---|---|
| `/status` | Overall pipeline status |
| `/run ` | Run full A→B→C pipeline |
| `/run_agent_a ` | Run HTR only |
| `/hotfolder` | Process all files in hot folder |
| `/agent_d [corpus]` | Run corpus analysis |
| `/agent_e` | Run meta agent |
| `/progress` | Show phase progress |

## Environment Variables

| Variable | Description |
|---|---|
| `DISCORD_BOT_TOKEN` | Discord bot token |
| `GPUSTACK_URL` | GPUStack endpoint |
| `GPUSTACK_API_KEY` | GPUStack API key |
| `VLM_MODEL` | VLM model name (default: internvl3-8b-instruct) |

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
- Add an **offline test** (mock `gpustack_client`) for new agent logic so the suite runs without the VPN. Run from the package dir: `python tests/<test>.py` or `pytest`.

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

## Phases

- Phase 0 — GitHub setup & exec approvals ✅
- Phase 1 — Scaffold & Discord bot ✅
- Phase 2 — Knowledge hub ✅
- Phase 3 — OCR (HTR) pipeline ✅
- Phase 4 — Source description ✅
- Phase 5 — Entity extraction (NER) ✅
- Phase 6 — Corpus analysis ✅
- Phase 7 — Meta agent ✅
- Phase 8 — Hot folder integration 🔄
- Phase 9 — Testing & tuning ⬜