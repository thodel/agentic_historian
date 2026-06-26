"""
orchestrator.py — Orchestriert die fünf Agenten und koordiniert die Pipeline.
"""

import json
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

    def to_json(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "transcription": self.transcription,
            "description": self.description,
            "entities": self.entities,
            "errors": self.errors,
        }


def run_full_pipeline(
    file_path: str | Path,
    image_path: Optional[str | Path] = None,
    run_agent_d: bool = False,
) -> PipelineResult:
    """
    Führt A → B → C (→ D) Pipeline aus.
    Optional: Agent D (Corpus) anschliessend.
    """
    fp = Path(file_path)
    doc_id = fp.stem
    ctx = PipelineContext(doc_id)

    logger.info(f"[Orchestrator] Starte Pipeline: {doc_id}")

    # ── Agent A: Text Recognition ──────────────────────────────────────────
    try:
        a_result = agent_a.process_file(image_path or fp)
        ctx.transcription = a_result.get("transcription", "")
        logger.info(f"[Orchestrator] Agent A fertig (QA: {a_result.get('qa_score', 0):.2f})")
    except Exception as e:
        logger.error(f"[Orchestrator] Agent A fehlgeschlagen: {e}")
        ctx.errors.append({"agent": "A", "error": str(e)})

    # ── Agent B: Source Description ────────────────────────────────────────
    if ctx.transcription:
        try:
            ctx.description = agent_b.describe(
                doc_id=doc_id,
                transcription=ctx.transcription,
                image_path=str(image_path) if image_path else None,
            )
            logger.info("[Orchestrator] Agent B fertig")
        except Exception as e:
            logger.error(f"[Orchestrator] Agent B fehlgeschlagen: {e}")
            ctx.errors.append({"agent": "B", "error": str(e)})

    # ── Agent C: Entity Extraction ─────────────────────────────────────────
    if ctx.transcription:
        try:
            ctx.entities = agent_c.extract_entities(doc_id, ctx.transcription)
            logger.info("[Orchestrator] Agent C fertig")
        except Exception as e:
            logger.error(f"[Orchestrator] Agent C fehlgeschlagen: {e}")
            ctx.errors.append({"agent": "C", "error": str(e)})

    # ── Agent D: Optional Corpus ───────────────────────────────────────────
    if run_agent_d:
        try:
            agent_d.analyse_corpus(corpus_name="default")
            logger.info("[Orchestrator] Agent D fertig")
        except Exception as e:
            logger.error(f"[Orchestrator] Agent D fehlgeschlagen: {e}")
            ctx.errors.append({"agent": "D", "error": str(e)})

    # Pipeline-Resultat speichern
    _save_pipeline_result(doc_id, ctx)

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
        transcription = transcription.split("\n\n", 2)[-1]
    return agent_b.describe(doc_id, transcription)


def run_agent_c(doc_id: str) -> dict:
    """Agent C mit bestehender Transkription."""
    txt_path = config.TRANSCRIPTIONS_DIR / f"{doc_id}.txt"
    if not txt_path.exists():
        raise FileNotFoundError(f"Transkription nicht gefunden: {doc_id}")
    transcription = txt_path.read_text(encoding="utf-8")
    if transcription.startswith("#"):
        transcription = transcription.split("\n\n", 2)[-1]
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
            except Exception as e:
                logger.error(f"[Orchestrator] Hot-folder Fehler bei {fp.name}: {e}")
                results.append({"doc_id": fp.stem, "error": str(e)})
    return results


def _save_pipeline_result(doc_id: str, ctx: PipelineContext):
    out = config.OUTPUTS_DIR / f"{doc_id}_pipeline.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(ctx.to_json(), f, ensure_ascii=False, indent=2)
    logger.info(f"[Orchestrator] Pipeline-Resultat: {out}")