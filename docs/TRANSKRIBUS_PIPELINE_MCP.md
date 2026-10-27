# A Transkribus volume over the ATR MCP

Stand 2026-10-23. Everything the Rümlang (A V 1446) and Hertwig (A V 1443) runs
did by hand on tei can be started from a Claude session through the ATR MCP
(`mcp_atr`, `https://tei.dh.unibe.ch/mcp/atr`). The tools run the scripts in
`agentic_historian/ruemlang/` as jobs, with fixed argv and checked arguments
(`mcp_atr/transkribus.py`). Four steps stay manual, each for a reason given
below.

**Prerequisite:** the code has to be on `main` and deployed on tei
(`update.sh`), because the MCP server runs what is deployed. Until then the
tools are not there, and the manual commands in [`HERTWIG_RUN.md`](HERTWIG_RUN.md)
apply.

## The steps

| # | Step | MCP | By hand (tei) |
|---|---|---|---|
| 0 | What a document holds: statuses, corrected (DONE) pages, the HTR a human started from, suggested test pages | `transkribus_pipeline(step="survey", run="doc-13922579", doc="13922579")` | `pipeline.py survey --doc …` |
| 1 | Pages without text | `transkribus_pipeline(step="plan", run="hertwig-run", doc="13922499")` | `pipeline.py plan --doc …` |
| 2 | Ground truth → training tree | `transkribus_gt_export(doc="13922499", pages="3-60", test_pages="8,17,26,39,48,55", tree="hertwig-gt")`, then the same with `dry_run=false` | `gt_export.py …` |
| 3 | Tree → private HF dataset | `upload_hf(tree="hertwig-gt", repo_id="dh-unibe/image-text_bern-disputation-1528", dry_run=false)` | — |
| 4 | Fine-tune kraken | `kraken_train()` (shows the request), then `kraken_train(action="submit", model_id="kraken-bern-hertwig-v2", lrate=5e-4, epochs=150)` | `train_kraken.py request` / `submit` |
| 5 | Follow the training | `kraken_train(action="status", model_id="kraken-bern-hertwig-v2")`, `kraken_train(action="list")`, `kraken_train(action="log", model_id=…, stage="train")` | `train_kraken.py status` |
| 6 | Model servable? | `gateway_models` → the id with `enabled: true`; `status` shows `promoted` | — |
| 7 | Measure on the held-out pages | `transkribus_pipeline(step="eval", run="hertwig", doc="13922499", pages="8,17,26,39,48,55", extra_model="kraken-bern-hertwig-v2")` | `pipeline.py eval …` |
| 8 | Trial without upload | `transkribus_pipeline(step="run", run="hertwig-run", doc="13922499", extra_model="kraken-bern-hertwig-v2", primary="D", only_extra=true, no_llm=true, limit=3)` | `ruemlang_full_run.sh` with `PAGES=… NO_UPLOAD=1` |
| 9 | Full run with upload | the same without `limit`, with `upload=true` | `ruemlang_full_run.sh start` |
| 10 | Progress | `transkribus_status(run="hertwig-run")`, `job_status`, `job_log`; stop with `stop_job` — every step resumes | `ruemlang_full_run.sh status` |

Steps 4, 7 and 9 change something outside tei: GPU time on asteraix, and a new
version on every page in Transkribus. That is why each of these tools defaults
to not doing it (`request`, no `upload`). An upload adds an IN_PROGRESS version
and never overwrites an earlier one.

**A volume whose corrected pages are the ground truth** (e.g. 13922579):
`transkribus_gt_export(status="DONE", test_pages=…)` exports every DONE page
except the test pages, and `eval` with `status="DONE", prior=true` adds reader
**T**. T is the version with text a human started from, as a rule Transkribus'
own HTR. Its toolName is printed. That puts the readers next to what Transkribus
gave the transcribers.

### Re-reading machine text on the page's own lines (A V 1447)

A volume whose uncorrected pages already carry an HTR reading (Schlussredaktion:
549 pages of Text Titan) is re-read with the volume's own model on **the lines
Transkribus drew**:

```
transkribus_pipeline(step="run", run="schlussredaktion-reread", doc="13922579",
                     extra_model="kraken-bern-schlussredaktion-v1", primary="D",
                     only_extra=true, no_llm=true,
                     replace_machine_text=true, on_page_lines=true, limit=3)
```

- `replace_machine_text` plans every page whose current text is HTR output
  nobody edited, plus pages without text. DONE/FINAL pages are left out, and so
  are pages someone edited after the HTR. Both are counted.
- `on_page_lines` sends the current version's lines (baseline, polygon,
  region) to the gateway. The kraken engine reads on exactly those
  (`segmented_by: given`), and the reading is written back into that PAGE XML:
  polygons, regions and reading order stay Transkribus' own. Pages without
  lines are segmented as before.
- The upload adds an IN_PROGRESS version on top. The HTR version stays in the
  history.

Needs serving-atr-inference with given-line support on idhefix (kraken engine
and gateway), and an `atr-client` that sends `lines`.

### Choosing the readers (step 7 decides)

`eval` prints the page CER, the **line CER** (each recognized line against the
transcribed line it lies on, so reading order does not count), the rules-only
fusion with A, B or D as primary (`det-X`), and how many transcribed lines the
segmentation lost. Hertwig, 6 pages:

| line CER | A qwen3vl | B TrOCR | D kraken v1 | **D kraken v2** | fusion gpt-oss | rules only, D |
|---|---|---|---|---|---|---|
| mean | 0.460 | 0.442 | 0.382 | **0.371** | 0.505 | 0.383 |

If D alone wins, the full run is `only_extra=true, primary="D", no_llm=true`.
That run needs no vLLM, so party can stay up, and it takes hours instead of a
day. If A, B or C stay in, party on idhefix must be stopped first; check with
`gateway_models` → `headroom`.

### External readers: Gemini and Argon (reader G, #747)

`eval` can add one or more models behind the external API (`GEMINI_BASE_URL`,
the key in `.env.gpustack`): `external_model="gemini-3.8-flash,gemini-4-argon"`
gives columns `G1`, `G2` (`G` for one). They read the page image and return
page text without geometry, so:

- **page CER** as for the other readers;
- **line CER** only on pages where the answer has as many non-empty lines as
  the ground truth, line *i* against GT line *i*; otherwise `—`, and the summary
  says on how many pages it applied. Pairing lines by their text was tried on
  22.10. and scored readers above 1.

The prompt is `prompts/bern_1528.md` (diplomatic, abbreviations as written, one
answer line per page line), `external_prompt` names another file under
`prompts/`. `external_reasoning` defaults to `GEMINI_REASONING` ("low"):
thinking tokens are billed and count against the output ceiling. Every model is
preflighted against the API's model list before a page is sent; tokens and
truncated pages are printed under the table. Eval only: a whole volume through
an API is a decision for after the numbers.

```
transkribus_pipeline(step="eval", run="hertwig-g", doc="13922499", pages="8,17,26,39,48,55",
  prior=true, extra_model="kraken-bern-hertwig-v2", line_model="none", trocr_model="none",
  page_model="none", no_llm=true, variants="",
  external_model="gemini-3.8-flash,gemini-4-argon")
```

### The same readers on a published line set

`external_lineset_eval` reads a line-level test set through the external API
and scores it as the trainer scores its adapters: corpus CER, `truncated_cer`,
`length_ratio`, plus empty answers with their `finish_reason` and the most
frequent confusions. The default set is the Federal Council minutes
(`dh-unibe/image-text_federal-minutes-testset`, 2,751 Kurrent lines,
1848–1903, Hodel & Schoch 2021). Its numbers for the trainer's arms are in
training-atr-models `docs/EVAL_SETS.md`. Billed per line, so it needs `sample`
or `full=true`:

```
external_lineset_eval(run="fedmin-argon", model="gemini-4-argon", sample=100)
external_lineset_eval(run="fedmin-argon", model="gemini-4-argon", full=true)
```

The second call reads only the lines the first did not.

### When the models are trained again (decided 27.10.)

- **A V 1443 Hertwig:** pages are corrected first (option B: towards about 100
  DONE pages, from 58); a new `kraken-bern-hertwig-v3` is trained **on request**,
  not on a threshold.
- **A V 1447 Schlussredaktion:** `kraken-bern-schlussredaktion-v2` once **250
  pages are DONE** (130 on 26.10.; `step="survey"` counts them). Then: GT
  export, HF upload, training, `eval` on the same 8 test pages, and the
  re-read on the page's own lines for the pages still uncorrected.

## What stays manual, and why

1. **Switching on a model whose gate did not run.** A second training on the
   same data reuses the compiled corpus. The job then has no held-out page, and
   the model stays registered with `enabled: false` ("no held-out page was
   available to test with", v2 on 22.10.). Enabling it rewrites a registration
   on the trainer's share, from the trainer's venv on asteraix. tei has
   neither, and a tool that can switch any model on would be a far larger door
   than the rest of this server. The fix belongs in the trainer (the gate must
   find a page on a cache hit). Until then, on asteraix:

   ```bash
   cd ~/Repo/training-atr-models && set -a && . ./.env && set +a
   PYTHONPATH=$PWD/src .venvs/kraken-train/bin/python - <<'PY'
   from atr_training.settings import get_settings
   from atr_training.promote import held_out_page, http_recognizer, promote
   from atr_training.registration import set_enabled
   s = get_settings()
   page = held_out_page(s.jobs_root / "<an earlier job on the same data>" / "data")
   v = promote("<model_id>", page, http_recognizer(s.gateway_url, s.gateway_api_key), wait_s=60, retry_every_s=15)
   print(v)
   if v.promoted: print("enabled:", set_enabled(s.registry_root, "<model_id>", True))
   PY
   ```

   It is the same gate the trainer runs: a page through `/ocr`, and `enabled`
   only if text comes back.

2. **Stopping and starting services on idhefix** (party for vLLM runs; the
   engines after an outage, as on 22.10. 16:00). The MCP runs on tei and does
   not control the serving machine's units. A tool that can stop services there
   could stop the gateway for everyone. `gateway_models` shows when the engines
   are unreachable (`health.engines[].reachable`). Then, on idhefix:
   `systemctl --user start atr-kraken atr-trocr atr-party`.

3. **Training a line segmenter.** The trainer has no `segtrain`, so it runs
   by hand on asteraix ([`HERTWIG_SEGMENTATION.md`](HERTWIG_SEGMENTATION.md)).
   The export for it goes over MCP: `transkribus_gt_export(segmentation=true)`.

4. **Deploying the code** (merge to `main`, `update.sh` on tei). That is the
   step that decides what the tools run, and it goes through review, not
   through the tools.
