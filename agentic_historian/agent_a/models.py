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

import config


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
class GatewayVLMModel:
    """A VLM the ATR gateway serves under ``engine: vllm`` (#540).

    Not a ``VLMModel``: those are GPUStack models reached with an OpenAI-style
    chat call, these are gateway models reached with an image POST to
    ``/recognize``. Two backends, two call shapes; one dataclass for both would
    hide which one a given id needs.

    ``level`` is kept because it changes what the call *is*. Five of the seven
    served models are line-level, and ``/recognize`` does not auto-segment the
    way ``/ocr`` does for TrOCR — so a page handed whole to a line model is a
    different operation, not a worse reading.
    """
    model_id: str
    name: str
    level: str = "page"                                  # "line" | "page"
    scripts: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    centuries: list[int] = field(default_factory=list)
    base_model: str = ""
    hf_repo: str = ""
    notes: str = ""

    # ── The fields model_selector.score_model reads (#539) ───────────────────
    #
    # The gateway reports `scripts` and `languages` as lists for every engine,
    # and `refresh_kraken_registry` already flattens them exactly like this for
    # its own rows. Mirroring it here lets the kraken scorer — weights, script
    # mismatch penalty, bilingual handling and all — score a VLM unchanged,
    # which is what #539 meant by "im Vokabular des bestehenden Selektors".
    #
    # Flattening the same way is the point. If a VLM and a kraken model built
    # from the same gateway row disagreed about what their script is, the two
    # selectors would be two classification systems, and #538 is what that
    # costs.

    @property
    def script(self) -> str:
        return ", ".join(self.scripts) or "Latin"

    @property
    def lang(self) -> str:
        return self.languages[0] if self.languages else "mul"


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
#
# This table **describes** VLMs. It does not decide which one runs — that is
# ``config.GPUSTACK_MODEL_VISION``, and ``get_primary_vlm()`` reads it (#538).
#
# It used to decide, by holding one entry and returning it as "the first
# available". When the config default moved from internvl3-8b to qwen3.8-27b on
# 08.09.2026, this table did not, and the repository carried two answers to the
# same question for four weeks. #537 is what that cost: the run went to config,
# the record was stamped from here, and every VLM reading was attributed to a
# model that had not produced it.
#
# ``KRAKEN_MODELS`` below records the same failure one layer down — 28 of 43
# entries described a model other than the one their DOI loaded. A hand-kept
# table that names something authoritative drifts away from it. So this one
# names nothing authoritative any more.

VLM_MODELS: dict[str, VLMModel] = {
    "qwen3.8-27b": VLMModel(
        name="Qwen3.8-27B",
        endpoint="https://gpustack.unibe.ch/v1",
        model_id="qwen3.8-27b",
        api_key_env="GPUSTACK_API_KEY",
        max_tokens=32768,
        supports_vision=True,
        description=(
            "Vision role since 08.09.2026 (f197be4). 27.7 % CER on the "
            "Inzigkofen ground truth, 291 lines / 8 pages (AH-11, 07.09.2026)."
        ),
    ),
    "internvl3-8b": VLMModel(
        name="InternVL3-8B-Instruct",
        endpoint="https://gpustack.unibe.ch/v1",
        model_id="internvl3-8b-instruct",
        api_key_env="GPUSTACK_API_KEY",
        max_tokens=32768,
        supports_vision=True,
        description=(
            "Retired from the vision role on 08.09.2026. Collapses on this "
            "material: 189.8 % CER on the same Inzigkofen set that gives "
            "qwen3.8-27b 27.7 % (AH-11, 07.09.2026). Kept so the next reader "
            "sees the measurement rather than re-adopting the name."
        ),
    ),
    # The gateway's seven fine-tuned VLMs (qwen3vl-medieval-german-v3 among
    # them) are not here: they are served by the ATR gateway, not GPUStack, and
    # reaching them is #540.
}
# ── Kraken models (Path 2 — baseline detection + OCR) ────────────────────────
# Generated from the ATR gateway's registry after serving-atr-inference#198, which
# renamed every kraken entry after the Zenodo record its DOI loads. **This table
# was the source of the error it now reflects**: 28 of its 43 entries described a
# model other than the one their DOI contains — `catmus_caroline` for a Hebrew
# Sephardi model, `mccatmus` for LECTAUREP Contemporary French, `medieval_generic_e`
# for Fanny Mendelssohn's letters — and the gateway registry was ported from here.
#
# Every name, language, script and century below is the record's own, and a century
# list is empty where the record states none: `select_kraken_model` then scores that
# model on script and language alone, which is better than scoring it on a guess.
# Keys are the gateway ids without the `kraken-` prefix, so a pick made from this
# fallback and one made from KRAKEN_MODELS_LIVE name the same model.
#
# Two entries are deliberately absent: zenodo.18732245 (MiDRASH Geniza) publishes no
# .mlmodel at all, so kraken cannot load it, and the party model (zenodo.20642057) is
# served with `engine: party` and is not a kraken model.
#
# This is the FALLBACK. When the gateway answers, KRAKEN_MODELS_LIVE overlays it with
# the same metadata, straight from `GET /models`.

KRAKEN_MODELS: dict[str, KrakenModel] = {
    "austrian_fraktur": KrakenModel(
        model_id="10.5281/zenodo.7933402",
        name="Fraktur model trained from the enhanced Austrian Newspapers dataset",
        lang="de",
        script="Fraktur",
        notes=(
            "Weil & Kamlah, 2023. 19th c. German Fraktur."
        ),
        pretrained_on="Fraktur model trained from the enhanced Austrian Newspapers dataset",
        centuries=[19],
        scripts=['Fraktur'],
        languages=['de'],
    ),
    "bastarda_inzigkofen": KrakenModel(
        model_id="10.5281/zenodo.18207779",
        name="Bastarda HTR model related to the Augustinian canonesses in Inzigkofen",
        lang="de",
        script="Bastarda",
        notes=(
            "Eichenberger, 2026. The hand of Jos von Pfullendorf (d. ca. 1430), German. "
            "Fine-tune of 10.5281/zenodo.15030337 (served as kraken-catmus_medieval): not "
            "an independent candidate beside it. Trained on "
            "https://doi.org/10.5281/zenodo.17978574."
        ),
        pretrained_on="Bastarda HTR model related to the Augustinian canonesses in Inzigkofen",
        centuries=[15],
        scripts=['Bastarda'],
        languages=['de'],
    ),
    "bifrost_old_norse": KrakenModel(
        model_id="10.5281/zenodo.15366732",
        name="Bifrost",
        lang="non",
        script="Latin",
        notes=(
            "Kapitan & Vidal-Gorène, 2025. Old Norse manuscripts, fine-tuned from CATMuS "
            "Medieval. The record states no century range. Fine-tune of "
            "10.5281/zenodo.15030337 (served as kraken-catmus_medieval): not an "
            "independent candidate beside it. Trained on "
            "https://doi.org/10.5281/zenodo.15366896."
        ),
        pretrained_on="Bifrost",
        centuries=[],
        scripts=[],
        languages=['non'],
    ),
    "bohemian_19th": KrakenModel(
        model_id="10.5281/zenodo.11673242",
        name="Kraken HTR recognition model, Bohemian provenance 19th century",
        lang="de",
        script="Kurrent",
        notes=(
            "Baránek, 2024. Mostly German-language Jewish registers from the Czech lands. "
            "Kurrent is inferred from that material; the record does not name a script."
        ),
        pretrained_on="Kraken HTR recognition model, Bohemian provenance 19th century",
        centuries=[19],
        scripts=['Kurrent'],
        languages=['de', 'cs'],
    ),
    "catmus_gothic_print": KrakenModel(
        model_id="10.5281/zenodo.10599911",
        name="CATMuS Gothic Print",
        lang="fr",
        script="Gothic",
        notes=(
            "Solfrini & Gabay, 2024. Prints in Gothic typefaces and 16th c. French (SETAF "
            "data), fine-tuned on CATMuS Medieval. Fine-tune of 10.5281/zenodo.15030337 "
            "(served as kraken-catmus_medieval): not an independent candidate beside it."
        ),
        pretrained_on="CATMuS Gothic Print",
        centuries=[16],
        scripts=['Gothic'],
        languages=['fr', 'la'],
    ),
    "catmus_medieval": KrakenModel(
        model_id="10.5281/zenodo.15030337",
        name="CATMuS Medieval",
        lang="fro",
        script="Medieval",
        notes=(
            "Pinche & Clérice, 2025. Graphematic transcriptions, no abbreviations "
            "resolved; Old/Middle French, Latin, Spanish, Italian. The record names no "
            "century range, so centuries stays empty rather than invented."
        ),
        pretrained_on="CATMuS Medieval",
        centuries=[],
        scripts=['Medieval'],
        languages=['fro', 'la', 'es', 'it'],
    ),
    "catmus_print_large": KrakenModel(
        model_id="10.5281/zenodo.10592716",
        name="CATMuS-Print [Large]",
        lang="fr",
        script="Latin",
        notes=(
            "Gabay & Clérice, 2024. Diachronic model for French and other West European "
            "prints, first prints of the 16th c. to digital documents of the 21st. "
            "Typefaces various; the record names no single script."
        ),
        pretrained_on="CATMuS-Print [Large]",
        centuries=[16, 17, 18, 19, 20, 21],
        scripts=[],
        languages=['fr', 'es', 'de', 'en', 'it', 'la'],
    ),
    "cremma_medieval": KrakenModel(
        model_id="10.5281/zenodo.7631619",
        name="Generic CREMMA model for medieval manuscripts (Latin and Old French), 8th–15th c.",
        lang="la",
        script="Medieval",
        notes=(
            "Clérice & Pinche, 2023."
        ),
        pretrained_on="Generic CREMMA model for medieval manuscripts (Latin and Old French), 8th–15th c.",
        centuries=[8, 9, 10, 11, 12, 13, 14, 15],
        scripts=['Medieval'],
        languages=['la', 'fro'],
    ),
    "cursive_inzigkofen": KrakenModel(
        model_id="10.5281/zenodo.18207767",
        name="Cursive HTR model related to the Augustinian canonesses in Inzigkofen",
        lang="de",
        script="Cursive",
        notes=(
            "Eichenberger, 2026. The hand of Johannes Jaeck (d. 1466), German. Fine-tune "
            "of 10.5281/zenodo.15030337 (served as kraken-catmus_medieval): not an "
            "independent candidate beside it. Trained on "
            "https://doi.org/10.5281/zenodo.17978574."
        ),
        pretrained_on="Cursive HTR model related to the Augustinian canonesses in Inzigkofen",
        centuries=[15],
        scripts=['Cursive'],
        languages=['de'],
    ),
    "cyrillic_uncial": KrakenModel(
        model_id="10.5281/zenodo.7755483",
        name="Generic HTR model for Old Cyrillic uncial and semi-uncial, 11th–16th c.",
        lang="cu",
        script="Cyrillic uncial",
        notes=(
            "Rabus & Thompson, 2023. Church Slavonic."
        ),
        pretrained_on="Generic HTR model for Old Cyrillic uncial and semi-uncial, 11th–16th c.",
        centuries=[11, 12, 13, 14, 15, 16],
        scripts=['Cyrillic uncial'],
        languages=['cu'],
    ),
    "english_print": KrakenModel(
        model_id="10.5281/zenodo.2577813",
        name="A generalized model for English printed text",
        lang="en",
        script="Latin",
        notes=(
            "Kiessling, 2019. Modern printed English plus ~10 000 lines of historical "
            "print."
        ),
        pretrained_on="A generalized model for English printed text",
        centuries=[],
        scripts=[],
        languages=['en'],
    ),
    "estournelles_typewritten": KrakenModel(
        model_id="10.5281/zenodo.10556673",
        name="Transcription model for Paul d'Estournelles de Constant's French typewritten letters (1914–1924)",
        lang="fr",
        script="Typewritten",
        notes=(
            "Chiffoleau, 2024."
        ),
        pretrained_on="Transcription model for Paul d'Estournelles de Constant's French typewritten letters (1914–1924)",
        centuries=[20],
        scripts=['Typewritten'],
        languages=['fr'],
    ),
    "fondue_gd_v2": KrakenModel(
        model_id="10.5281/zenodo.21536798",
        name="FoNDUE-GD",
        lang="fr",
        script="Latn",
        notes=(
            "Simon Gabay, Université de Genève, 2026. A large multilingual model "
            "aggregating 28 published corpora; the widest coverage in this registry, and "
            "the reason training_datasets is recorded: two of those corpora are test sets "
            "this project measures on, which is not visible from the model's name. "
            "Trained on https://doi.org/10.5281/zenodo.4746342, "
            "https://github.com/PonteIneptique/valais-recensement, "
            "https://doi.org/10.5281/zenodo.5153262, "
            "https://doi.org/10.5281/zenodo.3517776, "
            "https://doi.org/10.5281/zenodo.7695130, "
            "https://doi.org/10.5281/zenodo.3945087, https://github.com/FoNDUE- "
            "HTR/FONDUE-DE-MSS-18, https://github.com/FoNDUE-HTR/FONDUE-DE-AAEB-17, "
            "https://github.com/FoNDUE-HTR/FONDUE-FR-MSS-19, https://github.com/FoNDUE- "
            "HTR/FONDUE-FR-MSS-18, https://github.com/HTR-United/CREMMA-MSS-18, "
            "https://github.com/HTR-United/CREMMA-MSS-20, https://github.com/HTR- "
            "United/cremma-wikipedia, https://github.com/HTR-United/lectaurep- "
            "repertoires, https://github.com/HTR-United/lectaurep-bronod, "
            "https://github.com/HTR-United/CREMMA-AN-TestamentsDePoilus, "
            "https://github.com/HTRomance-Project/modern-roman-languages, "
            "https://github.com/Proyecto-Ocupacion-Araucania-UChile/HTR_Araucania_XIX, "
            "https://github.com/16thExegesisDH/HTR-Corpus-A, "
            "https://github.com/16thExegesisDH/HTR-Corpus-C, https://github.com/ARCHEO- "
            "POL/OCR_corpus_principal, https://github.com/Masculinites- "
            "Esclavagistes/MEGV-FR-MSS-18, "
            "https://github.com/PaulineJac/GasparoSardiToponomasia, "
            "https://github.com/alix-tz/moonshines, https://github.com/alix-tz/peraire- "
            "ground-truth, https://github.com/FoNDUE-HTR/FoNDUE_Wolfflin_Fotosammlung."
        ),
        pretrained_on="FoNDUE-GD",
        centuries=[16, 17, 18, 19, 20, 21],
        scripts=['Latn'],
        languages=['fr', 'de', 'es', 'la', 'nl'],
    ),
    "french_vietnamese": KrakenModel(
        model_id="10.5281/zenodo.17690418",
        name="HTR model for 19th–20th century French-Vietnamese historical documents (KQNBSEI)",
        lang="vi",
        script="Latin",
        notes=(
            "Le & Bui, 2025. Quốc Ngữ, Bulletin de la Société des Études Indochinoises. "
            "Fine-tune of 10.5281/zenodo.10592716 (served as kraken-catmus_print_large): "
            "not an independent candidate beside it."
        ),
        pretrained_on="HTR model for 19th–20th century French-Vietnamese historical documents (KQNBSEI)",
        centuries=[19, 20],
        scripts=[],
        languages=['vi', 'fr'],
    ),
    "gallicorpora": KrakenModel(
        model_id="10.5281/zenodo.7410529",
        name="Gallicorpora+",
        lang="fr",
        script="Latin",
        notes=(
            "Pinche & Gabay, 2022. OCR for French prints, 16th–19th c."
        ),
        pretrained_on="Gallicorpora+",
        centuries=[16, 17, 18, 19],
        scripts=[],
        languages=['fr'],
    ),
    "german_print": KrakenModel(
        model_id="10.5281/zenodo.10519596",
        name="OCR model for German prints trained from several datasets (german_print)",
        lang="de",
        script="Fraktur, Antiqua",
        notes=(
            "Weil & Kamlah, 2023. Generic: 15th c. incunabula to 20th c. prints."
        ),
        pretrained_on="OCR model for German prints trained from several datasets (german_print)",
        centuries=[15, 16, 17, 18, 19, 20],
        scripts=['Fraktur', 'Antiqua'],
        languages=['de', 'la'],
    ),
    "glagolitic_print": KrakenModel(
        model_id="10.5281/zenodo.7755504",
        name="HTR model for Glagolitic sources printed in the 16th c. Tübingen-Urach style",
        lang="cu",
        script="Glagolitic",
        notes=(
            "Rabus & Thompson, 2023. Transcribes INTO Latin script."
        ),
        pretrained_on="HTR model for Glagolitic sources printed in the 16th c. Tübingen-Urach style",
        centuries=[16],
        scripts=['Glagolitic'],
        languages=['cu'],
    ),
    "hebrew_sephardi": KrakenModel(
        model_id="10.5281/zenodo.5468665",
        name="Medieval Hebrew manuscripts in Sephardi bookhand v1.0",
        lang="he",
        script="Sephardi bookhand",
        notes=(
            "Stökl Ben Ezra, 2021. Trained on the Sephardi part of the BiblIA dataset."
        ),
        pretrained_on="Medieval Hebrew manuscripts in Sephardi bookhand v1.0",
        centuries=[],
        scripts=['Sephardi bookhand'],
        languages=['he'],
    ),
    "kuzushiji": KrakenModel(
        model_id="10.5281/zenodo.13942714",
        name="HTR model for (Japanese) Kuzushiji",
        lang="ja",
        script="Kuzushiji",
        notes=(
            "Stökl Ben Ezra, 2024. Kuronet dataset."
        ),
        pretrained_on="HTR model for (Japanese) Kuzushiji",
        centuries=[],
        scripts=['Kuzushiji'],
        languages=['ja'],
    ),
    "latin_expanded_11c": KrakenModel(
        model_id="10.5281/zenodo.13736584",
        name="Expanded transcription of 11th c. Latin manuscripts",
        lang="la",
        script="Latin",
        notes=(
            "Schonhardt, 2024. The companion of the graphematic model above, same "
            "project."
        ),
        pretrained_on="Expanded transcription of 11th c. Latin manuscripts",
        centuries=[11],
        scripts=[],
        languages=['la'],
    ),
    "latin_graphematic_11c": KrakenModel(
        model_id="10.5281/zenodo.13741957",
        name="Graphematic transcription of 11th c. Latin manuscripts",
        lang="la",
        script="Latin",
        notes=(
            "Schonhardt, 2024. Burchards Dekret Digital (Akademie Mainz)."
        ),
        pretrained_on="Graphematic transcription of 11th c. Latin manuscripts",
        centuries=[11],
        scripts=[],
        languages=['la'],
    ),
    "latin_incunabula": KrakenModel(
        model_id="10.5281/zenodo.11113737",
        name="Latin Incunabula and Early Prints",
        lang="la",
        script="Gothic, Antiqua",
        notes=(
            "Ost, 2024. Reichenau incunabula, Badische Landesbibliothek. Centuries "
            "inferred from 'incunabula' (pre-1501) and 'early prints'; the record gives "
            "no range. Fine-tune of 10.5281/zenodo.10592716 (served as kraken- "
            "catmus_print_large): not an independent candidate beside it. Trained on "
            "https://doi.org/10.5281/zenodo.11046062."
        ),
        pretrained_on="Latin Incunabula and Early Prints",
        centuries=[15, 16],
        scripts=['Gothic', 'Antiqua'],
        languages=['la'],
    ),
    "lectaurep_french": KrakenModel(
        model_id="10.5281/zenodo.6542744",
        name="LECTAUREP Contemporary French Model (Administration)",
        lang="fr",
        script="Latin",
        notes=(
            "Chagué, 2022. Ground truth from French administrative documents produced "
            "between 1742 and 1928."
        ),
        pretrained_on="LECTAUREP Contemporary French Model (Administration)",
        centuries=[18, 19, 20],
        scripts=[],
        languages=['fr'],
    ),
    "manu_mcfondue": KrakenModel(
        model_id="10.5281/zenodo.10886224",
        name="HTR-United",
        lang="fr",
        script="Latin",
        notes=(
            "Manu Mc Fondue (Manu McFrench v4) — Gabay & Chagué, 2024. Centuries and "
            "languages from the corpus table in the record (la 16th, fr 17th–19th, de "
            "18th)."
        ),
        pretrained_on="HTR-United",
        centuries=[16, 17, 18, 19],
        scripts=[],
        languages=['fr', 'la', 'de'],
    ),
    "mccatmus": KrakenModel(
        model_id="10.5281/zenodo.13788177",
        name="McCATMuS",
        lang="fr",
        script="Latn",
        notes=(
            "Chagué, 2024. Handwritten, printed and typewritten documents, 16th to 21st "
            "c.; 180+ manuscripts in 7 languages."
        ),
        pretrained_on="McCATMuS",
        centuries=[16, 17, 18, 19, 20, 21],
        scripts=['Latn'],
        languages=['fr', 'la', 'es', 'en', 'de', 'it', 'oc'],
    ),
    "medieval_latin_french_abbreviated": KrakenModel(
        model_id="10.5281/zenodo.7516310",
        name="HTR Model",
        lang="la",
        script="Medieval",
        notes=(
            "Medieval Latin and French 12th–15th c. WITH abbreviations (no expansion) — "
            "Camps & Vidal-Gorène, 2023. Manuscripts and charters."
        ),
        pretrained_on="HTR Model",
        centuries=[12, 13, 14, 15],
        scripts=['Medieval'],
        languages=['la', 'fro'],
    ),
    "medieval_latin_french_expanded": KrakenModel(
        model_id="10.5281/zenodo.7516057",
        name="HTR Model",
        lang="la",
        script="Medieval",
        notes=(
            "Medieval Latin and French 12th–15th c. EXPANDED (no abbreviation signs) — "
            "Camps & Vidal-Gorène, 2023. The companion of the abbreviated model. This is "
            "the model outremer measured as 'kraken-catmus_medieval' (#124)."
        ),
        pretrained_on="HTR Model",
        centuries=[12, 13, 14, 15],
        scripts=['Medieval'],
        languages=['la', 'fro'],
    ),
    "mendelssohn_letters": KrakenModel(
        model_id="10.5281/zenodo.18207676",
        name="Fanny loves Wilhelm",
        lang="de",
        script="Latin",
        notes=(
            "Kuhn, 2026. Letters and notes by Fanny Mendelssohn (Hensel, 1805–1847). "
            "Script not stated in the record. Trained on "
            "https://doi.org/10.5281/zenodo.15223354."
        ),
        pretrained_on="Fanny loves Wilhelm",
        centuries=[19],
        scripts=[],
        languages=['de'],
    ),
    "old_norse_am305": KrakenModel(
        model_id="10.5281/zenodo.20529753",
        name="HTR model for AM 305 fol (Járnsíða/Magnúsbók, Old Norse law, 13th c.)",
        lang="non",
        script="Latin",
        notes=(
            "Forester, 2026. Fine-tuned from a base trained on AM 302 fol (Grágás)."
        ),
        pretrained_on="HTR model for AM 305 fol (Járnsíða/Magnúsbók, Old Norse law, 13th c.)",
        centuries=[13],
        scripts=[],
        languages=['non'],
    ),
    "openiti_arabic_print": KrakenModel(
        model_id="10.5281/zenodo.7050270",
        name="Printed Arabic-Script Base Model Trained on the OpenITI Corpus",
        lang="ar",
        script="Arabic",
        notes=(
            "Kiessling, 2022. Arabic, Persian, Urdu and Ottoman print in diverse "
            "typefaces; the record calls it a base model for fine-tuning, not extensively "
            "verified."
        ),
        pretrained_on="Printed Arabic-Script Base Model Trained on the OpenITI Corpus",
        centuries=[],
        scripts=['Arabic'],
        languages=['ar', 'fa', 'ur', 'ota'],
    ),
    "openiti_ottoman_print": KrakenModel(
        model_id="10.5281/zenodo.7050342",
        name="Printed Ottoman Base Model Trained on the OpenITI Corpus",
        lang="ota",
        script="Arabic",
        notes=(
            "Kiessling, 2022. ~7100 lines of Ottoman print, fine-tuned from the Arabic- "
            "script base. Fine-tune of 10.5281/zenodo.7050270 (served as kraken- "
            "openiti_arabic_print): not an independent candidate beside it."
        ),
        pretrained_on="Printed Ottoman Base Model Trained on the OpenITI Corpus",
        centuries=[],
        scripts=['Arabic'],
        languages=['ota'],
    ),
    "openiti_persian_print": KrakenModel(
        model_id="10.5281/zenodo.7051644",
        name="Printed Persian Base Model Trained on the OpenITI Corpus",
        lang="fa",
        script="Arabic",
        notes=(
            "Kiessling, 2022. ~17k lines of Persian print, fine-tuned from the Arabic- "
            "script base. Fine-tune of 10.5281/zenodo.7050270 (served as kraken- "
            "openiti_arabic_print): not an independent candidate beside it."
        ),
        pretrained_on="Printed Persian Base Model Trained on the OpenITI Corpus",
        centuries=[],
        scripts=['Arabic'],
        languages=['fa'],
    ),
    "peraire_french": KrakenModel(
        model_id="10.5281/zenodo.8193498",
        name="Transcription model for Lucien Peraire's handwriting (French, 20th c.)",
        lang="fr",
        script="Latin",
        notes=(
            "Chagué, 2023. Fine-tuned from Manu McFrench."
        ),
        pretrained_on="Transcription model for Lucien Peraire's handwriting (French, 20th c.)",
        centuries=[20],
        scripts=[],
        languages=['fr'],
    ),
    "prima": KrakenModel(
        model_id="10.5281/zenodo.18220238",
        name="PRIMA HTR",
        lang="it",
        script="Latin",
        notes=(
            "Crespi, 2026. Italian early modern manuscripts, late 16th–18th c."
        ),
        pretrained_on="PRIMA HTR",
        centuries=[16, 17, 18],
        scripts=[],
        languages=['it'],
    ),
    "samaritan": KrakenModel(
        model_id="10.5281/zenodo.13814200",
        name="Recognition model for historical Samaritan manuscripts, one-column pages",
        lang="sam",
        script="Samaritan",
        notes=(
            "trained on 13 pentateuchal manuscripts, 2024. No century range in the "
            "record."
        ),
        pretrained_on="Recognition model for historical Samaritan manuscripts, one-column pages",
        centuries=[],
        scripts=['Samaritan'],
        languages=['sam'],
    ),
    "textualis_inzigkofen": KrakenModel(
        model_id="10.5281/zenodo.18207719",
        name="Textualis HTR model related to the Augustinian canonesses in Inzigkofen",
        lang="de",
        script="Textualis",
        notes=(
            "Eichenberger, 2026. The hand of Anna Jaeck (d. 1481); German, 15th c. Fine- "
            "tune of 10.5281/zenodo.13862096 (served as kraken-tridis_v2): not an "
            "independent candidate beside it. Trained on "
            "https://doi.org/10.5281/zenodo.17978574."
        ),
        pretrained_on="Textualis HTR model related to the Augustinian canonesses in Inzigkofen",
        centuries=[15],
        scripts=['Textualis'],
        languages=['de'],
    ),
    "tridis_v2": KrakenModel(
        model_id="10.5281/zenodo.13862096",
        name="TRIDIS v2",
        lang="la",
        script="Medieval",
        notes=(
            "Torres Aguilar, 2024. Documentary manuscripts (legal, administrative), "
            "11th–16th c. per the title, mostly 13th c. onwards per the description."
        ),
        pretrained_on="TRIDIS v2",
        centuries=[11, 12, 13, 14, 15, 16],
        scripts=['Medieval'],
        languages=['la', 'fr', 'es'],
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
    # TrOCR line-level models (served by trocr engine on idhefix :8202)
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
        model_id="dh-unibe/trocr-essoins-middle-latin",
        name="TrOCR Essoins Middle Latin",
        lang="la",
        task="line-ocr",
        requires_line_images=True,
        notes="Vision-encoder-decoder seq2seq. Middle Latin (legal documents, Essoins). 13th–15th c. Serviced by trocr engine.",
    ),
}


def get_primary_vlm() -> VLMModel:
    """The VLM the pipeline actually calls — ``config.GPUSTACK_MODEL_VISION``.

    Reads the configured id rather than returning this module's first entry
    (#538). "First available" never checked availability and could only return
    the one entry there was; when the config default moved and the table did
    not, the two disagreed for four weeks and #537 published the difference.
    Deriving one from the other is what makes a second answer impossible.

    An id the table does not describe still resolves, as a minimal entry. The
    table is a description, not a gate: a deployment sets
    ``GPUSTACK_MODEL_VISION`` and must not be broken by a row nobody added here.
    """
    known = next((m for m in VLM_MODELS.values()
                  if m.model_id == config.GPUSTACK_MODEL_VISION), None)
    if known is not None:
        return known
    return VLMModel(
        name=config.GPUSTACK_MODEL_VISION,
        endpoint=config.GPUSTACK_BASE_URL,
        model_id=config.GPUSTACK_MODEL_VISION,
        api_key_env="GPUSTACK_API_KEY",
        description="Configured via GPUSTACK_MODEL_VISION; not described in VLM_MODELS.",
    )


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
        # KRAKEN_MODELS_LIVE is the *kraken* registry that feeds select_kraken_model
        # and the kraken /ocr path. The gateway also serves trocr/party/vllm models;
        # they have their own registries and must NOT be picked here (a trocr id
        # sent to the kraken /ocr call returns 0 chars). #191 follow-up.
        #
        # This comment used to say the others "have their own selection paths",
        # which was true for trocr and party and false for vllm: there was none,
        # so those seven rows were filtered out here and collected nowhere.
        # ``refresh_vlm_registry`` below is where they go now (#540).
        if str(m.get("engine", "kraken")).lower() != "kraken":
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
            # gateway may return description=null → fall back to the id so the
            # selection log shows a real name, not "None".
            name=m.get("description") or model_id,
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


#: VLMs the ATR gateway serves, keyed by gateway id. Empty until the gateway
#: answers — there is no hand-maintained fallback on purpose, because a
#: hand-kept table of someone else's models is exactly what drifted in #538 and
#: in the KRAKEN_MODELS comment above.
VLM_GATEWAY_MODELS_LIVE: dict[str, GatewayVLMModel] = {}


def refresh_vlm_registry(client) -> dict[str, GatewayVLMModel]:
    """Collect the gateway's ``engine: vllm`` models (#540).

    These were being dropped by ``refresh_kraken_registry`` and picked up by
    nobody, so seven served models — including the project's own
    ``qwen3vl-medieval-german-v3`` — had no representation here at all.

    Registering a model does not select it. ``plan_models`` is untouched: a
    VLM selector is #539 and the measurement that would justify running one is
    #541. This only means the ids and their script/century/language metadata
    exist on this side of the wire.

    ``enabled: false`` rows are skipped. The gateway distinguishes *registered*
    from *servable*, and a disabled model in a runnable registry is a 404
    waiting for the first batch that picks it.
    """
    live: dict[str, GatewayVLMModel] = {}

    for m in client.list_models():
        if not isinstance(m, dict) or "id" not in m:
            continue
        if str(m.get("engine", "")).lower() != "vllm":
            continue
        if m.get("enabled") is False:
            continue

        centuries: list[int] = []
        for c in m.get("centuries") or []:
            try:
                centuries.append(int(c))
            except (ValueError, TypeError):
                pass

        model_id = m["id"]
        live[model_id] = GatewayVLMModel(
            model_id=model_id,
            # The gateway returns description=null for most of these, so the
            # id is the only name there is. Better than "None" in a log.
            name=m.get("description") or model_id,
            level=str(m.get("level") or "page"),
            scripts=list(m.get("scripts") or []),
            languages=list(m.get("languages") or []),
            centuries=centuries,
            base_model=m.get("base_model") or "",
            hf_repo=m.get("hf_repo") or "",
            notes=f"[live] {m.get('description') or ''}".strip(),
        )

    VLM_GATEWAY_MODELS_LIVE.clear()
    VLM_GATEWAY_MODELS_LIVE.update(live)
    return live
# Re-export from reconcile so agent_a.models is the stable public interface
from agent_a.reconcile import RECONCILE_SYSTEM, RECONCILE_DEFAULT_MAX_TOKENS
