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