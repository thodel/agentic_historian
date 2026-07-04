"""
orchestrator.py — Orchestriert die fünf Agenten und koordiniert die Pipeline.

Pipeline-Logik (A → B → Kraken-Re-Run → C → D):
  Phase 1  Agent A (VLM only) → liefert erste Transkription
  Phase 2  Agent B → Quellenbeschreibung + Modellvorschlag
  Phase 3  Kraken-Re-Run mit Agent-B-gesteurter Modellwahl
  Phase 4  Agent C → Entities
  Phase 5  Agent D (optional)

Die Kraken-Pfade (Path 2 + Path 3) werden also NACH Agent B
neu ausgefuehrt, mit dem besten Modell gemaess model_selector.
"""

import json
import re
from pathlib import Path
from typing import Optional

from loguru import logger

import config
from agents import (
    text_recognition as agent_a,
    source_description as agent_b,
    entity_agent as agent_c,
    corpus_analysis as agent_d,
    meta_agent as agent_e,
)
from knowledge_hub import hub

# Optional two-pronged HTR (requires agent_a package)
try:
    from agent_a import transcribe_dual, DualTranscriptionResult
    from agent_a.kraken_client import KrakenHTTPClient, KrakenClientError
    from agent_a.model_selector import select_kraken_model, SourceCriteria
    from agent_a.reconcile import reconcile
    from agent_a.models import refresh_kraken_registry, KRAKEN_MODELS_LIVE
    DUAL_AVAILABLE = True
except ImportError:
    DUAL_AVAILABLE = False
    DualTranscriptionResult = None
    refresh_kraken_registry = None
    KRAKEN_MODELS_LIVE = None

from run_state import RunState, StageStatus

# Result-Pipeline
PipelineResult = dict


class PipelineContext:
    """Sammelt Ergebnisse über alle Agenten hinweg."""

    def __init__(self, doc_id: str):
        self.doc_id = doc_id
        self.transcription: str = ""
        self.description: dict = {}
        self.entities: dict = {}
        self.errors: list = []
        self.a_meta: dict = {}
        self.dual_result: Optional[DualTranscriptionResult] = None

    def to_json(self) -> dict:
        base = {
            "doc_id": self.doc_id,
            "transcription": self.transcription,
            "description": self.description,
            "entities": self.entities,
            "errors": self.errors,
        }
        if self.a_meta:
            base["a_meta"] = self.a_meta
        return base


# ── Phase 3: kraken re-run with Agent B model selection ──────────────────────

def _rerun_kraken_with_model_selection(
    image_path: Path,
    source_description: str,
    lang: str = "de",
) -> dict:
    """
    Phase-3-Step: Bild + Agent-B-Beschreibung → kraken-Modellauswahl → Remote OCR.

    Returns a dict with kraken_transcription, party_transcription,
    kraken_model, party_model, and any errors.
    """
    logger.info("[Orchestrator] Phase 3: kraken re-run with Agent B model selection")

    result = {
        "kraken_transcription": "",
        "party_transcription": "",
        "kraken_model": None,
        "party_model": None,
        "error_kraken": "",
        "error_party": "",
    }

    # Select best kraken model using Agent B description.
    # NOTE: select_kraken_model returns a list[ModelMatch] (each has .model,
    # .score, .matched_on). The old code called select_best_kraken_model (which
    # returns a single KrakenModel|None) and indexed it as a list → TypeError.
    criteria = SourceCriteria.from_agent_b(source_description)
    best_matches = select_kraken_model(criteria, top_k=3)
    if best_matches:
        top = best_matches[0]
        kraken_model = top.model
        logger.info(
            f"[Phase 3] Best kraken model: {kraken_model.name} "
            f"(score={top.score:.2f}) — matched: {', '.join(top.matched_on)}"
        )
        result["kraken_model"] = kraken_model
    else:
        logger.warning("[Phase 3] No model match from Agent B description, using lang fallback")
        from agent_a import models as kraken_models
        kraken_model = kraken_models.kraken_model_for_lang(lang)

    # Run kraken via remote service
    if kraken_model:
        try:
            with KrakenHTTPClient() as client:
                ocr_result = client.transcribe(
                    image=image_path,
                    model=kraken_model.model_id,
                    seg_mode="baseline",
                )
            result["kraken_transcription"] = ocr_result.text
            logger.info(
                f"[Phase 3] kraken OCR done: {len(ocr_result.text)} chars, "
                f"conf={ocr_result.confidence:.2f}"
            )
        except KrakenClientError as e:
            result["error_kraken"] = str(e)
            logger.warning(f"[Phase 3] kraken service error: {e}")
        except Exception as e:
            result["error_kraken"] = str(e)
            logger.warning(f"[Phase 3] kraken error: {e}")

    # Also re-run Party/PARY if available (it is model-selected already)
    try:
        from agent_a.pary_ocr import party_transcribe, _party_available
        if _party_available():
            party_text, _ = party_transcribe(image_path)
            result["party_transcription"] = party_text
    except Exception as e:
        result["error_party"] = str(e)
        logger.warning(f"[Phase 3] Party/PARY error: {e}")

    return result


def _stage_needs_run(rs: RunState, stage: str) -> bool:
    """Return True if the stage is PENDING or DIRTY (needs execution)."""
    status = rs.stage_status(stage)
    return status in (StageStatus.PENDING, StageStatus.DIRTY)


def run_full_pipeline(
    file_path: str | Path,
    image_path: Optional[str | Path] = None,
    run_agent_d: bool = False,
    use_dual_htr: bool = False,
    source_description: Optional[str] = None,
    lang: str = "de",
) -> PipelineResult:
    """
    Führt A → B → Kraken-Re-Run → C (→ D) Pipeline aus.

    Zwei-Phasen-Agent-A-Logik:
      1. VLM-only erste Transkription ( fuer Agent B )
      2. Kraken-Re-Run nach Agent B mit modellbasierter Auswahl

    Args:
        file_path:           Pfad zum Dokument (Bild oder PDF)
        image_path:          Expliziter Bildpfad (optional)
        run_agent_d:         Agent D (Corpus) anschliessend
        use_dual_htr:        Wenn True: VLM + Kraken in Phase 1
        source_description:  Optionaler Agent-B-Description-String
        lang:                Sprache/Skriftskode (de, la, fr, ...)
    """
    fp = Path(file_path)
    doc_id = fp.stem
    img = Path(image_path) if image_path else fp
    ctx = PipelineContext(doc_id)

    # ── RunState: restore or create ─────────────────────────────────────────
    # RunState tracks PENDING/DONE/DIRTY for each stage.  DIRTY stages are
    # re-run after a human override; DONE stages are preserved (not re-run).
    rs = RunState.load(doc_id)
    if rs is None:
        rs = RunState.create(doc_id=doc_id)
        logger.info(f"[RunState] Created new state for {doc_id}")
    else:
        dirty = rs.dirty_stages()
        logger.info(f"[RunState] Resumed {doc_id} — dirty stages: {dirty}")

    logger.info(f"[Orchestrator] Starte Pipeline: {doc_id}")

    # ── Populate live kraken registry from ATR gateway ───────────────────────
    # Single source of truth: gateway's GET /models.  Local KRAKEN_MODELS
    # table is the offline fallback.  Errors here are non-fatal — the static
    # table keeps the pipeline running even if the gateway is unreachable.
    if DUAL_AVAILABLE and refresh_kraken_registry:
        try:
            with KrakenHTTPClient() as client:
                refresh_kraken_registry(client)
            logger.info(
                f"[Phase 0] Live kraken registry populated: "
                f"{len(KRAKEN_MODELS_LIVE)} models from gateway"
            )
        except KrakenClientError as e:
            logger.warning(f"[Phase 0] Could not reach ATR gateway for live "
                           f"registry — using static table: {e}")

    # ════════════════════════════════════════════════════════════════════════
    # PHASE 1: Agent A — VLM-only erste Transkription ( fuer Agent B )
    # Stage: model_select + kraken (VLM initial transcription)
    # ════════════════════════════════════════════════════════════════════════
    if _stage_needs_run(rs, "model_select") or _stage_needs_run(rs, "kraken"):
        try:
            if use_dual_htr and DUAL_AVAILABLE:
                # Phase 1: nur VLM-Pfad (ohne kraken — das kommt in Phase 3)
                logger.info("[Orchestrator] Phase 1: VLM-only HTR (preliminary for Agent B)")
                from agent_a.dual_pipeline import transcribe_dual
                dual = transcribe_dual(
                    img,
                    source_description=source_description,
                    lang=lang,
                    run_vlm=True,
                    run_kraken=False,   # ← Phase 3!
                    run_party=False,    # ← Phase 3!
                    run_hf=False,
                )
                ctx.dual_result = dual
                ctx.transcription = dual.vlm_transcription
                ctx.a_meta = dual.to_dict()
                logger.info(
                    f"[Orchestrator] Phase 1 (VLM) fertig — "
                    f"{len(ctx.transcription)} chars, score={dual.vlm_score:.2f}"
                )
            else:
                a_result = agent_a.process_file(img)
                ctx.transcription = a_result.get("transcription", "")
                ctx.a_meta = a_result
                logger.info(
                    f"[Orchestrator] Agent A fertig "
                    f"(QA: {a_result.get('qa_score', 0):.2f})"
                )
            rs.mark_done("model_select")
            rs.mark_done("kraken")
        except Exception as e:
            logger.error(f"[Orchestrator] Phase 1 (Agent A) fehlgeschlagen: {e}")
            ctx.errors.append({"agent": "A", "phase": 1, "error": str(e)})
            rs.mark_failed("model_select")
            rs.mark_failed("kraken")
    else:
        logger.info("[Orchestrator] Phase 1 skipped (model_select + kraken already DONE)")

    # ════════════════════════════════════════════════════════════════════════
    # PHASE 2: Agent B — Quellenbeschreibung + Modellvorschlag
    # Stage: agent_b
    # ════════════════════════════════════════════════════════════════════════
    if _stage_needs_run(rs, "agent_b") and ctx.transcription:
        try:
            ctx.description = agent_b.describe(
                doc_id=doc_id,
                transcription=ctx.transcription,
                image_path=str(img) if img != fp else None,
            )
            logger.info("[Orchestrator] Phase 2 (Agent B) fertig")
            rs.mark_done("agent_b")
        except Exception as e:
            logger.error(f"[Orchestrator] Phase 2 (Agent B) fehlgeschlagen: {e}")
            ctx.errors.append({"agent": "B", "error": str(e)})
            rs.mark_failed("agent_b")
    else:
        if ctx.transcription and not _stage_needs_run(rs, "agent_b"):
            logger.info("[Orchestrator] Phase 2 (Agent B) skipped (already DONE)")

    # ════════════════════════════════════════════════════════════════════════
    # PHASE 3: kraken-Re-Run mit Agent-B-gestuerter Modellwahl
    # Stage: reconcile (reconciles VLM + kraken results)
    # Only runs when DUAL_AVAILABLE — reconciler is the downstream consumer.
    # ════════════════════════════════════════════════════════════════════════
    if _stage_needs_run(rs, "reconcile") and DUAL_AVAILABLE and ctx.transcription and ctx.description:
        try:
            source_desc_text = ctx.description.get("source_description", "")
            if source_desc_text:
                kraken_results = _rerun_kraken_with_model_selection(
                    image_path=img,
                    source_description=source_desc_text,
                    lang=lang,
                )
                # Reconcile VLM (Phase 1) with kraken (Phase 3) instead of
                # blindly preferring kraken.  Both transcriptions are kept;
                # the better one (per the reconcile() diff/LLM logic) is used.
                if kraken_results["kraken_transcription"]:
                    vlm_text = ctx.transcription
                    kraken_text = kraken_results["kraken_transcription"]
                    rec_result = reconcile(vlm_text, kraken_text)
                    ctx.transcription = rec_result.reconciled
                    logger.info(
                        f"[Orchestrator] Phase 3: reconciled ({rec_result.method}), "
                        f"agreement={rec_result.agreement_score:.2f}, "
                        f"{len(ctx.transcription)} chars"
                    )
                # Store kraken metadata in a_meta
                ctx.a_meta["kraken_transcription"] = kraken_results["kraken_transcription"]
                ctx.a_meta["party_transcription"] = kraken_results["party_transcription"]
                ctx.a_meta["kraken_model"] = (
                    kraken_results["kraken_model"].model_id
                    if kraken_results["kraken_model"] else None
                )
                ctx.a_meta["error_kraken"] = kraken_results["error_kraken"]
                ctx.a_meta["error_party"] = kraken_results["error_party"]
                logger.info("[Orchestrator] Phase 3 (kraken re-run) fertig")
            rs.mark_done("reconcile")
        except Exception as e:
            logger.error(f"[Orchestrator] Phase 3 (kraken re-run) fehlgeschlagen: {e}")
            ctx.errors.append({"agent": "kraken_rerun", "phase": 3, "error": str(e)})
            rs.mark_failed("reconcile")
    else:
        if not DUAL_AVAILABLE:
            logger.info("[Orchestrator] Phase 3 skipped (DUAL_AVAILABLE=False)")
        elif not ctx.transcription:
            logger.info("[Orchestrator] Phase 3 skipped (no transcription)")
        elif not ctx.description:
            logger.info("[Orchestrator] Phase 3 skipped (no description)")
        elif not _stage_needs_run(rs, "reconcile"):
            logger.info("[Orchestrator] Phase 3 (reconcile) skipped (already DONE)")

    # ════════════════════════════════════════════════════════════════════════
    # PHASE 4: Agent C — Entity Extraction
    # Stage: agent_c
    # ════════════════════════════════════════════════════════════════════════
    if _stage_needs_run(rs, "agent_c") and ctx.transcription:
        try:
            ctx.entities = agent_c.extract_entities(
                doc_id=doc_id,
                transcription=ctx.transcription,
            )
            logger.info("[Orchestrator] Phase 4 (Agent C) fertig")
            rs.mark_done("agent_c")
        except Exception as e:
            logger.error(f"[Orchestrator] Phase 4 (Agent C) fehlgeschlagen: {e}")
            ctx.errors.append({"agent": "C", "error": str(e)})
            rs.mark_failed("agent_c")
    else:
        if ctx.transcription and not _stage_needs_run(rs, "agent_c"):
            logger.info("[Orchestrator] Phase 4 (Agent C) skipped (already DONE)")

    # ════════════════════════════════════════════════════════════════════════
    # PHASE 5: Agent D — optional corpus analysis
    # Stage: agent_d
    # ════════════════════════════════════════════════════════════════════════
    if _stage_needs_run(rs, "agent_d") and run_agent_d:
        try:
            agent_d.analyse_corpus(corpus_name=doc_id, doc_ids=[doc_id])
            logger.info("[Orchestrator] Phase 5 (Agent D) fertig")
            rs.mark_done("agent_d")
        except Exception as e:
            logger.error(f"[Orchestrator] Phase 5 (Agent D) fehlgeschlagen: {e}")
            ctx.errors.append({"agent": "D", "error": str(e)})
            rs.mark_failed("agent_d")
    else:
        if not run_agent_d:
            logger.info("[Orchestrator] Phase 5 (Agent D) skipped (run_agent_d=False)")
        elif not _stage_needs_run(rs, "agent_d"):
            logger.info("[Orchestrator] Phase 5 (Agent D) skipped (already DONE)")

    # ── Persist RunState ─────────────────────────────────────────────────────
    rs.save()
    logger.info(f"[RunState] Saved state for {doc_id}")

    # ── Save pipeline result ──────────────────────────────────────────────────
    _save_pipeline_result(doc_id, ctx)
    _append_errors_to_log(doc_id, ctx.errors)

    logger.info(f"[Orchestrator] Pipeline abgeschlossen: {doc_id}")
    return ctx.to_json()


def run_agent_a(file_path: str | Path) -> dict:
    """Nur Agent A (HTR)."""
    return agent_a.process_file(file_path)


def run_agent_b(doc_id: str) -> dict:
    """Agent B mit bestehender Transkription."""
    txt_path = config.TRANSCRIPTIONS_DIR / f"{doc_id}.txt"
    if not txt_path.exists():
        raise FileNotFoundError(f"Transkription nicht gefunden: {doc_id}")
    transcription = txt_path.read_text(encoding="utf-8")
    # Strip header
    if transcription.startswith("#"):
        transcription = transcription.split("\n\n", 1)[-1]
    return agent_b.describe(doc_id, transcription)


def run_agent_c(doc_id: str) -> dict:
    """Agent C mit bestehender Transkription."""
    txt_path = config.TRANSCRIPTIONS_DIR / f"{doc_id}.txt"
    if not txt_path.exists():
        raise FileNotFoundError(f"Transkription nicht gefunden: {doc_id}")
    transcription = txt_path.read_text(encoding="utf-8")
    if transcription.startswith("#"):
        transcription = transcription.split("\n\n", 1)[-1]
    return agent_c.extract_entities(doc_id, transcription)


def run_agent_d(corpus_name: str = "default") -> dict:
    """Agent D: Korpusanalyse."""
    return agent_d.analyse_corpus(corpus_name)


def run_agent_e() -> dict:
    """Agent E: Meta-Report."""
    return agent_e.generate_report()


def run_hot_folder() -> list[PipelineResult]:
    """Verarbeitet alle Dateien im Hot Folder."""
    config.ensure_dirs()
    results = []
    for fp in config.HOT_FOLDER.glob("*"):
        if fp.suffix.lower() in [".jpg", ".jpeg", ".png", ".tiff", ".webp", ".pdf"]:
            try:
                result = run_full_pipeline(fp)
                # Move to processed
                processed = config.PROCESSED_FOLDER / fp.name
                fp.rename(processed)
                result["moved_to"] = str(processed)
                results.append(result)   # #97: successes were never recorded
            except Exception as e:
                logger.error(f"[Orchestrator] Hot-folder Fehler bei {fp.name}: {e}")
                results.append({"doc_id": fp.stem, "error": str(e)})
    return results


def _append_errors_to_log(doc_id: str, errors: list) -> None:
    """Append ctx.errors to the persistent meta error log (META_LOG_PATH).

    The log is a JSON list of entries, one per pipeline run.
    Each entry is a dict with 'doc_id', 'timestamp', and 'errors'.
    Duplicate runs for the same doc_id append a new entry (not a merge).
    """
    log_path = config.META_LOG_PATH
    try:
        if log_path.exists():
            try:
                entries = json.loads(log_path.read_text(encoding="utf-8"))
            except Exception:
                entries = []
        else:
            entries = []
    except Exception:
        entries = []

    from datetime import datetime, timezone
    entries.append({
        "doc_id": doc_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "errors": errors,
    })
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[Orchestrator] {len(errors)} error(s) written to {log_path}")


def _save_pipeline_result(doc_id: str, ctx: PipelineContext):
    out = config.OUTPUTS_DIR / f"{doc_id}_pipeline.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(ctx.to_json(), f, ensure_ascii=False, indent=2)
    logger.info(f"[Orchestrator] Pipeline-Resultat: {out}")