# Agentic Historian — Progress

_Last updated: 2026-06-26T10:50:00+00:00_

```json
{
  "phase_0": {"status": "completed", "notes": "GitHub repo, .gitignore, .env.gpustack untracked."},
  "phase_1": {"status": "completed", "notes": "Scaffold, bot.py, requirements.txt, config.py, dirs."},
  "phase_2": {"status": "completed", "notes": "Knowledge hub (knowledge_hub/hub.py) — done."},
  "phase_3": {"status": "completed", "notes": "OCR pipeline (agents/text_recognition.py) — VLM HTR with QA retry."},
  "phase_4": {"status": "completed", "notes": "Source description (agents/source_description.py) — Ad Fontes UZH 16-element schema."},
  "phase_5": {"status": "completed", "notes": "Entity extraction (agents/entity_agent.py) — LLM NER with NER4all persona."},
  "phase_6": {"status": "completed", "notes": "Corpus analysis (agents/corpus_analysis.py) — done."},
  "phase_7": {"status": "completed", "notes": "Meta agent (agents/meta_agent.py) — done."},
  "phase_8": {"status": "completed", "notes": "Hot folder via /hotfolder command. Files in data/hot_folder/ processed via run_hot_folder(). Move-to-processed after success."},
  "phase_9": {"status": "pending", "notes": "Testing & tuning with real documents — pending."},
  "last_commit": "0894c7e",
  "last_activity": "2026-06-26T10:50:00+00:00"
}
```

## What Works

- `/status` — pipeline overview
- `/run_agent_a ` — HTR only
- `/run ` — full A→B→C pipeline
- `/hotfolder` — process all files in data/hot_folder/
- `/agent_d [corpus]` — corpus analysis
- `/agent_e` — meta agent report

## What's Left

- Discord bot token (`.env` placeholder)
- Bot hosting/startup
- Phase 9 — real-doc testing