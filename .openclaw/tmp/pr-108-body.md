## Summary

Issue #108 ([P1] HF OCR path uses AutoModelForCTC but TrOCR models are seq2seq) was already fixed in `dual_pipeline.py:_run_hf_ocr()` — it uses `AutoModelForVision2Seq.from_pretrained()` + `.generate()` (seq2seq decode), not `AutoModelForCTC`.

This PR adds offline tests that **prove** the acceptance criteria:

### Tests added (`test_ah_108_hf_ocr_trocr_seq2seq.py`)

**`TestAcceptance`** (2 tests):
- `test_vision2seq_is_called_not_ctc` — patches the `transformers` mock to track which class is actually instantiated; asserts `AutoModelForVision2Seq` was called AND `AutoModelForCTC` was NOT called
- `test_generate_is_called_on_model` — asserts `model.generate()` is invoked (seq2seq decode path), not a CTC forward pass

**`TestErrorPaths`** (4 tests):
- No HF model configured → error string with "No HF model"
- `ImportError` (missing `transformers`) → "Missing dependency"
- `OSError` (model not found) → error string propagates
- Lang without HF model → "No HF model"

**`TestRequiresLineImagesEarlyReturn`** (1 test):
- Verifies models with `requires_line_images=True` return early with the correct message

**`TestEndToEnd`** (1 test):
- Full happy path: returns transcription text matching `batch_decode` output + correct model ID

### Key mocking details
- `torch.no_grad` is a proper context manager (not a MagicMock — previous attempts failed because `with torch.no_grad():` silently swallowed errors when `__exit__` returned `False`)
- `model.to(device)` returns `self` (chainable), so `model_hf` remains valid after `.to(device)`
- `processor(images=...).to(device)` returns a `_Inputs(dict)` subclass so `**inputs` unpacking in `model_hf.generate(**inputs, ...)` works correctly

Closes #108