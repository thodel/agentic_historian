# Agentic Historian

Autonomous pipeline for transcribing, describing, and analysing historical handwritten documents (14th–16th century, Swiss/German administrative sources).

## POC

This Discord server (#allgemein, channel `1519707390956798034`) is the live proof-of-concept environment. Progress reports run hourly here.

## Architecture

```
agents/
  text_recognition.py   — Agent A: HTR (kraken-first when available, VLM fallback)
  source_description.py — Agent B: source description (Ad Fontes 16-element; JSON+MD; human pins)
  entity_agent.py       — Agent C: entity extraction (NER) + MCP-federation / hub linking
  corpus_analysis.py    — Agent D: corpus stats, topics, taxonomy, care, Voyant
  meta_agent.py         — Agent E: resource tracking / report
  search_agent.py       — federated person search (parallel MCP query + resolve + rank)
  source_heuristic.py   — Ad Fontes (UZH) codicological prompt framework (16 elements)
agent_a/          — two-pronged HTR (VLM + kraken/TrOCR via the serving-atr-inference gateway)
knowledge_hub/
  mcp_registry.py       — declarative registry of the KH MCP sources (see docs/knowledge_hub.md)
  hub.py                — controlled vocabulary + thin cache (authority data via MCP federation)
  rdf_export.py         — CIDOC-CRM RDF/Turtle export (toward the QLEVER triple-store, WP4)
utils/
  gpustack_client.py    — single GPUStack (OpenAI-compatible) client
  mcp_client.py         — async client over the KH MCP federation (PersonResult contract)
  entity_resolver.py    — cross-source entity resolver/merger
  switchdrive.py        — WebDAV ingestion from SwitchDrive (as an account)
  metrics.py            — per-run telemetry (Agent E)
orchestrator.py   — A→B→(kraken re-run)→C(→D) pipeline wiring (single doc + grouped "order")
ingest.py         — SwitchDrive order ingestion (UI-agnostic core; bot is a thin shell, #33)
ingest_mailbox.py — which way the hot folder is read: a share token or an account (#592)
runstate.py       — per-document run state + stage-invalidation state machine (HITL)
routing_card.py   — HITL Gate-1 routing card (metadata selects → re-route HTR)
path_compare.py   — HITL Gate-2 path-comparison card (measured CER)
uncertainty.py    — HITL gate-blocking rules + timeouts (when a gate interrupts)
agent_tools.py    — uniform tool registry over the agents (A–E callable by name, #41)
nl_orchestrator.py — NL/Scholar-in-the-Loop planner: LLM picks which agent tools to run (#32).
                    Optional overlay, OFF by default (ORCHESTRATOR_LLM_ENABLED); no slash
                    command reaches it, and the standalone bot runs without it (S4, #569).
semantic.py       — embedding retrieval + reproducible clustering with LLM labels (#28)
utils/publish_github.py — publish outputs to the public catalogue repo (one commit/doc, #200)
knowledge_hub/store.py  — swappable HubStore backend seam (JSON today, QLEVER at WP4, #26)
output_site/      — scaffolding installed into the output repo (Pages config, index Action)
bot.py            — Discord bot (py-cord slash commands; thin shell over the core modules)
config.py         — central config + role-based GPUStack routing
docs/knowledge_hub.md — MCP-federation methodology + how to add a source
```

All models run on the **unibe GPUStack** (`gpustack.unibe.ch`, OpenAI-compatible): vision `qwen3.8-27b` (A/B), text `gpt-oss-120b` (C/D/E), `minimax-m2.7` reserved for orchestration. HTR's kraken/TrOCR path is served by the companion **serving-atr-inference** gateway (`ATR_GATEWAY_URL`, `X-API-Key`). Knowledge-hub authority data (persons/places — HLS, HBLS, KF, EOS, plus GND/Wikidata) is federated over **MCP**; the registry lives in `knowledge_hub/mcp_registry.py` and the methodology in `docs/knowledge_hub.md`. See `IMPLEMENTATION_PLAN.md` and `AGENTIC_HITL_PLAN.md`.

## Prompt Framework

`agents/source_heuristic.py` contains the Ad Fontes (UZH) codicological prompt framework — 16 description elements with archival context and observation questions derived from the [Ad Fontes tutorial](https://www.adfontes.uzh.ch/tutorium/handschriften-beschreiben).

## Quick Start

Requires Python 3.11+, on the unibe VPN (GPUStack is IP-gated).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../deploy/gpustack.env.example ../.env.gpustack   # then fill in the rotated key + Discord token
python bot.py            # or: python -m agentic_historian  (entry point, see pyproject.toml)
```
`config.py` loads `.env.gpustack` from the **repo root** (real process env always wins; dotenv never overrides it). In production the bot runs under systemd (`agentic-historian.service`).

**Standalone deployment (separation epic #565).** In production the bot is its own
process, decoupled from the OpenClaw agent `dh-bot`: its own Discord application and
token (S1, #566); its own secrets file read via `AH_ENV_FILE` instead of the shared
`.env.gpustack`, plus a hardened systemd unit (S2, #567 — see
[`deploy/systemd/SEPARATION.md`](../deploy/systemd/SEPARATION.md) and
`deploy/systemd/dh-bot.env.example`); the LLM-orchestration overlay off by default
(S4, #569); and the OpenClaw home (`workspace/` — AGENTS.md, SOUL.md, skills/, …)
lifted out of this repository altogether (S3, #568; the env template moved to
`deploy/gpustack.env.example`). CI's **standalone** job proves the bot imports and
runs its start path (up to `bot.run`) without any `workspace/`.

## Discord Commands

| Command | Description |
|---|---|
| `/run <file>` | Full A→B→C pipeline on a file in the hot folder |
| `/run_agent_a <file>` | HTR only |
| `/hotfolder` | Process all files in the hot folder |
| `/pull [folder] [recursive]` | Pull images from a mailbox folder and process each. The answer names the way it took — share or account (#592) |
| `/pull_folder [folder] [reprocess]` | Process each mailbox subfolder as one multi-page document |
| `/pull_preflight [folder]` | Which of the four mailbox layers is broken — config, host, credentials, path — each reported separately, with the ones behind a failure left as *unchecked* rather than failed. For a share it also reports **which public endpoint answered** |
| `/env_reload` | Admin: re-read the `.env` files without a restart, then run the preflight so success is shown rather than claimed. Only the keys that came *from a file* — a value from the process environment wins over every `.env` (#106) and is named as unchangeable from here |
| `/agent_d [corpus]` | Corpus analysis |
| `/agent_e` | Meta report |
| `/search <name>` | Federated person search across the KH MCP sources (HLS/HBLS/KF/EOS) |
| `/route <doc_id>` | HITL Gate-1 routing card — correct inferred metadata, re-route HTR |
| `/votes <doc_id>` | HITL Gate-2 card — pick the closest reading when the engines disagree (see *Quality without ground truth*) |
| `/votes_queue [n]` | The pending Gate-2 votes, ranked by measured disagreement and by how starved the script×century×language bucket is — so the asking is deliberate rather than queue order |
| `/campaign [bestand] [budget]` | Vote campaign (admin): posts the G1-best cards for a doc_id prefix into their own thread, budget counted in cards, coverage baseline recorded at the start |
| `/campaign_status [bestand] [beenden]` | A campaign's coverage report — comparisons per bucket, which buckets crossed n≥10 *during* it, selection agreement |
| `/votes_stats [bereich]` | What the Gate-2 votes have measured: coverage (#333), selection agreement (#334), engine strength (#335), routing overrides (#154). The ranking lives here and deliberately nowhere else — see *Quality without ground truth* |
| `/find <query> [typ] [bestand] [seite]` | Semantic search over the passage index; each hit deep-links into the published catalogue at the place in the text |
| `/status`, `/progress` | Status |

Sensitive commands (`/run`, `/run_agent_a`, `/pull`, `/pull_folder`) are role-gated when `REQUIRED_DISCORD_ROLE_ID` is set. All commands are serialised through a single worker queue (responsive, no per-user races).

## Environment Variables (`.env.gpustack`, repo root)

| Variable | Description |
|---|---|
| `DISCORD_BOT_TOKEN` | Discord bot token |
| `REQUIRED_DISCORD_ROLE_ID` | Numeric role id allowed to run sensitive commands (empty = open) |
| `GPUSTACK_API_KEY` | GPUStack API key (rotate if leaked; gitignored) |
| `GPUSTACK_BASE_URL` | GPUStack endpoint (default `https://gpustack.unibe.ch/v1`) |
| `GPUSTACK_MODEL_VISION` / `_TEXT` / `_ORCHESTRATOR` | Role-based model routing |
| `GPUSTACK_MODEL_EMBEDDING` / `_RERANKER` | Retrieval models for Agent C linking |
| `GPUSTACK_TEXT_MAX_TOKENS` | Token budget floor for the gpt-oss reasoning model |
| `ATR_GATEWAY_URL` | serving-atr-inference gateway (kraken/TrOCR); falls back to legacy `KRAKEN_SERVICE_URL` |
| `ATR_API_KEY` | `X-API-Key` for the ATR gateway |
| `MCP_BASE_URL` / `MCP_TIMEOUT` | Knowledge-hub MCP federation base + per-request timeout |
| `ENABLE_MCP_LINKING` | Agent C links persons via the MCP federation (falls back to the local hub) |
| `SWITCHDRIVE_SHARE_URL` / `_PASS` | The hot folder as a **public share** (#592, recommended): the share token is the WebDAV user, so no edu-ID account is in the path and nothing dies when an app passcode is deleted on another device. Paste the browser URL. The password is optional — a share may have none. Set, this wins over the account values below |
| `SWITCHDRIVE_URL` / `_USER` / `_PASS` / `_REMOTE_DIR` | The hot folder as an **account** (app passcode). Still supported; used when no share is configured |
| `DISCORD_GUILD_ID` | Register slash commands in this guild, where Discord makes them usable at once. Empty = global commands, which take up to an hour to propagate — a new command is then indistinguishable from a missing one |
| `CREDENTIAL_WATCH_CHANNEL_ID` / `_INTERVAL_S` | Announce when the mailbox credentials stop being accepted, instead of letting a failed pull be the first sign. Empty = off |
| `BATCH_WORKERS` / `BATCH_MAX_ATTEMPTS` | Documents in flight at once for `batch`, and attempts before one goes to the dead letter (defaults 2 and 3; `--workers` / `--max-attempts` override per run) |
| `BATCH_PUBLISH_EVERY` | Documents per publish commit in a batch run (default 0 = once at the end, the fewest index rebuilds). `--publish-every` overrides per run |
| `ATR_GATEWAY_URL` / `ATR_API_KEY` | The recognition gateway on idhefix and its static key. Also what `/atr_engines`, `/atr_restart` and the batch run's engine preflight use; `/health` needs no key, the restart does |
| `NEXTCLOUD_SHARE_URL` / `_PASS` / `NEXTCLOUD_REMOTE_DIR` | Nextcloud **public share** ingestion — the share token is the WebDAV user (`docs/BATCH_ATR.md`) |
| `NEXTCLOUD_STAGING_DIR` / `VLM_TEST_ROOT` | Where a share is mirrored to, and the root for multi-model comparison runs |
| `ATR_BATCH_PAGE_CONCURRENCY` / `ATR_BATCH_RETRIES` | Pages in flight per model (default `1`) and per-page retries for timeouts/5xx (default `2`) |
| `VOYANT_API_URL` | Self-hosted Voyant instance (Agent D) |
| `ENABLE_HLS_LOOKUP` / `HLS_DATA_PATH` | Offline HLS fallback (primary path is the HLS MCP) |
| `ENABLE_GITHUB_PUBLISH` | Publish processed outputs to the public catalogue repo (default `false`) |
| `GITHUB_OUTPUT_REPO` / `_BRANCH` | Output repo (default `thodel/agentic-historian-outputs`, `main`) |
| `SOURCE_URL_BASE` | Base URL for source-image links on published pages (empty = no link) |
| `ENABLE_ENSEMBLE_HTR` | Multi-engine ensemble per page (VLM + kraken + TrOCR) instead of VLM-only (default `false`) |
| `ENSEMBLE_MIN_ENGINES` / `_PER_ENGINE` / `_MAX_LOOPS` | Engines in the initial batch (2), models ranked per engine (3), extra loops while candidates disagree (5). Two and five cap at the same seven picks as the earlier three and four; the third engine now runs only when the first two disagree (#390) |
| `ENSEMBLE_CONCURRENCY` | Engine calls of the initial batch run concurrently, bounded (default `3`; `1` = sequential, #389) |
| `ENSEMBLE_AGREEMENT_CER` | Pairwise CER above which the loop widens the pool (default `0.30`) |
| `ENSEMBLE_NO_MERGE_CER` | Pairwise CER above which candidates are **selected, not merged** (default `0.35`) |
| `ENABLE_VERBOSE_PROGRESS` | Live per-step progress board in Discord (default `false`) |
| `VERBOSE_PROGRESS_CHANNEL_ID` | Channel for background runs; unset = the invoking channel |
| `AUTO_RESUME_AFTER_GATE` | Re-run B/C automatically after a gate decision (default `false`) |
| `ENABLE_ROUTING_PRIOR` | Additive routing prior from historian feedback in model selection (default `false`) |
| `ORCHESTRATOR_LLM_ENABLED` | Optional LLM-orchestration overlay (`nl_orchestrator` + `orchestrator_llm`), **off by default**; no slash command reaches the planner while it is off (S4, #569) |
| `KH_BACKEND` | Knowledge-hub store backend (`json` today; QLEVER at WP4) |

See `deploy/gpustack.env.example` for the full template (and
`deploy/systemd/dh-bot.env.example` for the production secrets file).

## Reading a collection with several models — batch ATR

`atr_batch.py` answers a different question from the pipeline: not "read this
document well" but "how do these models read this collection". No selection, no
fusion, no gate — every model's reading of every page, kept apart and kept whole.

```bash
python -m agentic_historian pull-share --folder digitalisate
python -m agentic_historian atr-batch --source data/nextcloud/digitalisate \
    --models m1,m2,m3 --run atr_test_lassberg
python -m agentic_historian publish-batch --run-dir … --repo owner/name --path data/vlm-outputs/…
```

### The corpus batch runner (R2, #392)

`atr-batch` compares models over a collection; `batch` runs the **pipeline** over
one, outside the Discord queue — same code path as `/run`, N documents at a time,
and resumable because every document's state is in the R1 manifest
(`data/corpus_manifest.db`).

```bash
python -m agentic_historian batch data/hot_folder/lassberg --dry-run   # the plan
python -m agentic_historian batch data/hot_folder/lassberg --workers 3
python -m agentic_historian batch data/hot_folder/lassberg --requeue   # retry the dead
```

The mode is derived: subfolders of pages become one document each, loose pages
become one document per image. A folder that is **both** is refused rather than
guessed, because either guess silently drops half the work — `--mode` settles it.

Interrupt it and run the same command again: finished documents are skipped,
and claims a killed worker was holding are handed back at the start. A document
that exhausts `--max-attempts` goes to the dead letter with its error kept and
the run carries on; `--requeue` brings those back after the cause is fixed.
Publishing goes through the batch path (#394): the runner calls the pipeline
with `publish=False` and commits N documents' outputs in **one** commit, because
each commit to the output repo triggers its index-rebuild Action — 500 commits
and 500 Action runs for one holding was the thing this replaced. `0` (the
default) publishes once at the end of the run; `--publish-every N` trades more
index rebuilds for seeing the catalogue fill as it goes. A retry is safe: an
identical tree makes no second commit.

**It checks the engines before it claims anything (#599).** The run reads the
gateway's `/health` and compares it with the engines a plan will ask for
(`vlm`, `kraken`, `trocr`); a planned engine that does not answer refuses the
run, with exit 3 and the engine named. The measured alternative: on 2026-10-09
a `missiven` run put 16 of its 20 recognitions into a kraken service that was
not running, lost each one on its own, and published what was left — a page made
from one model wearing an ensemble's label. One refusal before the first image
is the whole of what the check costs.

`--restart-engines` asks the gateway to restart what is down
(`POST /engines/<name>/restart`, serving-atr-inference#209), once per engine,
and starts only if `/health` then says it is back. Not the default, and bounded,
for the same reason: a process that quietly restarts services hides the failures
somebody needs to see. Three distinctions it keeps:

* **`busy` is not `down`.** A read timeout means the engine accepted the
  connection and is working — restarting on that signal aborts the page it is
  reading, and 30–80 s per page is normal here. Only a refused connection counts.
* **Unmeasured is not down.** vLLM is *never* in `/health` (the gateway spawns it
  as subprocesses), so silence about it is the normal case and is reported rather
  than treated as absence.
* **Restarted is not running.** A `404` from the restart route means this gateway
  has no such route, not that the restart failed, and a transport error means it
  is unknown whether anything happened — reported as such, because a caller that
  reads it as failure asks again.

From Discord: `/atr_engines` says which engines answer, `/atr_restart` restarts
one — admin role and a Confirm button, being the first write this bot makes to
the ATR machines (#414). The gateway is not in the list: a service that restarts
itself from a request cuts off its own answer.

It iterates **model-major** — every page of one model, then the next — because the
gateway's VLMs are `residency: lazy` on one GPU that holds one at a time, so
page-major pays an evict-and-reload cycle per page. It is resumable (a page whose
result is on disk is skipped; the output *is* the state), it classifies failures
rather than just catching them (a 404 abandons that model at once, a timeout is
retried, a bad page is stepped over), and its report says what each model produced
and what it cost — explicitly **not** a ranking, because there is no ground truth
in a run like this. Full runbook: [`docs/BATCH_ATR.md`](../docs/BATCH_ATR.md).

### What a page says it was made from (#595)

Every `pipeline.json` carries how many of its planned readings produced text:

```
"a_meta": {"engines_planned": 20, "engines_answered": 4,
           "engines_failed": 16, "engines_with_text": 4, "coverage": 0.2}
"errors": [{"agent": "A", "phase": "recognition", "page": "…_49_97.JPG",
            "engine": "kraken", "model_id": "kraken-de-1", "error": "502 …"}, …]
```

The measured alternative: the `missiven` page of 2026-10-09 was built from
**4 of 20** readings — 16 lost to a kraken service that was not running — with
`errors: []` and no other trace but `qa_score: 0.0`, a number nobody reads. A
page made that way was indistinguishable from one made from a full ensemble, so
comparing two catalogue pages silently became a comparison between an ensemble
and a single VLM pass.

Three distinctions the counting keeps:

* **Failed is not empty (#483).** A reading that errored told us nothing; one
  that came back without text told us the engine read the page and found nothing
  — the address side of a missive is sparse by nature. The notice says which.
* **Unmeasured is not zero.** A document with no recognitions at all (the
  VLM-only path, or anything produced before the ensemble) has `coverage: null`
  and is never marked. `0.0` would condemn it retroactively.
* **Published is not unmarked.** A partial document **is** published and carries
  a visible notice on its page and a `4/20 ⚠️` in the catalogue listing — a VLM
  reading is worth having and the run cost real time. Refused in exactly one
  case: readings were attempted, none produced anything, and there is no
  transcription either (`DocumentUnreadable`, kept apart from
  `DocumentIdRefused` because one means "rename the material" and the other
  "fix the engines and run it again").

Also fixed here: `timing_ms` was `0` on every recognition, including an
824-character VLM call. Two causes — the gateway's measurement landed in
`KrakenResult` and was dropped when the `RecognitionResult` was built, and an
absent value was read as `0`. It is now carried through, and `None` when the
gateway reported nothing.

### The mailbox: a share token, not somebody's account (#592)

The ingest used to authenticate as a **user** — `SWITCHDRIVE_USER` plus an app
passcode against that account's own root — so the corpus entrance hung on a
personal edu-ID account. On 09./10.10.2026 that cost two days: a valid App
Passcode stopped being accepted and **why is still unknown**
(`docs/LESSONS_2026-10.md` §9 — three mechanisms proposed, two refuted). The
shape is worse than the incident: SWITCH documents one passcode *per device* and
deleting one closes that connection at once, so a passcode shared between tei, a
laptop and a backup dies the moment any of them is tidied up.

|  | account | share token |
|---|---|---|
| hangs on an edu-ID account | yes | no |
| dies with an app passcode | yes | no |
| revoking | delete the passcode (hits every device) | delete the share |
| rotating | new passcode, everywhere | new share, one value |
| giver needs an account | yes | no — they get a link |

Set `SWITCHDRIVE_SHARE_URL` and the ingest reads the share; leave it unset and it
reads the account exactly as before. **Both ways stay** — a switch that keeps the
old one is a switch with a return ticket. `ingest_mailbox.py` is the one place
that decides, so `/pull`, `/pull_folder`, `/pull_preflight` and the credential
watcher cannot disagree about which way is in use, and **every answer says which
way it took**: without that the next failure is the puzzle #563 was filed for, a
message about a credential nobody can tell is the one in use.

Three things this gets right that are easy to get wrong:

* **The token is not printed.** For a share the token *is* the credential — and
  it sits inside the endpoint URL (`public.php/dav/files/<token>`), which the
  report prints. Masking the username was not enough; the probe redacts, and a
  test asserts the token appears nowhere in a report or a watcher announcement.
* **The advice never names the other way's credential.** A 401 on the share path
  says the share may have expired or been deleted, and says explicitly that no
  app passcode is involved. Saying "rotate the app passcode" there sends somebody
  to a settings page where there is nothing to fix.
* **Two things are measured, not assumed.** `/pull_preflight` tries both public
  endpoints (`public.php/dav/files/<token>` and the legacy `public.php/webdav`)
  and reports which answered — SwitchDrive is an ownCloud descendant and the
  reading taken against the GWDG instance does not transfer. And a file-drop
  share that authenticates but refuses to list lands on a 403 with its own
  sentence, rather than looking like a credential problem.

`NEXTCLOUD_SHARE_*` is deliberately *not* reused for this: those name the Laßberg
corpus share on the GWDG instance, which is mounted and read in place (#487).
Pointing the hot folder at a 160 GB holding is the one confusion #592 says not to
reintroduce.

## Publishing outputs — GitHub + Pages

With `ENABLE_GITHUB_PUBLISH=true`, every processed document is committed to the
public **output repo** ([thodel/agentic-historian-outputs](https://github.com/thodel/agentic-historian-outputs))
as `docs/<doc_id>/` — transcription, source description, entities, `pipeline.json`
and a rendered `index.md` (metadata, entities with GND/HLS/Wikidata links, source
link via `SOURCE_URL_BASE`) — **one atomic commit per document**, so every re-run
is a reviewable diff. Only text artifacts are published; source images are linked,
never committed. A build Action in the output repo regenerates the catalogue
index; GitHub Pages (Settings → Pages → `main` `/docs`) serves it. One-time
output-repo setup lives in [`output_site/README.md`](output_site/README.md).
The publishing token needs `contents: write` on the output repo; failures are
logged and never break the pipeline.

### Document id policy (#521)

`<doc_id>` becomes a permanent public URL, so it is checked before it becomes
one. An id must start and end with a letter or digit and may otherwise contain
`.`, `_` and `-`; it may not carry a delimited `test` component. Anything else
is refused by `publish_doc` with the reason, and the run's publish event
carries that reason — see `utils/document_id.py`.

Ids come from the material: `ingest` takes the name of the ingested folder,
`text_recognition` takes an image's stem. Nothing used to check them, so
directories called `u-17__` and `kf-` became published addresses that had to be
retired by hand afterwards in the output repo, and six engineering fixtures
reached it the same way and had to be withdrawn. Refusing costs a rename;
retiring costs a URL that has to keep resolving forever.

The policy is refused rather than normalised on purpose: a normalised id can
collide with an existing document and silently move a published URL. A name
that is wrong is fixed in the material. The output repo enforces the same
policy at build time; the two copies are pinned to the same answers by
`tests/test_ah_521_document_id_policy.py`.

## Contributing — PR & issue rules

This repo has multiple contributors (human and agents) working in parallel. These
rules exist because we have hit each of these failure modes — follow them.

### Pull requests
- **One focused change per PR.** Small and additive; don't refactor unrelated code.
- **Branch from the latest `main`** (`git fetch && git rebase origin/main`), and rebase again before opening if `main` moved.
- **NEVER stack on another unmerged issue's branch.** Always branch from `origin/main`, even when your issue "depends on" another that's still in review. Stacking (branch B built on unmerged branch A) has caused three near-misses — it drags A's commits into B's PR, mixes concerns, and risks silently reverting work `main` gained since the branch point. If you truly need code from an unmerged branch, say so on the issue and wait for it to merge, then rebase on `main`. One issue = one branch off `main` = one PR.
- **Check your true delta before opening:** `git diff --stat origin/main...HEAD` should show *only your issue's files*. If it lists other issues' files, you've stacked or drifted — rebase onto `origin/main`.
- **Don't modify another epic's files** without coordinating — `agent_a/` (HTR) and the orchestrator are actively worked on.
- **Verify before opening:** the code imports/compiles, the relevant test passes, and every import is declared in `requirements.txt`. Exercise runtime/bot changes (LLM calls need the VPN).
- **Title:** imperative summary. **Body:** what changed, why, and how you verified it.
- **Link the issue with `Closes #N`** so it closes automatically on merge.
- **Never commit secrets.** `.env.gpustack` is gitignored; only `gpustack.env.example` (placeholders) is tracked.

### Solving / closing issues
- **An issue is "done" only when its fix is merged to `origin/main`** — not when a commit exists in a local branch, worktree, or sandbox.
- **Close issues through the PR** (`Closes #N`). Do **not** hand-close as "completed" before merge.
- **If you cite a commit, it must be reachable on `origin`.** Verify with `git cat-file -t <sha>` and `git branch -a --contains <sha>`; a SHA that doesn't resolve on origin does not count as a fix. _(This is exactly how #18 was wrongly closed — a cited commit that was never pushed.)_
- **Search before opening** to avoid duplicates; reference the backlog task ID (`AH-NN`) and link related issues.
- **Don't close another contributor's/agent's issue** as done without confirming the artifact is on `main`.

### Models & infrastructure
- **GPUStack only** (`gpustack.unibe.ch`) — no Claude/Gemini. Routing is role-based in `config.py`: vision `qwen3.8-27b`, text `gpt-oss-120b`, orchestration `minimax-m2.7`.
- **`gpt-oss-120b` is a reasoning model** — it spends tokens on reasoning before emitting `content`; give text calls a generous `max_tokens` (the client enforces a floor + retry).
- **The endpoint is VPN-gated** — live LLM calls need the unibe VPN (off-VPN returns `403`).

### Tests
- Add an **offline test** (mock `gpustack_client`, the kraken client, or the MCP transport) for new logic so the suite runs without the VPN. Run **from the repo root**: `pytest agentic_historian/tests/`. GitHub Actions runs the import smoke + full suite on every PR — **CI must be green before merge**.

The supported local test environment is Python 3.11 or 3.12. From the repository
root (the directory that contains `requirements-dev.txt`), use:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install -e agentic_historian --no-deps --no-build-isolation
.venv/bin/python -m ruff check agentic_historian
.venv/bin/python -m pytest agentic_historian/tests --cov=agentic_historian --cov-report=term-missing
```

The pytest suite is offline. Tests must mock GPUStack, ATR, MCP, SwitchDrive,
Voyant, Discord, and GitHub boundaries; live/VPN checks belong in a separate
manual or scheduled job.

## Voyant Tools — Integration

Voyant Tools is available at **https://tei.dh.unibe.ch/voyant/**.

### Infrastructure (since 2026-08-28, #315)

- **Server:** VoyantServer **2.6.21** on `tei.dh.unibe.ch`, port 8888, served **with
  context path `/voyant`** — the app itself emits `baseUrl: '//host/voyant/'`, so no
  URL rewriting happens anywhere. Runs the `JettyRunTime` child command directly
  (see the unit), pinned to **Java 11** — 2.6 does not start on Java 17 (silent 503).
- **Systemd:** `/etc/systemd/system/voyant.service`; working copy + deploy notes in
  `/home/dh/voyant/voyant26/` (`DEPLOY.md`, rollback backups beside it).
- **Restart:** `sudo systemctl restart voyant`
- **Storage (persistent):** `/home/dh/voyant/voyant26-data/trombone5_2` — corpora
  survive reboots. (The old 2.4 instance stored in `/tmp`, which is why published
  `?corpus=` links used to die on every reboot.) Corpus ids are content hashes:
  re-ingesting identical text yields the identical id.
- **App root (read-only, root-owned):** `/opt/voyant/voyant-2.6/VoyantServer2_6_21/`
- **Legacy (2.4, retired):** `/home/dh/voyant/VoyantServer2_4-M45/`

### How Voyant Receives Text

Voyant accepts plain-text documents and produces interactive analysis views (word frequency, KWIC, trends, etc.).

#### 1. Direct text via URL parameter

```
https://tei.dh.unibe.ch/voyant/?text=your+plain+text+here
```

For longer texts, POST the content as form data:

```bash
curl -X POST 'https://tei.dh.unibe.ch/voyant/?text=' \
  -d 'text=Erstes Beispiel. Zweites Beispiel. Drittes Beispiel.'
```

#### 2. Upload a plain-text file via multipart form

```bash
curl -X POST 'https://tei.dh.unibe.ch/voyant/?upload=1' \
  -F 'file=@/path/to/document.txt'
```

Or via the web UI at **https://tei.dh.unibe.ch/voyant/?upload=1**.

#### 3. Load text from a URL

```
https://tei.dh.unibe.ch/voyant/?input=https://example.com/document.txt
```

#### 4. Programmatic access via Spyral.js API

For embedding Voyant tools in notebooks or automated pipelines:

```javascript
// Load a text or corpus
const corpus = new Spyral.Load('https://tei.dh.unibe.ch/voyant/?text=your text here');

// Get word frequencies
const counts = corpus.getTermCounts({ limit: 100 });

// Create a KWIC view
new Spyral.KWIC().corpus(corpus).window(5).show();
```

Full API: **https://tei.dh.unibe.ch/voyant/docs/** (navigate to *Spyral* module docs).

#### 5. Corpus by ID

If a corpus already exists on the server:

```
https://tei.dh.unibe.ch/voyant/?corpus=<corpusId>
```

### nginx reverse proxy

Because the app runs **with context path `/voyant`**, nginx needs exactly one
plain proxy block — no `sub_filter` rewriting, no separate `/resources/` or
`/trombone/` locations, no 8889 helper vhost (all three existed for the old
root-context deployment and were removed in #315):

```nginx
location /voyant/ {
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_pass http://127.0.0.1:8888/voyant/;
}
```

**Why the sub_filter approach could never work:** the JSP builds the client
config as `baseUrl: '//<host>/'` from the Host header **and the servlet context
path**. That protocol-relative URL matches none of the `href="/`/`src="/`
filters, so the SPA kept calling `/trombone` at the domain root and never loaded
its corpus. Deploying with the real context path fixes it at the source.

**Cache caveat after the switch:** Jetty serves `/voyant/resources/**` with
`Last-Modified` only, so browsers apply heuristic freshness. A browser that
cached the old 2.4 resources may mix stale JS into the 2.6 app (white page,
`Spyral.Categories is not a constructor`) until a hard reload. If that ever
matters at scale, add `add_header Cache-Control "no-cache";` to the block above
— Jetty answers revalidations with cheap 304s.

### Adding Voyant analysis to agent_d

agent_d can integrate Voyant by passing transcriptions to the server. Example flow:

```python
import requests

def analyse_with_voyant(text: str, endpoint: str = "https://tei.dh.unibe.ch/voyant/"):
    """Upload text to Voyant and return the session URL."""
    resp = requests.post(
        endpoint,
        params={"text": text},
        headers={"Accept": "application/json"},
        timeout=30
    )
    # Voyant redirects to /?corpus=<id> on success
    return resp.url
```

**Important:** The Voyant endpoint is internal-only (`localhost:8888` proxy) — always route through `https://tei.dh.unibe.ch/voyant/`.

## Language is an approximation, at every level

Model selection needs a language. The sources do not reliably have one.

A cartulary such as `saa-0428` (Königsfelden, e-codices) has German front matter
and Latin charters. Agent B describes it correctly as *"Deutsch und Latein,
gemischt"*, and the pipeline keeps **both** — a declared language is a set, so both
languages' models stay eligible on every page.

Pass 2 then narrows that per page, from the pass-1 text, choosing only **between
the languages the source already declared**. It never proposes a language nobody
claimed: guessing freely from 500-year-old HTR output, with its systematic
misreadings, would invent evidence. When a page is genuinely mixed, or the sample is
thin, the detector returns nothing and the order-level criteria apply — a wrong page
language is worse than no page language, because it removes the right models from
contention.

**A page is not a language.** This is the part worth stating plainly rather than
burying in a docstring: in these manuscripts the register can change **between
sentences, and within them** — a German entry quoting a Latin formula, a Latin
charter naming German persons and places, an abbreviation that belongs to neither.
Agent B reported exactly this for `saa-0428`: *"überwiegend mittelhochdeutsche
Formen, aber mit lateinischen Floskeln und Abkürzungen."*

The page is simply the unit the recognition pipeline already has. It is a better
approximation than the order, and it is still an approximation. Sub-page language
would need line- or region-level criteria, which the ensemble has no shape for
today — and even a line is not safe, since a single line can carry a German name
inside a Latin formula. Whatever granularity is chosen, the honest position is that
a mixed source has no single correct answer, only a level at which the error is
tolerable.

That is why the other language is never dropped, even on a page confidently
identified: the specialised language leads the ranking, the rest stay eligible.

## Quality without ground truth

When several engines read the same page and disagree badly, there is no automatic
way to tell which reading is right. The pipeline does not pretend otherwise.

**Above `ENSEMBLE_NO_MERGE_CER` the candidates are selected, not merged.** Merging
assumes engines make independent errors around a shared signal; when they genuinely
disagree there is no shared signal, and the vote returns noise. Measured on BAT_664
at 70 % pairwise CER, the fused text was a good TrOCR reading with its correct parts
voted out by three garbage candidates.

**A Gate-2 selection is the closest available reading, never ground truth.** The
historian picks from what *we* produced, bounded by the pool. Measuring CER against
that choice would certify our own errors, cap quality at "reproduces the existing
pool", and penalise any better model that disagrees with it. So `/votes` records
**comparisons**, not text:

    chosen ≻ each offered-but-not-chosen   (within one page)

`data/feedback/preferences.jsonl` therefore contains no transcription at all, and
voter ids are pseudonymised with a per-deployment salt.

What that buys, none of it needing a reference text:

| Signal | Question it answers |
|---|---|
| **Coverage** | Did the ensemble produce *anything* usable? ("Keine brauchbar" is a recorded answer, not silence) |
| **Selection agreement** | Did the automatic pick match the historian's? — measures the *selector* |
| **`regret_cer`** | How far apart were they? A distance between two of our own candidates — **not** an accuracy |
| **Engine strength** | Which engine wins for e.g. Kurrent 15th c., via Bradley–Terry over the comparisons |

One click over N candidates yields N-1 comparisons, which is why the log stores
comparisons rather than texts. Below ~10 comparisons a bucket reports *insufficient
data* rather than a number: a confident-looking strength from three clicks would
steer model selection on nothing.

Two things the card deliberately does **not** show: the model's match score, and
which candidate the selector picked. Both would anchor the choice, and the choice
is the evidence the agreement and strength metrics are computed from — a display
that steers it would make those metrics measure the display.

Match score ranks *fit to the source's metadata*, not output quality. Live on
BAT_664 the 0.20-scored model read the page correctly and the 0.80-scored one did
not. A candidate whose output is in the wrong writing system altogether (CJK for a
Latin-script German page) can never be selected — that is not a quality judgement
but a statement about possibility.

## Models

Model registry lives in the sibling repo **serving-atr-inference**:

**`serving-atr-inference/config/models.yaml`** — authoritative ATR model registry, exposed by the gateway at `GET /models`.

`agent_a/models.py` holds a static fallback table; at startup `refresh_kraken_registry()`
fetches the live registry from the gateway (`GET /models`) so `model_selector.py`
routes language/script/century → model against what's actually served (no drift).

### Which VLM runs (#537, #538)

Exactly one, and it is `config.GPUSTACK_MODEL_VISION` (default `qwen3.8-27b`,
GPUStack). `VLM_MODELS` in `agent_a/models.py` **describes** VLMs; it does not
decide which one runs — `get_primary_vlm()` reads the configured id, so the two
cannot disagree.

They did for four weeks. The config default moved from `internvl3-8b-instruct`
to `qwen3.8-27b` on 08.09.2026 after AH-11 measured 189.8 % against 27.7 % CER
on the Inzigkofen set, the table did not move with it, and every VLM reading
was recorded under the name of the model that had *not* produced it. A reading
now carries the id `_run_vlm` asked the gateway for, returned from the call
rather than chosen by the caller.

### Gateway VLMs — registered, not selected (#540)

The ATR gateway serves its own fine-tuned VLMs under `engine: vllm`
(`qwen3vl-medieval-german-v3` among them). Phase 0 collects them into
`VLM_GATEWAY_MODELS_LIVE` from the same `GET /models` response, and
`KrakenHTTPClient.read(image, model, engine)` posts them to `/recognize` —
`/ocr` takes kraken and TrOCR only and answers anything else with
`400: use /recognize for '<engine>'`.

Nothing selects them yet. `plan_models` still emits one VLM pick, the GPUStack
one, and the escalation tail still draws from kraken and TrOCR. A VLM selector
is #539 and the CER measurement that would justify running one is #541.

Note the engine names: `vlm` is the GPUStack path, `vllm` is the gateway's
engine. One letter, two backends.

### Measuring one before it runs (#541)

`eval/vlm_bench.py` is what turns "addressable" into "known". `run` transcribes a
page set with every candidate (GPU + gateway); `score` reports CER against hand-
corrected ground truth, separated by level, with the collapse share and the
fusion effect beside it. The two phases are separate so the expensive half runs
once and the analysis runs anywhere.

```bash
python -m eval.vlm_bench run   --pages <dir> --out runs/bench
python -m eval.vlm_bench score --run runs/bench --gt <pagexml>
```

It will not rank two working models on a small set — `docs/EVALUATION_HARNESS.md`
says why, and what it does instead.

### TrOCR line-level models (currently deployed)

| Model ID | HF repo | Languages | Centuries |
|---|---|---|---|
| `trocr-medieval-escriptmask` | `dh-unibe/trocr-medieval-escriptmask` | de, fr, la, nl | 13–16 |
| `trocr-kurrent-xvi-xvii` | `dh-unibe/trozco-kurrent-XVI-XVII` | de | 16–17 |
| `trocr-essoins-middle-latin` | `dh-unibe/trozco-essoins-middle-latin` | la | 13–15 |

All three are **vision-encoder-decoder seq2seq** models served by the trocr engine
(GPU1, port :8202). They require pre-segmented **line images** — page-level input must
be segmented first (e.g. via kraken `blla`).

### Other engines

- **VLM (vLLM)** — GPU0/1, e.g. InternVL3-8B, Qwen3-VL — page-level, no prior seg needed
- **Kraken** — GPU1, various Zenodo models — page-level (segments internally)
- **Party/PARY** — GPU1, Zenodo `10.5281/zenodo.20642057` — page-level HTR

## Status

The core pipeline is operational and verified live end-to-end: VLM HTR →
Ad-Fontes description → criteria-driven re-run with script-aware model selection →
entities with 4/4 MCP knowledge-hub sources. CI runs the full offline suite on
every PR. See `IMPLEMENTATION_PLAN.md` and `AGENTIC_HITL_PLAN.md` for history.

Since 2026-08 the recognition path is the **multi-engine ensemble** on both the
grouped and the single-document route (they share one code path, so they cannot
drift apart), with a two-pass design: pass 1 runs blind, Agent B describes the
source, pass 2 reads it again with the models that description implies. Gate-2
voting, the preference log and the progress board are live on tei.

Honest about its own limits — each of these was a real defect, found live:

- a page where fewer than two candidates are usable gets **no quality score**;
  "nothing to compare" is not "perfect agreement"
- a candidate in the wrong writing system is never the automatic pick
- a language or script that cannot be recognised yields **no value** rather than a
  plausible-looking wrong one
- `/votes` on an unknown document says so, instead of reporting that the engines agreed

- Scaffold + Discord bot, Agents A–E, hot-folder/SwitchDrive ingestion ✅
- **Knowledge Hub — MCP federation ✅** (SSE + streamable-HTTP transports; HLS/HBLS/KF/EOS live; federated `/search`; Agent C linking)
- **serving-atr-inference gateway ✅** (auth, live registry, kraken via `/ocr`; loud 4xx tracked in serving-atr-inference#21)
- **Agentic HITL ✅ as machinery** (Gates 1–3, RunState invalidation, uncertainty rules, feedback log, routing prior, optional LLM router) — auto-wiring the gates into the pipeline is a Phase-0 follow-up
- **Publishing to GitHub + Pages ✅** (opt-in; see "Publishing outputs")
- NL/SitL planner prototype, semantic clustering, agent tool registry ✅ (built + tested; Discord wiring lands in Phase 1)
- **Multi-engine ensemble + no-merge band ✅** (#298/#300; one recognition path for `/run` and grouped orders)
- **Gate-2 voting + preference log ✅** (#313/#332; comparisons only, never text — see *Quality without ground truth*)
- **Live progress board ✅** (#287/#289; per-candidate events, so an engine failure is visible instead of averaged away)
- **Measurement layer ✅ as machinery** (#326: coverage, selection agreement, `regret_cer`, Bradley–Terry engine strength) — it needs *votes*, not code: with one manuscript voted on, every figure is still n≈1, and `ENABLE_ROUTING_PRIOR` stays off until preferences span several documents

## Roadmap — Phase 1 (in progress)

Tracked in **[#230](https://github.com/thodel/agentic_historian/issues/230)**;
designed for parallel bots in three waves (each issue carries its order, tests,
and a live-verification step):

| Epic | Goal | Issues |
|---|---|---|
| [#218](https://github.com/thodel/agentic_historian/issues/218) P1-A | Search + cross-document **entity pages** on the Pages catalogue, `/entity` command | #221 → #222/#223 → #224 |
| [#219](https://github.com/thodel/agentic_historian/issues/219) P1-B | **Semi-automatic reprocessing**: RunState as single source of truth, `reprocess()` API, hot-folder/gate triggers | #225 → #226 → #227 |
| [serving-atr-inference#22](https://github.com/thodel/serving-atr-inference/issues/22) P1-C | Weekly **model discovery** (HuggingFace + Zenodo) → curated report issue | #23 → #24 (that repo) |
| [#220](https://github.com/thodel/agentic_historian/issues/220) P1-D | **MCP onboarding via Discord**: `/mcp_propose` probes an endpoint and opens a reviewed registry PR (never hot-loads) | #228 → #229 |

Wave 1 issues (#221, #225, #228, serving-atr#23) are independent and can start
immediately; Wave-3 issues all touch `bot.py` and must run sequentially.
