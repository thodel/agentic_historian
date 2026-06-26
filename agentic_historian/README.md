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