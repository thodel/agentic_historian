# Agentic Historian — Progress

_Last updated: 2026-06-26T13:15:00+00:00_

```json
{
  "phase_0": {"status": "completed", "notes": "GitHub repo, .gitignore, .env.gpustack untracked."},
  "phase_1": {"status": "completed", "notes": "Scaffold, bot.py, requirements.txt, config.py, dirs."},
  "phase_2": {"status": "completed", "notes": "Knowledge hub (knowledge_hub/hub.py) — done."},
  "phase_3": {"status": "completed", "notes": "Two-pronged HTR: (1) VLM path via GPUStack + (2) kraken baseline/OCR + HuggingFace models. LLM reconciliation of both outputs. Model registry in agent_a/models.py."},
  "phase_4": {"status": "completed", "notes": "Source description (agents/source_description.py) — Ad Fontes UZH 16-element schema."},
  "phase_5": {"status": "completed", "notes": "Entity extraction (agents/entity_agent.py) — LLM NER with NER4all persona."},
  "phase_6": {"status": "completed", "notes": "Corpus analysis (agents/corpus_analysis.py) — done."},
  "phase_7": {"status": "completed", "notes": "Meta agent (agents/meta_agent.py) — done."},
  "phase_8": {"status": "completed", "notes": "Hot folder via /hotfolder command. Files in data/hot_folder/ processed via run_hot_folder(). Move-to-processed after success."},
  "phase_9": {"status": "pending", "notes": "Testing & tuning with real documents — pending."},
  "last_commit": "ba3af12",
  "last_activity": "2026-06-26T13:15:00+00:00"
}
```

## What Works

- `/status` — pipeline overview
- `/run_agent_a ` — HTR only (single VLM path)
- `/run ` — full A→B→C pipeline
- `/run --dual` — two-pronged HTR (VLM + kraken + reconciliation)
- `/hotfolder` — process all files in data/hot_folder/
- `/agent_d [corpus]` — corpus analysis
- `/agent_e` — meta agent report

## Two-Pronged HTR (Phase 3 extension)

New in this session — `agent_a/`:

```
agent_a/
  __init__.py        — public API: transcribe_dual()
  models.py          — VLM / kraken / HuggingFace model registry
  dual_pipeline.py   — orchestration of both paths
  kraken_ocr.py      — kraken CLI wrapper (segment + OCR)
  reconcile.py       — LLM-based comparison of VLM vs kraken outputs
```

**Pathway 1 (VLM):** InternVL3-8B via GPUStack, prompt optionally enriched with Agent B source description.

**Pathway 2 (kraken + HF):** kraken baseline detection + OCR with community models; optionally HuggingFace end-to-end or line-level OCR models (e.g. LightOnOCR).

**Reconciliation:** LLM-based (difflib fallback). Result carries `agreement_score`, `method_used`, and per-path error messages.

To enable kraken: `pip install kraken && kraken get <model-id>`

Model lists for kraken and HuggingFace still need to be provided by Tobias.

## What's Left

- Discord bot token (`.env` placeholder)
- Bot hosting/startup
- kraken/HF model list from Tobias
- Phase 9 — real-doc testing