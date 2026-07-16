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
from eval.metrics import cer
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
    from agent_a.model_selector import select_kraken_model, select_best, SourceCriteria, RecognitionResult
    from agent_a.reconcile import reconcile
    from agent_a.models import refresh_kraken_registry, KRAKEN_MODELS_LIVE
    DUAL_AVAILABLE = True
except ImportError:
    DUAL_AVAILABLE = False
    DualTranscriptionResult = None
    refresh_kraken_registry = None
    KRAKEN_MODELS_LIVE = None

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
        self.source_url: Optional[str] = None   # link back to the source (#208)
        self.dual_result: Optional[DualTranscriptionResult] = None
        self.recognitions: list[RecognitionResult] = []       # all OCR candidates (#234)

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
        if self.source_url:
            base["source_url"] = self.source_url
        base["recognitions"] = [
            {
                "engine": r.engine,
                "model_id": r.model_id,
                "text": r.text,
                "confidence": r.confidence,
                "error": r.error,
                "timing_ms": r.timing_ms,
                "segmented_by": r.segmented_by,
            }
            for r in self.recognitions
        ]
        return base


def _derive_source_url(fp: Path, explicit: Optional[str] = None) -> Optional[str]:
    """Source-image URL for a doc (#208): an explicit override, else derived from
    ``config.SOURCE_URL_BASE`` + filename. None when no base is configured."""
    if explicit:
        return explicit
    base = getattr(config, "SOURCE_URL_BASE", "")
    return f"{base}/{fp.name}" if base else None


# ── Phase 3: kraken re-run with Agent B model selection ──────────────────────

def _rerun_kraken_with_model_selection(
    image_path: Path,
    source_description: str,
    lang: str = "de",
    source_json: Optional[dict] = None,
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
    criteria = SourceCriteria.from_agent_b_and_json(source_description, source_json)
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


def _emit(on_phase, doc_id: str, phase: str, agent: str, *, status: str = "done",
          output=None, decision: str = "", error: str = "") -> None:
    """Emit one PhaseEvent for a pipeline step (#288).

    Best-effort by construction: a broken callback (or a snippet of some exotic
    object) must NEVER break the pipeline — the whole point is observability, not
    a new failure mode. ``output`` is rendered with progress.snippet (V-1, #287) so
    the excerpt is a short, safe preview (first 3 lines / first 3 entries).
    """
    try:
        from runstate import PhaseEvent
        excerpt = ""
        if output is not None:
            try:
                from progress import snippet
                excerpt = snippet(output)
            except Exception:                       # pragma: no cover — defensive
                excerpt = str(output)[:200]
        ev = PhaseEvent(doc_id=doc_id, phase=phase, agent=agent, status=status,
                        excerpt=excerpt, decision=decision, error=error or "")
        (on_phase or _log_phase)(ev)
    except Exception as e:                          # pragma: no cover — defensive
        logger.warning(f"[Orchestrator] phase emit failed ({phase}): {e}")


def _log_phase(ev) -> None:
    """Default sink: log the event (unchanged behaviour when no on_phase given)."""
    if ev.status == "error":
        logger.warning(f"[phase] {ev.doc_id}/{ev.phase} ({ev.agent}) ERROR: {ev.error}")
    else:
        logger.info(f"[phase] {ev.doc_id}/{ev.phase} ({ev.agent}) — "
                    f"{ev.decision or ev.excerpt[:80]}")


def run_full_pipeline(
    file_path: str | Path,
    image_path: Optional[str | Path] = None,
    run_agent_d: bool = False,
    use_dual_htr: bool = False,
    source_description: Optional[str] = None,
    lang: str = "de",
    source_url: Optional[str] = None,
    on_phase=None,
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
        source_url:          Link back to the source image (#208); else derived
                             from config.SOURCE_URL_BASE.
    """
    fp = Path(file_path)
    doc_id = fp.stem
    img = Path(image_path) if image_path else fp
    ctx = PipelineContext(doc_id)
    ctx.source_url = _derive_source_url(fp, source_url)

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
    # ════════════════════════════════════════════════════════════════════════
    try:
        if use_dual_htr and DUAL_AVAILABLE:
            # Phase 1: nur VLM-Pfad (ohne kraken — das kommt in Phase 3)
            logger.info("[Orchestrator] Phase 1: VLM-only HTR (preliminary for Agent B)")
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
            ctx.recognitions = list(dual.recognitions)
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
            try:
                from runstate import RunState, DONE, ERROR
                state = RunState.load_or_new(doc_id)
                state.stage_status["vlm"] = DONE
                state.artifacts["transcription"] = ctx.transcription
                state.artifacts["a_meta"] = ctx.a_meta
                if ctx.recognitions:
                    state.artifacts["recognitions"] = ctx.recognitions
                state.save()
            except Exception as e2:
                logger.warning(f"[Orchestrator] Phase 1 RunState persist skipped: {e2}")
        _emit(on_phase, doc_id, "vlm", "A", output=ctx.transcription,
              decision=f"qa={ctx.a_meta.get('qa_score', 0)} "
                       f"{len(ctx.transcription)} chars")
    except Exception as e:
        logger.error(f"[Orchestrator] Phase 1 (Agent A) fehlgeschlagen: {e}")
        ctx.errors.append({"agent": "A", "phase": 1, "error": str(e)})
        _emit(on_phase, doc_id, "vlm", "A", status="error", error=str(e))
        try:
            from runstate import RunState, DONE, ERROR
            state = RunState.load_or_new(doc_id)
            state.stage_status["vlm"] = ERROR
            state.save()
        except Exception:
            pass

    # #226: preserve the inputs a later isolated re-run (reprocess) needs — the
    # pure VLM transcription (before Phase 3 reconciles ctx.transcription) and the
    # source image path — so reprocess(fields=["script"]) can re-run kraken+reconcile.
    try:
        from runstate import RunState
        state = RunState.load_or_new(doc_id)
        if ctx.transcription:
            state.artifacts["vlm_transcription"] = ctx.transcription
        state.artifacts["image_path"] = str(img)
        state.save()
    except Exception as e:
        logger.warning(f"[Orchestrator] reprocess-input persist skipped ({doc_id}): {e}")

    # ════════════════════════════════════════════════════════════════════════
    # PHASE 2: Agent B — Quellenbeschreibung + Modellvorschlag
    # ════════════════════════════════════════════════════════════════════════
    if ctx.transcription:
        try:
            ctx.description = agent_b.describe(
                doc_id=doc_id,
                transcription=ctx.transcription,
                image_path=str(img) if img != fp else None,
            )
            logger.info("[Orchestrator] Phase 2 (Agent B) fertig")
            _emit(on_phase, doc_id, "agent_b", "B",
                  output=(ctx.description or {}).get("source_json"),
                  decision=(ctx.description or {}).get("source_description", "")[:80])
            try:
                from runstate import RunState, DONE, ERROR
                state = RunState.load_or_new(doc_id)
                state.stage_status["agent_b"] = DONE
                state.artifacts["description"] = ctx.description
                state.save()
            except Exception as e2:
                logger.warning(f"[Orchestrator] Phase 2 RunState persist skipped: {e2}")
        except Exception as e:
            logger.error(f"[Orchestrator] Phase 2 (Agent B) fehlgeschlagen: {e}")
            ctx.errors.append({"agent": "B", "error": str(e)})
            _emit(on_phase, doc_id, "agent_b", "B", status="error", error=str(e))

    # ════════════════════════════════════════════════════════════════════════
    # PHASE 3: kraken-Re-Run mit Agent-B-gestuerter Modellwahl
    # Nur wenn das kraken/dual-HTR-Paket verfügbar ist (sonst ist
    # select_best_kraken_model nicht importiert → NameError).
    # ════════════════════════════════════════════════════════════════════════
    if DUAL_AVAILABLE and ctx.transcription and ctx.description:
        try:
            source_desc_text = ctx.description.get("source_description", "")
            src_json = ctx.description.get("source_json")
            if source_desc_text or src_json:
                # ── Phase 3: transcribe_dual with concurrent kraken + party fan-out
                dual_p3 = transcribe_dual(
                    img,
                    source_description=source_desc_text,
                    lang=lang,
                    run_vlm=False,   # VLM already done in Phase 1
                    run_kraken=True,
                    run_party=True,
                    run_hf=False,
                    use_llm_reconcile=True,
                )
                for rec in dual_p3.recognitions:
                    if rec not in ctx.recognitions:
                        ctx.recognitions.append(rec)
                _selected = next((r for r in dual_p3.recognitions
                                  if (r.engine or "") == "kraken"), None)
                if _selected is not None:
                    _conf = getattr(_selected, "confidence", None)
                    _emit(on_phase, doc_id, "model_select", "A",
                          output=f"{_selected.engine}/{_selected.model_id}",
                          decision=f"model={_selected.model_id}"
                                   + (f", confidence={_conf:.2f}" if _conf is not None else ""))
                _emit(on_phase, doc_id, "kraken", "A",
                      output=[f"{r.engine}/{r.model_id}: {len(r.text or '')} chars"
                              + (f" — {r.error}" if r.error else "")
                              for r in dual_p3.recognitions],
                      decision=f"{len(dual_p3.recognitions)} candidate(s)")

                # ── Phase 3 fusion or 2-way reconcile ────────────────────────
                # When ENABLE_MULTI_ENGINE_FUSION is on, feed ALL candidates
                # (VLM + kraken + TrOCR + PARTY + any others) into fusion.fuse():
                #   • alignment on the longest candidate (pivot)
                #   • majority-vote per position
                #   • LLM arbitration scoped to disagreement slots only
                # An "agreement gate" short-circuits the LLM when max pairwise
                # CER between candidates is below FUSION_AGREEMENT_CER_THRESHOLD
                # (cost control; consensus is taken directly).
                # When fusion is off, behaviour is byte-identical to the old
                # 2-way reconcile (VLM vs kraken).
                _do_fuse = config.ENABLE_MULTI_ENGINE_FUSION and len(ctx.recognitions) >= 2
                if _do_fuse:
                    from fusion import fuse as _fusion_fuse
                    # Agreement gate: compute max pairwise CER; if low, skip LLM arb.
                    from eval.metrics import cer as _cer
                    _cands = [(r.engine or "?", r.text or "") for r in ctx.recognitions if r.text]
                    _pairwise_cers = [
                        _cer(a, b, ignore_case=False, ignore_whitespace=False,
                             ignore_punctuation=False)
                        for _, a in _cands
                        for _, b in _cands
                        if a != b
                    ]
                    _max_cer = max(_pairwise_cers) if _pairwise_cers else 1.0
                    _skip_llm = _max_cer < config.FUSION_AGREEMENT_CER_THRESHOLD
                    if _skip_llm:
                        logger.info(
                            f"[Orchestrator] Phase 3: agreement gate (CER={_max_cer:.2%} "
                            f"< {config.FUSION_AGREEMENT_CER_THRESHOLD:.2%}); "
                            f"taking consensus without LLM arbitration"
                        )
                    _fuse_result = _fusion_fuse(
                        ctx.recognitions,
                        llm_fn=None,          # deterministic if LLM skipped
                        strategy=config.FUSION_STRATEGY,
                    )
                    ctx.transcription = _fuse_result.text
                    ctx.a_meta["fusion_strategy"] = _fuse_result.strategy
                    ctx.a_meta["fusion_arbitrated"] = _fuse_result.arbitrated
                    ctx.a_meta["fusion_agreement_cer"] = _max_cer
                    ctx.a_meta["fusion_llm_skipped"] = _skip_llm
                    _emit(on_phase, doc_id, "fusion", "A", output=ctx.transcription,
                          decision=f"{_fuse_result.strategy}, "
                                   f"{_fuse_result.arbitrated} arbitrated, "
                                   f"agreement CER {_max_cer:.1%}")
                    logger.info(
                        f"[Orchestrator] Phase 3: fused ({_fuse_result.strategy}), "
                        f"arbitrated={_fuse_result.arbitrated} slots, "
                        f"agreement={_max_cer:.2%}, llm_skipped={_skip_llm}, "
                        f"{len(ctx.transcription)} chars"
                    )
                elif dual_p3.kraken_transcription:
                    # Legacy 2-way reconcile (when fusion is disabled)
                    rec_result = reconcile(ctx.transcription, dual_p3.kraken_transcription)
                    ctx.transcription = rec_result.reconciled
                    _emit(on_phase, doc_id, "reconcile", "A", output=ctx.transcription,
                          decision=f"{rec_result.method}, "
                                   f"agreement={rec_result.agreement_score:.2f}")
                    logger.info(
                        f"[Orchestrator] Phase 3: reconciled ({rec_result.method}), "
                        f"agreement={rec_result.agreement_score:.2f}, "
                        f"{len(ctx.transcription)} chars"
                    )
                # Store kraken metadata in a_meta
                ctx.a_meta["kraken_transcription"] = dual_p3.kraken_transcription
                ctx.a_meta["party_transcription"] = dual_p3.party_transcription
                ctx.a_meta["error_kraken"] = dual_p3.error_kraken
                ctx.a_meta["error_party"] = dual_p3.error_party
                logger.info("[Orchestrator] Phase 3 (kraken re-run) fertig")
            # Record kraken + reconcile stages into RunState
            try:
                from runstate import RunState, DONE, ERROR
                state = RunState.load_or_new(doc_id)
                state.stage_status["model_select"] = DONE
                state.stage_status["kraken"] = DONE
                state.stage_status["reconcile"] = DONE
                state.artifacts["transcription"] = ctx.transcription
                state.artifacts["a_meta"] = ctx.a_meta
                if ctx.recognitions:
                    state.artifacts["recognitions"] = ctx.recognitions
                state.save()
            except Exception as e2:
                logger.warning(f"[Orchestrator] Phase 3 RunState persist skipped: {e2}")
        except Exception as e:
            logger.error(f"[Orchestrator] Phase 3 (kraken re-run) fehlgeschlagen: {e}")
            ctx.errors.append({"agent": "kraken_rerun", "phase": 3, "error": str(e)})
            _emit(on_phase, doc_id, "kraken", "A", status="error", error=str(e))

    # ════════════════════════════════════════════════════════════════════════
    # PHASE 4: Agent C — Entity Extraction
    # ════════════════════════════════════════════════════════════════════════
    if ctx.transcription:
        try:
            ctx.entities = agent_c.extract_entities(doc_id, ctx.transcription)
            logger.info("[Orchestrator] Phase 4 (Agent C) fertig")
            _emit(on_phase, doc_id, "agent_c", "C",
                  output=(ctx.entities or {}).get("entities") or ctx.entities,
                  decision=f"{len((ctx.entities or {}).get('entities', []) or [])} entities")
            try:
                from runstate import RunState, DONE, ERROR
                state = RunState.load_or_new(doc_id)
                state.stage_status["agent_c"] = DONE
                state.artifacts["entities"] = ctx.entities
                state.save()
            except Exception as e2:
                logger.warning(f"[Orchestrator] Phase 4 RunState persist skipped: {e2}")
        except Exception as e:
            logger.error(f"[Orchestrator] Agent C fehlgeschlagen: {e}")
            ctx.errors.append({"agent": "C", "error": str(e)})
            _emit(on_phase, doc_id, "agent_c", "C", status="error", error=str(e))

    # ════════════════════════════════════════════════════════════════════════
    # PHASE 5: Agent D (optional)
    # ════════════════════════════════════════════════════════════════════════
    if run_agent_d:
        try:
            _d_result = agent_d.analyse_corpus(corpus_name="default")
            logger.info("[Orchestrator] Phase 5 (Agent D) fertig")
            _emit(on_phase, doc_id, "agent_d", "D", output=_d_result,
                  decision="corpus analysis done")
            try:
                from runstate import RunState, DONE, ERROR
                state = RunState.load_or_new(doc_id)
                state.stage_status["agent_d"] = DONE
                state.save()
            except Exception as e2:
                logger.warning(f"[Orchestrator] Phase 5 RunState persist skipped: {e2}")
        except Exception as e:
            logger.error(f"[Orchestrator] Agent D fehlgeschlagen: {e}")
            ctx.errors.append({"agent": "D", "error": str(e)})
            _emit(on_phase, doc_id, "agent_d", "D", status="error", error=str(e))

    # ── Persist a RunState so /route can render a populated Gate-1 card ───────
    # The core pipeline does not itself gate; it records the criteria Agent B
    # inferred (script/lang/century/type) so the routing card + uncertainty
    # gating have real values to work with. Human-pinned criteria (from a prior
    # correction) are preserved — we only fill fields not already set.
    try:
        from runstate import RunState, DONE, ERROR
        from agent_a.model_selector import SourceCriteria
        state = RunState.load_or_new(doc_id)
        desc_text = (ctx.description or {}).get("source_description", "")
        src_json = (ctx.description or {}).get("source_json")
        if desc_text or src_json:
            crit = SourceCriteria.from_agent_b_and_json(desc_text, src_json)
            for k, v in {
                "script": crit.script,
                "lang": crit.lang or lang,
                "century": crit.century,
                "document_type": crit.document_type,
            }.items():
                if v is not None and k not in state.criteria:
                    state.criteria[k] = v
        state.artifacts["transcription"] = ctx.transcription
        state.save()
    except Exception as e:
        logger.warning(f"[Orchestrator] RunState persist skipped ({doc_id}): {e}")

    # ── Persist RunState (single source of truth) ─────────────────────────
    # RunState is kept up-to-date after every phase so a crash at any point
    # leaves a resumable, partially-done record in data/runs/<doc_id>.json.
    # pipeline.json is derived from it on completion — never written directly.
    try:
        from runstate import RunState, DONE, ERROR
        state = RunState.load_or_new(doc_id)
        # Carry source_url forward
        state.source_url = ctx.source_url
        # Persist a back-compat hint so downstream readers know which version
        # of the pipeline produced this RunState.
        state.artifacts["pipeline_version"] = "225-derived"
        state.save()
    except Exception as e:
        logger.warning(f"[Orchestrator] RunState persist skipped ({doc_id}): {e}")

    # Derive pipeline.json from RunState (same shape as old ctx.to_json())
    _save_pipeline_result(doc_id, ctx, from_runstate=True)
    _published, _detail = _publish_outputs(doc_id, ctx.source_url)
    _emit(on_phase, doc_id, "publish", "publish_github",
          output=ctx.source_url or doc_id, decision=_detail)

    return ctx.to_json()


def _publish_outputs(doc_id: str, source_url: Optional[str] = None) -> tuple[bool, str]:
    """Publish this doc's outputs to the GitHub output repo (#200), linking back
    to ``source_url`` (#208).

    Opt-in (ENABLE_GITHUB_PUBLISH) and non-fatal — any failure is swallowed so
    publishing never breaks the pipeline.

    Returns ``(published, detail)`` so the caller can emit a truthful publish event
    (#288): publishing is off by default, and a bare "done" when nothing was
    published would be exactly the false-green signal V-2 exists to remove.
    Callers that don't care (ingest.py) simply ignore it.
    """
    try:
        from utils import publish_github
        if not publish_github.is_enabled():
            return False, "disabled (ENABLE_GITHUB_PUBLISH=false)"
        publish_github.publish_doc(doc_id, source_url=source_url)
        return True, "published to the outputs repo"
    except Exception as e:
        logger.warning(f"[Orchestrator] GitHub publish skipped ({doc_id}): {e}")
        return False, str(e)


_GATEWAY_REGISTRY_CACHE = None


def _gateway_registry() -> list:
    """The ATR gateway's model registry, fetched once per process (#277).

    Used to map local model ids (kraken Zenodo DOI / TrOCR HF repo) to the ids the
    gateway actually accepts. Unavailable gateway → [] → callers fall back to raw
    ids (kraken DOIs still resolve; TrOCR would 404, which surfaces as an engine
    error rather than a silent wrong-model call).
    """
    global _GATEWAY_REGISTRY_CACHE
    if _GATEWAY_REGISTRY_CACHE is None:
        try:
            from agent_a.kraken_client import KrakenHTTPClient
            with KrakenHTTPClient() as c:
                _GATEWAY_REGISTRY_CACHE = c.list_models()
        except Exception as e:
            logger.warning(f"[ensemble] gateway registry unavailable, using raw ids: {e}")
            _GATEWAY_REGISTRY_CACHE = []
    return _GATEWAY_REGISTRY_CACHE


def _recognize_page_ensemble(img, criteria):
    """#272: run the multi-engine ensemble on one page — VLM + best kraken + best
    TrOCR (≥ ENSEMBLE_MIN_ENGINES), expanding with the next-ranked model while the
    candidates disagree, then fuse. Real backends: VLM via GPUStack, kraken/TrOCR
    via the ATR gateway ``/ocr`` with the explicit model id (#25), resolved to the
    gateway's registry id (#277). Returns the ensemble.EnsembleResult."""
    from agent_a import ensemble
    from agent_a.model_selector import RecognitionResult
    from agent_a.dual_pipeline import _run_vlm
    from agent_a.kraken_client import KrakenHTTPClient, KrakenClientError

    registry = _gateway_registry()

    def _recognize_fn(pick, image_path):
        p = Path(image_path)
        if pick.engine == "vlm":
            text, score = _run_vlm(p)
            return RecognitionResult(engine="vlm", model_id=pick.model_id,
                                     text=text, confidence=score)
        # local id (kraken DOI / TrOCR HF repo) → the gateway's registry id (#277)
        gw_id = ensemble.resolve_gateway_id(pick, registry)
        try:
            with KrakenHTTPClient() as c:
                res = c.transcribe(p, model=gw_id)
            return RecognitionResult(engine=pick.engine, model_id=gw_id,
                                     text=res.text, confidence=res.confidence)
        except KrakenClientError as e:
            return RecognitionResult(engine=pick.engine, model_id=gw_id,
                                     text="", error=str(e))

    result = ensemble.recognize_ensemble(
        img, criteria, _recognize_fn,
        min_engines=config.ENSEMBLE_MIN_ENGINES,
        max_loops=config.ENSEMBLE_MAX_LOOPS,
        agreement_cer=config.ENSEMBLE_AGREEMENT_CER,
        per_engine=getattr(config, "ENSEMBLE_PER_ENGINE", 3),
    )
    # Tag every candidate with its source page (#284) so a multi-page order's
    # exports can be attributed to the page they transcribe.
    page = Path(img).name
    for rec in result.recognitions:
        try:
            rec.page = page
        except Exception:                       # plain-dict candidates (tests)
            if isinstance(rec, dict):
                rec["page"] = page
    return result


def _emit_model_select(on_phase, doc_id: str, criteria, *, label: str) -> None:
    """Emit the model plan for one ensemble pass (#288/#299).

    plan_models is pure selection (no I/O), so calling it here purely to report the
    plan is cheap — and it is the number that matters: with empty criteria the
    picks score ~0.05/0.20 ("no match"), with Agent B's criteria the right model
    should top the list. That contrast is the whole point of #299, so make it
    visible rather than inferable from a later log line.
    """
    try:
        from agent_a import ensemble
        picks = ensemble.plan_models(
            criteria, per_engine=getattr(config, "ENSEMBLE_PER_ENGINE", 3))
        top = "; ".join(f"{p.engine}/{p.model_id} {p.score:.2f}" for p in picks[:3])
        _emit(on_phase, doc_id, "model_select", "A",
              output=[f"{p.engine}/{p.model_id} score={p.score:.2f}" for p in picks[:5]],
              decision=f"{label}: {top}")
    except Exception as e:
        logger.warning(f"[Orchestrator] model_select emit skipped: {e}")


def _ensemble_pass(pages, criteria, ctx, doc_id: str, on_phase, *, label: str):
    """Run the page ensemble over every page with one set of criteria (#299).

    Shared by Phase 1 (blind criteria) and Phase 3 (Agent B's criteria) so the two
    passes cannot drift apart. New candidates are appended to ``ctx.recognitions``
    — the first pass is never discarded, it is evidence, and #284 exports every
    candidate for comparison.
    """
    parts, scores = [], []
    for img in pages:
        try:
            er = _recognize_page_ensemble(img, criteria)
            parts.append(f"--- {img.name} ---\n{er.text}")
            scores.append(round(1.0 - er.max_pairwise_cer, 2))   # agreement-based QA
            for rec in er.recognitions:
                if rec not in ctx.recognitions:
                    ctx.recognitions.append(rec)
            logger.info(f"[Orchestrator] {img.name}: {label} ensemble "
                        f"{len(er.recognitions)} engine(s), {er.loops} loop(s), "
                        f"agreement CER {er.max_pairwise_cer:.2%}")
            # One event per candidate, so the historian sees WHICH engine read what —
            # the u-17__ failure was invisible precisely because only the merged text
            # was ever shown.
            for rec in er.recognitions:
                _emit(on_phase, doc_id, "vlm", "A",
                      status="error" if rec.error else "done",
                      output=rec.text, error=rec.error or "",
                      decision=f"{img.name} · {label} · {rec.engine}/{rec.model_id}")
            _emit(on_phase, doc_id, "vlm", "A", output=er.text,
                  decision=f"{img.name} · {label} ensemble: {len(er.recognitions)} "
                           f"engine(s), {er.loops} loop(s), "
                           f"agreement CER {er.max_pairwise_cer:.1%}")
        except Exception as e:
            logger.error(f"[Orchestrator] Agent A Seite {img.name} fehlgeschlagen: {e}")
            ctx.errors.append({"agent": "A", "page": img.name, "error": str(e)})
            _emit(on_phase, doc_id, "vlm", "A", status="error", error=str(e),
                  decision=img.name)
    return parts, scores


def run_full_pipeline_group(
    doc_id: str,
    image_paths: list,
    run_agent_d: bool = False,
    on_phase=None,
) -> PipelineResult:
    """Process a set of images as ONE multi-page document (a WebDAV "order"/folder).

    Agent A transcribes each page; the pages are combined into a single
    transcription (one .txt named after the order), then Agent B (one source
    description) and Agent C (entities over the whole order) run on it.
    """
    # Natural sort: page_2, page_10 (not page_10, page_2)
    def _natural_key(name: str):
        return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", name)]
    pages = sorted((Path(p) for p in image_paths), key=lambda p: _natural_key(p.name))
    ctx = PipelineContext(doc_id)
    # Link the order back to its source folder (first page's parent), if a base is set.
    if pages and getattr(config, "SOURCE_URL_BASE", ""):
        ctx.source_url = f"{config.SOURCE_URL_BASE}/{pages[0].parent.name}"
    logger.info(f"[Orchestrator] Order-Pipeline: {doc_id} ({len(pages)} Seite(n))")

    # PHASE 1: per-page recognition → combined transcription.
    # #272: when ENABLE_ENSEMBLE_HTR is on, each page runs the multi-engine
    # ensemble (VLM + kraken + TrOCR + disagreement-driven feedback loop) instead
    # of VLM-only — the u-17__ failure was a VLM repetition-collapse with no other
    # engine to outvote it. OFF → the original VLM-only behaviour (byte-identical).
    use_ensemble = getattr(config, "ENABLE_ENSEMBLE_HTR", False) and DUAL_AVAILABLE
    criteria = None
    if use_ensemble:
        try:
            from agent_a.model_selector import SourceCriteria
            criteria = SourceCriteria()   # general pool (no source description yet in Phase 1)
        except Exception as e:
            logger.warning(f"[Orchestrator] ensemble disabled (criteria init failed): {e}")
            use_ensemble = False

    if use_ensemble:
        _emit_model_select(on_phase, doc_id, criteria, label="blind (no description yet)")
        parts, scores = _ensemble_pass(pages, criteria, ctx, doc_id, on_phase,
                                       label="pass 1")
    else:
        parts, scores = [], []
        for img in pages:
            try:
                r = agent_a.transcribe_image(img)
                parts.append(f"--- {img.name} ---\n{r.get('transcription', '')}")
                scores.append(r.get("qa_score", 0.0))
                _emit(on_phase, doc_id, "vlm", "A", output=r.get("transcription", ""),
                      decision=f"{img.name} · qa={r.get('qa_score', 0.0)}")
            except Exception as e:
                logger.error(f"[Orchestrator] Agent A Seite {img.name} fehlgeschlagen: {e}")
                ctx.errors.append({"agent": "A", "page": img.name, "error": str(e)})
                _emit(on_phase, doc_id, "vlm", "A", status="error", error=str(e),
                      decision=img.name)
    ctx.transcription = "\n\n".join(parts).strip()
    avg_qa = round(sum(scores) / len(scores), 2) if scores else 0.0
    ctx.a_meta = {"pages": len(pages), "qa_score": avg_qa,
                  "source": "grouped-ensemble" if use_ensemble else "grouped"}
    if ctx.transcription:
        agent_a.save_transcription(doc_id, ctx.transcription, avg_qa, "grouped")
        try:
            from runstate import RunState, DONE, ERROR
            state = RunState.load_or_new(doc_id)
            state.stage_status["vlm"] = DONE
            state.artifacts["transcription"] = ctx.transcription
            state.artifacts["a_meta"] = ctx.a_meta
            if ctx.recognitions:                       # #272 ensemble candidates
                state.artifacts["recognitions"] = ctx.recognitions
            state.save()
        except Exception as e2:
            logger.warning(f"[Orchestrator] Group Phase 1 RunState persist skipped: {e2}")

    # PHASE 2: Agent B — one source description for the whole order
    if ctx.transcription:
        try:
            ctx.description = agent_b.describe(
                doc_id=doc_id,
                transcription=ctx.transcription,
                image_path=str(pages[0]) if pages else None,
            )
            try:
                from runstate import RunState, DONE, ERROR
                state = RunState.load_or_new(doc_id)
                state.stage_status["agent_b"] = DONE
                state.artifacts["description"] = ctx.description
                state.save()
            except Exception as e2:
                logger.warning(f"[Orchestrator] Group Phase 2 RunState persist skipped: {e2}")
            _emit(on_phase, doc_id, "agent_b", "B",
                  output=(ctx.description or {}).get("source_json"),
                  decision=(ctx.description or {}).get("source_description", "")[:80])
        except Exception as e:
            logger.error(f"[Orchestrator] Agent B fehlgeschlagen: {e}")
            ctx.errors.append({"agent": "B", "error": str(e)})
            _emit(on_phase, doc_id, "agent_b", "B", status="error", error=str(e))

    # ════════════════════════════════════════════════════════════════════════
    # PHASE 3 (#299): re-run recognition with Agent B's criteria.
    #
    # Phase 1 has no description yet, so it plans with EMPTY criteria and picks
    # blind — measured on tei: kraken 0.05, TrOCR 0.20, "no match". Agent B then
    # identifies the source correctly, and until now that knowledge was thrown
    # away: the grouped path had no Phase 3, unlike run_full_pipeline. The right
    # model was only ever reached by accident, when the disagreement loop happened
    # to add it. This closes the loop — describe the source, then read it again
    # with the model that description implies.
    # ════════════════════════════════════════════════════════════════════════
    if (use_ensemble and ctx.transcription and ctx.description
            and not ctx.description.get("low_confidence")):
        try:
            from agent_a.model_selector import SourceCriteria
            criteria_b = SourceCriteria.from_agent_b_and_json(
                ctx.description.get("source_description", ""),
                ctx.description.get("source_json"),
            )
            _emit_model_select(on_phase, doc_id, criteria_b, label="from Agent B")
            parts_b, scores_b = _ensemble_pass(pages, criteria_b, ctx, doc_id,
                                               on_phase, label="pass 2 (criteria)")
            if parts_b:
                ctx.transcription = "\n\n".join(parts_b).strip()
                avg_qa = round(sum(scores_b) / len(scores_b), 2) if scores_b else avg_qa
                ctx.a_meta["qa_score"] = avg_qa
                ctx.a_meta["source"] = "grouped-ensemble-criteria"
                ctx.a_meta["criteria_rerun"] = True
                agent_a.save_transcription(doc_id, ctx.transcription, avg_qa, "grouped")
                logger.info(f"[Orchestrator] Phase 3 (#299): criteria re-run done — "
                            f"QA {avg_qa:.2f}, {len(ctx.transcription)} chars")
                try:
                    from runstate import RunState, DONE, ERROR
                    state = RunState.load_or_new(doc_id)
                    state.stage_status["model_select"] = DONE
                    state.stage_status["kraken"] = DONE
                    state.artifacts["transcription"] = ctx.transcription
                    state.artifacts["a_meta"] = ctx.a_meta
                    if ctx.recognitions:      # BOTH passes — the first is evidence
                        state.artifacts["recognitions"] = ctx.recognitions
                    state.save()
                except Exception as e2:
                    logger.warning(f"[Orchestrator] Phase 3 RunState persist skipped: {e2}")
        except Exception as e:
            logger.error(f"[Orchestrator] Phase 3 (#299) criteria re-run failed: {e}")
            ctx.errors.append({"agent": "kraken_rerun", "phase": 3, "error": str(e)})
            _emit(on_phase, doc_id, "kraken", "A", status="error", error=str(e))
    elif use_ensemble and (ctx.description or {}).get("low_confidence"):
        # No usable description → the criteria would be just as empty as Phase 1's,
        # so a re-run burns GPU to reach the same blind picks. #301 fixes the cause
        # by describing from the image when the transcription is unreadable.
        logger.info("[Orchestrator] Phase 3 (#299) skipped — Agent B has no usable "
                    "description (low_confidence)")

    # PHASE 4: Agent C — entities across the order
    if ctx.transcription:
        try:
            ctx.entities = agent_c.extract_entities(doc_id, ctx.transcription)
            try:
                from runstate import RunState, DONE, ERROR
                state = RunState.load_or_new(doc_id)
                state.stage_status["agent_c"] = DONE
                state.artifacts["entities"] = ctx.entities
                state.save()
            except Exception as e2:
                logger.warning(f"[Orchestrator] Group Phase 3 RunState persist skipped: {e2}")
            _emit(on_phase, doc_id, "agent_c", "C",
                  output=(ctx.entities or {}).get("entities") or ctx.entities,
                  decision=f"{len((ctx.entities or {}).get('entities', []) or [])} entities")
        except Exception as e:
            logger.error(f"[Orchestrator] Agent C fehlgeschlagen: {e}")
            ctx.errors.append({"agent": "C", "error": str(e)})
            _emit(on_phase, doc_id, "agent_c", "C", status="error", error=str(e))

    # PHASE 5: optional corpus analysis over just this order
    if run_agent_d:
        try:
            _d_result = agent_d.analyse_corpus(corpus_name=doc_id, doc_ids=[doc_id])
            _emit(on_phase, doc_id, "agent_d", "D", output=_d_result,
                  decision="corpus analysis done")
        except Exception as e:
            ctx.errors.append({"agent": "D", "error": str(e)})
            _emit(on_phase, doc_id, "agent_d", "D", status="error", error=str(e))

    _save_pipeline_result(doc_id, ctx)
    _published, _detail = _publish_outputs(doc_id, ctx.source_url)
    _emit(on_phase, doc_id, "publish", "publish_github",
          output=ctx.source_url or doc_id, decision=_detail)
    logger.info(f"[Orchestrator] Order fertig: {doc_id} (QA {avg_qa:.2f}, {len(pages)} Seiten)")
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


def _save_pipeline_result(doc_id: str, ctx: PipelineContext, *, from_runstate: bool = False):
    """Write <doc_id>_pipeline.json.

    When ``from_runstate=True`` (normal path, #225): the RunState at
    data/runs/<doc_id>.json is the authoritative record; pipeline.json is
    derived from it so it is always consistent with what actually ran.
    When ``from_runstate=False`` (legacy callers / back-compat): writes
    ctx.to_json() directly.
    """
    out = config.OUTPUTS_DIR / f"{doc_id}_pipeline.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    if from_runstate:
        try:
            from runstate import RunState, DONE, ERROR
            state = RunState.load(doc_id)
            # Derive the same field-shape that ctx.to_json() used to produce
            pipeline = {
                "doc_id": doc_id,
                "transcription": state.artifacts.get("transcription", ""),
                "description": state.artifacts.get("description", {}),
                "entities": state.artifacts.get("entities", {}),
                "errors": ctx.errors,
                # a_meta derived from RunState artifacts
                "a_meta": state.artifacts.get("a_meta", {}),
                # all OCR engine candidates (#234 P1-3)
                # RecognitionResult is a Pydantic model — serialise to dict
                "recognitions": [
                    r.model_dump() if hasattr(r, "model_dump") else r
                    for r in state.artifacts.get("recognitions", [])
                ],
            }
            if state.source_url:
                pipeline["source_url"] = state.source_url
            with open(out, "w", encoding="utf-8") as f:
                json.dump(pipeline, f, ensure_ascii=False, indent=2)
            logger.info(f"[Orchestrator] Pipeline-Resultat (derived from RunState): {out}")
        except Exception as e:
            # Fallback: if RunState load fails for any reason, fall back to ctx
            logger.warning(f"[Orchestrator] RunState derive failed ({doc_id}), "
                           f"falling back to ctx.to_json(): {e}")
            with open(out, "w", encoding="utf-8") as f:
                json.dump(ctx.to_json(), f, ensure_ascii=False, indent=2)
    else:
        # Legacy / back-compat path (e.g. direct callers)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(ctx.to_json(), f, ensure_ascii=False, indent=2)
        logger.info(f"[Orchestrator] Pipeline-Resultat: {out}")

    # Persist errors to the persistent meta error log
    _append_errors_to_log(doc_id, ctx.errors)