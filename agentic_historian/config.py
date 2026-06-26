"""
config.py — Zentrale Konfiguration für den Agentic Historian.
Lädt .env und stellt默认值 bereit.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Projektwurzel
BASE_DIR = Path(__file__).parent.resolve()

# .env laden
load_dotenv(BASE_DIR / ".env")


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# ── Discord ──────────────────────────────────────────────────────────────────
DISCORD_BOT_TOKEN = _get("DISCORD_BOT_TOKEN")

# ── GitHub ───────────────────────────────────────────────────────────────────
GITHUB_TOKEN = _get("GITHUB_TOKEN")
GITHUB_REPO = _get("GITHUB_REPO", "thodel/agentic_historian")
GITHUB_BRANCH = _get("GITHUB_BRANCH", "main")

# ── GPUStack ─────────────────────────────────────────────────────────────────
GPUSTACK_BASE_URL = _get("GPUSTACK_BASE_URL", "https://gpustack.unibe.ch/v1")
GPUSTACK_MODEL_TEXT = _get("GPUSTACK_MODEL_TEXT", "minimax-m2.7")
GPUSTACK_MODEL_VISION = _get("GPUSTACK_MODEL_VISION", "internvl3-8b-instruct")
GPUSTACK_API_KEY = _get("GPUSTACK_API_KEY")

# ── HTR / OCR ────────────────────────────────────────────────────────────────
HTR_QUALITY_THRESHOLD = float(_get("HTR_QUALITY_THRESHOLD", "0.75"))
MAX_RETRIES = int(_get("MAX_RETRIES", "3"))

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
    """Prüft.Required tokens und gibt fehlende Keys zurück."""
    missing = []
    if not DISCORD_BOT_TOKEN:
        missing.append("DISCORD_BOT_TOKEN")
    if not GPUSTACK_API_KEY:
        missing.append("GPUSTACK_API_KEY")
    # GITHUB_TOKEN und HF_TOKEN sind optional
    return missing