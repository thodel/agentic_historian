"""
config.py — Zentrale Konfiguration für den Agentic Historian.
Lädt Umgebungsvariablen und stellt Default-Werte bereit.
"""

import os
import re
from pathlib import Path
from dotenv import dotenv_values, load_dotenv
from loguru import logger

# BASE_DIR = Python-Package-Wurzel; REPO_ROOT = project root (git repo or working dir)
BASE_DIR = Path(__file__).parent.resolve()
# When installed as a package, BASE_DIR is site-packages; use CWD as REPO_ROOT fallback.
# Set AGENTIC_HISTORIAN_ROOT env var to override for non-standard layouts.
_REPO_ROOT_candidate = Path(os.environ.get(
    "AGENTIC_HISTORIAN_ROOT",
    str(BASE_DIR.parent if (BASE_DIR / "../.git").exists() else Path.cwd()),
)).resolve()
REPO_ROOT = _REPO_ROOT_candidate

# Env-Dateien laden. WICHTIG: echte Prozess-Umgebungsvariablen (systemd
# `Environment=`/`EnvironmentFile=`, exportierte Secrets) haben IMMER Vorrang und
# werden NIE von einer .env-Datei überschrieben (override=False). Andernfalls
# würden (u.U. veraltete, eingecheckte) .env-Werte die echte Server-Umgebung
# stompen (#106).
#
# Reihenfolge = Priorität UNTER den Dateien: bei override=False gewinnt die
# zuerst geladene Datei. Die dedizierten GPUStack-Secrets (.env.gpustack) haben
# daher Vorrang vor generischen .env-Dateien und werden zuerst geladen.
#: Which file first defined each key, so a value can be traced back to the file
#: that has to be edited. Only keys that came from a file are in here: a key from
#: the real process environment never reaches the loop.
ENV_SOURCE: dict[str, Path] = {}

#: The files, in priority order. Named so :func:`reload_env` uses exactly this
#: order — a reload that read them in another order would hand a different file
#: the win and change a value nobody edited.
ENV_FILES: tuple[Path, ...] = (
    REPO_ROOT / ".env.gpustack",
    REPO_ROOT / ".env",
    BASE_DIR / ".env",
)

for _env_file in ENV_FILES:
    if _env_file.exists():
        for _k in dotenv_values(_env_file):
            if _k not in os.environ and _k not in ENV_SOURCE:
                ENV_SOURCE[_k] = _env_file
        load_dotenv(_env_file, override=False)


def reload_env() -> dict[str, list[str]]:
    """Re-read the `.env` files for the keys that came **from a file**.

    Returns ``{"changed": [...], "unchanged": [...], "from_environment": [...]}``
    — key **names** only. A reload that printed values would put every secret in
    the channel it was invoked from.

    Why this is not ``importlib.reload``, and not dotenv with overriding on
    ──────────────────────────────────────────────────────────────────────────
    ``load_dotenv(override=False)`` writes a file's value into ``os.environ`` on
    the first load, so every later load finds the key already present and leaves
    it alone. Measured::

        1. first load            : alt
        2. file changed, load    : alt   <- override=False
        3. with overriding on    : neu

    A plain re-import therefore reports success and changes nothing, which is
    worse than no reload at all. And switching overriding on across the board is
    the opposite error: it would let a committed template overwrite a password
    systemd supplied, which is the precedence #106 exists to protect — and the
    guard in `test_ah_106_dotenv_no_override.py` greps this file's source for
    that flag, prose included. It is right to be that blunt, so this paragraph
    describes the flag instead of spelling it.

    So the keys are taken from ``ENV_SOURCE``, which holds exactly those whose
    value came from a file. Anything not in it came from the real process
    environment and is **not touched** — it is reported under
    ``from_environment`` instead, because "I reloaded and nothing changed" and
    "that key cannot be reloaded from here" are different answers and the second
    is the one that tells you where to go (``/proc/<pid>/environ``, the unit's
    ``EnvironmentFile=``).

    The caller still has to re-read the module attributes it cares about; this
    refreshes ``os.environ`` and the module-level constants derived from it.
    """
    from_file = dict(ENV_SOURCE)
    before = {key: os.environ.get(key) for key in from_file}
    for key in from_file:
        os.environ.pop(key, None)
    for env_file in ENV_FILES:
        if env_file.exists():
            load_dotenv(env_file, override=False)

    changed, unchanged = [], []
    for key, was in before.items():
        (changed if os.environ.get(key) != was else unchanged).append(key)
    # A key whose file no longer defines it would now be gone from os.environ
    # entirely. Put the old value back rather than silently unsetting a secret
    # mid-run: a reload may not be a way to lose configuration.
    vanished = [key for key in changed
                if os.environ.get(key) is None and before[key] is not None]
    for key in vanished:
        os.environ[key] = before[key]
        changed.remove(key)
        unchanged.append(key)
        logger.warning(f"[config] {key} is no longer in any .env file — "
                       f"kept the running value")
    _refresh()
    logger.info(f"[config] reload: {len(changed)} changed, "
                f"{len(unchanged)} unchanged")
    return {"changed": sorted(changed), "unchanged": sorted(unchanged),
            "from_environment": sorted(k for k in os.environ
                                       if k.startswith(("SWITCHDRIVE_",
                                                        "NEXTCLOUD_",
                                                        "ATR_", "GPUSTACK_"))
                                       and k not in from_file)}


def _refresh() -> None:
    """Recompute the module constants that :func:`reload_env` can change.

    Deliberately a short, explicit list rather than a re-exec of the module:
    re-executing would rebuild ``ENV_SOURCE`` from an ``os.environ`` that now
    contains the file values, so every key would look as though it came from the
    environment — the reload would destroy the one piece of information that
    makes the next reload possible.
    """
    globals().update(
        SWITCHDRIVE_URL=_get("SWITCHDRIVE_URL",
                             "https://drive.switch.ch/remote.php/webdav"),
        SWITCHDRIVE_USER=_get("SWITCHDRIVE_USER", ""),
        SWITCHDRIVE_PASS=_get("SWITCHDRIVE_PASS", ""),
        SWITCHDRIVE_REMOTE_DIR=_get("SWITCHDRIVE_REMOTE_DIR",
                                    "agentic_historian_hotfolder"),
        NEXTCLOUD_SHARE_URL=_get("NEXTCLOUD_SHARE_URL", ""),
        NEXTCLOUD_SHARE_PASS=_get("NEXTCLOUD_SHARE_PASS", ""),
        NEXTCLOUD_REMOTE_DIR=_get("NEXTCLOUD_REMOTE_DIR", ""),
        ATR_API_KEY=_get("ATR_API_KEY", ""),
    )


#: What an unfilled template value looks like.
#:
#: The angle-bracket branch is narrower than "anything in brackets", because
#: plenty of real values are written that way. A Discord mention is
#: ``<@817396581317738546>``, a channel is ``<#…>``, a role ``<@&…>``, a custom
#: emoji ``<:name:id>`` — the first version of this check reported the bot's own
#: mention as an unfilled template the first time it ran on tei. So the brackets
#: have to contain a *word*: it starts with a letter, and holds only letters,
#: digits, spaces, hyphens, dots and underscores. ``<Passwort>`` and
#: ``<your-token>`` match; ``<@123…>`` and ``<noreply@example.org>`` do not.
#:
#: A false positive costs trust in the check, and a check nobody trusts is worse
#: than no check — it is one more line to scroll past when something is actually
#: wrong.
_PLACEHOLDER_RE = re.compile(
    r"^(<[A-Za-z][\w .\-]*>|changeme|change_me|your[-_ ].+|todo|xxx+|\.\.\.)$",
    re.IGNORECASE,
)


def unfilled_secrets() -> dict[str, str]:
    """``{key: why}`` for every configured value that is still a template.

    **Why this is worth a check of its own.** A missing secret announces itself:
    the call fails with 401 and the variable is empty. A secret whose value is
    the literal string ``<Passwort>`` is set, is truthy, passes every "is it
    configured" test in this repo, and fails at the far end of a network call
    with an error about the server.

    It also hides behind the load order. The first file to define a key wins
    (``override=False``), so a committed template at ``.env.gpustack`` shadows the
    real password three files later and nothing says so — which is exactly what
    happened on tei on 2026-09-18: two files carried ``<Passwort>`` and the third
    the real one, and the mount failed with "rejected Basic challenge". So this
    names the **file**, not just the key: knowing which of three copies to edit is
    the whole problem.
    """
    found: dict[str, str] = {}
    for key, source in ENV_SOURCE.items():
        value = os.getenv(key, "")
        if value and _PLACEHOLDER_RE.match(value.strip()):
            try:
                where = source.relative_to(REPO_ROOT)
            except ValueError:
                where = source
            found[key] = f"still a template ({value.strip()}) in {where}"
    return found


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# ── Discord ──────────────────────────────────────────────────────────────────
DISCORD_BOT_TOKEN = _get("DISCORD_BOT_TOKEN")
# Numeric role ID that is allowed to run sensitive commands (/run, /pull, etc.).
# Set to 0 or empty to disable role-gating (NOT recommended for shared servers).
REQUIRED_DISCORD_ROLE_ID: int | None = int(_get("REQUIRED_DISCORD_ROLE_ID", "0")) or None
# Numeric role ID for admin-only operations (/update).  Defaults to the same
# role as REQUIRED_DISCORD_ROLE_ID (allows the same people to update); set to 0
# or empty to disable the admin gate (not recommended).
REQUIRED_ADMIN_ROLE_ID: int | None = int(_get("REQUIRED_ADMIN_ROLE_ID", "0")) or None

#: The guild to register slash commands in. Set it, and a new command is usable
#: the moment the bot restarts.
#:
#: Empty keeps the old behaviour — **global** commands, which Discord propagates
#: with a delay of up to an hour. That is not a theoretical cost: `/pull_preflight`
#: was merged, the bot was updated, and the command was still not there, with no
#: way to tell "the code is missing" from "Discord is still rolling it out"
#: (#589). A research bot lives on one server, so naming it is strictly better.
DISCORD_GUILD_ID: int | None = int(_get("DISCORD_GUILD_ID", "0")) or None
if REQUIRED_ADMIN_ROLE_ID is None:  # "0" → None fallback, inherit from ROLE_ID
    REQUIRED_ADMIN_ROLE_ID = REQUIRED_DISCORD_ROLE_ID

# ── GitHub ───────────────────────────────────────────────────────────────────
GITHUB_TOKEN = _get("GITHUB_TOKEN")
GITHUB_REPO = _get("GITHUB_REPO", "thodel/agentic_historian")
GITHUB_BRANCH = _get("GITHUB_BRANCH", "main")

# ── GitHub output publishing (#200) ──────────────────────────────────────────
# Processed outputs are committed to a dedicated PUBLIC repo (rendered as a
# GitHub Pages catalogue, #201) for inspection/versioning/sharing. Only text
# artifacts are published — source images are never committed. Opt-in; when off,
# the pipeline is byte-identical.
GITHUB_OUTPUT_REPO = _get("GITHUB_OUTPUT_REPO", "thodel/agentic-historian-outputs")

#: Where the published catalogue is served. Empty = derive it from
#: GITHUB_OUTPUT_REPO by the GitHub Pages convention
#: (``https://<owner>.github.io/<repo>``), which is where it lives today. Set it
#: for a custom domain: the convention would then be wrong, and a wrong base
#: makes every `/find` link wrong at once (#397).
CATALOGUE_BASE_URL = _get("CATALOGUE_BASE_URL", "").rstrip("/")
GITHUB_OUTPUT_BRANCH = _get("GITHUB_OUTPUT_BRANCH", "main")
ENABLE_GITHUB_PUBLISH = _get("ENABLE_GITHUB_PUBLISH", "false").lower() == "true"
# This code repo — where /mcp_propose opens a reviewed PR adding a source (#229).
GITHUB_CODE_REPO = _get("GITHUB_CODE_REPO", "thodel/agentic_historian")
GITHUB_CODE_BRANCH = _get("GITHUB_CODE_BRANCH", "main")

# ── Where recognised text goes ───────────────────────────────────────────────
# One destination, named here rather than retyped into every publish command:
# the Lassberg edition repository. The scans stay in the Nextcloud share (which
# is mounted, not copied) and the readings are published to git — the two halves
# of the corpus live in different places on purpose, and only the text half is
# ours to distribute. `publish-batch` defaults to these and takes overrides.
GITHUB_TEXT_REPO = _get("GITHUB_TEXT_REPO", "michaelscho/lassberg")
GITHUB_TEXT_BRANCH = _get("GITHUB_TEXT_BRANCH", "main")
GITHUB_TEXT_PATH = _get("GITHUB_TEXT_PATH", "data/textrecognition")
# The fork the commits land in. The edition is somebody else's repository and its
# maintainer decides what enters it: `publish-batch` proposes a pull request from
# here instead of pushing, which needs no write access to GITHUB_TEXT_REPO at all.
# Set it equal to GITHUB_TEXT_REPO to branch inside the repository itself.
GITHUB_TEXT_FORK = _get("GITHUB_TEXT_FORK", "thodel/lassberg")
# The edition's correspondence data: data/letters of a GITHUB_TEXT_REPO
# checkout, one TEI file per letter whose correspDesc names the sender with a
# GND. It is the only *record* of whose hand a page is in — everything else in
# this repository infers it from a dateline — so where it reaches, it decides.
#   git clone --depth 1 https://github.com/michaelscho/lassberg
# Empty = no register, and the dateline inference is all there is.
LASSBERG_REGISTER = _get("LASSBERG_REGISTER", "")

# Base URL for the source image of a published doc (#208). If set, each doc's
# page links back to "<SOURCE_URL_BASE>/<filename>" — point it at a SwitchDrive
# share, a IIIF image server, or any public mirror. Empty = no source link.
SOURCE_URL_BASE = _get("SOURCE_URL_BASE", "").rstrip("/")

# ── Knowledge Hub (MCP-federated) ────────────────────────────────────────────
# The Knowledge Hub is realised as a federation of MCP servers (one per
# authority source), not a local store. The declarative source registry lives
# in knowledge_hub/mcp_registry.py; adding a source = adding a registry entry.
# Only the host base is configurable here, so the whole federation can be
# repointed (staging/prod) with one env var. See docs/knowledge_hub.md.
MCP_BASE_URL = _get("MCP_BASE_URL", "https://tei.dh.unibe.ch/mcp").rstrip("/")
# Per-request timeout (seconds) when querying an MCP source.
MCP_TIMEOUT = float(_get("MCP_TIMEOUT", "15"))
# Agent C links PERSON entities via the MCP federation (#92). When the
# federation is unreachable it degrades gracefully to the local hub chain.
ENABLE_MCP_LINKING = _get("ENABLE_MCP_LINKING", "true").lower() == "true"

# ── GPUStack (unibe) ─────────────────────────────────────────────────────────
# Verified served models (GET /v1/models, 2026-06-26):
#   VLMs : qwen3-vl-30b-a3b-instruct (64K), qwen3-vl-8b-instruct (31K),
#          internvl3-8b-instruct (64K)
#   LLMs : gpt-oss-120b (78K, reasoning), minimax-m2.7 (98K),
#          qwen3-coder-30b-a3b-instruct (146K)
#   Embeddings: qwen3-embedding-0.6b, granite-embedding-107m-multilingual
#   Reranker  : jina-reranker-v2-base-multilingual
# Re-checked 2026-09-07: the qwen3-vl-* entries are no longer served. The vision
# role moves to qwen3.8-27b, which is vision-capable and measured at 27.7 % CER
# on the Inzigkofen page set (docs/PIPELINE_CONSEQUENCES.md §1, §6).
GPUSTACK_BASE_URL = _get("GPUSTACK_BASE_URL", "https://gpustack.unibe.ch/v1")
GPUSTACK_API_KEY = _get("GPUSTACK_API_KEY")

# Role-based model routing (team decision 2026-06-26):
#   VISION  → Agent A (HTR) and Agent B (source description)
#   TEXT/LLM→ Agent C (NER), corpus/meta agents, reconciliation, care-flag
#   ORCH    → reserved for the future natural-language / SitL orchestrator (WP1)
GPUSTACK_MODEL_VISION = _get("GPUSTACK_MODEL_VISION", "qwen3.8-27b")
GPUSTACK_MODEL_TEXT = _get("GPUSTACK_MODEL_TEXT", "gpt-oss-120b")
GPUSTACK_MODEL_ORCHESTRATOR = _get("GPUSTACK_MODEL_ORCHESTRATOR", "minimax-m2.7")
ORCHESTRATOR_LLM_ENABLED = _get("ORCHESTRATOR_LLM_ENABLED", "false").lower() == "true"

# Retrieval models (planned: hub linking + corpus semantic analysis)
GPUSTACK_MODEL_EMBEDDING = _get("GPUSTACK_MODEL_EMBEDDING", "qwen3-embedding-0.6b")
GPUSTACK_MODEL_RERANKER = _get("GPUSTACK_MODEL_RERANKER", "jina-reranker-v2-base-multilingual")

# HLS-DHS live linking. The legacy hits4.php endpoint is dead (redirects to 404);
# disabled by default until the current HLS search/LOD API is wired (see issue).
ENABLE_HLS_LOOKUP = _get("ENABLE_HLS_LOOKUP", "false").lower() == "true"
# Local HLS dump used for offline linking (no web calls). JSON: see knowledge_hub/hub.py.
HLS_DATA_PATH = BASE_DIR / _get("HLS_DATA_PATH", "knowledge_hub/data/hls.json")

# ── SwitchDrive (WebDAV ingestion) ───────────────────────────────────────────
# App password: drive.switch.ch → Settings → Security → create app password.
# Official SWITCHdrive WebDAV root (fixed path, no username) per help.switch.ch.
SWITCHDRIVE_URL = _get("SWITCHDRIVE_URL", "https://drive.switch.ch/remote.php/webdav")
SWITCHDRIVE_USER = _get("SWITCHDRIVE_USER", "")
SWITCHDRIVE_PASS = _get("SWITCHDRIVE_PASS", "")
SWITCHDRIVE_REMOTE_DIR = _get("SWITCHDRIVE_REMOTE_DIR", "agentic_historian_hotfolder")

# ── Nextcloud public share (WebDAV ingestion) ────────────────────────────────
# A *public share*, not an account: the share token is the WebDAV username and
# NEXTCLOUD_SHARE_PASS its password (empty for a share without one). See
# utils/nextcloud.py — paste the browser URL, any of Nextcloud's shapes parses.
NEXTCLOUD_SHARE_URL = _get("NEXTCLOUD_SHARE_URL", "")
NEXTCLOUD_SHARE_PASS = _get("NEXTCLOUD_SHARE_PASS", "")
# Subfolder inside the share to ingest ("" = the share root).
NEXTCLOUD_REMOTE_DIR = _get("NEXTCLOUD_REMOTE_DIR", "")

# gpt-oss-120b is a REASONING model: it spends tokens on reasoning_content before
# emitting content. Give text calls a generous budget or content comes back null.
GPUSTACK_TEXT_MAX_TOKENS = int(_get("GPUSTACK_TEXT_MAX_TOKENS", "4096"))

# ── HTR / OCR ────────────────────────────────────────────────────────────────
HTR_QUALITY_THRESHOLD = float(_get("HTR_QUALITY_THRESHOLD", "0.75"))
MAX_RETRIES = int(_get("MAX_RETRIES", "3"))
# Anti-repetition decoding for the HTR VLM call only (#275).  These values are
# passed explicitly by agent_a.dual_pipeline._run_vlm; other vision, text,
# reconciliation, and orchestrator calls keep their existing decoding settings.
VLM_FREQUENCY_PENALTY = float(_get("VLM_FREQUENCY_PENALTY", "0.2"))
VLM_PRESENCE_PENALTY = float(_get("VLM_PRESENCE_PENALTY", "0.0"))
# Add a small additive routing prior from historian feedback to kraken model
# scores (#155). OFF by default → byte-identical scoring; the prior is capped
# below a full script match so it only breaks near-ties.
# Agent B's description is the INPUT to model selection, so its sampling decides
# which engines a page is read with. Free sampling made the same manuscript come
# back as "Kursive", then "Fraktur", then "Gothische Textura" across three runs of
# identical images — three different script families, and three different winning
# model pools (#379). Deterministic by default; raise only for experiments.
# Reuse Agent B's description when the same page images are seen again (#387).
# On by default: it is what makes two runs of the same document comparable at all.
# Off for experiments where a fresh description is the point.
AGENT_B_CACHE = _get("AGENT_B_CACHE", "true").lower() == "true"

AGENT_B_TEMPERATURE = float(_get("AGENT_B_TEMPERATURE", "0.0"))
# Sent when set; backends may ignore it. Temperature 0 is what actually pins the
# output, the seed only helps where the endpoint honours it.
AGENT_B_SEED: int | None = int(_get("AGENT_B_SEED", "0")) or None

ENABLE_ROUTING_PRIOR = _get("ENABLE_ROUTING_PRIOR", "false").lower() == "true"

# ── Multi-engine HTR fusion (#237) ───────────────────────────────────────────
# Fuse all engine candidates (VLM/kraken/TrOCR/PARTY) into a best-fit
# transcription via consensus + bounded LLM arbitration. OFF by default → the
# pipeline keeps the 2-way reconcile behaviour (byte-identical). Strategy:
# "vote" (align+vote+arbitrate) | "llm_merge" (LLM merges the raw texts).
ENABLE_MULTI_ENGINE_FUSION = _get("ENABLE_MULTI_ENGINE_FUSION", "false").lower() == "true"
FUSION_STRATEGY = _get("FUSION_STRATEGY", "vote")
# Agreement gate: when max pairwise CER between candidates is below this threshold,
# skip LLM arbitration and take the consensus directly (cost control).
FUSION_AGREEMENT_CER_THRESHOLD = float(_get("FUSION_AGREEMENT_CER_THRESHOLD", "0.05"))

# Multi-engine ENSEMBLE for grouped/multi-page orders (#272). When ON, each page
# of a grouped order runs ≥ ENSEMBLE_MIN_ENGINES recognitions (VLM + best kraken +
# best TrOCR); if they disagree (max pairwise CER > ENSEMBLE_AGREEMENT_CER) the

# #300: above this max-pairwise-CER the ensemble does NOT merge — it returns the
# best single candidate verbatim. Majority-voting assumes engines err independently
# around a shared signal; at real disagreement there is no shared signal, so the
# vote returns noise. Measured on BAT_664 at 70% CER the fused text was worse than
# the best single engine — TrOCR's good reading with its good parts voted out.
ENSEMBLE_NO_MERGE_CER = float(_get("ENSEMBLE_NO_MERGE_CER", "0.35"))
# ensemble adds the next-ranked kraken/TrOCR model, up to ENSEMBLE_MAX_LOOPS extra
# loops, then fuses. OFF by default → grouped orders keep the VLM-only behaviour.
# Token budget for fusion's arbitration call. The default TEXT model is a REASONING
# model: it spends tokens on reasoning before emitting content, so too small a
# budget returns nothing and the client retries at double — the page pays for both.
# Measured on saa-0428/001r: 4096 returned empty (finish=length), the 8192 retry
# succeeded, and fusion took 51.8s, half of a 104s page (#406). Starting at the
# budget that works removes an attempt that structurally cannot finish.
FUSION_ARBITRATE_MAX_TOKENS = int(_get("FUSION_ARBITRATE_MAX_TOKENS", "8192"))

#: Whether fusion asks an LLM to resolve the columns where candidates disagree.
#: False resolves them with the most-backed reading and makes no call.
#:
#: Default True, which is the behaviour as measured — not an endorsement of it.
#: #406 scored 13 pages of the 2021 Federal Council minutes against ground
#: truth: 4.82 % CER voting alone against 5.01 % arbitrated, identical output on
#: 7 of 13 pages, better on 3 and worse on 3, at 3.7 s a page (42 s on the tei
#: page that opened the issue). That reads as "switch it off", and the reason it
#: is still on is the sample: 13 pages of three CTC candidates are what #416
#: cites for widening the measurement before changing the pipeline, and
#: production fuses three to seven, often with a VLM whose errors are
#: distributed differently — the condition under which voting won on the
#: 150-line sample.
#:
#: `FusionResult.changed` is what makes the decision answerable from real runs:
#: arbitrated slots where the model chose against the vote. Arbitration that
#: never changes anything is an LLM agreeing with a majority at 3.7 s a page.
FUSION_ARBITRATE = _get("FUSION_ARBITRATE", "true").lower() not in ("0", "false", "no")

ENABLE_ENSEMBLE_HTR = _get("ENABLE_ENSEMBLE_HTR", "false").lower() == "true"

# ── V-3 (#289): live progress board in Discord ───────────────────────────────
# Stream the pipeline's PhaseEvents (#288) into ONE editing Discord message
# (progress.format_board, #287). Default OFF → zero behaviour change. The channel
# id is the fallback for background runs (hot-folder / SwitchDrive orders) that
# have no invoking command context; unset → those runs stay log-only.
ENABLE_VERBOSE_PROGRESS = _get("ENABLE_VERBOSE_PROGRESS", "false").lower() == "true"
VERBOSE_PROGRESS_CHANNEL_ID: int | None = int(_get("VERBOSE_PROGRESS_CHANNEL_ID", "0")) or None
# Start with the most promising ENSEMBLE_MIN_ENGINES (VLM + best kraken + best
# TrOCR), then add further candidates while they disagree (#284). Pool depth is
# ENSEMBLE_PER_ENGINE per HTR engine, so the pool is 1 + 2*PER_ENGINE picks;
# MAX_LOOPS caps how many of the extras actually run. Each extra candidate is real
# GPU inference (~30–60 s/model/page) — this is the coverage/cost dial.
# #390: two, not three. Where the first two candidates already agree the third
# buys nothing — the machinery for disagreement is the escalation loop below, and
# it runs whenever they do not. MAX_LOOPS is raised by one in exchange, so a
# disagreeing page reaches exactly the depth it reached before: 3+4 and 2+5 both
# cap at seven picks, and after the first escalation the state is identical to
# the old initial batch. The saving falls entirely on agreeing pages.
ENSEMBLE_MIN_ENGINES = int(_get("ENSEMBLE_MIN_ENGINES", "2"))
ENSEMBLE_PER_ENGINE = int(_get("ENSEMBLE_PER_ENGINE", "3"))
ENSEMBLE_MAX_LOOPS = int(_get("ENSEMBLE_MAX_LOOPS", "5"))
ENSEMBLE_AGREEMENT_CER = float(_get("ENSEMBLE_AGREEMENT_CER", "0.30"))
# #389: the initial batch runs its engine calls concurrently — they are
# independent network calls (ATR gateway / GPUStack), so page latency approaches
# the slowest engine instead of the sum. Bounded because the gateway box is
# shared; 1 restores the sequential behaviour. The feedback loop stays
# sequential — each extra pick is a decision made from the previous results.
ENSEMBLE_CONCURRENCY = int(_get("ENSEMBLE_CONCURRENCY", "3"))
# #402: the escalation loop was 64% of a run and strictly serial — each added
# model waited for the previous one before the code decided whether to add
# another. It now sends this many at a time. Two is the honest compromise the
# issue names: a batch can overshoot when the first pick alone would have
# settled the disagreement, and the wider the batch the more often that happens.
# The overshoot is counted on the result rather than hidden, because an extra
# candidate changes what the Gate-2 card offers (#313). 1 restores the serial
# behaviour exactly.
ENSEMBLE_ESCALATION_BATCH = int(_get("ENSEMBLE_ESCALATION_BATCH", "2"))

# ── ATR gateway (serving-atr-inference on idhefix) ──────────────────────────
# Recognition backend: kraken / TrOCR / party / vllm behind one FastAPI gateway
# (verified contract: GET /health, GET /models, POST /segment, /recognize, /ocr).
# Reachable only from tei on port 8200, gated by a static X-API-Key.
#
# ATR_GATEWAY_URL supersedes the legacy KRAKEN_SERVICE_URL; the latter is kept
# as a backward-compatible fallback so existing .env files keep working.
KRAKEN_SERVICE_URL = _get("KRAKEN_SERVICE_URL", "http://localhost:8765")
ATR_GATEWAY_URL = (_get("ATR_GATEWAY_URL") or KRAKEN_SERVICE_URL).rstrip("/")
# Static shared secret sent as the `X-API-Key` header. Empty = unauthenticated
# (only works against a local/dev gateway that has auth disabled).
ATR_API_KEY = _get("ATR_API_KEY")
# How long to wait for one gateway call. Must not be TIGHTER than the gateway's
# own engine budget (300s, HTTPEngineClient in serving-atr-inference), or we hang
# up on work the gateway is still doing and record a phantom "timed out" for an
# engine that was about to answer. That is exactly what happened to
# trocr-medieval-escriptmask on 2026-07-16: the old hardcoded 120s expired while
# the gateway ran a cold model load plus ~4s/line across a full page — the engine
# itself is healthy and answers a line in ~7s.
ATR_HTTP_TIMEOUT = float(_get("ATR_HTTP_TIMEOUT", "300"))
# Announce training outcomes and unexplained GPU memory into a channel (#418).
# Default OFF and no channel: a watcher that posts somewhere nobody chose is a
# watcher that gets muted, and muting it removes the pull commands' value too.
# Set both to switch it on.
ATR_WATCH_CHANNEL_ID: int | None = int(_get("ATR_WATCH_CHANNEL_ID", "0")) or None
ENABLE_ATR_WATCH = (_get("ENABLE_ATR_WATCH", "false").lower() == "true"
                    and ATR_WATCH_CHANNEL_ID is not None)
# How often to look. A training run is measured in hours, so this is not a
# latency problem; five minutes keeps the gateway quiet and still catches a
# failure long before anybody would have thought to ask.
ATR_WATCH_INTERVAL_S = float(_get("ATR_WATCH_INTERVAL_S", "300"))
# Prefixed to the announcements that warrant interrupting someone — a failed run,
# or memory nothing accounts for. Discord only pushes to a phone reliably when the
# message mentions the reader, so without this the watcher reaches a laptop and
# not a pocket. Raw mention syntax, because a user and a role differ: "<@123…>"
# for a person, "<@&123…>" for a role. Empty = never mention, and then a failure
# is something you find when you next look at the channel.
ATR_WATCH_MENTION = _get("ATR_WATCH_MENTION", "").strip()
# NB: defined below, next to DATA_DIR — it does not exist yet at this point.

# ── Hot Folder ───────────────────────────────────────────────────────────────
ENABLE_HOT_FOLDER_WATCH = _get("ENABLE_HOT_FOLDER_WATCH", "true").lower() == "true"
HOT_FOLDER = BASE_DIR / _get("HOT_FOLDER", "data/hot_folder")
PROCESSED_FOLDER = BASE_DIR / _get("PROCESSED_FOLDER", "data/hot_folder/processed")
AUTO_RESUME_AFTER_GATE = _get("AUTO_RESUME_AFTER_GATE", "false").lower() == "true"
HOT_FOLDER_DEBOUNCE_SEC = float(_get("HOT_FOLDER_DEBOUNCE_SEC", "2.0"))
WATCHED_EXTENSIONS = frozenset(
    e.strip().lower() for e in _get("WATCHED_EXTENSIONS", ".jpg,.jpeg,.png,.tif,.tiff,.pdf").split(",")
    if e.strip()
)

# ── Datenverzeichnisse ───────────────────────────────────────────────────────
DATA_DIR = BASE_DIR / "data"
# What the ATR watcher has already announced (#418), see above.
ATR_WATCH_STATE = DATA_DIR / "atr_watch.json"

# ── Credential watch (#589) ──────────────────────────────────────────────────
#: Poll interval for the mailbox-credential watcher. Hourly: an App Passcode
#: does not break every minute, and the cost of noticing an hour late is far
#: below the two hours the unwatched 401 of 2026-10-09 actually cost.
CREDENTIAL_WATCH_INTERVAL_S = float(_get("CREDENTIAL_WATCH_INTERVAL_S", "3600"))

# ── Corpus batch runner (R2, #392) ───────────────────────────────────────────
#: Documents in flight at once. Conservative, because the gateway serves one
#: model at a time on one GPU, so more workers than this mostly queue inside it;
#: `--workers` overrides per run.
BATCH_WORKERS = int(_get("BATCH_WORKERS", "2"))
#: Attempts before a document goes to the manifest's dead letter and the run
#: carries on without it.
BATCH_MAX_ATTEMPTS = int(_get("BATCH_MAX_ATTEMPTS", "3"))
#: Where the watcher announces. Empty = the watcher does not run, which is right
#: for any host that is not the one doing the ingesting.
CREDENTIAL_WATCH_CHANNEL_ID: int | None = (
    int(_get("CREDENTIAL_WATCH_CHANNEL_ID", "0")) or None)
ENABLE_CREDENTIAL_WATCH = CREDENTIAL_WATCH_CHANNEL_ID is not None
TRANSCRIPTIONS_DIR = DATA_DIR / "transcriptions"
DESCRIPTIONS_DIR = DATA_DIR / "descriptions"
OUTPUTS_DIR = DATA_DIR / "outputs"
# Where a Nextcloud share is mirrored to (utils/nextcloud.py). Kept apart from the
# hot folder on purpose: the hot folder is watched and consumed, and a mirror of a
# share is neither — it is the corpus a batch reads repeatedly, once per model.
NEXTCLOUD_STAGING_DIR = Path(_get("NEXTCLOUD_STAGING_DIR", str(DATA_DIR / "nextcloud")))
# Per-request timeout for the share's WebDAV calls. httpx defaults to 5 s, which
# webdav4 inherits, and a PROPFIND over a folder of several hundred scans does not
# finish in five seconds on a server that stats each entry — the walk died on one
# after nine minutes of work (2026-09-15). This is a directory listing budget, not
# a transfer budget: downloads stream and are not covered by it.
NEXTCLOUD_TIMEOUT = float(_get("NEXTCLOUD_TIMEOUT", "120"))
# How often a listing that times out is tried again before the walk gives up. A
# slow directory is usually slow once; a directory that fails three times is a
# problem worth stopping for, because silently skipping it would mean a corpus
# quietly missing pages.
NEXTCLOUD_LS_ATTEMPTS = int(_get("NEXTCLOUD_LS_ATTEMPTS", "3"))

#: How long a cached share listing stays usable, in seconds. 0 disables it.
#:
#: The enumeration is one PROPFIND per folder — 24 minutes for this share's 1000
#: folders, measured three times on 2026-09-21 — and it runs before a batch reads
#: a single page, at every start. For a 25-page smoke run that is more waiting
#: than work, and a resumed run pays it again for a list it already had.
#:
#: **Forty-eight hours, not twelve.** Twelve was calibrated against the material —
#: a scanning project's output folder, where pages arrive in batches days apart —
#: and not against our own runs, which is the mistake. A corpus run over this share
#: takes 10 to 13 hours; it has to be restarted at least once; and the restart then
#: lands just past the expiry of the listing its own first attempt wrote. Measured
#: three times:
#:
#:   2026-09-24 08:56  restart 13.1 h after the walk — expired by one hour
#:   2026-10-01 19:43  a THREE-page smoke run spent 24 minutes enumerating
#:
#: A cache whose lifetime is shorter than the job it serves is a cache that is
#: always cold exactly when it is needed. Two days outlives any single run and any
#: same-evening restart.
#:
#: The cost of being wrong is unchanged and still bounded: a page added since the
#: listing is simply not read until the cache expires, `--no-listing-cache` forces
#: a fresh walk, and a walk that skipped an unreadable folder is never cached at
#: all (see utils/nextcloud.WalkState).
NEXTCLOUD_LISTING_TTL_S = float(_get("NEXTCLOUD_LISTING_TTL_S", str(48 * 3600)))

#: Where harvested ground truth lives. One root, so an MCP caller names a harvest
#: rather than a path and cannot reach outside it — the same containment rule the
#: run names follow, and for the same reason: the server is reachable with a bearer
#: token and that validation is what stands between the token and the box.
GT_ROOT = Path(_get("GT_ROOT", str(DATA_DIR / "gt")))

#: Convert archival scans to a JPEG working copy as they are mirrored. The
#: Lassberg digitisations are uncompressed TIFF — 25 MB a page, ~160 GB for the
#: share against the 92 GB tei has — and the first full pull filled the disk at
#: page 899. Full resolution is kept; only the encoding changes.
NEXTCLOUD_CONVERT = _get("NEXTCLOUD_CONVERT", "true").lower() not in ("0", "false", "no")

#: Where working copies of pages read off a **mounted** share are kept.
#:
#: Mirroring the share to tei is no longer the plan: the GWDG Nextcloud is
#: mounted and the corpus read in place, which removes the copy and its 160 GB
#: but makes every page open a network transfer of an uncompressed 25 MB TIFF.
#: With a cache directory set, each page crosses the network once and every later
#: read — a retry, the next model's pass, a re-run — is local. Empty = no cache,
#: pages are read where they are, which is right for a corpus already on disk.
ATR_PAGE_CACHE = Path(_get("ATR_PAGE_CACHE", "")) if _get("ATR_PAGE_CACHE", "") else None

#: Where the cache is when nobody names one — ``ATR_PAGE_CACHE`` if it is set,
#: and this path otherwise.
#:
#: **One answer, because three rounds went into having two.** `cache_dir_for` on
#: the MCP path already fell back here, so a `dav:` run started from a session
#: worked; the CLI had no fallback and refused the same run with "dav: sources
#: need --cache-dir". `export-hf` had no fallback either, so the runbook told
#: people to pass `"$ATR_PAGE_CACHE"` — and on 2026-10-03 that shell variable was
#: set but not exported, naming a different directory that happened to hold
#: images, which produced 357 "no image" lines and two wrong diagnoses before the
#: right one.
#:
#: A cache nobody configured is still a cache. What must not differ is *which*
#: directory each entry point thinks it is.
#:
#: A function rather than a constant, because a constant is computed once at import
#: and then stops reading `DATA_DIR` — five tests that move the data directory
#: caught that immediately, and they were right to: a derived value that ignores
#: what it is derived from is the same trap as a key that ignores where its file is.
def page_cache_dir() -> Path:
    """Where the page cache is, for every entry point that needs to know."""
    return ATR_PAGE_CACHE or (DATA_DIR / "page_cache")

#: A cold tier for the page cache, on the research share. Empty = nothing
#: changes, which is the default and right for any host but tei.
#:
#: tei has 92 GB and no buffer: the page cache grew 3.8 GB to 9.6 GB on
#: 24.09.2026 and took `/` from 65 to 74 per cent. Since then a daily job moves
#: anything older than two days to the share (tei-vm-sanity#7), paths preserved,
#: with a verified copy before the delete. But `fetch` looked in exactly one
#: directory, so a moved entry was a miss and the next pass re-fetched the 25 MB
#: TIFF and converted it again — the eviction bought disk and no second life for
#: the working copies, which is what the cache is for (#487).
#:
#: The numbers that make the difference: a working copy is about 1.5 MB against
#: the original's 25 MB, and the share reads at 410 MB/s. Bringing a page back
#: is roughly an order of magnitude cheaper than making it again.
ATR_PAGE_CACHE_ARCHIVE = (
    Path(_get("ATR_PAGE_CACHE_ARCHIVE", ""))
    if _get("ATR_PAGE_CACHE_ARCHIVE", "") else None)

#: Where the share is mounted read-only. A batch may read pages from here as
#: well as from the mirror: it *is* the corpus now, and the MCP path's
#: containment check has to know that or every run over the mount is refused.
#: Set it to "" on a host with no mount.
ATR_MOUNT_DIR = Path(_get("ATR_MOUNT_DIR", "/mnt/gwdg")) if _get("ATR_MOUNT_DIR", "/mnt/gwdg") else None

# ── Batch ATR (atr_batch.py) ─────────────────────────────────────────────────
# Root for model-comparison runs: <VLM_TEST_ROOT>/<run>/<model id>/.
VLM_TEST_ROOT = Path(_get("VLM_TEST_ROOT", str(DATA_DIR / "vlm_test")))
# Pages in flight per model. Default 1: the gateway already recognises the lines
# of one page ~6 at a time, so a second page in flight queues against the same
# GPU rather than using an idle one, and it makes a failure harder to attribute.
# Raise it only for engines that leave the GPU idle between lines.
ATR_BATCH_PAGE_CONCURRENCY = max(1, int(_get("ATR_BATCH_PAGE_CONCURRENCY", "1")))
# Retries for a page-level failure (timeout / 5xx), with exponential backoff.
# A 4xx is never retried — it means the request itself is wrong, and repeating it
# just burns the budget before the model-level abort that should follow.
ATR_BATCH_RETRIES = max(0, int(_get("ATR_BATCH_RETRIES", "2")))

# ── HuggingFace (optional) ───────────────────────────────────────────────────
HF_TOKEN = _get("HF_TOKEN", "")

# ── Voyant (optional) ────────────────────────────────────────────────────────
# Self-hosted Voyant instance (see README "Voyant Tools — Integration").
# Reads VOYANT_API_URL; the legacy misspelled name is kept as a fallback.
VOYANT_API_URL = _get("VOYANT_API_URL", _get("Voyant_API_URL", "https://tei.dh.unibe.ch/voyant"))

# ── Agent E: Meta Agent ──────────────────────────────────────────────────────
META_REPORT_PATH = OUTPUTS_DIR / "meta_report.md"
META_LOG_PATH = OUTPUTS_DIR / "meta_agent_log.json"

# ── HITL Feedback Logging (#154) ─────────────────────────────────────────────
FEEDBACK_DIR = DATA_DIR / "feedback"
ROUTING_LOG_PATH = FEEDBACK_DIR / "routing.jsonl"
# Candidate voting (#290): one or many historians vote on which transcription wins.
# VOTING_MIN_VOTES=1 keeps today's Gate-2 behaviour (a single click decides); raise
# it to require consensus before a winner is applied.
VOTES_LOG_PATH = FEEDBACK_DIR / "votes.jsonl"
# #332: historian selections recorded as PREFERENCES (chosen ≻ the alternatives that
# were offered), not as a reference text. A Gate-2 selection is the closest of the
# options we produced, never ground truth — see #326/#336 — so this log carries
# comparisons, never the chosen text.
PREFERENCES_LOG_PATH = FEEDBACK_DIR / "preferences.jsonl"
# Local, per-deployment salt for pseudonymising voter ids. Never commit it; never
# publish a raw Discord id. Created on first use if absent.
PSEUDONYM_SALT_PATH = FEEDBACK_DIR / ".pseudonym_salt"
VOTING_MIN_VOTES = int(_get("VOTING_MIN_VOTES", "1"))

# ── Knowledge Hub ────────────────────────────────────────────────────────────
KH_DIR = BASE_DIR / "knowledge_hub" / "data"
# Active Knowledge-Hub backend (#26). "json" = the local KnowledgeHub store; a
# QLEVER triple-store backend (WP4) is selected here without touching agents.
KH_BACKEND = _get("KH_BACKEND", "json")


def ensure_dirs():
    """Erstellt alle Datenverzeichnisse, falls nicht vorhanden."""
    for d in [
        HOT_FOLDER,
        PROCESSED_FOLDER,
        TRANSCRIPTIONS_DIR,
        DESCRIPTIONS_DIR,
        OUTPUTS_DIR,
        FEEDBACK_DIR,
        KH_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)


def check_config() -> list[str]:
    """Konfigurationsprobleme, die den Start betreffen — leere Liste = in Ordnung.

    Fehlende Pflicht-Tokens **und** Werte, die noch Platzhalter sind. Ein
    Platzhalter ist der schlimmere der beiden Fälle: er ist gesetzt, er ist
    truthy, und er scheitert erst am anderen Ende eines Netzwerkaufrufs mit einer
    Fehlermeldung über den Server.
    """
    missing = []
    if not DISCORD_BOT_TOKEN:
        missing.append("DISCORD_BOT_TOKEN")
    if not GPUSTACK_API_KEY:
        missing.append("GPUSTACK_API_KEY")
    # KRAKEN_SERVICE_URL is optional — kraken falls back to local CLI if not set
    missing += [f"{key}: {why}" for key, why in sorted(unfilled_secrets().items())]
    return missing
