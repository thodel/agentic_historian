# Triggering model training from agentic_historian

Draft, 2026-08-07. How the Discord bot on `tei.dh.unibe.ch` starts and monitors
kraken training runs, and how the results reach the public site.

> **Since the split (16.09.2026).** Training left the serving box for **asteraix**
> (130.92.59.242) and its own repository,
> [training-atr-models](https://github.com/thodel/training-atr-models). The route
> below is unchanged and that is the point of it: the bot calls `/train/*` on the
> gateway (`:8200`, idhefix), which proxies to the trainer on `:8204`. Read the
> topology diagram as two machines rather than one, and "idhefix" in the older
> sentences as the box that then served *and* trained. T1 (`TrainingClient`) has
> since landed; it has no caller yet.

Training itself lives in
[training-atr-models](https://github.com/thodel/training-atr-models) — this
repo does not train anything. It requests, watches, and reports.

---

## 1. Topology and the one hard prerequisite

```
Discord  ──▶  bot (tei.dh.unibe.ch)  ──▶  gateway :8200 (idhefix)  ──▶  atr-train :8204 (asteraix)
             agentic_historian            X-API-Key                    own key + client allowlist
```

The trainer service could not be reached from this host, so everything goes
through the gateway's `/train/*` proxy — **serving-atr-inference#35 must land
first**. Nothing in this plan works without it, and no firewall change is needed
once it does.

> **What changed with the split.** The proxy landed as serving-atr-inference#137.
> The trainer now binds `0.0.0.0:8204` on asteraix, because asteraix's `ufw` does
> not filter high ports and nobody there has sudo to add a rule; what keeps the
> bot out is the trainer's own key plus a client allowlist that admits only
> idhefix. The conclusion is the one this section drew: the bot reaches training
> only through the gateway, with the key it already holds.

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
   while the training run continues on asteraix, unwatched.

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
| `/train_log job_id:<id> stage:<stage> [lines]` | role | tail of a stage log |
| `/train_cancel job_id:<id>` | admin + confirm | `POST /train/jobs/{id}/cancel` |

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

As built (#363), `train_watch.py` holds the registry and every decision as pure
functions; `bot.py` holds the socket, the clock and the message edit. Three
things it does on purpose, each the opposite of the obvious first draft:

- **No silence on first sight.** `atr_watch` announces nothing on its first poll
  because it watches the whole box; this watcher only ever sees runs somebody
  asked about, so a terminal state reached while the bot was down is reported
  rather than swallowed. There is deliberately no `seeded` flag.
- **A failed poll never drops a run.** The gateway is restarted for every deploy
  of the serving stack. Silence is announced once after half an hour and the run
  is kept; a **404** is a different fact — the gateway answered and the trainer
  has no such job — and is given up on only after a day, with a message.
- **A stage is claimed only where the record says it ran.** A run that adopts a
  cached artefact never prepares, so the trail marks `prepare` as not reached
  instead of inferring it from the current stage.

Polling, not webhooks: the trainer has no outbound path to tei, and one HTTP GET
per minute per active run is nothing. One run at a time is the trainer's own
limit, so this is at most a handful of requests per hour.

### As built (#364)

`train_ops.py` holds the wording for both commands as pure functions. Two
things in it are there because the obvious version is wrong:

- **The log tail keeps the end.** A traceback is at the bottom of a log, and
  `atr_status._fence` cuts the other way (`body[:limit]`) — right for a table,
  wrong for a log. The cut is marked with how many lines were dropped, because
  an unmarked tail reads as the whole log. `/atr_progress` goes through the same
  function now, so its own tail is cut at the right end too.
- **A 404 and a 409 each mean two things on these routes, and the record says
  which.** The trainer answers 404 both for an unknown job and for a stage whose
  log is not on disk; 409 both for a job that is *already terminal* and for one
  being *started this instant* (`_cancel_unstarted` holds a claim and says "ask
  again in a few seconds"). Neither is told apart by reading the detail text:
  after a refusal the job record is fetched and asked. An operator told the
  wrong 409 either waits for a run that ended hours ago or stops waiting for one
  that is about to start.

`/train_cancel` takes a confirmation beyond the admin gate #364 asked for. It is
this repository's standard for a command that spends or destroys shared work
(`/update`, `/atr_restart`, `/train_start`), and the cancel is the one that
cannot be undone: a run ten hours in loses those ten hours. Persistent view plus
a record on disk (#614), because "is the box free again?" is exactly the
question that gets asked between the card and the click.

The cancel does **not** claim success: the trainer SIGTERMs the process group
and the *runner* marks the job cancelled, so the record that comes back can
still say `training`. The command says the signal is away and the watcher (#363)
reports the terminal state when it arrives.

## 5. What lands on the public site

On completion the trainer writes `training.json` (serving-atr-inference#38, now
training-atr-models'). The
publication path mirrors how recognitions already reach the catalogue:

```
asteraix: training.json ──▶  tei: fetched by the bot/publisher
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

### As built (#638)

`training_report.py` is pure: a job record and a curve payload in, a dict of
files out. `publish_github.publish_training` commits them; nothing else about
publishing changed.

Reading the contracts turned "two numbers" into **four**, all of which look like
accuracy:

| number | scope | unit |
|---|---|---|
| `metrics.cer` / `char_accuracy` (`ketos test`) | validation split, **line crops from the ground-truth segmentation**, and the split overlaps the training projects | fraction / percent |
| `metrics.benchmark_cer` + `measured_on` | the named held-out benchmark — the trainer's own contract calls this the primary claim and `cer` a secondary check | fraction |
| `best.val_metric` / `val_error` (`training.json`) | kraken's validation during training, over the **ten best checkpoints only** | fraction, but an **accuracy** — higher is better |
| eval-harness CER | a full page through our own segmentation | **not in this record** |

So the page states, for every row, its scope, its unit and **which way is
good** — `val_metric` points the other way from everything around it, and
`curves.py` emits both values for that reason. A number that was not measured
has no direction at all, and prints none: giving an absence a property makes it
look like a result.

Two further honesty rules the page keeps:

- **The curve is incomplete by construction** and says so with the reason.
  `still_improving` is turned into the finding it implies: checkpoints 41–50
  mean the run was still getting better when it stopped (train longer),
  checkpoints 3–12 mean it peaked early and got worse for forty epochs (the data
  or the configuration is the question). A live mid-run reading is marked as not
  final.
- **The missing eval-harness number is visibly missing.** Omitting it would let
  the `ketos test` number stand in for it, which is exactly what this catalogue
  refuses.

Triggered by T3's watcher when a run reaches `completed`, and by
`/train_publish` (admin) for any other case — a catalogue entry about a failed
run is a decision, not a side effect. A run id is checked before it becomes a
path, like a document id. `ENABLE_GITHUB_PUBLISH` gates it as it gates
everything else.

Not this issue: drawing the curves (the outputs repo's epic), and pushing the
**model** to the hub — that is `ATR_TRAIN_AUTO_PUBLISH_MIN_ACCURACY` in the
trainer, off by default, and untouched here.

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

| # | milestone | depends on | state |
|---|---|---|---|
| T1 | `TrainingClient` — typed HTTP client for `/train/*`, mirroring `kraken_client` | serving#35 | **done** (#361) |
| T2 | presets + `/train_start` with the confirm view | T1 | **done** (#362) |
| T3 | watcher task + persisted registry + `/train_status`, `/train_list` | T1 | **done** (#363) |
| T4 | `/train_log`, `/train_cancel` | T1 | **done** (#364) |
| T5 | publish `training.json` to the outputs repo on completion | serving#38, outputs epic | **done** (#638) |
