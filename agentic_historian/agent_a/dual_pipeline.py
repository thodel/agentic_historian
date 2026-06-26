"""
agent_a/dual_pipeline.py — Two-pronged HTR/OCR entry point.

Combines:
  Path 1 (VLM):  InternVL3 via GPUStack, prompt enriched with Agent B description
  Path 2 (kraken): Baseline segmentation + kraken OCR models
  Path 2 (HF):   HuggingFace OCR models (e.g. LightOnOCR)
  Comparison:    LLM-based reconciliation of all available outputs

Usage:
  result = transcribe_dual("path/to/image.jpg")
  result = transcribe_dual("path/to/image.jpg", lang="la", source_description=description_md)
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

import config
from utils import gpustack_client as gs
from agent_a import models
from agent_a.kraken_ocr import kraken_transcribe, _kraken_available
from agent_a.reconcile import reconcile, ReconciliationResult

SYSTEM_HTR = (
    "Du bist ein Experte fuer historische Handschriftenerkennung (HTR). "
    "Transkribiere den Text EXAKT wie er erscheint — achte auf Gotische/Kursive "
    "Schriften des 14.-16. Jahrhunderts. "
    "Beibehalten: Abkuerzungen, Nasalstriche, Kuerzel, Zeilenumbruch-Marker. "
    "Gib nur die Transkription aus, keine Kommentare."
)


@dataclass
class DualTranscriptionResult:
    """Result of the dual-pathway HTR pipeline."""
    doc_id: str
    vlm_transcription: str = ""
    kraken_transcription: str = ""
    hf_transcription: str = ""
    reconciliation: Optional[ReconciliationResult] = None
    vlm_score: float = 0.0
    kraken_available: bool = False
    hf_available: bool = False
    error_vlm: str = ""
    error_kraken: str = ""
    error_hf: str = ""
    method_used: str = "dual"

    def best_transcription(self) -> str:
        """Returns the best available transcription."""
        if self.reconciliation:
            return self.reconciliation.reconciled
        if self.vlm_transcription.strip():
            return self.vlm_transcription
        if self.kraken_transcription.strip():
            return self.kraken_transcription
        return self.hf_transcription

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "best_transcription": self.best_transcription(),
            "vlm_transcription": self.vlm_transcription,
            "kraken_transcription": self.kraken_transcription,
            "hf_transcription": self.hf_transcription,
            "vlm_score": self.vlm_score,
            "agreement_score": (
                self.reconciliation.agreement_score
                if self.reconciliation else None
            ),
            "reconciliation_method": (
                self.reconciliation.method
                if self.reconciliation else None
            ),
            "kraken_available": self.kraken_available,
            "hf_available": self.hf_available,
            "error_vlm": self.error_vlm,
            "error_kraken": self.error_kraken,
            "error_hf": self.error_hf,
            "method_used": self.method_used,
        }


def _build_vlm_prompt(source_description: Optional[str] = None) -> str:
    """Build HTR prompt, optionally enriched with source description."""
    base = (
        "Transkribiere den Handschrifttext EXAKT. "
        "Erhalte Abkuerzungen, Nasalstriche, Kuerzel. "
        "Trenne Seiten mit '--- SEITE N ---'."
    )
    if source_description:
        base += (
            f"\n\nKontext zur Quelle (von Agent B):\n{source_description[:1000]}\n\n"
            "Nutze diesen Kontext, um Unsicherheiten in der Transkription besser zu entschärfen."
        )
    return base


def _run_vlm(
    image_path: Path,
    source_description: Optional[str] = None,
    model: Optional[models.VLMModel] = None,
) -> tuple[str, float]:
    """Run VLM (InternVL3) transcription with optional QA score."""
    if model is None:
        model = models.get_primary_vlm()

    prompt = _build_vlm_prompt(source_description)
    try:
        transcription = gs.chat_vision(
            prompt=prompt,
            image_source=str(image_path),
            temperature=1.0,
            max_tokens=32768,
        ).strip()
        # QA score
        qa_raw = gs.chat_vision(
            prompt=(
                f"Begutachte diese Transkription:\n\n{transcription}\n\n"
                "Vergib einen QC-Score von 0.0 bis 1.0. "
                "Antworte NUR mit einer Zahl."
            ),
            image_source=str(image_path),
            temperature=0.3,
            max_tokens=50,
        )
        import re
        match = re.search(r"0\.\d+", qa_raw)
        score = float(match.group()) if match else 0.5
        return transcription, score
    except Exception as e:
        logger.warning(f"[VLM path] Transcription failed: {e}")
        return "", 0.0


def _run_kraken(
    image_path: Path,
    lang: str = "de",
) -> tuple[str, str]:
    """Run kraken OCR pipeline. Returns (transcription, model_used_or_error)."""
    if not _kraken_available():
        return "", "kraken CLI not installed"

    model = models.kraken_model_for_lang(lang)
    if model is None:
        # Try to find any German/Latin model
        for m in models.KRAKEN_MODELS.values():
            model = m
            break
        if model is None:
            return "", f"No kraken model configured for lang={lang}"

    try:
        transcription, _ = kraken_transcribe(image_path, model.model_id)
        return transcription, model.model_id
    except Exception as e:
        return "", str(e)


def _run_hf_ocr(
    image_path: Path,
    lang: str = "la",
) -> tuple[str, str]:
    """Run HuggingFace OCR model (e.g. LightOnOCR)."""
    model = models.hf_model_for_lang(lang)
    if model is None:
        return "", f"No HF model configured for lang={lang}"

    try:
        # Lazy-import to avoid hard dependency when not used
        import torch
        from transformers import AutoProcessor, AutoModelForCTC
        from PIL import Image as PILImage

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype  = torch.bfloat16 if device == "cuda" else torch.float32

        processor = AutoProcessor.from_pretrained(model.model_id)
        model_hf  = AutoModelForCTC.from_pretrained(
            model.model_id,
            torch_dtype=dtype,
        ).to(device)

        image = PILImage.open(image_path).convert("RGB")

        if model.requires_line_images:
            # TODO: wire kraken segmentation first to get line crops
            return "", "line-image mode requires kraken pre-segmentation (not yet wired)"
        else:
            inputs = processor(images=image, return_tensors="pt").to(device)
            with torch.no_grad():
                logits = model_hf(inputs.pixel_values).logits
            pred = processor.batch_decode(torch.argmax(logits, dim=-1))[0]
            return pred, model.model_id

    except ImportError as e:
        return "", f"Missing dependency: {e}"
    except Exception as e:
        return "", str(e)


def transcribe_dual(
    image_path: str | Path,
    *,
    source_description: Optional[str] = None,
    lang: str = "de",
    run_vlm: bool = True,
    run_kraken: bool = True,
    run_hf: bool = False,
    use_llm_reconcile: bool = True,
) -> DualTranscriptionResult:
    """
    Two-pronged HTR/OCR pipeline.

    Args:
        image_path:        Path to the manuscript image
        source_description: Optional Agent B description for prompt enrichment
        lang:              Language/script code (de, la, fr, etc.)
        run_vlm:           Enable VLM path
        run_kraken:        Enable kraken path
        run_hf:            Enable HuggingFace OCR path
        use_llm_reconcile: Use LLM for reconciliation (if False, diff-based)

    Returns:
        DualTranscriptionResult with all transcriptions and reconciliation
    """
    image_path = Path(image_path)
    doc_id = image_path.stem
    logger.info(f"[dual_pipeline] Starting: {doc_id}")

    result = DualTranscriptionResult(doc_id=doc_id)
    result.kraken_available = _kraken_available()

    # ── Path 1: VLM ──────────────────────────────────────────────────────────
    if run_vlm:
        result.vlm_transcription, result.vlm_score = _run_vlm(
            image_path, source_description
        )
        if not result.vlm_transcription:
            result.error_vlm = "No output from VLM"

    # ── Path 2a: kraken ──────────────────────────────────────────────────────
    if run_kraken:
        kraken_text, kraken_model = _run_kraken(image_path, lang)
        if kraken_text:
            result.kraken_transcription = kraken_text
        else:
            result.error_kraken = kraken_model  # contains error message

    # ── Path 2b: HuggingFace ──────────────────────────────────────────────────
    if run_hf:
        hf_text, hf_model_id = _run_hf_ocr(image_path, lang)
        if hf_text:
            result.hf_transcription = hf_text
        else:
            result.error_hf = hf_model_id  # contains error message
        result.hf_available = bool(hf_text)

    # ── Reconciliation ────────────────────────────────────────────────────────
    available = [
        (result.vlm_transcription, "vlm"),
        (result.kraken_transcription, "kraken"),
        (result.hf_transcription, "hf"),
    ]
    texts = {src: txt for txt, src in available if txt.strip()}

    if len(texts) >= 2:
        # Reconcile first two (VLM vs kraken as primary comparison)
        vlm_txt  = texts.get("vlm", "")
        kraken_txt = texts.get("kraken", texts.get("hf", ""))
        result.reconciliation = reconcile(vlm_txt, kraken_txt, use_llm=use_llm_reconcile)
        result.method_used = f"dual_reconcile_{result.reconciliation.method}"
        logger.info(
            f"[dual_pipeline] Reconciled ({result.reconciliation.method}): "
            f"agreement={result.reconciliation.agreement_score:.2f}"
        )
    elif len(texts) == 1:
        src = list(texts.keys())[0]
        result.method_used = f"single_{src}"
        logger.info(f"[dual_pipeline] Single path ({src}), no reconciliation needed")
    else:
        result.method_used = "no_result"
        logger.warning(f"[dual_pipeline] All paths failed for {doc_id}")

    logger.info(f"[dual_pipeline] Done: {doc_id} — method: {result.method_used}")
    return result