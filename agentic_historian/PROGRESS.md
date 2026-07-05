# Agentic Historian — Progress

_Last updated: 2026-07-05T00:00:00+00:00_

```json
{
  "phase_0": {"status": "completed", "notes": "GitHub repo, .gitignore, .env.gpustack untracked."},
  "phase_1": {"status": "completed", "notes": "Scaffold, bot.py, requirements.txt, config.py, dirs."},
  "phase_2": {"status": "completed", "notes": "Knowledge hub — MCP-federated (mcp_registry.py + docs/knowledge_hub.md)."},
  "phase_3": {"status": "completed", "notes": "HTR: VLM-first (operational) + kraken/TrOCR via serving-atr-inference gateway (enhancement). Registry synced from gateway /models."},
  "phase_4": {"status": "completed", "notes": "Source description (Ad Fontes 16-element) + human pins authoritative (quelle=historiker)."},
  "phase_5": {"status": "completed", "notes": "Entity extraction (NER) + MCP-federation linking (Agent C)."},
  "phase_6": {"status": "completed", "notes": "Corpus analysis + Voyant."},
  "phase_7": {"status": "completed", "notes": "Meta agent + per-run telemetry (utils/metrics.py)."},
  "phase_8": {"status": "completed", "notes": "Hot folder + SwitchDrive ingestion; success reporting fixed (#97)."},
  "phase_9": {"status": "completed", "notes": "Federated search (epic #129): mcp_client, resolver, /search, Agent C linking. CI on every PR."},
  "phase_10": {"status": "in_progress", "notes": "Agentic HITL (epic #142): RunState + Gate 1/2 cards done (#145-149); Gate 3, persistence, gating remaining."},
  "phase_11": {"status": "in_progress", "notes": "serving-atr-inference gateway (epic #143): auth + live registry done; TrOCR-via-gateway + reconciliation remaining."},
  "last_commit": "cf24e14",
  "last_activity": "2026-07-05T00:00:00+00:00"
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