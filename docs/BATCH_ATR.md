# Reading a whole collection with several models

How to take a folder of scans and get every model's reading of every page, kept
apart so they can be compared. Written against the first run it was built for —
the Laßberg *digitalisate* through the three `dh-unibe` German-XIX fine-tunes —
but nothing here is specific to that corpus except the values.

The commands run on **tei**, where the stack, the venv and the gateway key already
live. The GPU work happens on **asterAIx** either way: tei uploads each page to
the ATR gateway on `:8200`, and the models run there. Driving it from asterAIx
instead only changes `ATR_GATEWAY_URL` to `http://127.0.0.1:8200` and needs a
checkout plus a venv on a box whose single partition has been full before.

---

## 0 · Before anything: are the models actually servable?

Registration in `config/models.yaml` is not the same as being servable. See
[`serving-atr-inference/docs/GERMAN_XIX_MODELS.md`](https://github.com/thodel/serving-atr-inference/blob/main/docs/GERMAN_XIX_MODELS.md)
for the box-side work — in short, on asterAIx:

```bash
cd ~/Repo/serving-atr-inference && . ./.env      # HF_TOKEN: the repos are private
df -h /                                          # need ~40 GB; it has hit 0 before
.venvs/vllm/bin/python scripts/merge_loras.py --only qwen3vl-german-xix-v1
.venvs/vllm/bin/python scripts/merge_loras.py --only qwen3.5-4b-german-xix-v1
.venvs/vllm/bin/python scripts/merge_loras.py --only qwen3.5-2b-german-xix-v1
systemctl --user restart atr-gateway
```

Then, from tei, confirm the gateway agrees:

```bash
curl -sH "X-API-Key: $ATR_API_KEY" "$ATR_GATEWAY_URL/models" \
  | python3 -c "import json,sys; print([m['id'] for m in json.load(sys.stdin)['models'] if 'german-xix' in m['id']])"
```

Three ids, or stop here. A batch against an unregistered model is one 404 and an
abandoned model — which is the right behaviour, and still a wasted trip.

**And check the token ceiling.** These are served page-level, and
`ATR_VLLM_MAX_NEW_TOKENS` defaults to 512 — enough for a line, not for a page.
Past it vLLM stops and returns a normal `200` whose transcription simply ends
mid-sentence, with nothing anywhere saying it was cut off. Set
`ATR_VLLM_MAX_NEW_TOKENS=4096` in the gateway's `.env` and restart before the
first real run, then read the **end** of a smoke-run page: a reading that stops
mid-word is the ceiling, not the model.

---

## 1 · Configure the share

In `.env.gpustack` at the repo root (never committed):

```ini
NEXTCLOUD_SHARE_URL=https://cloud.gugw.tu-darmstadt.de/nextcloud/s/FaGXMmkkoY23eaA
NEXTCLOUD_SHARE_PASS=…          # the share password
NEXTCLOUD_REMOTE_DIR=digitalisate
```

Paste the URL as the browser shows it — including the `…/authenticate/showshare`
a password-protected share redirects to. The share token is the WebDAV username;
`utils/nextcloud.py` takes it apart.

Look before you pull:

```bash
cd ~/agentic_historian
python -m agentic_historian pull-share --list
```

That prints every ingestable file with its size and a total. It is also the check
that the password is right: a wrong one fails here, in a second, rather than an
hour into a transfer.

---

## 2 · Mirror it

```bash
python -m agentic_historian pull-share --folder digitalisate
# → agentic_historian/data/nextcloud/digitalisate/…
```

Mirrored rather than mounted: the recogniser uploads bytes per page, so the files
have to be local anyway, and a fuse mount only moves the same transfer somewhere
a dropped connection kills a multi-hour run instead of one download.

Safe to re-run. A file already present at the remote size is skipped, and every
download lands through a `.part` file, so an interrupted transfer is never
mistaken for a finished one. `--limit 5` first, if you want to see the shape of
the material before committing to all of it.

---

## 3 · Read every page with every model

```bash
python -m agentic_historian atr-batch \
    --source data/nextcloud/digitalisate \
    --models qwen3vl-german-xix-v1,qwen3.5-4b-german-xix-v1,qwen3.5-2b-german-xix-v1 \
    --run    atr_test_lassberg
```

Output lands in `$VLM_TEST_ROOT/atr_test_lassberg/<model id>/`, two files per page:

| file | what it is |
|---|---|
| `<key>.txt` | the transcription, nothing else |
| `<key>.json` | the same text plus timings, the engine that answered, party's second opinion, the **sha256 of the source image**, and — for line-level models — the per-line readings with their geometry |
| `manifest.jsonl` | one append-only line per page attempt, across all models |
| `report.md` / `report.json` | what each model produced and what it cost |

The three German-XIX models are registered **page-level**: the whole image goes to
the model in one call, so `lines` is empty for them and `segmented_by` is null.
That is not a gap — there was no segmentation step to report. A line-level model
(TrOCR, LightOnOCR) run through the same command fills both in.

`<key>` is the page's path inside the share with separators folded to `__`
(`letter-01/001.jpg` → `letter-01__001`), so one flat directory per model holds
the whole corpus and every file still says where it came from.

**Dry-run first.** It costs nothing and it is the only place the page count,
the model list and the total number of gateway calls appear before the run
starts:

```bash
python -m agentic_historian atr-batch --source … --models … --run … --dry-run
```

**Smoke-run second.** `--limit 3` reads three pages with each model. Do this
once per new corpus: it pays the cold vLLM start for each model and proves the
whole path end to end in minutes, instead of finding out at 3 a.m. that the
models will not take this scanner's TIFFs or that every reading is stopping at
the token ceiling. Read the *end* of one of the transcriptions before you let the
full run go.

### Why it iterates model-major

All three models are `residency: lazy` on GPU 1, which holds **one** at a time.
The runner therefore does every page of one model before touching the next.
Page-major — all three models per page — would evict and reload weights on every
single page, which on a few hundred pages is the dominant cost of the entire run
and buys nothing.

Nothing else in the repo enforces this, so anything else driving the gateway over
several models has to do the same.

### What it does when things go wrong

| what happened | what the runner does |
|---|---|
| timeout, connection dropped, 5xx | retries (2 by default), backing off 2s → 4s; then records the page and moves on |
| 400/422 on one page | no retry — the request was wrong for *this* page. Records it, moves on |
| 404 (model id the gateway does not have), 401/403 | abandons **that model at once** — every remaining page would fail identically |
| 5 failures in a row | abandons that model. Something is wrong with the model or the gateway, and the rest of the run still has models to get through |
| a model abandoned | the other models still run; `report.md` names which stopped and why; the process exits non-zero |

The counter resets on every success, so a corpus with a few unreadable scans runs
to the end while a gateway that has gone away does not consume the night first.

### Interrupted? Re-run the same command

A page whose `.json` is on disk and parses is skipped. There is no state file to
reconcile — the output *is* the state. Ctrl-C, a reboot, a gateway restart: run
the same line again and it picks up where it stopped.

### Timing

Page-level: one gateway call per page, so a warm model is seconds a page, plus a
minute or more of cold vLLM start the first time each model is asked for
anything. With three models that cold start is paid three times and no more —
which is the whole reason for the model-major order. Multiply by pages × models,
then run it under `tmux`: an ssh session that drops takes the run with it, and
although a re-run resumes, the time already spent is gone.

```bash
tmux new -s atr
# … run the command …          detach: Ctrl-B d      reattach: tmux attach -t atr
```

---

## 4 · Publish the outputs

```bash
python -m agentic_historian publish-batch \
    --run-dir $VLM_TEST_ROOT/atr_test_lassberg \
    --repo    thodel/lassberg \
    --path    data/vlm-outputs/atr_test_lassberg
```

Text only. `publish_tree` works from an allowlist of suffixes
(`.txt .json .md .jsonl .csv .xml`) and names anything it skipped — the scans
themselves are never committed, and a format nobody anticipated is left out
rather than pushed to a public repository.

Needs `GITHUB_TOKEN` with `contents: write` on the target repo. Large trees are
split into commits of 200 files: one commit would be tidier, and a failure at
file 900 of a single commit publishes nothing at all. Re-running is safe — a
chunk that rewrites identical bytes is an empty diff.

---

## 5 · Reading the result

`report.md` gives, per model: pages read, pages skipped, pages failed, **pages cut
off**, characters per page, seconds per page, wall time.

**Check the "cut off" column first.** It counts pages where the model stopped at
its token ceiling instead of at the end of the text. Those readings are real but
short, and they end mid-sentence — indistinguishable, reading them, from a model
that gave up. If the column is not zero, raise `ATR_VLLM_MAX_NEW_TOKENS` on the
gateway, restart it, delete the affected `.json` files, and re-run the same
command: only the missing pages are read again.

It counts zero if the gateway does not report truncation at all (before
serving-atr-inference#123), so a zero there is "not reported", not "did not
happen" — which is another reason to read the end of a page by hand on the first
run.

**None of those columns is quality.** There is no ground truth in a run like this.
Characters per page says how much a model wrote, not how much of it is right, and
a model that hallucinates fluently leads that column. Confidence is the model's
opinion of itself. The comparison is the point — three readings of the same page,
side by side, for a historian to look at — and the numbers only say what each run
cost and whether it finished.

Measuring accuracy is a different job and a different instrument: transcribed
lines and `eval/linebench.py` (see [EVALUATION_HARNESS.md](EVALUATION_HARNESS.md)).
Two cautions if you go there with these models:

- all three were trained on the same four corpora, listed in their registry
  entries. An evaluation set drawn from any of them measures memorisation.
- the CER on their model cards (≈1 %) is each run's **own** held-out split. It
  says the training converged. It does not transfer to a corpus they have not
  seen, and it is not comparable to any other CER in this project.

---

## Configuration reference

| variable | default | what it does |
|---|---|---|
| `NEXTCLOUD_SHARE_URL` | — | the share, as pasted from a browser |
| `NEXTCLOUD_SHARE_PASS` | — | share password (empty if it has none) |
| `NEXTCLOUD_REMOTE_DIR` | `""` | folder inside the share |
| `NEXTCLOUD_STAGING_DIR` | `data/nextcloud` | where the mirror lands |
| `VLM_TEST_ROOT` | `data/vlm_test` | root for comparison runs |
| `ATR_BATCH_PAGE_CONCURRENCY` | `1` | pages in flight per model |
| `ATR_BATCH_RETRIES` | `2` | retries per page for timeouts and 5xx |
| `ATR_GATEWAY_URL`, `ATR_API_KEY` | — | the gateway (existing) |
| `ATR_HTTP_TIMEOUT` | `300` | per-call budget; must not be tighter than the gateway's own |

`ATR_BATCH_PAGE_CONCURRENCY` defaults to 1 on purpose. The gateway already
recognises the lines of one page about six at a time, so a second page in flight
queues against the same GPU rather than finding an idle one — and it makes a
failure harder to attribute. Raise it only for an engine that leaves the GPU idle
between lines, and check `report.md` actually improved.
