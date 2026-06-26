"""
config.py — Zentrale Konfiguration für den Agentic Historian.
Lädt Umgebungsvariablen und stellt Default-Werte bereit.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# BASE_DIR = Python-Package-Wurzel; REPO_ROOT = Git-Repo-Wurzel (eine Ebene höher)
BASE_DIR = Path(__file__).parent.resolve()
REPO_ROOT = BASE_DIR.parent

# Env-Dateien laden. Die dedizierten GPUStack-Secrets (.env.gpustack) liegen im
# Repo-Root und müssen explizit geladen werden; spätere Dateien überschreiben.
for _env_file in (
    BASE_DIR / ".env",
    REPO_ROOT / ".env",
    REPO_ROOT / ".env.gpustack",
):
    if _env_file.exists():
        load_dotenv(_env_file, override=True)


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# ── Discord ──────────────────────────────────────────────────────────────────
DISCORD_BOT_TOKEN = _get("DISCORD_BOT_TOKEN")

# ── GitHub ───────────────────────────────────────────────────────────────────
GITHUB_TOKEN = _get("GITHUB_TOKEN")
GITHUB_REPO = _get("GITHUB_REPO", "thodel/agentic_historian")
GITHUB_BRANCH = _get("GITHUB_BRANCH", "main")

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

# gpt-oss-120b is a REASONING model: it spends tokens on reasoning_content before
# emitting content. Give text calls a generous budget or content comes back null.
GPUSTACK_TEXT_MAX_TOKENS = int(_get("GPUSTACK_TEXT_MAX_TOKENS", "4096"))

# ── HTR / OCR ────────────────────────────────────────────────────────────────
HTR_QUALITY_THRESHOLD = float(_get("HTR_QUALITY_THRESHOLD", "0.75"))
MAX_RETRIES = int(_get("MAX_RETRIES", "3"))

# ── Kraken remote service (Path 2/3 — runs on a dedicated server) ────────────
KRAKEN_SERVICE_URL = _get("KRAKEN_SERVICE_URL", "http://localhost:8765")

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
VOYANT_API_URL = _get("Voyant_API_URL", "https://voyant-tools.org/voyant/api")

# ── Agent E: Meta Agent ──────────────────────────────────────────────────────
META_REPORT_PATH = OUTPUTS_DIR / "meta_report.md"
META_LOG_PATH = OUTPUTS_DIR / "meta_agent_log.json"

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