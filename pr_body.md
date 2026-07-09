## Issue
Closes #234 — Multi-engine fan-out with unified model selection, concurrent execution, all candidates persisted for CER evaluation.

## What was built

### Architecture
- **RecognitionResult** (Pydantic BaseModel): engine / model_id / text / confidence / error / timing_ms / segmented_by — stored in `PipelineContext.recognitions` and serialised into `pipeline.json`
- **select_best(engine, criteria)**: unified factory that dispatches to `select_kraken_model` / `select_tocr_model` / `select_party_model`
- **Concurrent fan-out**: `transcribe_dual` uses `ThreadPoolExecutor(max_workers=4)` — all engines run in parallel, not sequentially
- **Phase 3**: replaced inline `_rerun_kraken_with_model_selection` with `transcribe_dual` so kraken+party also benefit from the thread pool

### Changes
| File | Change |
|---|---|
| `model_selector.py` | `RecognitionResult` dataclass → Pydantic `BaseModel`; `select_tocr_model`, `select_party_model`, `select_best` factory |
| `dual_pipeline.py` | `ThreadPoolExecutor` fan-out; `dual.recognitions` list; back-populates individual result fields for backward compat |
| `orchestrator.py` | `ctx.recognitions` wired from `dual.recognitions`; Phase 3 uses `transcribe_dual`; RunState sync + `pipeline.json` serialisation |
| `runstate.py` | artifacts now carry `recognitions` |

## Tests
- 557 → **559 passed** (+2 new RunState recognitions round-trip tests)
- Full suite: `agentic_historian/.venv/bin/python -m pytest agentic_historian/tests/`