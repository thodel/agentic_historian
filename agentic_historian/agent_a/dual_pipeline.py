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

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

import config
from utils import gpustack_client as gs
from agent_a import models
from agent_a.model_selector import select_kraken_model, select_best, SourceCriteria, RecognitionResult
from agent_a.kraken_ocr import _kraken_available
from agent_a.pary_ocr import party_transcribe, _party_available
from agent_a.reconcile import (
    reconcile,
    ReconciliationResult,
    RECONCILE_SYSTEM,
    RECONCILE_DEFAULT_MAX_TOKENS,
)
from agent_a.kraken_client import KrakenHTTPClient, KrakenClientError

# ── transformers lazy-loads (module-level so tests can patch them) ────────────
# Resolved lazily via _ensure_transformers_ready() so this package imports without
# transformers/torch installed. Tests patch _AutoProcessor / _AutoModelCls here.
_AutoProcessor: type | None = None          # transformers.AutoProcessor
_AutoModelCls: type | None = None           # seq2seq vision model class (TrOCR family)


def _ensure_transformers_ready() -> None:
    """Resolve the transformers classes on first use.

    AutoModelForVision2Seq was removed in transformers 5.x, so fall back through
    the seq2seq / vision-encoder-decoder chain. VisionEncoderDecoderModel exists
    in every version, so the loop always resolves _AutoModelCls.
    """
    global _AutoProcessor, _AutoModelCls
    if _AutoProcessor is not None:
        return
    import importlib
    tf = importlib.import_module("transformers")
    _AutoProcessor = tf.AutoProcessor
    for _name in ("AutoModelForVision2Seq", "AutoModelForSeq2SeqLM", "VisionEncoderDecoderModel"):
        cls = getattr(tf, _name, None)
        if cls is not None:
            _AutoModelCls = cls
            break



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
    recognitions: list = field(default_factory=list)  # all candidates (#234)
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


def _quality_score(transcription: str) -> float:
    """Independent HTR quality heuristic — NOT self-referential (#107).

    Deliberately does not ask the producing VLM to grade its own output (that
    signal is worthless and burns tokens). Mirrors
    agents.text_recognition._quality_score; kept as a separate copy because
    text_recognition imports from this module (importing it back would be a
    circular import).
    """
    if not transcription.strip():
        return 0.0
    text = transcription.strip()
    alpha_ratio = sum(c.isalpha() for c in text) / max(len(text), 1)
    if alpha_ratio < 0.1:      # mostly punctuation/noise
        return 0.2
    if len(text) < 20:         # too little to evaluate
        return 0.3
    return 0.8                 # non-empty, readable text is usable


def _run_vlm(
    image_path: Path,
    source_description: Optional[str] = None,
    model: Optional[models.VLMModel] = None,
) -> tuple[str, float]:
    """Run VLM transcription and score it with an INDEPENDENT heuristic.

    QA is no longer the same VLM grading itself (#107): we transcribe at
    temperature 0.0 (diplomatic/verbatim transcription is deterministic, not
    creative) and score the result with _quality_score.
    """
    if model is None:
        model = models.get_primary_vlm()

    prompt = _build_vlm_prompt(source_description)
    try:
        transcription = gs.chat_vision(
            prompt=prompt,
            image_source=str(image_path),
            temperature=0.0,
            max_tokens=32768,
            frequency_penalty=config.VLM_FREQUENCY_PENALTY,
            presence_penalty=config.VLM_PRESENCE_PENALTY,
        ).strip()
        return transcription, _quality_score(transcription)
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
    """Run Party/PARY HTR via the ATR gateway. Returns (transcription, error_or_model_id).

    Gateway first, exactly as _run_kraken does. This used to go straight to the
    LOCAL path: `_party_available()` shells out to `kraken list` on THIS host and
    checks whether the party model is downloaded here — but recognition does not
    happen here, that is the entire point of the gateway. tei has no local kraken
    models, so party reported
        "party model not available (run: kraken get 10.5281/zenodo.20642057)"
    on every run, while asterAIx sat there with a healthy atr-party service on
    :8203 that nobody called. The error even reads like a host instruction, which
    is how it got mis-filed against the gateway host (serving-atr-inference#30).

    Party is also not kraken-loadable (it is a party/safetensors model needing the
    standalone `party` package), so `kraken list` could never have listed it — the
    local check was doomed twice over.

    The local path stays as a fallback for a dev box that genuinely has party.
    """
    try:
        with KrakenHTTPClient() as client:
            # party is page-level → /recognize, NOT /ocr (which is kraken+trocr
            # auto-segment only and 400s on party). See #310 follow-up.
            result = client.recognize(image=image_path, model="party")
        return result.text, "party"
    except KrakenClientError as e:
        logger.warning(f"[party] Service error, falling back to local: {e}")
    except Exception as e:
        return "", str(e)

    # Fallback: a local party install (dev boxes); on tei this correctly reports
    # that it is not available rather than pretending.
    if not _party_available():
        return "", "party unavailable (gateway unreachable, not installed locally)"
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
        from PIL import Image as PILImage

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype  = torch.bfloat16 if device == "cuda" else torch.float32

        # Lazy-load the right model class (handles transformers version differences)
        _ensure_transformers_ready()

        # The deployed registry models (TrOCR family, LightOnOCR) are seq2seq
        # vision-encoder-decoder models — decode via generate(), not CTC logits.
        processor = _AutoProcessor.from_pretrained(model.model_id)
        model_hf  = _AutoModelCls.from_pretrained(
            model.model_id,
            torch_dtype=dtype,
        ).to(device)

        image = PILImage.open(image_path).convert("RGB")

        if model.requires_line_images:
            # ── TrOCR / line-model path: kraken pre-segmentation (#235 P2-2) ──
            try:
                with KrakenHTTPClient() as client:
                    seg = client.segment(image=image_path, seg_mode="baseline")
                lines_raw = seg.get("lines", [])
            except KrakenClientError as e:
                return "", f"[TrOCR] kraken segmentation failed: {e}"

            if not lines_raw:
                return "", "[TrOCR] no lines found by kraken segmenter"

            results: list[str] = []
            for line_meta in lines_raw:
                polygon = line_meta.get("baseline", [])
                if len(polygon) < 2:
                    continue
                xs = [p[0] for p in polygon]
                ys = [p[1] for p in polygon]
                x0, x1 = max(0, int(min(xs)) - 2), int(max(xs)) + 2
                y0, y1 = max(0, int(min(ys)) - 2), int(max(ys)) + 2
                line_img = image.crop((x0, y0, x1, y1))
                inputs = processor(images=line_img, return_tensors="pt").to(device)
                with torch.no_grad():
                    generated = model_hf.generate(**inputs, max_new_tokens=512)
                pred = processor.batch_decode(generated, skip_special_tokens=True)[0]
                if pred.strip():
                    results.append(pred.strip())

            if not results:
                return "", f"[TrOCR] no lines produced text (model={model.model_id})"
            return "\n".join(results), model.model_id
        else:
            inputs = processor(images=image, return_tensors="pt").to(device)
            with torch.no_grad():
                generated = model_hf.generate(**inputs, max_new_tokens=1024)
            pred = processor.batch_decode(generated, skip_special_tokens=True)[0]
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
        # chat_text already routes to GPUSTACK_MODEL_TEXT (do not pass model=).
        reconciled = gs.chat_text(
            prompt,
            system=None,
            max_tokens=RECONCILE_DEFAULT_MAX_TOKENS,
            temperature=0.3,
        ).strip()
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

    # ── Concurrent fan-out: all enabled OCR paths run in parallel ─────────────
    # Each _run_* function returns (text_or_empty_str, model_id_or_error_str).
    # Results are collected into recognitions list for the CER harness (#234).
    def _make_run_vlm():
        if not run_vlm:
            return None
        def _task():
            text, score = _run_vlm(image_path, source_description)
            return RecognitionResult(
                engine="vlm",
                model_id=models.get_primary_vlm().model_id,
                text=text,
                confidence=score,
                error="" if text else "No output from VLM",
            )
        return _task

    def _make_run_kraken():
        if not run_kraken:
            return None
        def _task():
            text, model_or_err = _run_kraken(
                image_path, source_description=source_description, lang=lang
            )
            return RecognitionResult(
                engine="kraken",
                model_id=model_or_err if not text else _kraken_model_id_for_text(image_path, source_description, lang) or model_or_err,
                text=text,
                error=model_or_err if not text else "",
            )
        return _task

    def _make_run_party():
        if not run_party:
            return None
        def _task():
            text, msg = _run_party(image_path)
            return RecognitionResult(
                engine="party",
                model_id="10.5281/zenodo.20642057",
                text=text,
                error=msg if not text else "",
            )
        return _task

    def _make_run_hf():
        if not run_hf:
            return None
        def _task():
            text, model_or_err = _run_hf_ocr(image_path, lang)
            return RecognitionResult(
                engine="hf",
                model_id=model_or_err if not text else model_or_err,
                text=text,
                error=model_or_err if not text else "",
            )
        return _task

    # Helper: resolve the model_id for kraken after the fact
    def _kraken_model_id_for_text(image_path, source_description, lang):
        if source_description:
            matches = select_best("kraken", SourceCriteria.from_agent_b(source_description), top_k=1)
            if matches:
                return matches[0].model.model_id
        from agent_a import models as m
        fallback = m.kraken_model_for_lang(lang)
        return fallback.model_id if fallback else ""

    # Submit all enabled tasks to the thread pool
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {}
        for maker in [_make_run_vlm, _make_run_kraken, _make_run_party, _make_run_hf]:
            task = maker()
            if task:
                futures[pool.submit(task)] = None

        for future in as_completed(futures):
            try:
                rec = future.result()
                result.recognitions.append(rec)
                # Also populate DualTranscriptionResult fields for backward compat
                if rec.engine == "vlm":
                    result.vlm_transcription = rec.text
                    result.vlm_score = rec.confidence
                    if not rec.text:
                        result.error_vlm = rec.error
                elif rec.engine == "kraken":
                    result.kraken_transcription = rec.text
                    if not rec.text:
                        result.error_kraken = rec.error
                elif rec.engine == "party":
                    result.party_transcription = rec.text
                    if not rec.text:
                        result.error_party = rec.error
                elif rec.engine == "hf":
                    result.hf_transcription = rec.text
                    result.hf_available = bool(rec.text)
                    if not rec.text:
                        result.error_hf = rec.error
            except Exception as exc:
                logger.warning(f"[dual_pipeline] Path task failed: {exc}")

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
