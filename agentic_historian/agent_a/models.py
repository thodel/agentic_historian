"""
agent_a/models.py — Model registry for HTR/OCR.

Three pathways:
  1. VLM path — General vision-language models (InternVL, etc.) via GPUStack
  2. Kraken path — Baseline segmentation + OCR with community kraken models
  3. Party/PARY   — kraken-format HTR model for medieval documents

This registry holds available models per pathway.
Tobias will provide the actual kraken model list for each category.
"""

from dataclasses import dataclass, field
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
    centuries: list[int] = field(default_factory=list)  # training centuries, e.g. [14,15]
    scripts: list[str] = field(default_factory=list)   # scripts supported (from gateway, e.g. ["Caroline minuscule", "Textura"])
    languages: list[str] = field(default_factory=list)  # languages supported (from gateway, e.g. ["la", "de"])


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
    # ── CatMuS family (medieval Latin, multiple scripts) ──────────────────────
    "catmus_medieval": KrakenModel(
        model_id="10.5281/zenodo.7516057",
        name="CatMuS Medieval — full model",
        lang="la",
        script="Caroline minuscule",
        notes="CatMuS Medieval full model. 400+ manuscripts, 14th–16th c.",
        pretrained_on="Medieval Latin manuscripts (14th–16th c.)",
        centuries=[14, 15, 16],
    ),
    "catmus_caroline": KrakenModel(
        model_id="10.5281/zenodo.5468665",
        name="CatMuS — Caroline minuscule",
        lang="la",
        script="Caroline minuscule",
        notes="Trained on Caroline minuscule manuscripts.",
        pretrained_on="Medieval Latin manuscripts (Carolingian era)",
        centuries=[9, 10, 11, 12],
    ),
    # ── McCATMuS (Medieval Catalan Arabic and beyond) ──────────────────────────
    "mccatmus": KrakenModel(
        model_id="10.5281/zenodo.6542744",
        name="McCATMuS — Medieval Transcription",
        lang="la",
        script="Medieval",
        notes="Medieval Catalan Arabic model.",
        pretrained_on="Medieval manuscripts (Catalan/Arabic context)",
        centuries=[13, 14, 15],
    ),
    # ── Czech / Bohemian models ────────────────────────────────────────────────
    "bohemian_19th": KrakenModel(
        model_id="10.5281/zenodo.2577813",
        name="Kraken HTR — Bohemian 19th c.",
        lang="de",
        script="Kurrent",
        notes="Czech lands, 19th c. Mostly German-language Jewish registers.",
        pretrained_on="19th c. Bohemian German-language records",
        centuries=[19],
    ),
    # ── Arabic / Urdu / OpenITI ────────────────────────────────────────────────
    "printed_urdu": KrakenModel(
        model_id="10.5281/zenodo.20529753",
        name="Printed Urdu Base — Kraken",
        lang="ur",
        script="Nastaliq",
        notes="Printed Urdu. OpenITI corpus trained.",
        pretrained_on="OpenITI Arabic/Urdu printed texts",
        centuries=[19, 20],
    ),
    # ── Early medieval / Insular ───────────────────────────────────────────────
    "early_medieval_latin": KrakenModel(
        model_id="10.5281/zenodo.19222213",
        name="Early Medieval Latin (9th–12th c.)",
        lang="la",
        script="Caroline minuscule",
        notes="Early medieval Latin manuscripts (9th–12th c.).",
        pretrained_on="Latin manuscripts 9th–12th c.",
        centuries=[9, 10, 11, 12],
    ),
    # ── Medieval charter / diplomatic ─────────────────────────────────────────
    "medieval_charters": KrakenModel(
        model_id="10.5281/zenodo.18732245",
        name="Medieval Charters — Latin",
        lang="la",
        script="Caroline minuscule",
        notes="Trained on medieval charters and diplomatic documents.",
        pretrained_on="Medieval charters (Latin, 11th–15th c.)",
        centuries=[11, 12, 13, 14, 15],
    ),
    # ── Further medieval models (need metadata confirmation) ───────────────────
    "medieval_generic_a": KrakenModel(
        model_id="10.5281/zenodo.18207779",
        name="Medieval HTR Model A",
        lang="la",
        script="Medieval",
        notes="Medieval manuscript HTR.",
        pretrained_on="Medieval manuscripts",
        centuries=[13, 14, 15],
    ),
    "medieval_generic_b": KrakenModel(
        model_id="10.5281/zenodo.18220238",
        name="Medieval HTR Model B",
        lang="la",
        script="Medieval",
        notes="Medieval manuscript HTR.",
        pretrained_on="Medieval manuscripts",
        centuries=[14, 15, 16],
    ),
    "medieval_generic_c": KrakenModel(
        model_id="10.5281/zenodo.18207767",
        name="Medieval HTR Model C",
        lang="la",
        script="Medieval",
        notes="Medieval manuscript HTR.",
        pretrained_on="Medieval manuscripts",
        centuries=[13, 14],
    ),
    "medieval_generic_d": KrakenModel(
        model_id="10.5281/zenodo.18207719",
        name="Medieval HTR Model D",
        lang="la",
        script="Medieval",
        notes="Medieval manuscript HTR.",
        pretrained_on="Medieval manuscripts",
        centuries=[14, 15],
    ),
    "medieval_generic_e": KrakenModel(
        model_id="10.5281/zenodo.18207676",
        name="Medieval HTR Model E",
        lang="la",
        script="Medieval",
        notes="Medieval manuscript HTR.",
        pretrained_on="Medieval manuscripts",
        centuries=[15, 16],
    ),
    # ── Late medieval / early print ─────────────────────────────────────────────
    "late_medieval_latin": KrakenModel(
        model_id="10.5281/zenodo.17690418",
        name="Late Medieval Latin (14th–16th c.)",
        lang="la",
        script="Textura",
        notes="Late medieval Latin manuscripts, 14th–16th c.",
        pretrained_on="Late medieval Latin manuscripts",
        centuries=[14, 15, 16],
    ),
    "late_medieval_german": KrakenModel(
        model_id="10.5281/zenodo.15366732",
        name="Late Medieval German (14th–16th c.)",
        lang="de",
        script="Textura",
        notes="Late medieval German-language manuscripts, 14th–16th c.",
        pretrained_on="German-language medieval manuscripts",
        centuries=[14, 15, 16],
    ),
    "early_modern_german": KrakenModel(
        model_id="10.5281/zenodo.15030337",
        name="Early Modern German (16th–17th c.)",
        lang="de",
        script="Kurrent",
        notes="Early modern German documents, 16th–17th c.",
        pretrained_on="German early modern manuscripts",
        centuries=[16, 17],
    ),
    # ── McCATMuS variant ──────────────────────────────────────────────────────
    "mccatmus_transcription": KrakenModel(
        model_id="10.5281/zenodo.13788177",
        name="McCATMuS Transcription model",
        lang="la",
        script="Medieval",
        notes="HTR/OCR generic model for handwritten medieval texts.",
        pretrained_on="Medieval manuscripts (broad)",
        centuries=[12, 13, 14, 15],
    ),
    # ── Additional medieval / early modern ─────────────────────────────────────
    "medieval_12_14": KrakenModel(
        model_id="10.5281/zenodo.13814200",
        name="Medieval 12th–14th c. Latin",
        lang="la",
        script="Caroline minuscule",
        notes="Medieval Latin 12th–14th c.",
        pretrained_on="Latin manuscripts 12th–14th c.",
        centuries=[12, 13, 14],
    ),
    "medieval_14_16": KrakenModel(
        model_id="10.5281/zenodo.13862096",
        name="Medieval 14th–16th c. Latin",
        lang="la",
        script="Textura",
        notes="Medieval Latin 14th–16th c.",
        pretrained_on="Latin manuscripts 14th–16th c.",
        centuries=[14, 15, 16],
    ),
    "medieval_15_16": KrakenModel(
        model_id="10.5281/zenodo.13942714",
        name="Medieval/Early Modern 15th–16th c.",
        lang="la",
        script="Humanistische Kursive",
        notes="Transition period medieval to early modern, 15th–16th c.",
        pretrained_on="Late medieval / early modern manuscripts",
        centuries=[15, 16],
    ),
    "early_modern_latin": KrakenModel(
        model_id="10.5281/zenodo.13741957",
        name="Early Modern Latin (16th–17th c.)",
        lang="la",
        script="Humanistisch",
        notes="Humanist minuscule / early printed Latin, 16th–17th c.",
        pretrained_on="Early modern Latin manuscripts",
        centuries=[16, 17],
    ),
    "early_modern_german_16": KrakenModel(
        model_id="10.5281/zenodo.13736584",
        name="Early Modern German 16th c.",
        lang="de",
        script="Kurrent",
        notes="German early modern, 16th c.",
        pretrained_on="German manuscripts 16th c.",
        centuries=[16],
    ),
    # ── Czech 19th c. registers (duplicate variant) ────────────────────────────
    "bohemian_19th_v2": KrakenModel(
        model_id="10.5281/zenodo.11673242",
        name="Kraken HTR — Bohemian 19th c. (v2)",
        lang="de",
        script="Kurrent",
        notes="Czech lands 19th c. German-language Jewish registers.",
        pretrained_on="19th c. Bohemian registers",
        centuries=[19],
    ),
    # ── OpenITI corpus / Arabic ────────────────────────────────────────────────
    "openiti_arabic": KrakenModel(
        model_id="10.5281/zenodo.11113737",
        name="OpenITI Arabic — Kraken",
        lang="ar",
        script="Arabic",
        notes="OpenITI Arabic corpus.",
        pretrained_on="OpenITI Arabic printed/manuscript texts",
        centuries=[12, 13, 14, 15, 16, 17, 18, 19],
    ),
    "openiti_urdu": KrakenModel(
        model_id="10.5281/zenodo.10886224",
        name="OpenITI Urdu — Kraken",
        lang="ur",
        script="Nastaliq",
        notes="OpenITI Urdu/Arabic script model.",
        pretrained_on="OpenITI Urdu printed texts",
        centuries=[19, 20],
    ),
    # ── General purpose / printed ──────────────────────────────────────────────
    "printed_latin": KrakenModel(
        model_id="10.5281/zenodo.10599911",
        name="Printed Latin — general",
        lang="la",
        script="Printed",
        notes="General printed Latin model.",
        pretrained_on="Printed Latin texts",
        centuries=[15, 16, 17, 18],
    ),
    "printed_french": KrakenModel(
        model_id="10.5281/zenodo.10592716",
        name="Printed French — Kraken default",
        lang="fr",
        script="Antiqua",
        notes="Default model for printed French. kraken default.",
        pretrained_on="Printed French texts",
        centuries=[16, 17, 18, 19],
    ),
    "printed_generic": KrakenModel(
        model_id="10.5281/zenodo.10556673",
        name="Generic Printed model",
        lang="la",
        script="Printed",
        notes="Generic printed text model.",
        pretrained_on="Printed texts",
        centuries=[15, 16, 17, 18],
    ),
    "printed_medieval": KrakenModel(
        model_id="10.5281/zenodo.10519596",
        name="Printed Medieval Latin",
        lang="la",
        script="Textura",
        notes="Printed medieval Latin (incunabula, early print).",
        pretrained_on="Incunabula and early printed Latin",
        centuries=[15, 16],
    ),
    "printed_arabic": KrakenModel(
        model_id="10.5281/zenodo.8193498",
        name="Printed Arabic — Kraken",
        lang="ar",
        script="Arabic",
        notes="Printed Arabic OCR.",
        pretrained_on="Printed Arabic texts",
        centuries=[18, 19, 20],
    ),
    "printed_urdu_base": KrakenModel(
        model_id="10.5281/zenodo.7933402",
        name="Printed Urdu Base (OpenITI)",
        lang="ur",
        script="Nastaliq",
        notes="OpenITI Urdu base model.",
        pretrained_on="OpenITI Urdu printed texts",
        centuries=[19, 20],
    ),
    "printed_urdu_wide": KrakenModel(
        model_id="10.5281/zenodo.7755504",
        name="Printed Urdu — wide",
        lang="ur",
        script="Nastaliq",
        notes="Printed Urdu, wide coverage.",
        pretrained_on="OpenITI Urdu printed corpus",
        centuries=[19, 20],
    ),
    "printed_urdu_openiti": KrakenModel(
        model_id="10.5281/zenodo.7755483",
        name="Printed Urdu (OpenITI extended)",
        lang="ur",
        script="Nastaliq",
        notes="OpenITI Urdu, extended training.",
        pretrained_on="OpenITI Urdu extended corpus",
        centuries=[19, 20],
    ),
    "printed_urdu_extended": KrakenModel(
        model_id="10.5281/zenodo.7631619",
        name="Printed Urdu Extended",
        lang="ur",
        script="Nastaliq",
        notes="OpenITI Urdu extended model.",
        pretrained_on="OpenITI Urdu extended corpus",
        centuries=[19, 20],
    ),
    "printed_generic_v2": KrakenModel(
        model_id="10.5281/zenodo.7516310",
        name="Printed generic v2",
        lang="la",
        script="Printed",
        notes="Generic printed text model v2.",
        pretrained_on="Printed texts",
        centuries=[16, 17, 18],
    ),
    # ── Additional Czech / Germanic ─────────────────────────────────────────────
    "czech_historic": KrakenModel(
        model_id="10.5281/zenodo.7050270",
        name="Czech Historic — Kraken",
        lang="cs",
        script="Kurrent",
        notes="Czech historical documents.",
        pretrained_on="Czech historical records",
        centuries=[18, 19],
    ),
    "czech_historic_v2": KrakenModel(
        model_id="10.5281/zenodo.7050342",
        name="Czech Historic v2 — Kraken",
        lang="cs",
        script="Kurrent",
        notes="Czech historical documents v2.",
        pretrained_on="Czech historical records",
        centuries=[19],
    ),
    # ── OpenITI Arabic variants ─────────────────────────────────────────────────
    "openiti_arabic_v2": KrakenModel(
        model_id="10.5281/zenodo.7051644",
        name="OpenITI Arabic v2",
        lang="ar",
        script="Arabic",
        notes="OpenITI Arabic v2 (extended training).",
        pretrained_on="OpenITI Arabic corpus v2",
        centuries=[12, 13, 14, 15, 16, 17, 18, 19],
    ),
    "openiti_arabic_v3": KrakenModel(
        model_id="10.5281/zenodo.7410529",
        name="OpenITI Arabic v3",
        lang="ar",
        script="Arabic",
        notes="OpenITI Arabic v3 (broader coverage).",
        pretrained_on="OpenITI Arabic extended corpus",
        centuries=[12, 13, 14, 15, 16, 17, 18, 19],
    ),
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
    # TrOCR line-level models (served by trocr engine on asterAIx :8202)
    "trocr_medieval_escriptmask": HFModel(
        model_id="dh-unibe/trocr-medieval-escriptmask",
        name="TrOCR Medieval EscriptMask",
        lang="mul",  # de, fr, la, nl
        task="line-ocr",
        requires_line_images=True,
        notes="Vision-encoder-decoder seq2seq. Medieval manuscript lines (Carolingian/Textura). Serviced by trocr engine.",
    ),
    "trocr_kurrent_xvi_xvii": HFModel(
        model_id="dh-unibe/trocr-kurrent-XVI-XVII",
        name="TrOCR Kurrent XVI–XVII",
        lang="de",
        task="line-ocr",
        requires_line_images=True,
        notes="Vision-encoder-decoder seq2seq. Early modern German Kurrent, 16th–17th c. Serviced by trocr engine.",
    ),
    "trocr_essoins_middle_latin": HFModel(
        model_id="dh-unibe/trozco-essoins-middle-latin",
        name="TrOCR Essoins Middle Latin",
        lang="la",
        task="line-ocr",
        requires_line_images=True,
        notes="Vision-encoder-decoder seq2seq. Middle Latin (legal documents, Essoins). 13th–15th c. Serviced by trocr engine.",
    ),
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


# ── Live registry overlay ─────────────────────────────────────────────────────
# Populated at startup (or on-demand) by refresh_kraken_registry() from the
# ATR gateway's GET /models endpoint.  The local KRAKEN_MODELS table remains
# the authoritative fallback when the gateway is unreachable.

KRAKEN_MODELS_LIVE: dict[str, KrakenModel] = {}


def refresh_kraken_registry(
    client: "KrakenHTTPClient",
) -> dict[str, KrakenModel]:
    """
    Fetch the live model registry from the ATR gateway and return a
    KrakenModel dict overlay.

    The gateway's ``GET /models`` returns ``ModelInfo`` dicts with fields
    ``id, engine, scripts, centuries, languages, level, description``.
    ``KrakenHTTPClient.list_models()`` returns ``list[dict]``.

    Returns a dict keyed by model id (same shape as ``KRAKEN_MODELS``),
    and also updates the module-level ``KRAKEN_MODELS_LIVE`` in place.
    Raises ``KrakenClientError`` on network failure (callers handle gracefully).

    Pitfalls from the failed first attempt (fix/ah-110-kraken-registry-drift):
      - Class is ``KrakenHTTPClient``, NOT ``KrakenClient``
      - ``list_models()`` returns ``list[dict]`` (ModelInfo dicts), NOT ``list[str]``
      - Must be called INSIDE the ``with KrakenHTTPClient() as client:`` block
      - Dict key is ``scripts`` (not ``script``) and ``languages`` (not ``lang``)
    """
    live_models: dict[str, KrakenModel] = {}
    raw_models = client.list_models()

    for m in raw_models:
        if not isinstance(m, dict) or "id" not in m:
            continue

        model_id = m["id"]
        centuries: list[int] = []
        raw_centuries = m.get("centuries", [])
        if isinstance(raw_centuries, list):
            for c in raw_centuries:
                try:
                    centuries.append(int(c))
                except (ValueError, TypeError):
                    pass

        live_models[model_id] = KrakenModel(
            model_id=model_id,
            name=m.get("description", model_id),
            lang=m.get("languages", ["mul"])[0] if m.get("languages") else "mul",
            script=", ".join(m.get("scripts", [])) or "Latin",
            notes=f"[live] {m.get('description', '')}",
            pretrained_on=m.get("description", ""),
            centuries=centuries,
            scripts=m.get("scripts", []),
            languages=m.get("languages", []),
        )

    KRAKEN_MODELS_LIVE.clear()
    KRAKEN_MODELS_LIVE.update(live_models)
    return live_models
# Re-export from reconcile so agent_a.models is the stable public interface
from agent_a.reconcile import RECONCILE_SYSTEM, RECONCILE_DEFAULT_MAX_TOKENS
