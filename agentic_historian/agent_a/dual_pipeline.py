"""
agent_a/dual_pipeline.py — Multi-pathway HTR/OCR entry point.

Combines:
  Path 1 (VLM):  InternVL3 via GPUStack, prompt enriched with Agent B description
  Path 2 (kraken): Baseline segmentation + kraken OCR models
  Path 3 (Party): PARY HTR via kraken (zenodo:20642057)
  Path 4 (HF):   HuggingFace OCR models (e.g. LightOnOCR)
  Comparison:    LLM-based reconciliation of all available outputs

Usage:
  result = transcribe_dual("path/to/image.jpg")
  result = transcribe_dual("path/to/image.jpg", lang="la", source_description=description_md)
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

import config
from utils import gpustack_client as gs
from agent_a import models
from agent_a.model_selector import select_kraken_model, SourceCriteria
from agent_a.kraken_ocr import _kraken_available
from agent_a.pary_ocr import party_transcribe, _party_available
from agent_a.reconcile import (
    reconcile,
    ReconciliationResult,
    RECONCILE_SYSTEM,
)
from agent_a.kraken_client import KrakenHTTPClient, KrakenClientError


SYSTEM_HTR = (
    "Du bist ein Experte fuer historische Handschriftenerkennung (HTR). "
    "Transkribiere den Text EXAKT wie er erscheint — achte auf Gotische/Kursive "
    "Schriften des 14.-16. Jahrhunderts. "
    "Beibehalten: Abkuerzungen, Nasalstriche, Kuerzel, Zeilenumbruch-Marker. "
    "Gib nur die Transkription aus, keine Kommentare."
)


@dataclass
class DualTranscriptionResult:
    """Result of the multi-pathway HTR pipeline."""
    doc_id: str
    vlm_transcription: str = ""
    kraken_transcription: str = ""
    party_transcription: str = ""
    hf_transcription: str = ""
    reconciliation: Optional[ReconciliationResult] = None
    vlm_score: float = 0.0
    kraken_available: bool = False
    party_available: bool = False
    hf_available: bool = False
    error_vlm: str = ""
    error_kraken: str = ""
    error_party: str = ""
    error_hf: str = ""
    method_used: str = "multi"

    def best_transcription(self) -> str:
        """Returns the best available transcription."""
        if self.reconciliation:
            return self.reconciliation.reconciled
        for txt in (self.vlm_transcription, self.kraken_transcription,
                    self.party_transcription, self.hf_transcription):
            if txt.strip():
                return txt
        return ""

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "best_transcription": self.best_transcription(),
            "vlm_transcription": self.vlm_transcription,
            "kraken_transcription": self.kraken_transcription,
            "party_transcription": self.party_transcription,
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
            "party_available": self.party_available,
            "hf_available": self.hf_available,
            "error_vlm": self.error_vlm,
            "error_kraken": self.error_kraken,
            "error_party": self.error_party,
            "error_hf": self.error_hf,
            "method_used": self.method_used,
        }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _build_vlm_prompt(source_description: Optional[str] = None) -> str:
    base = (
        "Transkribiere den Handschrifttext EXAKT. "
        "Erhalte Abkuerzungen, Nasalstriche, Kuerzel. "
        "Trenne Seiten mit '--- SEITE N ---'."
    )
    if source_description:
        base += (
            f"\n\nKontext zur Quelle (von Agent B):\n{source_description[:1000]}\n\n"
            "Nutze diesen Kontext, um Unsicherheiten besser zu entschaerfen."
        )
    return base


def _run_vlm(
    image_path: Path,
    source_description: Optional[str] = None,
    model: Optional[models.VLMModel] = None,
) -> tuple[str, float]:
    """Run VLM (InternVL3) transcription with QA score."""
    if model is None:
        model = models.get_primary_vlm()

    prompt = _build_vlm_prompt(source_description)
    try:
        transcription = gs.chat_vision(
            prompt=prompt,
            image_source=str(image_path),
            temperature=0.2,
            max_tokens=32768,
        ).strip()
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
        match = re.search(r"0\.\d+", qa_raw)
        score = float(match.group()) if match else 0.5
        return transcription, score
    except Exception as e:
        logger.warning(f"[VLM path] Transcription failed: {e}")
        return "", 0.0


def _run_kraken(
    image_path: Path,
    source_description: Optional[str] = None,
    lang: str = "de",
) -> tuple[str, str]:
    """
    Run kraken OCR via the remote HTTP service.

    Model selection is driven by Agent B's source description:
      → model_selector.select_best_kraken_model(source_description)

    Falls back to ``lang``‑only lookup if no description is provided.

    Returns (transcription, model_id_or_error).
    """
    # 1. Pick the best model using Agent B metadata.
    # select_kraken_model returns list[ModelMatch] (.model, .score, .matched_on).
    if source_description:
        best = select_kraken_model(SourceCriteria.from_agent_b(source_description), top_k=1)
        if best:
            kraken_model = best[0].model
            logger.info(
                f"[kraken] Model selected via Agent B description: "
                f"{kraken_model.name} (score={best[0].score:.2f})"
            )
        else:
            kraken_model = models.kraken_model_for_lang(lang)
            logger.warning(
                f"[kraken] No model matched '{source_description[:80]}...', "
                f"falling back to lang={lang}"
            )
    else:
        kraken_model = models.kraken_model_for_lang(lang)

    if kraken_model is None:
        return "", f"No kraken model found for lang={lang}"

    # 2. Call the remote kraken service
    try:
        with KrakenHTTPClient() as client:
            result = client.transcribe(
                image=image_path,
                model=kraken_model.model_id,
                seg_mode="baseline",
            )
        return result.text, kraken_model.model_id
    except KrakenClientError as e:
        logger.warning(f"[kraken] Service error, falling back to local CLI: {e}")
        # Graceful degradation: try local kraken CLI if service is down
        return _run_kraken_local(image_path, kraken_model)
    except Exception as e:
        return "", str(e)


def _run_kraken_local(image_path: Path, model: models.KrakenModel) -> tuple[str, str]:
    """Fallback: run kraken locally via CLI if the service is unavailable."""
    if not _kraken_available():
        return "", "kraken unavailable (CLI not installed, service unreachable)"
    try:
        from agent_a.kraken_ocr import kraken_transcribe
        transcription, _ = kraken_transcribe(image_path, model.model_id)
        return transcription, f"{model.model_id} (local fallback)"
    except Exception as e:
        return "", str(e)


def _run_party(image_path: Path) -> tuple[str, str]:
    """Run Party/PARY HTR. Returns (transcription, error_or_model_id)."""
    if not _party_available():
        return "", "party model not available (run: kraken get 10.5281/zenodo.20642057)"

    try:
        transcription, _ = party_transcribe(image_path)
        return transcription, models.PARTY_MODEL.model_id
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


def _reconcile_merge(all_text: str, use_llm: bool = True) -> ReconciliationResult:
    """Merge 3+ transcriptions via LLM."""
    if not use_llm:
        # Simple first-available fallback
        lines = [ln.strip() for ln in all_text.splitlines() if ln.strip()]
        return ReconciliationResult(
            reconciled="\n".join(lines[:100]),
            vlm_only_lines=[],
            kraken_only_lines=[],
            agreement_score=0.0,
            diff_lines=0,
            method="merge_fallback",
        )

    prompt = (
        f"{RECONCILE_SYSTEM}\n\n"
        f"Fasse die folgenden Transkriptionen zu einer einzigen, "
        f"moeglichst vollstaendigen und korrekten Fassung zusammen. "
        f"Markiere abweichende Stellen mit der Quellbezeichnung.\n\n"
        f"{all_text}\n\n=== REKONCILIERTE FASSUNG ==="
    )
    try:
        reconciled = gs.chat_text(prompt, system=None, max_tokens=16384, temperature=0.3).strip()
        return ReconciliationResult(
            reconciled=reconciled,
            vlm_only_lines=[],
            kraken_only_lines=[],
            agreement_score=0.5,
            diff_lines=0,
            method="llm_merge",
        )
    except Exception as e:
        logger.warning(f"[reconcile] LLM merge failed: {e}")
        return ReconciliationResult(
            reconciled=all_text,
            vlm_only_lines=[],
            kraken_only_lines=[],
            agreement_score=0.0,
            diff_lines=0,
            method="merge_error",
        )


# ── Public API ───────────────────────────────────────────────────────────────

def transcribe_dual(
    image_path: str | Path,
    *,
    source_description: Optional[str] = None,
    lang: str = "de",
    run_vlm: bool = True,
    run_kraken: bool = True,
    run_party: bool = True,
    run_hf: bool = False,
    use_llm_reconcile: bool = True,
) -> DualTranscriptionResult:
    """
    Multi-pathway HTR/OCR pipeline (3+ paths).

    Paths:
      1. VLM (InternVL3 via GPUStack) — enriched with Agent B description
      2. kraken  — baseline segmentation + community OCR models
      3. Party/PARY — kraken-format medieval HTR model (zenodo:20642057)
      4. HuggingFace OCR (optional)

    Args:
        image_path:         Path to the manuscript image
        source_description: Optional Agent B description for VLM prompt enrichment
        lang:               Language/script code (de, la, fr, etc.)
        run_vlm:            Enable VLM path
        run_kraken:         Enable kraken community model path
        run_party:          Enable Party/PARY HTR path
        run_hf:             Enable HuggingFace OCR path
        use_llm_reconcile:  Use LLM for reconciliation (if False, diff-based)

    Returns:
        DualTranscriptionResult with all transcriptions and reconciliation
    """
    image_path = Path(image_path)
    doc_id = image_path.stem
    logger.info(f"[dual_pipeline] Starting: {doc_id}")

    result = DualTranscriptionResult(doc_id=doc_id)
    result.kraken_available = _kraken_available()
    result.party_available = _party_available()

    # ── Path 1: VLM ──────────────────────────────────────────────────────────
    if run_vlm:
        result.vlm_transcription, result.vlm_score = _run_vlm(
            image_path, source_description
        )
        if not result.vlm_transcription:
            result.error_vlm = "No output from VLM"

    # ── Path 2: kraken (remote service + Agent B model selection) ──────────
    if run_kraken:
        kraken_text, kraken_model = _run_kraken(
            image_path, source_description=source_description, lang=lang
        )
        if kraken_text:
            result.kraken_transcription = kraken_text
        else:
            result.error_kraken = kraken_model

    # ── Path 3: Party / PARY HTR ──────────────────────────────────────────────
    if run_party:
        party_text, party_msg = _run_party(image_path)
        if party_text:
            result.party_transcription = party_text
        else:
            result.error_party = party_msg

    # ── Path 4: HuggingFace OCR ──────────────────────────────────────────────
    if run_hf:
        hf_text, hf_model_id = _run_hf_ocr(image_path, lang)
        if hf_text:
            result.hf_transcription = hf_text
        else:
            result.error_hf = hf_model_id
        result.hf_available = bool(hf_text)

    # ── Reconciliation ────────────────────────────────────────────────────────
    available = [
        (result.vlm_transcription, "vlm"),
        (result.kraken_transcription, "kraken"),
        (result.party_transcription, "party"),
        (result.hf_transcription, "hf"),
    ]
    texts = {src: txt for txt, src in available if txt.strip()}

    if len(texts) >= 2:
        primary   = texts.get("vlm", "")
        secondary = (
            texts.get("kraken")
            or texts.get("party")
            or texts.get("hf", "")
        )
        if primary and secondary:
            result.reconciliation = reconcile(
                primary, secondary, use_llm=use_llm_reconcile
            )
            result.method_used = f"multi_reconcile_{result.reconciliation.method}"
        else:
            all_text = "\n\n".join(
                f"=== [{src.upper()}] ===\n{txt}" for src, txt in texts.items()
            )
            result.reconciliation = _reconcile_merge(all_text, use_llm=use_llm_reconcile)
            result.method_used = "multi_merge"
        logger.info(
            f"[dual_pipeline] Reconciled ({result.method_used}): "
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