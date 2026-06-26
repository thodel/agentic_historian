"""
agent_a/models.py — Model registry for HTR/OCR.

Three pathways:
  1. VLM path — General vision-language models (InternVL, etc.) via GPUStack
  2. Kraken path — Baseline segmentation + OCR with community kraken models
  3. Party/PARY   — kraken-format HTR model for medieval documents

This registry holds available models per pathway.
Tobias will provide the actual kraken model list for each category.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class VLMModel:
    """A VLM available via GPUStack or compatible API."""
    name: str
    endpoint: str                           # e.g. "https://gpustack.unibe.ch/v1"
    model_id: str                           # e.g. "internvl3-8b-instruct"
    api_key_env: str                        # env var holding the API key
    max_tokens: int = 32768
    supports_vision: bool = True
    description: str = ""


@dataclass
class KrakenModel:
    """A kraken segmentation/OCR model."""
    model_id: str          # Zenodo ID or local path, e.g. "10.5281/zenodo.10592716"
    name: str              # Human-readable name, e.g. "CatMuS Caroline minuscule"
    lang: str              # ISO 639-1 language code, e.g. "la"
    script: str = "Latin"  # e.g. "Latin", "German", "Greek"
    notes: str = ""
    pretrained_on: str = ""


@dataclass
class HFModel:
    """A HuggingFace OCR model (e.g. LightOnOCR, TrOCR, etc.)."""
    model_id: str          # HuggingFace model ID, e.g. "wjbmattingly/LightOnOCR-2-1B-catmus-caroline"
    name: str
    lang: str              # Primary language
    task: str = "ocr"      # "ocr" | "line-ocr" | "htr"
    requires_line_images: bool = False  # True = model expects cropped line images
    notes: str = ""


# ── VLM models (Path 1) ──────────────────────────────────────────────────────

VLM_MODELS: dict[str, VLMModel] = {
    "internvl3-8b": VLMModel(
        name="InternVL3-8B-Instruct",
        endpoint="https://gpustack.unibe.ch/v1",
        model_id="internvl3-8b-instruct",
        api_key_env="GPUSTACK_API_KEY",
        max_tokens=32768,
        supports_vision=True,
        description="Primary VLM. Strong on historical handwriting with proper prompting.",
    ),
    # Add more VLM entries here as they become available:
    # "qwen2.5-vl": VLMModel(...),
}

# ── Kraken models (Path 2 — baseline detection + OCR) ────────────────────────
# List will be provided by Tobias. Populated from `kraken list` output.

KRAKEN_MODELS: dict[str, KrakenModel] = {
    # Placeholder — to be replaced with actual model list from Tobias:
    # "catmus_caroline": KrakenModel(
    #     model_id="10.5281/zenodo.XXXXX",
    #     name="CatMuS Caroline minuscule",
    #     lang="la",
    #     script="Latin",
    #     notes="Trained on Caroline minuscule manuscripts.",
    # ),
}

# ── Party / PARY HTR model (Path 3) ─────────────────────────────────────────
# https://zenodo.org/records/20642057
# Download: kraken get 10.5281/zenodo.20642057

PARTY_MODEL = KrakenModel(
    model_id="10.5281/zenodo.20642057",
    name="Party / PARY HTR",
    lang="mul",
    script="Medieval",
    notes="Kraken HTR model for medieval/historical documents (Swiss context).",
    pretrained_on="Swiss medieval manuscripts, 14th–16th c.",
)

# ── HuggingFace OCR models (Path 2b — end-to-end or line-level) ───────────────
# Populated from HuggingFace model listings.

HF_MODELS: dict[str, HFModel] = {
    # Placeholder — to be replaced with actual model list:
    # "lightonocr_caroline": HFModel(
    #     model_id="wjbmattingly/LightOnOCR-2-1B-catmus-caroline",
    #     name="LightOnOCR-2-1B (CatMuS Caroline)",
    #     lang="la",
    #     task="line-ocr",
    #     requires_line_images=True,
    #     notes="1B params. Line-level input. CER 13.71% on CatMuS test set.",
    # ),
}


def get_primary_vlm() -> VLMModel:
    """Returns the primary VLM (first available)."""
    return next(iter(VLM_MODELS.values()))


def kraken_model_for_lang(lang: str) -> Optional[KrakenModel]:
    """Returns first kraken model matching the language."""
    for m in KRAKEN_MODELS.values():
        if m.lang == lang.lower():
            return m
    return None


def hf_model_for_lang(lang: str, require_line: bool = False) -> Optional[HFModel]:
    """Returns first HF model matching language and line-image requirement."""
    for m in HF_MODELS.values():
        if m.lang == lang.lower() and (not require_line or m.requires_line_images == require_line):
            return m
    return None