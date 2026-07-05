"""
config.py — Zentrale Konfiguration für den Agentic Historian.
Lädt Umgebungsvariablen und stellt Default-Werte bereit.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

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
for _env_file in (
    REPO_ROOT / ".env.gpustack",
    REPO_ROOT / ".env",
    BASE_DIR / ".env",
):
    if _env_file.exists():
        load_dotenv(_env_file, override=False)


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# ── Discord ──────────────────────────────────────────────────────────────────
DISCORD_BOT_TOKEN = _get("DISCORD_BOT_TOKEN")
# Numeric role ID that is allowed to run sensitive commands (/run, /pull, etc.).
# Set to 0 or empty to disable role-gating (NOT recommended for shared servers).
REQUIRED_DISCORD_ROLE_ID: int | None = int(_get("REQUIRED_DISCORD_ROLE_ID", "0")) or None

# ── GitHub ───────────────────────────────────────────────────────────────────
GITHUB_TOKEN = _get("GITHUB_TOKEN")
GITHUB_REPO = _get("GITHUB_REPO", "thodel/agentic_historian")
GITHUB_BRANCH = _get("GITHUB_BRANCH", "main")

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
GPUSTACK_BASE_URL = _get("GPUSTACK_BASE_URL", "https://gpustack.unibe.ch/v1")
GPUSTACK_API_KEY = _get("GPUSTACK_API_KEY")

# Role-based model routing (team decision 2026-06-26):
#   VISION  → Agent A (HTR) and Agent B (source description)
#   TEXT/LLM→ Agent C (NER), corpus/meta agents, reconciliation, care-flag
#   ORCH    → reserved for the future natural-language / SitL orchestrator (WP1)
GPUSTACK_MODEL_VISION = _get("GPUSTACK_MODEL_VISION", "qwen3-vl-30b-a3b-instruct")
GPUSTACK_MODEL_TEXT = _get("GPUSTACK_MODEL_TEXT", "gpt-oss-120b")
GPUSTACK_MODEL_ORCHESTRATOR = _get("GPUSTACK_MODEL_ORCHESTRATOR", "minimax-m2.7")

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

# gpt-oss-120b is a REASONING model: it spends tokens on reasoning_content before
# emitting content. Give text calls a generous budget or content comes back null.
GPUSTACK_TEXT_MAX_TOKENS = int(_get("GPUSTACK_TEXT_MAX_TOKENS", "4096"))

# ── HTR / OCR ────────────────────────────────────────────────────────────────
HTR_QUALITY_THRESHOLD = float(_get("HTR_QUALITY_THRESHOLD", "0.75"))
MAX_RETRIES = int(_get("MAX_RETRIES", "3"))
# Add a small additive routing prior from historian feedback to kraken model
# scores (#155). OFF by default → byte-identical scoring; the prior is capped
# below a full script match so it only breaks near-ties.
ENABLE_ROUTING_PRIOR = _get("ENABLE_ROUTING_PRIOR", "false").lower() == "true"

# ── ATR gateway (serving-atr-inference on asterAIx) ──────────────────────────
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

# ── Hot Folder ───────────────────────────────────────────────────────────────
ENABLE_HOT_FOLDER_WATCH = _get("ENABLE_HOT_FOLDER_WATCH", "true").lower() == "true"
HOT_FOLDER = BASE_DIR / _get("HOT_FOLDER", "data/hot_folder")
PROCESSED_FOLDER = BASE_DIR / _get("PROCESSED_FOLDER", "data/hot_folder/processed")

# ── Datenverzeichnisse ───────────────────────────────────────────────────────
DATA_DIR = BASE_DIR / "data"
TRANSCRIPTIONS_DIR = DATA_DIR / "transcriptions"
DESCRIPTIONS_DIR = DATA_DIR / "descriptions"
OUTPUTS_DIR = DATA_DIR / "outputs"

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

# ── Knowledge Hub ────────────────────────────────────────────────────────────
KH_DIR = BASE_DIR / "knowledge_hub" / "data"


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
    """Prüft erforderliche Tokens und gibt fehlende Keys zurück."""
    missing = []
    if not DISCORD_BOT_TOKEN:
        missing.append("DISCORD_BOT_TOKEN")
    if not GPUSTACK_API_KEY:
        missing.append("GPUSTACK_API_KEY")
    # KRAKEN_SERVICE_URL is optional — kraken falls back to local CLI if not set
    return missing