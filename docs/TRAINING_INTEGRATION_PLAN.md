# Triggering model training from agentic_historian

Draft, 2026-08-07. How the Discord bot on `tei.dh.unibe.ch` starts and monitors
kraken training runs on asterAIx, and how the results reach the public site.

Training itself lives in
[`serving-atr-inference`](https://github.com/thodel/serving-atr-inference) — this
repo does not train anything. It requests, watches, and reports.

---

## 1. Topology and the one hard prerequisite

```
Discord  ──▶  bot (tei.dh.unibe.ch)  ──▶  gateway :8200 (asterAIx)  ──▶  atr-train :8204
             agentic_historian            X-API-Key, ufw-scoped        127.0.0.1 only
```

The trainer service binds `127.0.0.1` and the `ufw` rule opens **only** `:8200`
to this host, so the bot can never reach `:8204` directly. Everything goes
through the gateway's `/train/*` proxy — **serving-atr-inference#35 must land
first**. Nothing in this plan works without it, and no firewall change is needed
once it does.

Auth is the shared `X-API-Key` the bot already holds for `/ocr` (`kraken_client`).

## 2. Why training cannot use the existing job queue

`bot.py` runs blocking work through a single FIFO queue drained by one worker
(`_run_blocking`, #15/#148): the command awaits the result, and the queue
serialises everything. That is right for agents that take minutes. It is wrong
for training, for three independent reasons:

1. **Duration.** A real run is hours to days. It would sit at the head of the
   queue and block every other command for that whole time.
2. **Discord's interaction window.** A followup can be edited for ~15 minutes.
   An awaited six-hour job has nowhere to reply to.
3. **Restarts.** `/update` restarts the bot. An awaited future dies with it,
   while the training run continues on asterAIx, unwatched.

So training is **fire-and-forget plus polling**: the trainer already owns the job
lifecycle and persists every job to disk (that is what its detached-runner design
is for). The bot owns notification only, and must be able to reattach to a run it
did not start — after a restart, or from a different channel.

## 3. Command surface

| command | who | what |
|---|---|---|
| `/train_start preset:<name> model_id:<id>` | admin | resolve preset → confirm → `POST /train/jobs` |
| `/train_status [job_id]` | role | one run, or the active one |
| `/train_list` | role | recent runs, newest first |
| `/train_log job_id:<id> stage:<stage>` | role | tail of a stage log |
| `/train_cancel job_id:<id>` | admin | `POST /train/jobs/{id}/cancel` |

**Presets, not raw hyperparameters.** Discord is a poor place to type a VGSL
spec. `config.TRAINING_PRESETS` (or a small YAML next to it) maps a name to a
complete request body — dataset projects, batch size, learning rate, epochs,
architecture. The command takes a preset name and a model id; anything else is a
code change that gets reviewed. This mirrors `datasets.yaml` in
`lassberg/vlm_training` and keeps an expensive, shared-resource action from being
improvised in a chat box.

**Admin-gated with a confirmation step.** A run occupies GPU 1 on a shared box
for hours. `/train_start` uses the `admin_only` gate and the `ConfirmView`
pattern from `/update`: the bot resolves the preset, shows what it will cost —
projects, estimated pages and lines, steps per epoch, projected wall time — and
starts only on an explicit click. The estimate comes from the preset, not from a
guess at call time.

## 4. Watching a run

A background task (started in `on_ready`, like `_ensure_worker`) polls
`GET /train/jobs/{id}` every 60 s for every run in the **watch registry**, and:

- edits **one** message per run in place (the `ProgressReporter` board pattern
  from Epic V) rather than posting a line per tick;
- announces stage transitions (`prepare → compile → train → test → register`);
- reports `queued_reason` verbatim when a run is waiting — "waiting for job X" and
  "GPU 1 has 2000 MB free" are different situations and the caller should see
  which;
- on a terminal state, posts the CER/WER, the model id, and (once the site
  section exists) a link to the published training report;
- on failure, posts the error plus the log tail the job record already carries.

The registry — `{job_id: {channel_id, message_id, started_by}}` — is persisted to
disk next to the other run state, for the same reason the trainer persists job
state: a restart must reattach, not forget. `/train_status <job_id>` can adopt a
run that is not in the registry, so a run started from the API or by a colleague
can still be watched from Discord.

Polling, not webhooks: the trainer has no outbound path to tei, and one HTTP GET
per minute per active run is nothing. One run at a time is the trainer's own
limit, so this is at most a handful of requests per hour.

## 5. What lands on the public site

On completion the trainer writes `training.json` (serving-atr-inference#38). The
publication path mirrors how recognitions already reach the catalogue:

```
asterAIx: training.json  ──▶  tei: fetched by the bot/publisher
                              ──▶  agentic-historian-outputs: docs/training/<run_id>/
                              ──▶  GitHub Pages: curves, dataset provenance, model card
```

The bot's role is to fetch the record and commit it, exactly as it does for
`pipeline.json` today. The rendering — training/validation curves, dataset
provenance, reproducibility panel — belongs to the outputs repo and is planned as
its own epic there.

**Quality vocabulary applies.** The outputs site deliberately refuses unscoped
accuracy figures. A training report must label every number: which reference,
which normalisation, which scope — and must keep `ketos test` CER (line crops
from ground-truth segmentation) visibly distinct from the eval-harness CER (full
page through our own segmentation). They are different measurements and will
disagree.

## 6. Failure modes to handle explicitly

| situation | behaviour |
|---|---|
| gateway unreachable | command fails loudly with the reason; never a fabricated job id |
| trainer refuses (507 disk, 500 TMPDIR) | surface the detail verbatim — it names the fix |
| job queued behind another | show `queued_reason`; do not pretend it started |
| bot restarts mid-run | watcher reattaches from the persisted registry |
| run cancelled outside Discord | watcher reports the terminal state and drops it |
| two `/train_start` in a row | second is queued by the trainer; the bot says so |

## 7. Milestones

| # | milestone | depends on |
|---|---|---|
| T1 | `TrainingClient` — typed HTTP client for `/train/*`, mirroring `kraken_client` | serving#35 |
| T2 | presets + `/train_start` with the confirm view | T1 |
| T3 | watcher task + persisted registry + `/train_status`, `/train_list` | T1 |
| T4 | `/train_log`, `/train_cancel` | T1 |
| T5 | publish `training.json` to the outputs repo on completion | serving#38, outputs epic |
